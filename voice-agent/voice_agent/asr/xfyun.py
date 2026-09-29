"""科大讯飞语音听写（流式版）WebAPI adapter."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import hmac
import io
import json
from typing import TYPE_CHECKING, AsyncIterable, Callable
from urllib.parse import urlencode, urlsplit
import wave

from .base import ASRError

if TYPE_CHECKING:
    from ..config import Settings


FRAME_BYTES = 1280
FRAME_INTERVAL_SECONDS = 0.04
MAX_XFYUN_AUDIO_SECONDS = 60


def create_authorized_url(endpoint: str, api_key: str, api_secret: str,
                          now: datetime | None = None) -> str:
    """Create the short-lived HMAC WebSocket URL required by XFYun."""
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "wss" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ASRError("XFYUN_ASR_URL 必须是无账号、查询参数和片段的 wss 地址。")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    date = format_datetime(instant.astimezone(timezone.utc), usegmt=True)
    path = parsed.path or "/"
    host = parsed.netloc
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    digest = hmac.new(api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()
    return f"{endpoint}?{urlencode({'authorization': authorization, 'date': date, 'host': host})}"


def wav_to_pcm16_mono_16k(audio: bytes, max_seconds: float) -> bytes:
    """Validate and unwrap the format accepted by the configured XFYun request."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getcomptype() != "NONE" or wav.getnchannels() != 1
                    or wav.getsampwidth() != 2 or wav.getframerate() != 16000):
                raise ASRError("讯飞 STT 需要 16kHz、16-bit、单声道 PCM WAV 音频。")
            frames = wav.getnframes()
            if frames <= 0:
                raise ASRError("音频为空，无法识别。")
            duration = frames / wav.getframerate()
            limit = min(max_seconds, MAX_XFYUN_AUDIO_SECONDS)
            if duration > limit:
                raise ASRError(f"讯飞语音听写单次音频不能超过 {limit:g} 秒。")
            pcm = wav.readframes(frames)
    except ASRError:
        raise
    except (EOFError, wave.Error) as exc:
        raise ASRError("讯飞 STT 仅接受有效的 PCM WAV 文件。") from exc
    if len(pcm) != frames * 2:
        raise ASRError("WAV 音频数据不完整。")
    return pcm


def result_text(result: dict) -> str:
    words = []
    for item in result.get("ws", []):
        candidates = item.get("cw") or []
        if candidates:
            words.append(str(candidates[0].get("w", "")))
    return "".join(words)


