# TaskRunner 接口交接文档

本文只描述当前 `taskrunner` 模块提供的 Python 接口、数据契约和调用规则。

## 1. 公共导入

常规调用方可以直接从 `taskrunner` 导入主要类型：

```python
from taskrunner import (
    OrderSnapshot,
    OrderStatus,
    QueueSnapshot,
    RobotTaskSnapshot,
    RobotTaskType,
    RunnerState,
    RunnerStatusSnapshot,
    TaskRunner,
    TaskStatus,
)
```

异常类型位于：

```python
from taskrunner.errors import TaskRunnerError
```

运行时工厂位于：

```python
from taskrunner.runtime import (
    create_hardware_runtime,
    create_recognition_test_runtime,
)
```

## 2. TaskRunner 构造

```python
TaskRunner(
    actions,
    *,
    queue_capacity: int = 10,
    monitor=None,
    on_fatal=None,
    order_id_factory=None,
)
```

参数含义：

- `actions`：饮料订单动作适配器，必须实现本文第 10 节的动作接口。
- `queue_capacity`：等待队列容量，必须是正整数。当前正在执行的订单不占用该容量。
- `monitor`：可选的 `ControllerMonitor`。Runner 启动和关闭时会同步管理它。
- `on_fatal`：可选回调，签名为 `Callable[[Exception], None]`。首次致命故障时调用。
- `order_id_factory`：可选的订单 ID 生成函数，主要用于测试；默认生成 UUID 字符串。

Runner 的订单、任务和队列状态只保存在当前进程内，不会持久化。

## 3. 生命周期接口

### `start() -> None`

启动唯一订单 Worker，并启动可选的控制器监控。

```python
runner.start()
```

调用规则：

- 第一次成功启动后，Runner 才允许接单。
- 对已启动的 Runner 重复调用是幂等的。
- 已正常关闭或已进入 `FAULTED` 的 Runner 不能重新启动，需要重新构造运行时。

### `shutdown() -> None`

永久关闭 Runner、等待队列和控制器监控。

```python
runner.shutdown()
```

调用规则：

- 正常状态下存在当前订单或等待订单时，抛出 `RunnerBusyError`。
- 致命故障后允许立即关闭。
- 已关闭后不能再次启动或提交订单。
- 该方法只管理 Runner 和监控，不代替 `runtime.close()` 关闭机器人、相机或 AGV。

## 4. 提交订单

### `submit_beverage(item_id: str) -> OrderSnapshot`

提交一个饮料订单，并返回提交完成瞬间的只读快照。

```python
order = runner.submit_beverage("water")
print(order.order_id)
print(order.status)  # OrderStatus.QUEUED
```

当前支持：

| `item_id` | 内部视觉目标 `target_id` |
| --- | --- |
| `water` | `mineral_water` |
| `cola` | `coco_cola` |
| `oolong_tea` | `oolong_tea` |

调用规则：

- 必须先调用 `start()`。
- 等待队列已满时抛出 `QueueFullError`。
- 不支持的商品抛出 `UnsupportedBeverageError`。
- Runner 已关闭或发生致命故障时抛出 `RunnerStoppedError`。
- 每个订单固定创建四个任务：抓取、运输、放置、返回复位。

## 5. 查询接口

### `get_status() -> RunnerStatusSnapshot`

返回 Runner 的生命周期状态、接单能力和致命故障信息。

```python
status = runner.get_status()

print(status.state)
print(status.accepting_orders)
print(status.fatal_error_type)
print(status.fatal_error_message)
```

`RunnerStatusSnapshot` 字段：

```python
state: RunnerState
accepting_orders: bool
fatal_error_type: str | None
fatal_error_message: str | None
```

`RunnerState`：

```python
RunnerState.NOT_STARTED
RunnerState.IDLE
RunnerState.RUNNING
RunnerState.FAULTED
RunnerState.STOPPED
```

`accepting_orders` 已综合 Runner 是否启动、是否关闭、是否故障以及等待队列是否已满，调用方不需要自行推导。

### `get_queue() -> QueueSnapshot`

返回当前活动订单和按 FIFO 顺序排列的等待订单。

```python
queue = runner.get_queue()

print(queue.capacity)
print(queue.pending_count)
print(queue.current_order)

for order in queue.pending_orders:
    print(order.order_id, order.item_id)
```

`QueueSnapshot` 字段：

```python
capacity: int
current_order: OrderSnapshot | None
pending_orders: tuple[OrderSnapshot, ...]
pending_count: int  # 只读计算属性
```

终态订单不会出现在 `current_order` 或 `pending_orders` 中。

### `get_order(order_id: str) -> OrderSnapshot`

按订单 ID 查询最新快照。

