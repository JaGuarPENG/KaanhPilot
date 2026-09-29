import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from voice_agent.asr import ASRError, XFYunASR
from voice_agent.asr.xfyun import ResultAccumulator, create_authorized_url, wav_to_pcm16_mono_16k
from voice_agent.audio.encoding import pcm_to_wav
from voice_agent.bootstrap import build_services
from voice_agent.config import Settings


def settings(**overrides):
    values = dict(
        _env_file=None, log_enabled=False, asr_provider="xfyun",
        xfyun_app_id="test-app", xfyun_api_key="test-key", xfyun_api_secret="test-secret",
    )
    values.update(overrides)
    return Settings(**values)


def test_authorized_url_matches_documented_hmac_shape():
    instant = datetime(2019, 7, 10, 7, 35, 43, tzinfo=timezone.utc)
    url = create_authorized_url(
        "wss://iat-api.xfyun.cn/v2/iat", "test-key", "test-secret", instant,
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "wss"
    assert query["host"] == ["iat-api.xfyun.cn"]
    assert query["date"] == ["Wed, 10 Jul 2019 07:35:43 GMT"]
    origin = "host: iat-api.xfyun.cn\ndate: Wed, 10 Jul 2019 07:35:43 GMT\nGET /v2/iat HTTP/1.1"
    expected = base64.b64encode(hmac.new(b"test-secret", origin.encode(), hashlib.sha256).digest()).decode()
    authorization = base64.b64decode(query["authorization"][0]).decode()
    assert 'api_key="test-key"' in authorization
    assert f'signature="{expected}"' in authorization
    assert "test-secret" not in url


@pytest.mark.parametrize("endpoint", [
    "http://iat-api.xfyun.cn/v2/iat",
    "wss://user@iat-api.xfyun.cn/v2/iat",
    "wss://iat-api.xfyun.cn/v2/iat?existing=value",
])
def test_authorized_url_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(ASRError):
        create_authorized_url(endpoint, "key", "secret")


def test_wav_validation_accepts_listener_format_and_rejects_wrong_rate():
    pcm = b"\x01\x00" * 1600
    assert wav_to_pcm16_mono_16k(pcm_to_wav(pcm), 60) == pcm
    with pytest.raises(ASRError, match="16kHz"):
        wav_to_pcm16_mono_16k(pcm_to_wav(pcm, 8000), 60)


def test_dynamic_result_replaces_previous_sequences():
    result = ResultAccumulator()
    result.add({"sn": 1, "ws": [{"cw": [{"w": "你好"}]}]})
    result.add({"sn": 2, "ws": [{"cw": [{"w": "世界"}]}]})
    result.add({"sn": 3, "pgs": "rpl", "rg": [2, 2],
                "ws": [{"cw": [{"w": "小助手"}]}]})
    assert result.text() == "你好小助手"


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = responses
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.responses:
            raise StopAsyncIteration
        return json.dumps(self.responses.pop(0))


class FakeConnection:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_xfyun_transcribe_sends_documented_frames_and_parses_correction():
    socket = FakeWebSocket([
        {"code": 0, "message": "success", "data": {"status": 0, "result": {
            "sn": 1, "ws": [{"cw": [{"w": "打开"}]}],
        }}},
        {"code": 0, "message": "success", "data": {"status": 1, "result": {
            "sn": 2, "ws": [{"cw": [{"w": "卧室"}]}],
        }}},
        {"code": 0, "message": "success", "data": {"status": 1, "result": {
            "sn": 3, "pgs": "rpl", "rg": [2, 2], "ws": [{"cw": [{"w": "客厅"}]}],
        }}},
        {"code": 0, "message": "success", "data": {"status": 2}},
    ])
    urls = []

    def connect(url):
        urls.append(url)
        return FakeConnection(socket)

    async def no_sleep(_):
        pass

    fixed = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)
    provider = XFYunASR(settings(), connect=connect, sleep=no_sleep, now=lambda: fixed)
    audio = pcm_to_wav(b"\x01\x00" * 2000)
    assert await provider.transcribe(audio) == "打开客厅"
    assert len(urls) == 1
    assert socket.sent[0]["common"] == {"app_id": "test-app"}
    assert socket.sent[0]["business"]["dwa"] == "wpgs"
    assert socket.sent[0]["data"]["status"] == 0
    assert len(base64.b64decode(socket.sent[0]["data"]["audio"])) == 1280
    assert socket.sent[-1] == {"data": {"status": 2}}


@pytest.mark.asyncio
async def test_xfyun_requires_all_three_credentials_before_connection():
    provider = XFYunASR(settings(xfyun_api_secret=""))
    with pytest.raises(ASRError, match="XFYUN_APP_ID"):
        await provider.prepare()


@pytest.mark.asyncio
async def test_provider_error_redacts_credentials():
    socket = FakeWebSocket([{
        "code": 10005,
        "message": "bad test-secret test-key test-app",
        "data": {"status": 2},
    }])

    async def no_sleep(_):
        pass

    provider = XFYunASR(settings(), connect=lambda _: FakeConnection(socket), sleep=no_sleep)
    with pytest.raises(ASRError) as error:
        await provider.transcribe(pcm_to_wav(b"\x00\x00" * 160))
    assert "10005" in str(error.value)
    assert "test-secret" not in str(error.value)
    assert "test-key" not in str(error.value)
    assert "test-app" not in str(error.value)


def test_bootstrap_selects_xfyun_provider():
    agent, llm, asr = build_services(settings())
    try:
        assert isinstance(asr, XFYunASR)
    finally:
        agent.audit.close()