class ResultAccumulator:
    """Apply normal and wpgs replacement results using their sequence numbers."""
    def __init__(self):
        self.parts: dict[int, str] = {}

    def add(self, result: dict) -> None:
        try:
            sequence = int(result["sn"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ASRError("讯飞返回了缺少有效序号的识别结果。") from exc
        if result.get("pgs") == "rpl":
            replacement = result.get("rg")
            if not (isinstance(replacement, list) and len(replacement) == 2):
                raise ASRError("讯飞返回了无效的动态修正范围。")
            try:
                start, end = int(replacement[0]), int(replacement[1])
            except (TypeError, ValueError) as exc:
                raise ASRError("讯飞返回了无效的动态修正范围。") from exc
            for index in range(start, end + 1):
                self.parts.pop(index, None)
        self.parts[sequence] = result_text(result)

    def text(self) -> str:
        return "".join(self.parts[index] for index in sorted(self.parts)).strip()


class XFYunASR:
    def __init__(self, settings: Settings, *, connect: Callable | None = None,
                 sleep: Callable = asyncio.sleep, now: Callable | None = None):
        self.settings = settings
        self._connect = connect
        self._sleep = sleep
        self._now = now

    def _credentials(self) -> tuple[str, str, str]:
        return (
            self.settings.xfyun_app_id.get_secret_value().strip(),
            self.settings.xfyun_api_key.get_secret_value().strip(),
            self.settings.xfyun_api_secret.get_secret_value().strip(),
        )

    async def prepare(self) -> None:
        app_id, api_key, api_secret = self._credentials()
        if not all((app_id, api_key, api_secret)):
            raise ASRError("ASR_PROVIDER=xfyun 时必须配置 XFYUN_APP_ID、XFYUN_API_KEY 和 XFYUN_API_SECRET。")
        create_authorized_url(self.settings.xfyun_asr_url, api_key, api_secret,
                              self._now() if self._now else None)

    def _safe_provider_error(self, code, message) -> ASRError:
        cleaned = str(message or "unknown error").replace("\n", " ")[:160]
        for secret in self._credentials():
            if secret:
                cleaned = cleaned.replace(secret, "[REDACTED]")
        return ASRError(f"讯飞语音识别失败（code={code}）：{cleaned}")

    def _connection(self, url: str):
        if self._connect is not None:
            return self._connect(url)
        try:
            from websockets import connect
        except ImportError as exc:
            raise ASRError("缺少讯飞 WebAPI 依赖，请运行 pip install -e '.[xfyun]'。") from exc
        return connect(url, open_timeout=self.settings.xfyun_asr_timeout_seconds,
                       close_timeout=5, max_size=2 * 1024 * 1024)

    def _first_message(self, app_id: str, chunk: bytes) -> dict:
        business = {
            "language": self.settings.xfyun_asr_language,
            "domain": self.settings.xfyun_asr_domain,
            "accent": self.settings.xfyun_asr_accent,
            "ptt": 1,
        }
        if (self.settings.xfyun_asr_dynamic_correction
                and self.settings.xfyun_asr_language == "zh_cn"):
            business["dwa"] = "wpgs"
        return {
            "common": {"app_id": app_id},
            "business": business,
            "data": {
                "status": 0,
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": base64.b64encode(chunk).decode(),
            },
        }

    async def _exchange(self, websocket, frames: AsyncIterable[bytes], app_id: str,
                        *, live=False) -> str:
        accumulator = ResultAccumulator()
        input_finished = False

        async def send_audio():
            nonlocal input_finished
            first = True
            async for chunk in frames:
                message = self._first_message(app_id, chunk) if first else {
                    "data": {
                        "status": 1,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": base64.b64encode(chunk).decode(),
                    }
                }
                await websocket.send(json.dumps(message))
                first = False
                await self._sleep(FRAME_INTERVAL_SECONDS)
            if first:
                raise ASRError("音频为空，无法识别。")
            input_finished = True
            await websocket.send(json.dumps({"data": {"status": 2}}))

        async def receive_results():
            async for raw in websocket:
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ASRError("讯飞返回了无法解析的响应。") from exc
                code = response.get("code")
                if code != 0:
                    raise self._safe_provider_error(code, response.get("message"))
                data = response.get("data") or {}
                if data.get("result"):
                    accumulator.add(data["result"])
                if data.get("status") == 2:
                    if live and not input_finished:
                        raise ASRError("讯飞在录音发送结束前终止识别，请重新说出完整指令。")
                    return accumulator.text()
            raise ASRError("讯飞连接在返回最终识别结果前已关闭。")

        sender = asyncio.create_task(send_audio())
        receiver = asyncio.create_task(receive_results())
        try:
            _, transcript = await asyncio.gather(sender, receiver)
            return transcript
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    async def transcribe(self, audio: bytes) -> str:
        pcm = wav_to_pcm16_mono_16k(audio, self.settings.max_audio_seconds)

        async def frames():
            for start in range(0, len(pcm), FRAME_BYTES):
                yield pcm[start:start + FRAME_BYTES]

        return await self._transcribe_frames(frames())

    async def transcribe_stream(self, frames: AsyncIterable[bytes]) -> str:
        async def chunks():
            buffer = bytearray()
            total = 0
            limit = min(self.settings.max_audio_seconds, MAX_XFYUN_AUDIO_SECONDS) * 32000
            async for pcm in frames:
                if not pcm or len(pcm) % 2:
                    raise ASRError("流式识别需要完整 PCM16 音频帧。")
                total += len(pcm)
                if total > limit:
                    raise ASRError("音频过长，已丢弃本次识别。")
                buffer.extend(pcm)
                while len(buffer) >= FRAME_BYTES:
                    yield bytes(buffer[:FRAME_BYTES])
                    del buffer[:FRAME_BYTES]
            if buffer:
                yield bytes(buffer)

        return await self._transcribe_frames(chunks(), live=True)

    async def _transcribe_frames(self, frames: AsyncIterable[bytes], *, live=False) -> str:
        await self.prepare()
        app_id, api_key, api_secret = self._credentials()
        url = create_authorized_url(self.settings.xfyun_asr_url, api_key, api_secret,
                                    self._now() if self._now else None)
        try:
            async with self._connection(url) as websocket:
                return await asyncio.wait_for(
                    self._exchange(websocket, frames, app_id, live=live),
                    timeout=self.settings.xfyun_asr_timeout_seconds,
                )
        except ASRError:
            raise
        except TimeoutError as exc:
            raise ASRError("讯飞语音识别超时，请检查网络或稍后重试。") from exc
        except Exception as exc:
            raise ASRError("无法连接科大讯飞语音听写服务，请检查网络、密钥和系统时间。") from exc