```python
from taskrunner.errors import UnknownOrderError

try:
    order = runner.get_order(order_id)
except UnknownOrderError:
    print("订单不存在")
```

当前实现会在进程退出前保留已经结束的订单，因此终态订单仍可通过该方法查询。

当前执行任务可以从任务列表中推导：

```python
active_task = next(
    (
        task
        for task in order.tasks
        if task.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLING,
        }
    ),
    None,
)
```

## 6. 取消接口

### `cancel_order(order_id: str) -> OrderSnapshot`

根据订单当前状态请求取消，并返回处理后的快照。

```python
order = runner.cancel_order(order_id)
print(order.status)
```

不同状态的处理规则：

| 订单状态 | 行为 |
| --- | --- |
| `QUEUED` | 从 FIFO 删除，首个任务变为 `CANCELLED`，后续任务变为 `SKIPPED` |
| `PAUSED` | 先变为 `CANCELLING`，由唯一 Worker 执行安全回位，完成后变为 `CANCELLED` |
| `RUNNING` | 拒绝取消并抛出 `OrderNotCancellableError` |
| `CANCELLING` | 拒绝重复请求并抛出 `OrderNotCancellableError` |
| `SUCCEEDED/FAILED/CANCELLED` | 幂等返回原快照 |

当前没有 `resume(order_id)` 接口。

## 7. 订单和任务快照

所有快照均为不可变 dataclass。调用方不能通过快照修改 Runner 内部状态。

### `OrderSnapshot`

```python
order.order_id    # str
order.item_id     # str
order.target_id   # str
order.status      # OrderStatus
order.tasks       # tuple[RobotTaskSnapshot, ...]
order.error_code  # str | None
order.message     # str | None
order.created_at  # float，Unix 秒
order.updated_at  # float，Unix 秒
```

订单状态：

```python
OrderStatus.QUEUED
OrderStatus.RUNNING
OrderStatus.PAUSED
OrderStatus.CANCELLING
OrderStatus.SUCCEEDED
OrderStatus.FAILED
OrderStatus.CANCELLED
```

### `RobotTaskSnapshot`

```python
task.task_id     # str，例如 "<order_id>:pick"
task.task_type   # RobotTaskType
task.status      # TaskStatus
task.error_code # str | None
task.message    # str | None
```

任务类型：

```python
RobotTaskType.PICK
RobotTaskType.TRANSPORT_TO_DROPOFF
RobotTaskType.PLACE
RobotTaskType.RETURN_AND_RESET
```

任务状态：

```python
TaskStatus.BLOCKED
TaskStatus.QUEUED
TaskStatus.RUNNING
TaskStatus.PAUSED
TaskStatus.CANCELLING
TaskStatus.SUCCEEDED
TaskStatus.FAILED
TaskStatus.CANCELLED
TaskStatus.SKIPPED
```

状态含义：

- `BLOCKED`：等待前序任务成功，尚不具备执行资格。
- `SKIPPED`：前序任务失败或订单取消，因此确定不再执行。
- `PAUSED`：等待人工处理；当前版本只能取消，不能恢复。
- `CANCELLING`：Worker 正在执行暂停订单的安全回位。

## 8. 执行结果和错误代码

动作适配器通过 `TaskExecutionResult` 向 Runner 返回正常的业务结果：

```python
from taskrunner.taskrunner_contracts import TaskExecutionResult

TaskExecutionResult.succeeded("任务完成")
TaskExecutionResult.failed("out_of_stock", "第一次识别不到目标")
TaskExecutionResult.paused(
    "second_detection_failed",
    "第二次识别不到目标",
)
```

只有 `SUCCEEDED`、`FAILED` 和 `PAUSED` 可以作为动作执行结果。控制器报警、连接失败等致命问题不应包装为普通结果，而应抛出异常。

当前抓取错误代码：

```text
out_of_stock
second_detection_failed
target_unreachable
fatal_error
```

对应处理：

- `out_of_stock`：当前抓取任务和订单变为 `FAILED`，后续任务变为 `SKIPPED`，Runner 继续下一单。
- `second_detection_failed`：当前抓取任务和订单变为 `PAUSED`。
- `target_unreachable`：当前抓取任务和订单变为 `PAUSED`。
- `fatal_error`：Runner 进入 `FAULTED` 并停止继续调度。

## 9. 致命故障接口

### `report_fatal(error: Exception) -> None`

该接口供控制器监控或宿主集成代码报告致命故障，普通订单调用方一般不需要调用。

```python
runner.report_fatal(RuntimeError("机器人连接断开"))
```

首次调用会：

