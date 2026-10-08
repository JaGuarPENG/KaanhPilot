from typing import AsyncIterable, Protocol


class ASRError(Exception):
    pass


class ASRProvider(Protocol):
    async def prepare(self) -> None: ...
    async def transcribe(self, audio: bytes) -> str: ...


class StreamingASRProvider(Protocol):
    async def transcribe_stream(self, frames: AsyncIterable[bytes]) -> str:
        """Consume 16kHz mono PCM16; return final text only after input ends."""
        ...


class DisabledASR:
    async def prepare(self) -> None:
        raise ASRError("请在 .env 中设置 ASR_PROVIDER=whisper 或 xfyun。")

    async def transcribe(self, audio: bytes) -> str:
        raise ASRError("请在 .env 中设置 ASR_PROVIDER=whisper 或 xfyun。")
