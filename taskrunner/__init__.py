"""内存型机器人订单队列的主要公共类型。

常规调用方通常只需从这里导入 ``TaskRunner`` 和快照/状态枚举；真机对象
组装与测试 CLI 分别位于 ``taskrunner.runtime`` 和 ``taskrunner.cli``。
"""

from taskrunner.runner import TaskRunner
from taskrunner.taskrunner_contracts import (
    OrderSnapshot,
    OrderStatus,
    PauseReason,
    QueueSnapshot,
    RobotTaskSnapshot,
    RobotTaskType,
    TaskStatus,
)

__all__ = [
    "OrderSnapshot",
    "OrderStatus",
    "PauseReason",
    "QueueSnapshot",
    "RobotTaskSnapshot",
    "RobotTaskType",
    "TaskRunner",
    "TaskStatus",
]