1. 保存原始异常。
2. 停止接收和调度新订单。
3. 将当前任务和当前订单标为 `FAILED(fatal_error)`。
4. 将当前订单尚未执行的后续任务标为 `SKIPPED`。
5. 关闭等待队列。
6. 调用构造 Runner 时传入的 `on_fatal(error)`。

后续重复调用不会覆盖第一次故障信息。

## 10. 动作适配接口

`TaskRunner` 依赖 `BeverageOrderActions` 协议：

```python
from taskrunner.taskrunner_contracts import (
    RobotTaskType,
    TaskExecutionResult,
)


class BeverageOrderActions:
    def execute(
        self,
        task_type: RobotTaskType,
        *,
        target_id: str,
    ) -> TaskExecutionResult:
        ...

    def cancel_paused_pick(self) -> None:
        ...
```

调用约束：

- `execute()` 由唯一 Worker 串行调用。
- `target_id` 是订单创建时由 `item_id` 映射出的视觉识别标签。
- 普通业务结果返回 `TaskExecutionResult`。
- 未处理异常会被 Runner 视为致命故障。
- `cancel_paused_pick()` 只在暂停抓取任务收到取消请求后由 Worker 调用。
- 动作适配器不维护订单状态，也不能直接访问 Runner 的队列或订单字典。

现有实现：

- `HardwareBeverageOrderActions`：正式机器人、灵巧手和 AGV 动作。
- `TestRecognitionOrders`：识别测试环境动作，不创建 AGV 或灵巧手。

抓取 Workflow 返回 `PickWorkflowResult`，动作适配器通过 `pick_result_to_task_result()` 将其转换为通用 `TaskExecutionResult`。这是 Workflow 业务结果与 Runner 状态机之间的边界。

## 11. 运行时工厂

### 正式硬件运行时

```python
create_hardware_runtime(
    *,
    config_dir: Path,
    camera_name: str = "left",
    agv_ip: str = "192.168.110.93",
    agv_port: int = 9201,
    agv_device_id: int = 1,
    queue_capacity: int = 10,
    monitor_interval_s: float = 0.02,
    on_fatal=None,
) -> HardwareRuntime
```

返回对象字段：

```python
runtime.runner
runtime.robot
runtime.monitor_robot
runtime.camera
runtime.snapshot_command
runtime.agv
runtime.close()
```

### 识别测试运行时

```python
create_recognition_test_runtime(
    *,
    config_dir: Path,
    robot_ip: str = "192.168.110.77",
    camera_name: str = "left",
    queue_capacity: int = 10,
    monitor_interval_s: float = 0.02,
    stage_delay_s: float = 3.0,
    on_fatal=None,
) -> RecognitionTestRuntime
```

返回对象字段：

```python
runtime.runner
runtime.robot
runtime.monitor_robot
runtime.camera
runtime.snapshot_command
runtime.close()
```

两个工厂都会完成资源连接和依赖组装，但不会自动调用 `runner.start()`。`runtime.close()` 只关闭该运行时实际创建的外部资源。

## 12. 异常类型

所有 TaskRunner 应用层异常都继承 `TaskRunnerError`：

| 异常 | 含义 |
| --- | --- |
| `QueueFullError` | 等待订单已经达到容量上限 |
| `QueueClosedError` | 内部等待队列已经关闭 |
| `UnknownOrderError` | `order_id` 不存在 |
| `OrderNotCancellableError` | 当前订单状态不允许取消 |
| `UnsupportedBeverageError` | 不支持该 `item_id` |
| `RunnerNotStartedError` | Runner 尚未启动 |
| `RunnerStoppedError` | Runner 已关闭或已发生致命故障 |
| `RunnerBusyError` | 仍有当前订单或等待订单，不能正常关闭 |
| `FatalExecutionError` | 必须停止 Runner 的执行错误 |
| `ControllerMonitorError` | 控制器监控连接或状态读取失败 |
| `ControllerFaultError` | 机器人掉使能、控制器报警或驱动器报警 |

## 13. 完整调用示例

```python
import time
from pathlib import Path

from taskrunner import OrderStatus
from taskrunner.runtime import create_hardware_runtime


runtime = create_hardware_runtime(config_dir=Path("config"))
runner = runtime.runner

try:
    runner.start()
    submitted = runner.submit_beverage("water")

    while True:
        order = runner.get_order(submitted.order_id)
        print(order.status.value, order.error_code, order.message)

        if order.status in {
            OrderStatus.SUCCEEDED,
            OrderStatus.FAILED,
            OrderStatus.CANCELLED,
        }:
            break
        time.sleep(0.5)

    runner.shutdown()
finally:
    runtime.close()
```

如果调用方需要在发生致命故障后自动关闭硬件资源，应在创建运行时前准备一个宿主回调，并通过 `on_fatal` 传入。回调不得尝试恢复或重新启动同一个 Runner。
