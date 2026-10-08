from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import PCMChunk, SynthesizedAudio, TTSError

if TYPE_CHECKING:
    from ..config import Settings


class MiniMaxTTS:
    """MiniMax HTTP T2A adapter for complete WAV or streaming PCM audio."""

    def __init__(self, settings: Settings, transport=None, client=None):
        key = settings.minimax_api_key.get_secret_value().strip()
        if not key:
            raise ValueError("Set MINIMAX_API_KEY in .env before selecting MiniMax TTS")
        self.settings = settings
        if client is not None:
            self.client = client
            return
        import httpx

        base_url = settings.tts_base_url.strip() or settings.minimax_base_url
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=settings.tts_timeout_seconds,
            transport=transport,
        )

    def _payload(self, text: str, *, stream=False) -> dict:
        text = text.strip()
        if not text:
            raise TTSError("不能合成空文本。")
        if len(text) > 10_000:
            raise TTSError("TTS 文本超过 10000 字符限制。")
        payload = {
            "model": self.settings.tts_model,
            "text": text,
            "stream": stream,
            "language_boost": self.settings.tts_language_boost,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self.settings.tts_voice_id,
                "speed": self.settings.tts_speed,
                "vol": self.settings.tts_volume,
                "pitch": self.settings.tts_pitch,
            },
            "audio_setting": {
                "sample_rate": self.settings.tts_sample_rate,
                "bitrate": self.settings.tts_bitrate,
                "format": "pcm" if stream else "wav",
                "channel": 1,
            },
        }
        if stream:
            payload["stream_options"] = {"exclude_aggregated_audio": True}
        return payload

    async def synthesize(self, text: str) -> SynthesizedAudio:
        payload = self._payload(text)
        try:
            response = await self.client.post("t2a_v2", json=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("base_resp", {}).get("status_code", 0) != 0:
                raise TTSError("MiniMax 拒绝了语音合成请求。")
            returned_format = body.get("extra_info", {}).get("audio_format")
            if returned_format is not None and returned_format != "wav":
                raise TTSError("MiniMax 返回的语音格式与请求不一致。")
            encoded = (body.get("data") or {}).get("audio")
            if not isinstance(encoded, str) or not encoded:
                raise TTSError("MiniMax 没有返回语音数据。")
            if len(encoded) > self.settings.tts_max_audio_bytes * 2:
                raise TTSError("MiniMax 返回的语音数据超过大小限制。")
            try:
                audio = bytes.fromhex(encoded)
            except ValueError as exc:
                raise TTSError("MiniMax 返回的语音编码无效。") from exc
            if not audio:
                raise TTSError("MiniMax 返回了空语音。")
            return SynthesizedAudio(audio, "wav")
        except TTSError:
            raise
        except Exception as exc:
            # Do not expose response bodies, credentials or provider details to callers.
            raise TTSError("MiniMax 语音合成请求失败。") from exc

    async def synthesize_stream(self, text: str):
        """Incremental PCM via HTTP SSE; /speech keeps using synthesize()."""
        payload = self._payload(text, stream=True)
        total = 0
        pending = b""
        try:
            async with self.client.stream("POST", "t2a_v2", json=payload) as response:
                response.raise_for_status()
                async for raw in _sse_events(response, self.settings.tts_max_audio_bytes * 2 + 65536):
                    if raw == "[DONE]":
                        break
                    body = json.loads(raw)
                    if body.get("base_resp", {}).get("status_code", 0) != 0:
                        raise TTSError("MiniMax 拒绝了语音合成请求。")
                    info = body.get("extra_info") or {}
                    for key, expected in (("audio_format", "pcm"), ("audio_channel", 1),
                                          ("audio_sample_rate", self.settings.tts_sample_rate)):
                        if key in info and info[key] != expected:
                            raise TTSError("MiniMax 返回的语音格式与请求不一致。")
                    data = body.get("data") or {}
                    encoded = data.get("audio") or ""
                    if not isinstance(encoded, str) or len(encoded) % 2:
                        raise TTSError("MiniMax 返回的语音编码无效。")
                    if total + len(encoded) // 2 > self.settings.tts_max_audio_bytes:
                        raise TTSError("MiniMax 返回的语音数据超过大小限制。")
                    audio = bytes.fromhex(encoded)
                    total += len(audio)
                    pending += audio
                    size = len(pending) // 2 * 2
                    if size:
                        yield PCMChunk(pending[:size], self.settings.tts_sample_rate)
                        pending = pending[size:]
                    if data.get("status") == 2:
                        if not total or pending:
                            raise TTSError("MiniMax 返回的 PCM 音频为空或不完整。")
                        return
                raise TTSError("MiniMax 语音流在完成前中断。")
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError("MiniMax 流式语音合成失败。") from exc

    async def close(self):
        await self.client.aclose()


async def _sse_events(response, max_chars):
    lines = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if lines:
                yield "\n".join(lines)
                lines, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].lstrip(" ")
            size += len(value)
            if size > max_chars:
                raise TTSError("MiniMax 语音流事件超过大小限制。")
            lines.append(value)
    if lines:
        yield "\n".join(lines)
