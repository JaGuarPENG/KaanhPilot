import struct


class PorcupineWakeDetector:
    """Optional dedicated keyword engine, with PCM re-framing independent of VAD frames."""

    def __init__(self, access_key: str, keyword_path: str, model_path: str,
                 sample_rate: int = 16000, sensitivity: float = 0.5):
        if not access_key or not keyword_path or not model_path:
            raise ValueError("Porcupine requires access key, keyword .ppn and language model .pv")
        import pvporcupine

        self.engine = pvporcupine.create(
            access_key=access_key, keyword_paths=[keyword_path], model_path=model_path,
            sensitivities=[sensitivity],
        )
        if self.engine.sample_rate != sample_rate:
            self.engine.delete()
            raise ValueError("Wake model sample rate does not match the microphone")
        self.buffer = bytearray()

    def process(self, pcm: bytes) -> bool:
        self.buffer.extend(pcm)
        size = self.engine.frame_length * 2
        while len(self.buffer) >= size:
            frame = bytes(self.buffer[:size])
            del self.buffer[:size]
            if self.engine.process(struct.unpack(f"<{self.engine.frame_length}h", frame)) >= 0:
                self.buffer.clear()
                return True
        return False

    def reset(self):
        self.buffer.clear()

    def close(self):
        self.engine.delete()
