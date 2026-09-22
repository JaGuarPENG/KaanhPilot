from __future__ import annotations

from collections import deque
import itertools
import threading
import time
import unittest

from taskrunner.errors import (
    OrderNotCancellableError,
    QueueFullError,
    RunnerStoppedError,
    UnsupportedBeverageError,
)
from taskrunner.runner import TaskRunner
from taskrunner.taskrunner_contracts import (
    OrderStatus,
    RobotTaskType,
    RunnerState,
    TaskExecutionResult,
    TaskStatus,
)


def wait_until(predicate, message: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(message)


class ScriptedActions:
    def __init__(self) -> None:
        self.results: deque[TaskExecutionResult] = deque()
        self.execution_log: list[tuple[str, RobotTaskType]] = []
        self.block_on: RobotTaskType | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_entered = threading.Event()
        self.cancel_release = threading.Event()
        self.cancel_release.set()
        self.cancel_error: Exception | None = None
        self.execute_error: Exception | None = None

    def execute(
        self, task_type: RobotTaskType, *, target_id: str
    ) -> TaskExecutionResult:
        self.execution_log.append((target_id, task_type))
        if self.block_on is task_type:
            self.entered.set()
            if not self.release.wait(2.0):
                raise TimeoutError("test action was not released")
        if self.execute_error is not None:
            error = self.execute_error
            self.execute_error = None
            raise error
        if self.results:
            return self.results.popleft()
        return TaskExecutionResult.succeeded()

    def cancel_paused_pick(self) -> None:
        self.cancel_entered.set()
        if not self.cancel_release.wait(2.0):
            raise TimeoutError("test cancellation was not released")
        if self.cancel_error is not None:
            raise self.cancel_error


class TaskRunnerTests(unittest.TestCase):
    def test_public_runner_status_lifecycle(self) -> None:
        actions = ScriptedActions()
        runner = TaskRunner(actions)
        self.assertEqual(runner.get_status().state, RunnerState.NOT_STARTED)
        self.assertFalse(runner.get_status().accepting_orders)

        runner.start()
        self.addCleanup(self._safe_shutdown, runner)
        self.assertEqual(runner.get_status().state, RunnerState.IDLE)
        self.assertTrue(runner.get_status().accepting_orders)

        actions.block_on = RobotTaskType.PICK
        runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))
        self.assertEqual(runner.get_status().state, RunnerState.RUNNING)
        actions.release.set()
        wait_until(
            lambda: runner.get_status().state is RunnerState.IDLE,
            "Runner 完成订单后没有回到 IDLE",
        )

        runner.shutdown()
        self.assertEqual(runner.get_status().state, RunnerState.STOPPED)

    def test_public_status_preserves_fatal_error(self) -> None:
        runner = TaskRunner(ScriptedActions())
        runner.start()
        self.addCleanup(self._safe_shutdown, runner)
        runner.report_fatal(RuntimeError("controller lost"))
        status = runner.get_status()
        self.assertEqual(status.state, RunnerState.FAULTED)
        self.assertFalse(status.accepting_orders)
        self.assertEqual(status.fatal_error_type, "RuntimeError")
        self.assertEqual(status.fatal_error_message, "controller lost")

    def make_runner(
        self,
        actions: ScriptedActions,
        *,
        capacity: int = 10,
        on_fatal=None,
    ) -> TaskRunner:
        ids = (f"order-{number}" for number in itertools.count(1))
        runner = TaskRunner(
            actions,
            queue_capacity=capacity,
            on_fatal=on_fatal,
            order_id_factory=lambda: next(ids),
        )
        runner.start()
        self.addCleanup(self._safe_shutdown, runner)
        return runner

    @staticmethod
    def _safe_shutdown(runner: TaskRunner) -> None:
        try:
            runner.shutdown()
        except Exception:
            pass

    def test_initial_task_states_and_successful_fixed_chain(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.PICK
        runner = self.make_runner(actions)

        submitted = runner.submit_beverage("water")
        self.assertEqual(
            [task.status for task in submitted.tasks],
            [
                TaskStatus.QUEUED,
                TaskStatus.BLOCKED,
                TaskStatus.BLOCKED,
                TaskStatus.BLOCKED,
            ],
        )
        self.assertTrue(actions.entered.wait(1.0))
        actions.release.set()
        wait_until(
            lambda: runner.get_order(submitted.order_id).status
            is OrderStatus.SUCCEEDED,
            "订单没有成功完成",
        )

        finished = runner.get_order(submitted.order_id)
        self.assertTrue(all(task.status is TaskStatus.SUCCEEDED for task in finished.tasks))
        self.assertEqual(
            [task_type for _, task_type in actions.execution_log],
            list(RobotTaskType),
        )
        self.assertIsNone(runner.get_queue().current_order)
        self.assertEqual(runner.get_queue().pending_count, 0)

    def test_orders_execute_fifo_without_interleaving(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.PICK
        runner = self.make_runner(actions)
        first = runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))
        second = runner.submit_beverage("cola")
        actions.release.set()

        wait_until(
            lambda: runner.get_order(second.order_id).status is OrderStatus.SUCCEEDED,
            "第二个订单没有完成",
        )
        self.assertEqual(
            [target for target, _ in actions.execution_log],
            ["mineral_water"] * 4 + ["coco_cola"] * 4,
        )
        self.assertEqual(
            runner.get_order(first.order_id).status, OrderStatus.SUCCEEDED
        )

    def test_blocking_transport_keeps_order_running_and_later_tasks_blocked(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.TRANSPORT_TO_DROPOFF
        runner = self.make_runner(actions)
        order = runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))

        snapshot = runner.get_order(order.order_id)
        self.assertEqual(snapshot.status, OrderStatus.RUNNING)
        self.assertEqual(snapshot.tasks[0].status, TaskStatus.SUCCEEDED)
        self.assertEqual(snapshot.tasks[1].status, TaskStatus.RUNNING)
        self.assertEqual(snapshot.tasks[2].status, TaskStatus.BLOCKED)
        self.assertEqual(snapshot.tasks[3].status, TaskStatus.BLOCKED)

        actions.release.set()
        wait_until(
            lambda: runner.get_order(order.order_id).status is OrderStatus.SUCCEEDED,
            "运输恢复后订单没有完成",
        )

    def test_out_of_stock_skips_remaining_tasks_and_continues(self) -> None:
        actions = ScriptedActions()
        actions.results.append(
            TaskExecutionResult.failed("out_of_stock", "第一次拍照未识别")
        )
        runner = self.make_runner(actions)
        failed = runner.submit_beverage("water")
        next_order = runner.submit_beverage("cola")

        wait_until(
            lambda: runner.get_order(next_order.order_id).status
            is OrderStatus.SUCCEEDED,
            "无库存后没有继续执行下一订单",
        )
        snapshot = runner.get_order(failed.order_id)
        self.assertEqual(snapshot.status, OrderStatus.FAILED)
        self.assertEqual(snapshot.error_code, "out_of_stock")
        self.assertEqual(snapshot.tasks[0].status, TaskStatus.FAILED)
        self.assertTrue(
            all(task.status is TaskStatus.SKIPPED for task in snapshot.tasks[1:])
        )

    def test_pause_blocks_queue_then_async_cancel_allows_next_order(self) -> None:
        actions = ScriptedActions()
        actions.results.append(
            TaskExecutionResult.paused(
                "second_detection_failed",
                "第二次拍照未识别",
            )
        )
        actions.cancel_release.clear()
        runner = self.make_runner(actions)
        paused = runner.submit_beverage("water")
        queued = runner.submit_beverage("cola")

        wait_until(
            lambda: runner.get_order(paused.order_id).status is OrderStatus.PAUSED,
            "订单没有进入暂停状态",
        )
        self.assertEqual(runner.get_order(queued.order_id).status, OrderStatus.QUEUED)

        cancelling = runner.cancel_order(paused.order_id)
        self.assertEqual(cancelling.status, OrderStatus.CANCELLING)
        self.assertEqual(cancelling.tasks[0].status, TaskStatus.CANCELLING)
        self.assertTrue(actions.cancel_entered.wait(1.0))
        self.assertEqual(runner.get_order(queued.order_id).status, OrderStatus.QUEUED)

        actions.cancel_release.set()
        wait_until(
            lambda: runner.get_order(queued.order_id).status is OrderStatus.SUCCEEDED,
            "取消暂停订单后没有继续下一订单",
        )
        cancelled = runner.get_order(paused.order_id)
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)
        self.assertEqual(cancelled.tasks[0].status, TaskStatus.CANCELLED)
        self.assertTrue(
            all(task.status is TaskStatus.SKIPPED for task in cancelled.tasks[1:])
        )

    def test_queued_cancel_and_terminal_cancel_are_idempotent(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.PICK
        runner = self.make_runner(actions)
        active = runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))
        queued = runner.submit_beverage("cola")

        cancelled = runner.cancel_order(queued.order_id)
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)
        self.assertEqual(cancelled.tasks[0].status, TaskStatus.CANCELLED)
        self.assertTrue(
            all(task.status is TaskStatus.SKIPPED for task in cancelled.tasks[1:])
        )
        self.assertEqual(
            runner.cancel_order(queued.order_id).status, OrderStatus.CANCELLED
        )
        self.assertEqual(runner.get_queue().pending_count, 0)
        actions.release.set()
        wait_until(
            lambda: runner.get_order(active.order_id).status is OrderStatus.SUCCEEDED,
            "活动订单没有完成",
        )

    def test_running_order_cannot_be_cancelled(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.PICK
        runner = self.make_runner(actions)
        order = runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))

        with self.assertRaises(OrderNotCancellableError):
            runner.cancel_order(order.order_id)

        actions.release.set()
        wait_until(
            lambda: runner.get_order(order.order_id).status is OrderStatus.SUCCEEDED,
            "活动订单没有完成",
        )

    def test_waiting_capacity_excludes_current_order(self) -> None:
        actions = ScriptedActions()
        actions.block_on = RobotTaskType.PICK
        runner = self.make_runner(actions, capacity=2)
        first = runner.submit_beverage("water")
        self.assertTrue(actions.entered.wait(1.0))
        second = runner.submit_beverage("cola")
        third = runner.submit_beverage("oolong_tea")

        self.assertEqual(runner.get_queue().pending_count, 2)
        with self.assertRaises(QueueFullError):
            runner.submit_beverage("water")

        actions.release.set()
        wait_until(
            lambda: runner.get_order(third.order_id).status is OrderStatus.SUCCEEDED,
            "排队订单没有完成",
        )
        self.assertEqual(runner.get_order(first.order_id).status, OrderStatus.SUCCEEDED)
        self.assertEqual(runner.get_order(second.order_id).status, OrderStatus.SUCCEEDED)

    def test_execution_exception_is_fatal_and_stops_new_submissions(self) -> None:
        actions = ScriptedActions()
        actions.execute_error = RuntimeError("robot disconnected")
        fatal_event = threading.Event()
        fatal_errors: list[Exception] = []

        def on_fatal(error: Exception) -> None:
            fatal_errors.append(error)
            fatal_event.set()

        runner = self.make_runner(actions, on_fatal=on_fatal)
        order = runner.submit_beverage("water")
        self.assertTrue(fatal_event.wait(1.0))

        snapshot = runner.get_order(order.order_id)
        self.assertEqual(snapshot.status, OrderStatus.FAILED)
        self.assertEqual(snapshot.tasks[0].status, TaskStatus.FAILED)
        self.assertTrue(
            all(task.status is TaskStatus.SKIPPED for task in snapshot.tasks[1:])
        )
        self.assertEqual(str(fatal_errors[0]), "robot disconnected")
        with self.assertRaises(RunnerStoppedError):
            runner.submit_beverage("cola")
        with self.assertRaises(RunnerStoppedError):
            runner.start()

    def test_failed_pause_cancellation_is_fatal(self) -> None:
        actions = ScriptedActions()
        actions.results.append(
            TaskExecutionResult.paused("target_unreachable")
        )
        actions.cancel_error = RuntimeError("cannot return to init pose")
        fatal_event = threading.Event()
        runner = self.make_runner(actions, on_fatal=lambda _error: fatal_event.set())
        order = runner.submit_beverage("water")
        wait_until(
            lambda: runner.get_order(order.order_id).status is OrderStatus.PAUSED,
            "订单没有暂停",
        )

        runner.cancel_order(order.order_id)
        self.assertTrue(fatal_event.wait(1.0))
        snapshot = runner.get_order(order.order_id)
        self.assertEqual(snapshot.status, OrderStatus.FAILED)
        self.assertEqual(snapshot.tasks[0].status, TaskStatus.FAILED)

    def test_invalid_beverage_is_rejected_without_creating_order(self) -> None:
        runner = self.make_runner(ScriptedActions())
        with self.assertRaises(UnsupportedBeverageError):
            runner.submit_beverage("coffee")
        self.assertEqual(runner.get_queue().pending_count, 0)

    def test_shutdown_is_terminal(self) -> None:
        runner = self.make_runner(ScriptedActions())
        runner.shutdown()
        with self.assertRaises(RunnerStoppedError):
            runner.start()
        with self.assertRaises(RunnerStoppedError):
            runner.submit_beverage("water")


if __name__ == "__main__":
    unittest.main()
