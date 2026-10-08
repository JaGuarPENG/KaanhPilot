from dataclasses import dataclass
from typing import AsyncIterator, Protocol


class TTSError(Exception):
    pass


class PlaybackError(Exception):
    pass


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    format: str = "wav"

    @property
    def media_type(self) -> str:
        return {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}.get(
            self.format, "application/octet-stream"
        )


class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> SynthesizedAudio: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class PCMChunk:
    """Signed little-endian PCM16, mono; data contains complete samples."""
    data: bytes
    sample_rate: int


class StreamingTTSProvider(Protocol):
    def synthesize_stream(self, text: str) -> AsyncIterator[PCMChunk]: ...


class AudioPlayer(Protocol):
    async def play(self, audio: SynthesizedAudio) -> None: ...


class DisabledTTS:
    async def synthesize(self, text: str) -> SynthesizedAudio:
        raise TTSError("TTS 未启用，请设置 TTS_PROVIDER=minimax。")

    async def close(self):
        pass
