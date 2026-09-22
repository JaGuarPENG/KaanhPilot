# TaskRunner 公开方法交接说明

本文档只介绍 `TaskRunner` 对外公开的 Python 方法、数据结构和调用示例，不涉及测试 UI 或 HTTP 适配层。

入口：

```python
from taskrunner import TaskRunner
```

## 1. 公开方法总览

`TaskRunner` 当前有以下公开方法：

| 方法 | 用途 | 主要调用方 |
| --- | --- | --- |
| `start()` | 启动 Runner、Worker 和控制器监控 | 程序宿主 |
| `shutdown()` | 在安全条件下永久关闭 Runner | 程序宿主 |
| `submit_beverage(item_id)` | 提交一个饮料订单 | 业务层 |
| `get_status()` | 查询 Runner 全局状态 | 业务层 |
| `get_queue()` | 查询当前订单和 FIFO 等待队列 | 业务层 |
| `get_order(order_id)` | 查询指定订单的完整快照 | 业务层 |
| `cancel_order(order_id)` | 取消排队或暂停中的订单 | 业务层 |
| `report_fatal(error)` | 向 Runner 报告致命故障 | 控制器监控或宿主 |

业务层通常只需要使用：

```python
runner.get_status()
runner.get_queue()
runner.get_order(order_id)
runner.submit_beverage(item_id)
runner.cancel_order(order_id)
```

`start()`、`shutdown()` 和 `report_fatal()` 属于运行时生命周期接口，不应由普通页面操作直接触发。

## 2. 创建与启动

### 构造函数

```python
TaskRunner(
    actions,
    *,
    queue_capacity=10,
    monitor=None,
    on_fatal=None,
    order_id_factory=None,
)
```

参数：

- `actions`：订单动作适配器，实现四阶段任务和暂停取消回位。
- `queue_capacity`：FIFO 等待队列最大长度；当前正在执行的订单不占用该容量。
- `monitor`：可选的独立控制器监控器。
- `on_fatal`：发生首个致命故障后的宿主回调，通常用于关闭硬件资源。
- `order_id_factory`：可选订单 ID 生成器；默认生成 UUID 字符串。

通常不应由业务层自己拼装硬件对象，而应使用 `taskrunner.runtime` 提供的运行时工厂，然后取得其中的 `runner`。

### `start() -> None`

启动唯一订单 Worker 和可选控制器监控。

```python
runtime = create_hardware_runtime(config_dir=config_dir)
runner = runtime.runner
runner.start()
```

行为：

- 重复调用已启动 Runner 的 `start()` 是幂等的。
- Runner 关闭后不能重新启动。
- Runner 发生致命故障后不能重新启动。

## 3. 查询 Runner 全局状态

### `get_status() -> RunnerStatusSnapshot`

返回 Runner 生命周期、是否允许接单以及首个致命错误。

```python
status = runner.get_status()

print(status.state.value)
print(status.accepting_orders)
print(status.fatal_error_type)
print(status.fatal_error_message)
```

返回结构：

```python
RunnerStatusSnapshot(
    state=RunnerState.IDLE,
    accepting_orders=True,
    fatal_error_type=None,
    fatal_error_message=None,
)
```

`state` 可能值：

| 枚举 | 字符串值 | 含义 |
| --- | --- | --- |
| `RunnerState.NOT_STARTED` | `not_started` | 尚未调用 `start()` |
| `RunnerState.IDLE` | `idle` | 已启动，没有当前订单和等待订单 |
| `RunnerState.RUNNING` | `running` | 存在当前订单或等待订单 |
| `RunnerState.FAULTED` | `faulted` | 已发生致命故障并停止调度 |
| `RunnerState.STOPPED` | `stopped` | 已正常关闭 |

是否允许提交订单应直接判断 `accepting_orders`，不要只根据 `state` 推断：

```python
status = runner.get_status()
if status.accepting_orders:
    order = runner.submit_beverage("water")
```

