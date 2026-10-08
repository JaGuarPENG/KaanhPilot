from typing import Protocol


class ProviderError(Exception):
    pass


class LLMProvider(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> str: ...
    async def close(self) -> None: ...


