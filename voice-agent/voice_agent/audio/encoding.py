import io
import wave


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    if len(pcm) % 2:
        raise ValueError("PCM16 requires complete two-byte samples")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
