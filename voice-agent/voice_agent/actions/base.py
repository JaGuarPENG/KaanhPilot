from typing import Protocol

from ..schemas import Action, ActionResult


class ActionExecutor(Protocol):
    async def execute(self, action: Action) -> ActionResult: ...
