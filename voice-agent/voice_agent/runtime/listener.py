"""Wake/utterance lifecycle. Inject sources, VAD, ASR, wake detectors and agent independently."""
from __future__ import annotations

import asyncio
import time
import math
from collections import deque
from typing import Callable

from ..asr.base import ASRProvider
from .asr_stream import StreamingRecognition
from ..audio.config import AudioConfig
from ..audio.encoding import pcm_to_wav
from ..audio.segmenter import SpeechSegmenter
from ..audio.source import AudioGapError, AudioSource
from ..audio.vad import VoiceActivityDetector
from ..wake.base import FrameWakeDetector, TranscriptWakeDetector, WakeDetectorError
from ..wake.keyword import KeywordWakeDetector
from .conversation import Conversation
from ..observability import AuditSink, NullAudit


class VoiceListener:
    def __init__(self, config: AudioConfig, vad: VoiceActivityDetector, asr: ASRProvider,
                 conversation: Conversation, *, keyword: TranscriptWakeDetector | None = None,
                 frame_wake: FrameWakeDetector | None = None,
                 emit: Callable[[dict], None] | None = None,
                 set_paused: Callable[[bool], None] | None = None, clock=time.monotonic,
                 audit: AuditSink | None = None, speaker=None, streaming_asr=True):
        self.config, self.vad, self.asr = config, vad, asr
        self.conversation = conversation
        self.keyword = getattr(frame_wake, "keyword", None) or keyword or KeywordWakeDetector()
        self.frame_wake = frame_wake
        self.emit = emit or (lambda event: None)
        self.set_paused = set_paused or (lambda paused: None)
        self.clock = clock
        self.audit = audit if audit is not None else NullAudit()
        self.speaker = speaker
        self.streaming_asr = streaming_asr and callable(getattr(asr, "transcribe_stream", None))
        self._stream = None
        self.segmenter = SpeechSegmenter(config)
        self.state = "idle"
        self.deadline = 0.0
        self._wake_frames = deque(maxlen=max(1, math.ceil(config.pre_roll_ms / config.frame_ms)))

    def event(self, name: str, **fields):
        self.emit({"event": name, "state": self.state, **fields})
        self.audit.record(f"voice.{name}", state=self.state, **fields)

    def reset(self):
        self.state = "idle"
        self.deadline = 0.0
        self.segmenter.reset()
        self._wake_frames.clear()
        if self.frame_wake is not None:
            self.frame_wake.reset()

    def arm(self):
        self.state = "armed"
        self.deadline = self.clock() + self.config.wake_timeout_seconds
        self.event("awake", message="已唤醒，请说指令。")

    def tick(self):
        if self.state == "armed" and not self.segmenter.recording and self.clock() >= self.deadline:
            self.reset()
            self.event("wake_timeout", message="未收到指令，返回等待唤醒。")

    async def process_frame(self, pcm: bytes):
        try:
            await self._process_frame(pcm)
        except asyncio.CancelledError:
            await self._close_stream()
            self.reset()
            raise
        except WakeDetectorError:
            await self._close_stream()
            raise
        except Exception:
            await self._close_stream()
            self.reset()
            self.event("error", message="识别或处理失败，已返回等待唤醒。")

    async def _close_stream(self):
        if self._stream is not None:
            stream, self._stream = self._stream, None
            await stream.close()

    async def _process_frame(self, pcm: bytes):
        self.tick()
        if len(pcm) != self.config.frame_bytes:
            raise ValueError("Incorrect PCM frame length")
        if self.state == "idle" and self.frame_wake is not None:
            retain_audio = getattr(self.frame_wake, "use_preroll", False)
            if retain_audio:
                self._wake_frames.append(pcm)
            try:
                detected = self.frame_wake.process(pcm)
            except Exception as exc:
                raise WakeDetectorError(str(exc)) from exc
            if detected:
                self.segmenter.reset()
                self.arm()
                frames = list(self._wake_frames)
                self._wake_frames.clear()
                # Replay only after waking: idle audio never starts cloud ASR.
                for frame in frames:
                    await self._process_frame(frame)
            return
        recording = self.segmenter.recording
        utterance = self.segmenter.feed(pcm, self.vad.is_speech(pcm))
        if not recording and self.segmenter.recording:
            self.event("speech_start")
            if self.streaming_asr:
                self._stream = StreamingRecognition(self.asr)
                self._stream.feed(self.segmenter.started_pcm)
        if utterance is not None and utterance.reason != "silence":
            await self._close_stream()
            if utterance.reason == "limit":
                self.reset()
            self.event("utterance_rejected", reason=utterance.reason)
            return
        if self._stream is not None and recording:
            self._stream.feed(pcm)
        if utterance is None:
            return
        self.event("speech_end", reason="silence", speech_ms=utterance.speech_ms)
        previous = self.state
        self.state = "processing"
        self.set_paused(True)
        try:
            if self._stream is not None:
                transcript = (await self._stream.finish()).strip()
            else:
                transcript = (await self.asr.transcribe(
                    pcm_to_wav(utterance.pcm, self.config.sample_rate)
                )).strip()
            if not transcript or len(transcript) > 8000:
                self.state = previous
                self.event("utterance_rejected", reason="empty_or_long_transcript")
                return
            if previous == "idle":
                command = self.keyword.extract_command(transcript)
                if command is None:
                    self.reset()
                    # Background transcripts are neither logged nor sent to MiniMax.
                    return
                self.arm()
            else:
                # Strip a repeated wake prefix, but do not require it while armed.
                extracted = self.keyword.extract_command(transcript)
                command = transcript if extracted is None else extracted
            if not command:
                if previous == "armed":
                    self.arm()
                return
            self.state = "processing"
            self.event("command", text=command)
            response = await self.conversation.handle(command)
            response.transcript = command
            self.event("agent_response", response=response.model_dump())
            if self.speaker is not None:
                self.event("tts_start")
                try:
                    await self.speaker.speak(response.reply)
                except Exception as exc:
                    # The command may already have executed. TTS failure must never make it retry.
                    self.event("tts_error", message="语音合成或播放失败。",
                               error_type=type(exc).__name__)
                else:
                    self.event("tts_finished")
            self.reset()
        except Exception:
            self.reset()
            self.event("error", message="识别或处理失败，已返回等待唤醒。")
        finally:
            await self._close_stream()
            self.segmenter.reset()
            self.set_paused(False)

    async def run(self, source: AudioSource):
        try:
            await self._run(source)
        finally:
            await self._close_stream()
            self.reset()

    async def _run(self, source: AudioSource):
        self.event("listening", message="正在等待唤醒词。")
        while True:
            try:
                frame = await source.read()
            except AudioGapError:
                await self._close_stream()
                self.reset()
                self.event("audio_gap", message="音频丢帧，已丢弃本次录音，请重新唤醒。")
                continue
            if frame is None:
                self.tick()
            else:
                await self.process_frame(frame)
