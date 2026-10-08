from dataclasses import dataclass


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    frame_ms: int = 30
    pre_roll_ms: int = 300
    start_speech_ms: int = 60
    min_speech_ms: int = 240
    end_silence_ms: int = 800
    max_utterance_ms: int = 15000
    wake_timeout_seconds: float = 8

    def __post_init__(self):
        if self.sample_rate != 16000 or self.frame_ms not in (10, 20, 30):
            raise ValueError("Audio requires 16kHz mono PCM16 and 10/20/30ms frames")
        if not self.frame_ms <= self.start_speech_ms <= self.min_speech_ms:
            raise ValueError("Require frame_ms <= start_speech_ms <= min_speech_ms")
        if not self.start_speech_ms <= self.pre_roll_ms <= 2000:
            raise ValueError("Pre-roll must retain speech onset and be <= 2 seconds")
        if not self.frame_ms <= self.end_silence_ms <= 5000:
            raise ValueError("Silence timeout must be between one frame and 5 seconds")
        if not self.pre_roll_ms + self.min_speech_ms + self.end_silence_ms < self.max_utterance_ms:
            raise ValueError("Utterance limit must exceed pre-roll, speech and silence")
        if not self.max_utterance_ms <= 60000 or not 1 <= self.wake_timeout_seconds <= 60:
            raise ValueError("Utterance/wake timeout exceeds supported range")

    @property
    def frame_samples(self):
        return self.sample_rate * self.frame_ms // 1000

    @property
    def frame_bytes(self):
        return self.frame_samples * 2
