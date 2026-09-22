"""TaskRunner 对外稳定数据契约。

本文件只描述状态、正常执行结果以及只读快照，不保存可变运行时状态，也不
直接依赖机器人、相机或 AGV。调用方应通过这些快照观察系统，而不是修改
``orders.py`` 中的内部实体。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderStatus(str, Enum):
    """订单级状态；一个订单覆盖完整的抓取、运输、放置和复位过程。"""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunnerState(str, Enum):
    """TaskRunner 进程内生命周期与调度状态。"""

    NOT_STARTED = "not_started"
    IDLE = "idle"
    RUNNING = "running"
    FAULTED = "faulted"
    STOPPED = "stopped"


class TaskStatus(str, Enum):
    """订单内部单个机器人任务的状态。"""

    # 前置任务尚未成功，因此当前任务还不具备执行资格。
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # 前序失败或订单取消后，本任务确定不会执行；这是终态。
    SKIPPED = "skipped"


class RobotTaskType(str, Enum):
    """首版饮料订单固定包含的四种执行阶段。"""

    PICK = "pick"
    TRANSPORT_TO_DROPOFF = "transport_to_dropoff"
    PLACE = "place"
    RETURN_AND_RESET = "return_and_reset"


TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.SUCCEEDED, OrderStatus.FAILED, OrderStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class TaskExecutionResult:
    """动作适配器返回给 Runner 的正常、非致命执行结果。

    只有 ``SUCCEEDED``、``FAILED`` 和 ``PAUSED`` 可以由动作返回。连接
    断开、控制器报警等致命问题不要包装成该对象，而应直接抛出异常，让
    Runner 进入统一的致命故障流程。
    """

    status: TaskStatus
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.PAUSED,
        }:
            raise ValueError("TaskExecutionResult 只能表示 SUCCEEDED、FAILED 或 PAUSED")
        if self.status is TaskStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("SUCCEEDED 结果不能包含 error_code")
        if self.status in {TaskStatus.FAILED, TaskStatus.PAUSED} and not self.error_code:
            raise ValueError("FAILED 或 PAUSED 结果必须包含 error_code")

    @classmethod
    def succeeded(cls, message: str | None = None) -> "TaskExecutionResult":
        """构造成功结果。"""

        return cls(TaskStatus.SUCCEEDED, message=message)

    @classmethod
    def failed(
        cls, error_code: str, message: str | None = None
    ) -> "TaskExecutionResult":
        """构造可归因、无需终止整个 Runner 的任务失败结果。"""

        return cls(TaskStatus.FAILED, error_code=error_code, message=message)

    @classmethod
    def paused(
        cls, error_code: str, message: str | None = None
    ) -> "TaskExecutionResult":
        """构造等待人工处理的暂停结果。"""

        return cls(TaskStatus.PAUSED, error_code=error_code, message=message)


@dataclass(frozen=True, slots=True)
class RobotTaskSnapshot:
    """单个机器人任务的不可变观察快照。"""

    task_id: str
    task_type: RobotTaskType
    status: TaskStatus
    error_code: str | None
    message: str | None


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """订单及其完整任务链的不可变观察快照。"""

    order_id: str
    item_id: str
    target_id: str
    status: OrderStatus
    tasks: tuple[RobotTaskSnapshot, ...]
    error_code: str | None
    message: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """当前活动订单和 FIFO 等待订单的不可变快照。

    已完成、失败或取消的历史订单不会出现在这里，但仍可在进程退出前通过
    ``TaskRunner.get_order(order_id)`` 查询。
    """

    capacity: int
    current_order: OrderSnapshot | None
    pending_orders: tuple[OrderSnapshot, ...]

    @property
    def pending_count(self) -> int:
        """返回等待队列长度；当前活动订单不计入。"""

        return len(self.pending_orders)


@dataclass(frozen=True, slots=True)
class RunnerStatusSnapshot:
    """Runner 的只读生命周期快照，供宿主和测试界面观察。"""

    state: RunnerState
    accepting_orders: bool
    fatal_error_type: str | None = None
    fatal_error_message: str | None = None
