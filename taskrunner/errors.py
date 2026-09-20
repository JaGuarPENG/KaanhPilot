"""TaskRunner 可供调用方分类处理的异常类型。"""


class TaskRunnerError(RuntimeError):
    """TaskRunner 应用层异常基类。"""


class QueueFullError(TaskRunnerError):
    """等待订单数量已经达到配置上限。"""


class QueueClosedError(TaskRunnerError):
    """底层 FIFO 已关闭，不能再读写。"""


class UnknownOrderError(TaskRunnerError):
    """调用方提供的 ``order_id`` 在当前进程中不存在。"""


class OrderNotCancellableError(TaskRunnerError):
    """订单当前状态不允许取消，例如正在执行普通动作。"""


class UnsupportedBeverageError(TaskRunnerError):
    """饮料标识不在首版支持列表中。"""


class RunnerNotStartedError(TaskRunnerError):
    """在调用 ``start()`` 前尝试提交订单。"""


class RunnerStoppedError(TaskRunnerError):
    """Runner 已关闭或已因致命故障停止。"""


class RunnerBusyError(TaskRunnerError):
    """仍有活动/等待订单，因此不能正常关闭。"""


class FatalExecutionError(TaskRunnerError):
    """必须停止 Runner 并由宿主清理硬件资源的执行故障。"""


class ControllerMonitorError(FatalExecutionError):
    """监控连接本身无法可靠读取控制器状态。"""


class ControllerFaultError(FatalExecutionError):
    """监控已读到掉使能、控制器报警或驱动器报警。"""
