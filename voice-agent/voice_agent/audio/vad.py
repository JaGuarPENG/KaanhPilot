from typing import Protocol


class VoiceActivityDetector(Protocol):
    def is_speech(self, pcm: bytes) -> bool: ...


class WebRTCVAD:
    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        try:
            import webrtcvad
        except ImportError as exc:
            raise RuntimeError("Install microphone dependencies: pip install -e '.[voice]'") from exc
        self.sample_rate = sample_rate
        self.engine = webrtcvad.Vad(aggressiveness)

    def is_speech(self, pcm: bytes) -> bool:
        return self.engine.is_speech(pcm, self.sample_rate)
