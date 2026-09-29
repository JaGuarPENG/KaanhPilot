from fastapi.testclient import TestClient

from voice_agent.config import Settings
from voice_agent.main import create_app
from voice_agent.tts import SynthesizedAudio, TTSError


def settings(**overrides):
    return Settings(_env_file=None, llm_provider="mock", log_enabled=False, **overrides)


def test_chat_returns_json_contract():
    with TestClient(create_app(settings())) as client:
        result = client.post("/chat", json={"text": "打开客厅的灯"})
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "success"
        assert body["action_results"][0]["status"] == "simulated"


def test_invalid_request_returns_contract():
    with TestClient(create_app(settings())) as client:
        response = client.post("/chat", json={"text": " "})
        assert response.status_code == 422
        assert response.json()["status"] == "error"


def test_audio_feeds_transcript_to_agent():
    class FakeASR:
        async def transcribe(self, audio):
            assert audio == b"sample fixture"
            return "打开客厅的灯"

    with TestClient(create_app(settings(), asr=FakeASR())) as client:
        response = client.post("/audio", files={"file": ("voice.wav", b"sample fixture")})
        assert response.status_code == 200
        body = response.json()
        assert body["transcript"] == "打开客厅的灯"
        assert body["actions"][0]["type"] == "set_light"


def test_oversized_audio_is_rejected_before_asr():
    with TestClient(create_app(settings(max_audio_bytes=4))) as client:
        response = client.post("/audio", files={"file": ("voice.wav", b"12345")})
        assert response.status_code == 413
        assert response.json()["actions"] == []


def test_websocket_recovers_from_bad_json():
    with TestClient(create_app(settings())) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("invalid")
            assert ws.receive_json()["status"] == "error"
            ws.send_json({"text": "你好"})
            assert ws.receive_json()["status"] == "success"


def test_direct_execution_and_removed_confirmation_api():
    with TestClient(create_app(settings(scene="robot_demo"))) as client:
        body = client.post("/chat", json={"text": "去厨房"}).json()
        assert body["status"] == "success"
        assert body["action_results"][0]["status"] == "simulated"
        assert "need_confirmation" not in body
        assert "confirmation_id" not in body
        assert client.post("/confirm", json={}).status_code == 404
        schema = client.get("/schema/response").json()
        assert "rejected" in schema["properties"]["status"]["enum"]
        assert "awaiting_confirmation" not in schema["properties"]["status"]["enum"]


def test_speech_endpoint_returns_audio_bytes():
    class FakeTTS:
        async def synthesize(self, text):
            assert text == "你好"
            return SynthesizedAudio(b"RIFF-test", "wav")

        async def close(self):
            pass

    with TestClient(create_app(settings(), tts=FakeTTS())) as client:
        response = client.post("/speech", json={"text": "你好"})
        assert response.status_code == 200
        assert response.content == b"RIFF-test"
        assert response.headers["content-type"] == "audio/wav"


def test_speech_endpoint_handles_provider_failure_without_audio():
    class FailingTTS:
        async def synthesize(self, text):
            raise TTSError("test failure")

        async def close(self):
            pass

    active = settings(tts_provider="minimax", minimax_api_key="test-placeholder")
    with TestClient(create_app(active, tts=FailingTTS())) as client:
        response = client.post("/speech", json={"text": "你好"})
        assert response.status_code == 502
        assert response.headers["content-type"].startswith("application/json")