等待队列已满时，即使 Runner 正在正常执行，`accepting_orders` 也会是 `False`。

## 4. 提交饮料订单

### `submit_beverage(item_id: str) -> OrderSnapshot`

创建一个订单并加入 FIFO 等待队列，返回提交瞬间的订单快照。

```python
order = runner.submit_beverage("water")

print(order.order_id)
print(order.item_id)
print(order.status.value)
```

支持的 `item_id`：

| `item_id` | 商品 | 内部识别标签 `target_id` |
| --- | --- | --- |
| `water` | 矿泉水 | `mineral_water` |
| `cola` | 可乐 | `coco_cola` |
| `oolong_tea` | 乌龙茶 | `oolong_tea` |

完整示例：

```python
from taskrunner.errors import (
    QueueFullError,
    RunnerNotStartedError,
    RunnerStoppedError,
    UnsupportedBeverageError,
)

try:
    order = runner.submit_beverage("cola")
except UnsupportedBeverageError:
    print("不支持该商品")
except QueueFullError:
    print("等待队列已满")
except RunnerNotStartedError:
    print("Runner 尚未启动")
except RunnerStoppedError:
    print("Runner 已停止或已发生致命故障")
else:
    print(f"订单已创建: {order.order_id}")
```

说明：

- 返回的是不可变快照，不是 Runner 内部的可变订单对象。
- Worker 可能在方法返回后立即领取订单，因此不要长期依赖提交瞬间的状态。
- 当前所有状态只保存在内存中，进程重启后订单丢失。

## 5. 查询当前订单与队列

### `get_queue() -> QueueSnapshot`

返回当前活动订单以及按 FIFO 排列的等待订单。

```python
queue = runner.get_queue()

print(f"等待容量: {queue.capacity}")
print(f"等待数量: {queue.pending_count}")

if queue.current_order is not None:
    print(f"当前订单: {queue.current_order.order_id}")

for position, order in enumerate(queue.pending_orders, start=1):
    print(position, order.order_id, order.item_id, order.status.value)
```

返回结构示例：

```python
QueueSnapshot(
    capacity=10,
    current_order=OrderSnapshot(...),
    pending_orders=(
        OrderSnapshot(...),
        OrderSnapshot(...),
    ),
)
```

语义：

- `current_order`：正在执行、暂停或执行安全取消回位的订单；没有时为 `None`。
- `pending_orders`：FIFO 等待订单，只读元组；第一项是下一单。
- `capacity`：等待队列容量，不包含 `current_order`。
- `pending_count`：等待订单数量，等价于 `len(pending_orders)`。
- 成功、失败或取消的终态订单不会继续出现在此快照中。

## 6. 查询指定订单

### `get_order(order_id: str) -> OrderSnapshot`

根据订单 ID 返回该订单的最新完整快照。

```python
from taskrunner.errors import UnknownOrderError

try:
    order = runner.get_order(order_id)
except UnknownOrderError:
    print("当前进程中不存在该订单")
else:
    print(order.status.value)
    print(order.message)
```

终态订单从 `get_queue()` 消失后，在当前进程退出前仍可通过该方法查询。

`OrderSnapshot` 字段：

```python
OrderSnapshot(
    order_id="...",
    item_id="water",
    target_id="mineral_water",
    status=OrderStatus.RUNNING,
    tasks=(...),
    error_code=None,
    message=None,
    created_at=1789970000.125,
    updated_at=1789970001.500,
)
```

时间字段是以秒为单位的 Unix 时间戳。

## 7. 取消订单

### `cancel_order(order_id: str) -> OrderSnapshot`

请求取消订单，并返回请求处理后的最新快照。

```python
from taskrunner.errors import OrderNotCancellableError, UnknownOrderError

try:
    order = runner.cancel_order(order_id)
except UnknownOrderError:
    print("订单不存在")
except OrderNotCancellableError as error:
    print(f"当前不能取消: {error}")
else:
    print(f"取消请求后的状态: {order.status.value}")
```

不同状态下的行为：

