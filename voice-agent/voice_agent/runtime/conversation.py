"""Keep voice requests in the same conversation session."""
from typing import Protocol

from ..schemas import AgentResponse


class Conversation(Protocol):
    async def handle(self, text: str) -> AgentResponse: ...


class LocalConversation:
    def __init__(self, agent):
        self.agent = agent
        self.session_id = None

    async def handle(self, text: str) -> AgentResponse:
        if self.session_id is not None:
            try:
                self.agent.sessions.get(self.session_id)
            except ValueError:
                self.session_id = None
        response = await self.agent.handle(text, self.session_id)
        self.session_id = response.session_id
        return response
