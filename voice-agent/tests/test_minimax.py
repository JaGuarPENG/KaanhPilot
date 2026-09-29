import httpx
import pytest

from voice_agent.config import Settings
from voice_agent.llm import MiniMaxClient, ProviderError


@pytest.mark.asyncio
async def test_minimax_request_and_content():
    def handler(request):
        import json
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.minimax.io/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-placeholder"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["reasoning_split"] is True
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": "{}"},
        }]})

    client = MiniMaxClient(Settings(_env_file=None, minimax_api_key="test-placeholder"),
                           transport=httpx.MockTransport(handler))
    try:
        assert await client.complete([{"role": "user", "content": "hi"}]) == "{}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_truncated_model_output_rejected():
    client = MiniMaxClient(Settings(_env_file=None, minimax_api_key="test-placeholder"),
                           transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                               "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]
                           })))
    try:
        with pytest.raises(ProviderError):
            await client.complete([])
    finally:
        await client.close()
