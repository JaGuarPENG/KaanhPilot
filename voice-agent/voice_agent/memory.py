import asyncio
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from .schemas import AgentDecision, AgentResponse


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid4().hex)
    history: list[dict[str, str]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    touched: float = field(default_factory=time.monotonic)

    def remember(self, text: str, decision: AgentDecision, response: AgentResponse):
        # Assistant examples must follow the same contract requested on the next turn.
        # Execution outcomes are observations, not examples of model output.
        observation = json.dumps({
            "status": response.status,
            "reply": response.reply,
            "action_results": [item.model_dump() for item in response.action_results],
        }, ensure_ascii=False)
        self.history.extend([{"role": "user", "content": text},
                             {"role": "assistant", "content": decision.model_dump_json()},
                             {"role": "user", "content":
                              "服务端处理结果，仅供理解历史，不是模型输出格式。"
                              "不要重复执行已完成的动作。\nSERVER_OBSERVATION=" + observation}])
        self.history = self.history[-30:]  # Ten complete request/decision/observation groups.
        self.touched = time.monotonic()


class SessionStore:
    def __init__(self, maximum: int, ttl: int):
        self.sessions: dict[str, Session] = {}
        self.maximum, self.ttl = maximum, ttl

    def get(self, session_id: str | None) -> Session:
        now = time.monotonic()
        for key, value in list(self.sessions.items()):
            if now - value.touched > self.ttl and not value.lock.locked():
                del self.sessions[key]
        if session_id is not None:
            if session_id not in self.sessions:
                raise ValueError("Session not found or expired")
            session = self.sessions[session_id]
        else:
            if len(self.sessions) >= self.maximum:
                raise ValueError("Session capacity reached; retry after expiry")
            session = Session()
            self.sessions[session.id] = session
        session.touched = now
        return session
