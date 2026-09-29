"""Submit to the existing card-order API; never retry an ambiguous POST."""
import httpx

from .registry import OrderParameters
from ..schemas import Action, ActionResult


class TaskRunnerExecutor:
    def __init__(self, base_url: str, timeout: float = 8, *, transport=None):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.transport = transport

    async def execute(self, action: Action) -> ActionResult:
        if action.type != "submit_order":
            raise ValueError("TaskRunner only accepts submit_order")
        params = OrderParameters.model_validate(action.parameters)

        def result(status, **details):
            return ActionResult(action_id=action.action_id, type=action.type, status=status,
                                details={"executor": "taskrunner", **details})

        # A client scoped to a single submission closes even on cancellation/failure.
        # No automatic retries or redirects: the backend has no idempotency key.
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout,
                                         transport=self.transport, follow_redirects=False) as client:
                response = await client.post("api/orders", json=params.model_dump())
        except httpx.RequestError:
            return result("unknown", error="submission_uncertain")
        if 400 <= response.status_code < 500:
            return result("failed", error="order_rejected", http_status=response.status_code)
        if response.status_code != 201:
            return result("unknown", error="submission_uncertain")
        try:
            order = response.json()
            if (not isinstance(order, dict)
                    or not isinstance(order.get("order_id"), str) or not order["order_id"].strip()
                    or order.get("item_id") != params.item_id
                    or not isinstance(order.get("status"), str) or not order["status"].strip()):
                return result("unknown", error="invalid_order_receipt")
        except ValueError:
            return result("unknown", error="invalid_order_receipt")
        return result("accepted", order_id=order["order_id"], item_id=params.item_id,
                      order_status=order["status"])
