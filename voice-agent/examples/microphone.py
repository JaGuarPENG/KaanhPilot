"""Record one short utterance locally, send WAV to the local /audio API."""
import argparse
import io
import json

import httpx
import sounddevice as sd
import soundfile as sf

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=float, default=5)
parser.add_argument("--url", default="http://127.0.0.1:8000/audio")
parser.add_argument("--session-id")
args = parser.parse_args()
if not 0 < args.seconds <= 60:
    parser.error("--seconds must be between 0 and 60")
print(f"开始录音 {args.seconds} 秒，请讲话……")
samples = sd.rec(int(args.seconds * 16000), samplerate=16000, channels=1, dtype="float32")
sd.wait()
buffer = io.BytesIO()
sf.write(buffer, samples, 16000, format="WAV", subtype="PCM_16")
data = {"session_id": args.session_id} if args.session_id else {}
response = httpx.post(args.url, data=data, files={"file": ("voice.wav", buffer.getvalue(),
                                                        "audio/wav")}, timeout=300)
print(json.dumps(response.json(), ensure_ascii=False, indent=2))
response.raise_for_status()
