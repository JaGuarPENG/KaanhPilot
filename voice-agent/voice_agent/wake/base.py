from typing import Protocol


class WakeDetectorError(RuntimeError):
    """A local wake engine failed; stop listening instead of silently staying idle."""


class TranscriptWakeDetector(Protocol):
    def extract_command(self, text: str) -> str | None:
        """None=no wake; empty string=wake only; nonempty=wake plus command."""
        ...


class FrameWakeDetector(Protocol):
    def process(self, pcm: bytes) -> bool: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...