| 当前订单状态 | 调用结果 |
| --- | --- |
| `queued` | 立即从 FIFO 移除并转为 `cancelled` |
| `paused` | 先转为 `cancelling`；Worker 执行安全回初始位后再转为 `cancelled` |
| `running` | 抛出 `OrderNotCancellableError` |
| `cancelling` | 抛出 `OrderNotCancellableError` |
| `succeeded` / `failed` / `cancelled` | 幂等返回原快照 |

取消暂停订单的完整等待示例：

```python
import time

order = runner.cancel_order(order_id)

while order.status.value == "cancelling":
    time.sleep(0.2)
    order = runner.get_order(order_id)

if order.status.value == "cancelled":
    print("机器人已完成安全回位，订单已取消")
```

`cancel_order()` 返回 `cancelling` 时，只表示 Worker 已收到请求，不表示机器人已经回位。

当前没有 `resume(order_id)` 方法。暂停状态只预留了未来恢复设计，目前只能取消暂停订单。

## 8. 订单与任务快照

### `OrderSnapshot`

```python
order.order_id          # str
order.item_id           # str
order.target_id         # str
order.status            # OrderStatus
order.tasks             # tuple[RobotTaskSnapshot, ...]
order.error_code        # str | None
order.message           # str | None
order.created_at        # float，Unix 秒
order.updated_at        # float，Unix 秒
```

订单状态：

```python
OrderStatus.QUEUED       # "queued"
OrderStatus.RUNNING      # "running"
OrderStatus.PAUSED       # "paused"
OrderStatus.CANCELLING   # "cancelling"
OrderStatus.SUCCEEDED    # "succeeded"
OrderStatus.FAILED       # "failed"
OrderStatus.CANCELLED    # "cancelled"
```

### `RobotTaskSnapshot`

```python
task.task_id       # str
task.task_type     # RobotTaskType
task.status        # TaskStatus
task.error_code    # str | None
task.message       # str | None
```

固定四阶段任务链：

```python
RobotTaskType.PICK                  # "pick"
RobotTaskType.TRANSPORT_TO_DROPOFF  # "transport_to_dropoff"
RobotTaskType.PLACE                 # "place"
RobotTaskType.RETURN_AND_RESET      # "return_and_reset"
```

任务状态：

```python
TaskStatus.BLOCKED      # "blocked"，等待前序任务成功
TaskStatus.QUEUED       # "queued"
TaskStatus.RUNNING      # "running"
TaskStatus.PAUSED       # "paused"
TaskStatus.CANCELLING   # "cancelling"
TaskStatus.SUCCEEDED    # "succeeded"
TaskStatus.FAILED       # "failed"
TaskStatus.CANCELLED    # "cancelled"
TaskStatus.SKIPPED      # "skipped"，因前序失败或取消而不再执行
```

抓取阶段可能产生的业务错误代码：

```python
"out_of_stock"              # 第一次识别不到目标
"second_detection_failed"   # 第二次识别不到目标，订单暂停
"target_unreachable"        # 目标点不可达，订单暂停
```

典型结果：

- 第一次识别不到目标：订单 `FAILED`，`error_code="out_of_stock"`，后三阶段为 `SKIPPED`。
- 第二次识别不到目标：订单 `PAUSED`，`error_code="second_detection_failed"`。
- `movel` 或 `movel_model` 目标不可达：订单 `PAUSED`，`error_code="target_unreachable"`。
- 致命硬件故障：Runner `FAULTED`，当前订单 `FAILED`，`error_code="fatal_error"`。

## 9. 报告致命故障

### `report_fatal(error: Exception) -> None`

该方法供 `ControllerMonitor` 或运行时宿主调用，不属于普通业务操作。

```python
try:
    check_external_controller()
except Exception as error:
    runner.report_fatal(error)
```

调用后：

