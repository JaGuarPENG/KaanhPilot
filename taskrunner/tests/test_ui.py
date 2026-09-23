from __future__ import annotations

import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from taskrunner.taskrunner_contracts import (
    OrderSnapshot,
    OrderStatus,
    QueueSnapshot,
    RobotTaskSnapshot,
    RobotTaskType,
    RunnerState,
    RunnerStatusSnapshot,
    TaskStatus,
)
from taskrunner.test_ui import TaskRunnerHTTPServer, handler_for


def make_order(order_id: str = "order-1", status: OrderStatus = OrderStatus.QUEUED):
    tasks = tuple(
        RobotTaskSnapshot(
            task_id=f"{order_id}:{task_type.value}",
            task_type=task_type,
            status=TaskStatus.QUEUED if index == 0 else TaskStatus.BLOCKED,
            error_code=None,
            message=None,
        )
        for index, task_type in enumerate(RobotTaskType)
    )
    return OrderSnapshot(
        order_id=order_id,
        item_id="water",
        target_id="mineral_water",
        status=status,
        tasks=tasks,
        error_code=None,
        message=None,
        created_at=time.time(),
        updated_at=time.time(),
    )


class PublicRunnerStub:
    """只实现测试页面获准调用的 TaskRunner 公共接口。"""

    def __init__(self) -> None:
        self.order = make_order()
        self.calls: list[tuple[str, object]] = []

    def get_status(self):
        self.calls.append(("get_status", None))
        return RunnerStatusSnapshot(RunnerState.IDLE, True)

    def get_queue(self):
        self.calls.append(("get_queue", None))
        return QueueSnapshot(10, None, (self.order,))

    def get_order(self, order_id):
        self.calls.append(("get_order", order_id))
        return self.order

    def submit_beverage(self, item_id):
        self.calls.append(("submit_beverage", item_id))
        return self.order

    def cancel_order(self, order_id):
        self.calls.append(("cancel_order", order_id))
        return self.order


class TestUIServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = PublicRunnerStub()
        self.server = TaskRunnerHTTPServer(
            ("127.0.0.1", 0),
            handler_for(self.runner),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def get_json(self, path: str):
        with urlopen(self.base + path, timeout=2.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, body: dict):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_status_and_queue_use_public_snapshots(self) -> None:
        status_code, status = self.get_json("/api/status")
        queue_code, queue = self.get_json("/api/queue")
        self.assertEqual(status_code, 200)
        self.assertEqual(status["state"], "idle")
        self.assertEqual(queue_code, 200)
        self.assertEqual(queue["pending_orders"][0]["tasks"][0]["task_type"], "pick")
        self.assertIn(("get_status", None), self.runner.calls)
        self.assertIn(("get_queue", None), self.runner.calls)

    def test_submit_get_and_cancel_map_to_public_methods(self) -> None:
        submit_code, submitted = self.post_json("/api/orders", {"item_id": "water"})
        get_code, _ = self.get_json("/api/orders/order-1")
        cancel_code, _ = self.post_json("/api/orders/order-1/cancel", {})
        self.assertEqual((submit_code, get_code, cancel_code), (201, 200, 200))
        self.assertEqual(submitted["order_id"], "order-1")
        self.assertIn(("submit_beverage", "water"), self.runner.calls)
        self.assertIn(("get_order", "order-1"), self.runner.calls)
        self.assertIn(("cancel_order", "order-1"), self.runner.calls)

    def test_static_page_and_unknown_api(self) -> None:
        with urlopen(self.base + "/", timeout=2.0) as response:
            html = response.read().decode("utf-8")
        self.assertIn("TaskRunner 测试台", html)
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/private", timeout=2.0)
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
