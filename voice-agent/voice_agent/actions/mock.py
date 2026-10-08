from ..schemas import Action, ActionResult


class MockExecutor:
    """No device/network side effects. Replace only after defining device safeguards."""

    async def execute(self, action: Action) -> ActionResult:
        return ActionResult(
            action_id=action.action_id,
            type=action.type,
            status="simulated",
            details={"executor": "mock", "parameters": action.parameters},
        )