- 只记录第一个致命故障，后续重复报告不会覆盖它。
- Runner 停止继续调度。
- 当前任务和订单标记为 `FAILED(fatal_error)`。
- 当前订单尚未执行的后续任务标记为 `SKIPPED`。
- 调用构造时传入的 `on_fatal(error)`，由宿主清理硬件资源。
- `get_status()` 返回 `RunnerState.FAULTED`，且不再允许提交订单。

业务层不能用该方法代替订单失败或订单取消。

## 10. 安全关闭

### `shutdown() -> None`

在 Runner 空闲时永久关闭 Worker、队列和控制器监控。

```python
from taskrunner.errors import RunnerBusyError

try:
    runner.shutdown()
except RunnerBusyError:
    print("仍有当前订单或等待订单，不能关闭")
else:
    runtime.close()
```

行为：

- Runner 未启动时调用不会报错。
- 正常运行时，只要存在当前订单或等待订单，就抛出 `RunnerBusyError`。
- 已发生致命故障时允许立即进入清理流程。
- 关闭是永久的；需要重新运行时必须重新创建完整 Runtime 和 TaskRunner。
- `shutdown()` 只管理 Runner 生命周期；外部机器人、相机和 AGV 资源仍应由 Runtime 的 `close()` 清理。

## 11. 完整业务调用示例

下面的例子演示启动、提交、观察到终态和安全关闭。正式程序不一定需要用循环轮询，可以根据宿主架构封装观察方式。

```python
import time
from pathlib import Path

from taskrunner.runtime import create_hardware_runtime
from taskrunner.taskrunner_contracts import TERMINAL_ORDER_STATUSES


runtime = create_hardware_runtime(config_dir=Path("config"))
runner = runtime.runner

try:
    runner.start()

    status = runner.get_status()
    if not status.accepting_orders:
        raise RuntimeError("TaskRunner 当前不允许接单")

    submitted = runner.submit_beverage("water")
    order_id = submitted.order_id
    print(f"已提交订单: {order_id}")

    while True:
        order = runner.get_order(order_id)
        print(order.status.value, order.message)

        if order.status in TERMINAL_ORDER_STATUSES:
            break
        time.sleep(0.5)

    print(f"订单最终状态: {order.status.value}")
finally:
    try:
        runner.shutdown()
    finally:
        runtime.close()
```

## 12. 异常类型

所有可分类的 TaskRunner 应用层异常都继承自 `TaskRunnerError`：

```python
from taskrunner.errors import TaskRunnerError

try:
    order = runner.submit_beverage("water")
except TaskRunnerError as error:
    print(type(error).__name__, str(error))
```

常用异常：

| 异常 | 含义 |
| --- | --- |
| `QueueFullError` | FIFO 等待队列已满 |
| `UnknownOrderError` | 当前进程中不存在该订单 ID |
| `OrderNotCancellableError` | 订单当前状态不允许取消 |
| `UnsupportedBeverageError` | 不支持该 `item_id` |
| `RunnerNotStartedError` | `start()` 前提交订单 |
| `RunnerStoppedError` | Runner 已关闭或已因致命故障停止 |
| `RunnerBusyError` | 存在活动/等待订单，不能正常关闭 |

## 13. 使用约束

- 所有查询方法返回只读快照，调用方不能通过快照修改 Runner 内部状态。
- 不要读取 `_orders`、`_queue`、`_current_order_id` 等私有字段。
- Runner 只有一个 Worker，订单严格按 FIFO 顺序串行执行。
- TaskRunner 不持久化；程序退出后订单、队列和终态记录全部丢失。
- 当前版本不支持恢复暂停订单，也不支持取消正在运行的订单。
- 当前版本不提供历史订单列表；调用方如果需要稍后查询终态，应保存 `submit_beverage()` 返回的 `order_id`。

相关代码：

- 公开类：[`runner.py`](runner.py)
- 快照与状态枚举：[`taskrunner_contracts.py`](taskrunner_contracts.py)
- 业务异常：[`errors.py`](errors.py)
- 订单和动作适配器：[`orders.py`](orders.py)
- Runtime 组装：[`runtime.py`](runtime.py)
