import asyncio
import queue
import sys
import threading
from typing import Protocol

from .config import AudioConfig


class AudioGapError(Exception):
    """Samples were lost; callers must discard partial utterances."""


class AudioSource(Protocol):
    async def read(self) -> bytes | None: ...
    def set_paused(self, paused: bool) -> None: ...


class MicrophoneSource:
    def __init__(self, config: AudioConfig, device=None):
        self.config, self.device = config, device
        self.queue = queue.Queue(maxsize=max(4, 1000 // config.frame_ms))
        self.paused = threading.Event()
        self.gap = threading.Event()
        self.generation = 0
        self.stream = None

    def _drain(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def set_paused(self, paused: bool):
        self.paused.set()
        self.generation += 1
        self._drain()
        self.gap.clear()
        if not paused:
            self.paused.clear()

    def _callback(self, data, frames, time_info, status):
        if self.paused.is_set():
            return
        generation = self.generation
        if status or frames != self.config.frame_samples:
            self.gap.set()
            return
        try:
            self.queue.put_nowait((generation, bytes(data)))
        except queue.Full:
            self.gap.set()

    def __enter__(self):
        if sys.byteorder != "little":
            raise RuntimeError("This microphone adapter requires little-endian PCM")
        import sounddevice as sd

        self.stream = sd.RawInputStream(
            samplerate=self.config.sample_rate, blocksize=self.config.frame_samples,
            channels=1, dtype="int16", device=self.device, callback=self._callback,
        )
        self.stream.start()
        return self

    async def read(self) -> bytes | None:
        if self.gap.is_set():
            self.set_paused(False)
            raise AudioGapError("Microphone overflow or missing audio frames")
        try:
            generation, frame = await asyncio.to_thread(self.queue.get, True, 0.25)
        except queue.Empty:
            return None
        if self.gap.is_set():
            self.set_paused(False)
            raise AudioGapError("Microphone overflow or missing audio frames")
        return frame if generation == self.generation else None

    def __exit__(self, *args):
        self.set_paused(True)
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
