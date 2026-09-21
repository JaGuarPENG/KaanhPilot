"""TaskRunner 主状态机和唯一机器人 Worker。

线程模型：调用线程负责提交、查询和发出取消请求；一个 Worker 串行执行所有
机器人动作；可选的 ControllerMonitor 使用另一线程及独立控制器连接。任何
时候都不会有两个线程同时向机器人发送任务动作。
"""

from __future__ import annotations

import threading
import time
from typing import Callable
import uuid

from taskrunner.errors import (
    OrderNotCancellableError,
    QueueClosedError,
    QueueFullError,
    RunnerBusyError,
    RunnerNotStartedError,
    RunnerStoppedError,
    UnknownOrderError,
    UnsupportedBeverageError,
)
from taskrunner.monitor import ControllerMonitor
from taskrunner.orders import (
    BEVERAGE_TARGET_IDS,
    BeverageOrder,
    BeverageOrderActions,
)
from taskrunner.queue import BoundedOrderQueue
from taskrunner.taskrunner_contracts import (
    OrderSnapshot,
    OrderStatus,
    QueueSnapshot,
    RunnerState,
    RunnerStatusSnapshot,
    TaskStatus,
    TERMINAL_ORDER_STATUSES,
)


class TaskRunner:
    """内存型 FIFO 订单调度器。

    ``actions`` 隔离具体硬件，``readiness_check`` 在启动 Worker 和控制器
    监控前执行安全基线检查，``on_fatal`` 交给宿主清理资源并退出。
    Runner 不持久化订单；进程退出后所有状态均丢弃。
    """

    def __init__(
        self,
        actions: BeverageOrderActions,
        *,
        queue_capacity: int = 10,
        monitor: ControllerMonitor | None = None,
        readiness_check: Callable[[], None] | None = None,
        on_fatal: Callable[[Exception], None] | None = None,
        order_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._actions = actions
        self._queue = BoundedOrderQueue(queue_capacity)
        self._monitor = monitor
        self._readiness_check = readiness_check
        self._on_fatal = on_fatal or (lambda _error: None)
        self._order_id_factory = order_id_factory or (lambda: str(uuid.uuid4()))

        self._orders: dict[str, BeverageOrder] = {}
        self._current_order_id: str | None = None
        self._lock = threading.RLock()
        self._state_changed = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._fatal_error: Exception | None = None

    def start(self) -> None:
        """检查启动基线并启动唯一 Worker 与可选控制器监控。

        重复调用已启动的 Runner 是幂等的；关闭或发生过致命故障后禁止
        重新启动，需重新构造完整运行时。
        """

        with self._lock:
            if self._closed:
                raise RunnerStoppedError("TaskRunner 已关闭，不能重新启动")
            if self._fatal_error is not None:
                raise RunnerStoppedError("TaskRunner 已因致命故障停止")
            if self._started:
                return
        if self._readiness_check is not None:
            self._readiness_check()
        with self._lock:
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_worker,
                name="taskrunner-worker",
                daemon=True,
            )
            self._started = True
            self._worker.start()
        if self._monitor is not None:
            try:
                self._monitor.start(self.report_fatal)
            except Exception as error:
                self.report_fatal(error)
                worker = self._worker
                if worker is not None and worker is not threading.current_thread():
                    worker.join(timeout=3.0)
                with self._lock:
                    self._started = False
                raise

    def shutdown(self) -> None:
        """在系统空闲时永久关闭 Runner。

        正常情况下仍有活动/等待订单会抛出 ``RunnerBusyError``，防止直接
        关闭连接打断机器人。致命故障后允许立即进入清理流程。
        """

        with self._lock:
            if not self._started:
                return
            if self._fatal_error is None and (
                self._current_order_id is not None or len(self._queue) > 0
            ):
                raise RunnerBusyError("仍有当前订单或等待订单，不能关闭 TaskRunner")
            self._stop_event.set()
            self._queue.close()
            self._closed = True
            self._state_changed.notify_all()
        if self._monitor is not None:
            self._monitor.stop()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        with self._lock:
            self._started = False

    def submit_beverage(self, item_id: str) -> OrderSnapshot:
        """提交饮料订单并返回提交瞬间的只读快照。

        支持 ``water``、``cola``、``oolong_tea``。容量只计算等待订单，
        Worker 当前正在执行的订单不占用等待容量。
        """

        with self._lock:
            self._ensure_accepting_orders()
            try:
                target_id = BEVERAGE_TARGET_IDS[item_id]
            except (KeyError, TypeError) as error:
                raise UnsupportedBeverageError(f"不支持的饮料: {item_id!r}") from error
            order_id = self._order_id_factory()
            if not isinstance(order_id, str) or not order_id:
                raise ValueError("order_id_factory 必须返回非空字符串")
            if order_id in self._orders:
                raise ValueError(f"order_id 重复: {order_id}")
            order = BeverageOrder.create(order_id, item_id, target_id)
            self._orders[order_id] = order
            try:
                self._queue.put(order_id)
            except (QueueFullError, QueueClosedError):
                del self._orders[order_id]
                raise
            self._state_changed.notify_all()
            return order.snapshot()

    def get_queue(self) -> QueueSnapshot:
        """查询当前活动订单和按 FIFO 排列的等待订单。"""

        with self._lock:
            pending_ids = self._queue.snapshot()
            current_order = self._current_order()
            current = (
                None
                if current_order is None
                or current_order.status in TERMINAL_ORDER_STATUSES
                else current_order.snapshot()
            )
            pending = tuple(
                self._orders[order_id].snapshot()
                for order_id in pending_ids
                if order_id in self._orders
            )
            return QueueSnapshot(
                capacity=self._queue.capacity,
                current_order=current,
                pending_orders=pending,
            )

    def get_status(self) -> RunnerStatusSnapshot:
        """返回 Runner 生命周期、接单能力和首个致命故障。

        该接口不暴露可变内部对象。``RUNNING`` 表示存在当前订单或等待订单；
        已启动且没有任何订单时为 ``IDLE``。
        """

        with self._lock:
            if self._fatal_error is not None:
                state = RunnerState.FAULTED
            elif self._closed:
                state = RunnerState.STOPPED
            elif not self._started:
                state = RunnerState.NOT_STARTED
            elif self._current_order_id is not None or len(self._queue) > 0:
                state = RunnerState.RUNNING
            else:
                state = RunnerState.IDLE

            accepting_orders = (
                self._started
                and not self._closed
                and self._fatal_error is None
                and not self._stop_event.is_set()
                and len(self._queue) < self._queue.capacity
            )
            error = self._fatal_error
            return RunnerStatusSnapshot(
                state=state,
                accepting_orders=accepting_orders,
                fatal_error_type=None if error is None else type(error).__name__,
                fatal_error_message=None if error is None else str(error),
            )

    def get_order(self, order_id: str) -> OrderSnapshot:
        """按 ID 查询订单；终态记录会保留到当前进程退出。"""

        with self._lock:
            return self._get_order(order_id).snapshot()

    def cancel_order(self, order_id: str) -> OrderSnapshot:
        """请求取消订单，并返回请求处理后的快照。

        - ``QUEUED``：直接从 FIFO 删除并取消。
        - ``PAUSED``：只设置取消请求，由唯一 Worker 执行安全回位。
        - ``RUNNING``：首版拒绝取消，避免在任意运动中间强行打断。
        - 终态：幂等返回原快照。
        """

        with self._state_changed:
            order = self._get_order(order_id)
            if order.status in TERMINAL_ORDER_STATUSES:
                return order.snapshot()
            if order.status is OrderStatus.QUEUED:
                if not self._queue.remove(order_id):
                    raise OrderNotCancellableError(
                        "订单已被 Worker 领取，不能按排队订单取消"
                    )
                first = order.tasks[0]
                first.status = TaskStatus.CANCELLED
                order.skip_unstarted_tasks()
                order.status = OrderStatus.CANCELLED
                order.message = "订单在执行前被取消"
                order.updated_at = time.time()
                self._state_changed.notify_all()
                return order.snapshot()
            if order.status is OrderStatus.PAUSED:
                task = order.current_task()
                if task is None or task.status is not TaskStatus.PAUSED:
                    raise RuntimeError("暂停订单没有对应的暂停任务")
                order.cancel_requested = True
                order.status = OrderStatus.CANCELLING
                task.status = TaskStatus.CANCELLING
                order.updated_at = time.time()
                self._state_changed.notify_all()
                return order.snapshot()
            raise OrderNotCancellableError(
                f"状态为 {order.status.value} 的订单不能取消"
            )

    def report_fatal(self, error: Exception) -> None:
        """记录首个致命故障、停止调度并通知宿主。

        当前任务/订单会标记为 ``FAILED(fatal_error)``，尚未执行的后续任务
        变为 ``SKIPPED``。后续重复故障不会覆盖首个故障原因。
        """

        callback_required = False
        with self._state_changed:
            if self._fatal_error is not None:
                return
            self._fatal_error = error
            self._stop_event.set()
            order = self._current_order()
            if order is not None and order.status not in TERMINAL_ORDER_STATUSES:
                task = order.current_task()
                if task is not None:
                    task.status = TaskStatus.FAILED
                    task.error_code = "fatal_error"
                    task.message = str(error)
                order.skip_unstarted_tasks()
                order.status = OrderStatus.FAILED
                order.error_code = "fatal_error"
                order.message = str(error)
                order.updated_at = time.time()
            self._queue.close()
            self._state_changed.notify_all()
            callback_required = True
        if callback_required:
            self._on_fatal(error)

    def _run_worker(self) -> None:
        """持续领取 FIFO 队首订单，并保证订单之间不会穿插。"""

        while not self._stop_event.is_set():
            try:
                order_id = self._queue.get(timeout=0.1)
            except TimeoutError:
                continue
            except QueueClosedError:
                return
            with self._state_changed:
                order = self._orders[order_id]
                if order.status is OrderStatus.CANCELLED:
                    continue
                self._current_order_id = order_id
                order.status = OrderStatus.RUNNING
                order.updated_at = time.time()
                self._state_changed.notify_all()
            try:
                self._execute_order(order)
            except Exception as error:
                self.report_fatal(error)
            finally:
                with self._state_changed:
                    if self._current_order_id == order_id:
                        self._current_order_id = None
                    self._state_changed.notify_all()

    def _execute_order(self, order: BeverageOrder) -> None:
        """按固定顺序解锁并执行一个订单的四个任务。"""

        for task in order.tasks:
            if self._stop_event.is_set():
                return
            with self._state_changed:
                # 能走到这里说明前一个任务已经成功；因此只解锁当前相邻任务，
                # 更后面的任务仍保持 BLOCKED。
                if task.status is TaskStatus.BLOCKED:
                    task.status = TaskStatus.QUEUED
                if task.status is not TaskStatus.QUEUED:
                    raise RuntimeError(
                        f"任务 {task.task_type.value} 处于非法起始状态 {task.status.value}"
                    )
                task.status = TaskStatus.RUNNING
                order.status = OrderStatus.RUNNING
                order.updated_at = time.time()
                self._state_changed.notify_all()

            result = self._actions.execute(
                task.task_type,
                item_id=order.item_id,
                target_id=order.target_id,
            )
            if self._stop_event.is_set():
                return
            if result.status is TaskStatus.SUCCEEDED:
                with self._state_changed:
                    task.status = TaskStatus.SUCCEEDED
                    task.message = result.message
                    order.updated_at = time.time()
                    self._state_changed.notify_all()
                continue
            if result.status is TaskStatus.FAILED:
                with self._state_changed:
                    task.status = TaskStatus.FAILED
                    task.error_code = result.error_code
                    task.message = result.message
                    order.skip_unstarted_tasks()
                    order.status = OrderStatus.FAILED
                    order.error_code = result.error_code
                    order.message = result.message
                    order.updated_at = time.time()
                    self._state_changed.notify_all()
                return
            if result.status is TaskStatus.PAUSED:
                self._pause_order(order, task, result.pause_reason, result.message)
                return
            raise RuntimeError(f"不支持的任务执行结果: {result.status}")

        with self._state_changed:
            order.status = OrderStatus.SUCCEEDED
            order.message = "饮料取送订单已完成"
            order.updated_at = time.time()
            self._state_changed.notify_all()

    def _pause_order(self, order, task, reason, message) -> None:
        """让 Worker 原地等待，直到收到取消请求或系统发生致命故障。"""

        with self._state_changed:
            task.status = TaskStatus.PAUSED
            task.pause_reason = reason
            task.message = message
            order.status = OrderStatus.PAUSED
            order.pause_reason = reason
            order.message = message
            order.updated_at = time.time()
            self._state_changed.notify_all()
            while not order.cancel_requested and not self._stop_event.is_set():
                self._state_changed.wait(timeout=0.5)
            if self._stop_event.is_set():
                return

        # API 调用线程只负责改变状态和唤醒条件变量。真正的回位仍由唯一
        # Worker 执行，避免取消请求线程与 Worker 同时控制机器人。
        self._actions.cancel_paused_pick()
        if self._stop_event.is_set():
            return
        with self._state_changed:
            task.status = TaskStatus.CANCELLED
            task.pause_reason = None
            task.message = "暂停订单已回到初始位并取消"
            order.skip_unstarted_tasks()
            order.status = OrderStatus.CANCELLED
            order.pause_reason = None
            order.message = task.message
            order.updated_at = time.time()
            self._state_changed.notify_all()

    def _ensure_accepting_orders(self) -> None:
        if self._closed:
            raise RunnerStoppedError("TaskRunner 已关闭，不能接收新订单")
        if not self._started:
            raise RunnerNotStartedError("TaskRunner 尚未启动")
        if self._fatal_error is not None or self._stop_event.is_set():
            raise RunnerStoppedError("TaskRunner 已停止，不能接收新订单")

    def _get_order(self, order_id: str) -> BeverageOrder:
        try:
            return self._orders[order_id]
        except KeyError as error:
            raise UnknownOrderError(f"未知订单: {order_id}") from error

    def _current_order(self) -> BeverageOrder | None:
        if self._current_order_id is None:
            return None
        return self._orders.get(self._current_order_id)
