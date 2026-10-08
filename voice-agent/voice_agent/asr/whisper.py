from __future__ import annotations

import asyncio
import io
import threading
from typing import TYPE_CHECKING

from .base import ASRError

if TYPE_CHECKING:
    from ..config import Settings


class WhisperASR:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.settings.whisper_model, device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
            )

    async def prepare(self):
        def load():
            with self._lock:
                try:
                    self._ensure_model()
                except Exception as exc:
                    raise ASRError("本地语音模型加载失败，请检查依赖、模型路径或下载连接。") from exc

        await asyncio.to_thread(load)

    def _transcribe(self, audio: bytes) -> str:
        # Keep native decoding and inference off the ASGI event loop.
        with self._lock:
            try:
                from faster_whisper.audio import decode_audio

                samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
                if len(samples) / 16000 > self.settings.max_audio_seconds:
                    raise ASRError("音频过长，请缩短后重试。")
                self._ensure_model()
                segments, _ = self._model.transcribe(
                    samples, language=self.settings.whisper_language or None, vad_filter=True,
                )
                return "".join(segment.text for segment in segments).strip()
            except ASRError:
                raise
            except ImportError as exc:
                raise ASRError("语音识别依赖未安装，请运行 pip install -e '.[asr]'。") from exc
            except Exception as exc:
                raise ASRError("音频识别失败，请检查文件格式和本地模型配置。") from exc

    async def transcribe(self, audio: bytes) -> str:
        return await asyncio.to_thread(self._transcribe, audio)
