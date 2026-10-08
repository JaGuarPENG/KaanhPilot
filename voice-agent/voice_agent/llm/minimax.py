from __future__ import annotations

from typing import TYPE_CHECKING

from .base import ProviderError

if TYPE_CHECKING:
    from ..config import Settings


class MiniMaxClient:
    def __init__(self, settings: Settings, transport=None):
        import httpx

        key = settings.minimax_api_key.get_secret_value()
        if not key:
            raise ValueError("Set MINIMAX_API_KEY in .env before selecting minimax")
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.minimax_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=settings.model_timeout_seconds,
            transport=transport,
        )

    async def complete(self, messages: list[dict[str, str]]) -> str:
        import httpx

        try:
            response = await self.client.post("chat/completions", json={
                "model": self.settings.minimax_model,
                "messages": messages,
                "thinking": {"type": self.settings.minimax_thinking},
                "reasoning_split": True,
                "max_completion_tokens": self.settings.max_completion_tokens,
            })
            response.raise_for_status()
            body = response.json()
            if body.get("base_resp", {}).get("status_code", 0) != 0:
                raise ProviderError("MiniMax rejected the request")
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ProviderError("MiniMax did not return a complete JSON decision")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError("MiniMax returned empty content")
            return content
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Do not relay provider bodies, headers, API keys or raw prompts to API users.
            raise ProviderError("MiniMax request failed") from exc

    async def close(self):
        await self.client.aclose()


