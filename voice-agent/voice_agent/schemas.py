from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Action(StrictModel):
    action_id: str = Field(default_factory=lambda: uuid4().hex, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(StrictModel):
    reply: str = Field(min_length=1, max_length=4000)
    intent: str = Field(min_length=1, max_length=100)
    actions: list[Action] = Field(max_length=8)
    status: Literal["ready", "waiting_for_user", "rejected"]

    @model_validator(mode="after")
    def consistent_state(self):
        if self.status != "ready" and self.actions:
            raise ValueError("Clarification or rejection must not contain actions")
        if not self.reply.strip():
            raise ValueError("Reply must contain a response, question or rejection reason")
        return self


class ActionResult(StrictModel):
    action_id: str
    type: str
    status: Literal["simulated", "accepted", "unknown", "failed", "skipped"]
    details: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(StrictModel):
    reply: str
    intent: str
    actions: list[Action] = Field(default_factory=list)
    status: Literal["success", "waiting_for_user", "rejected", "failed", "error"]
    session_id: str | None = None
    action_results: list[ActionResult] = Field(default_factory=list)
    transcript: str | None = None


class ChatRequest(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def nonempty_text(self):
        if not self.text.strip():
            raise ValueError("Text must not be blank")
        return self


class SpeechRequest(StrictModel):
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def nonempty_text(self):
        if not self.text.strip():
            raise ValueError("Text must not be blank")
        return self
