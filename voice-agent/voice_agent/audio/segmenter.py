import math
from collections import deque
from dataclasses import dataclass
from typing import Literal

from .config import AudioConfig


@dataclass(frozen=True)
class Utterance:
    pcm: bytes
    reason: Literal["silence", "too_short", "limit"]
    speech_ms: int


class SpeechSegmenter:
    """Pure frame state machine. No microphone, ASR, model or wall clock dependency."""

    def __init__(self, config: AudioConfig):
        self.config = config
        self.pre_roll = deque(maxlen=math.ceil(config.pre_roll_ms / config.frame_ms))
        self.reset()

    def reset(self):
        self.started_pcm = b""
        self.pre_roll.clear()
        self.frames = []
        self.recording = False
        self.start_ms = 0
        self.speech_ms = 0
        self.silence_ms = 0

    def feed(self, pcm: bytes, voiced: bool) -> Utterance | None:
        self.started_pcm = b""
        if len(pcm) != self.config.frame_bytes:
            raise ValueError("Incorrect PCM frame length")
        ms = self.config.frame_ms
        if not self.recording:
            self.pre_roll.append(pcm)
            self.start_ms = self.start_ms + ms if voiced else 0
            if self.start_ms < self.config.start_speech_ms:
                return None
            self.recording = True
            self.frames = list(self.pre_roll)
            self.started_pcm = b"".join(self.frames)
            self.pre_roll.clear()
            self.speech_ms = self.start_ms
        else:
            self.frames.append(pcm)
            if voiced:
                self.speech_ms += ms
        self.silence_ms = 0 if voiced else self.silence_ms + ms
        reason = None
        if len(self.frames) * ms >= self.config.max_utterance_ms:
            # Never execute a command truncated by the recording limit.
            reason = "limit"
        elif self.silence_ms >= self.config.end_silence_ms:
            reason = "silence" if self.speech_ms >= self.config.min_speech_ms else "too_short"
        if reason is None:
            return None
        result = Utterance(b"".join(self.frames), reason, self.speech_ms)
        self.reset()
        return result
