# TaskRunner 使用说明

## 1. 模块用途

TaskRunner 是一个只保存在内存中的饮料订单调度器。系统维护一个有界 FIFO
订单队列，并用唯一 Worker 串行执行每个订单的固定四阶段任务链：

```text
抓取识别 → 运送到放置位 → 放置 → 返回并复位
```

订单内部任务不是第二个 FIFO。`BLOCKED` 和 `SKIPPED` 只表达阶段依赖和
失败后的跳过结果。

当前版本提供本地可视化测试页。该页面只用于测试 TaskRunner，不属于正式
`frontend/`，也不提供持久化、历史订单或恢复功能。

## 2. 主要模块

| 模块 | 用途 |
| --- | --- |
| `taskrunner_contracts.py` | 状态枚举、执行结果和只读快照 |
| `orders.py` | 饮料订单、四阶段任务链和正式/测试动作适配器 |
| `queue.py` | 线程安全的有界 FIFO 等待队列 |
| `runner.py` | 唯一 Worker、状态转换和公共接口 |
| `monitor.py` | 独立连接监控掉使能和控制器报警 |
| `runtime.py` | 正式硬件运行时与识别测试运行时的对象组装 |
| `test_ui.py` | 本地 HTTP 服务及可视化测试入口 |
| `ui/` | 原生 HTML、CSS 和 JavaScript 页面 |

## 3. 公共接口

常规调用方只通过 `TaskRunner` 访问状态：

```python
start() -> None
shutdown() -> None
get_status() -> RunnerStatusSnapshot
submit_beverage(item_id: str) -> OrderSnapshot
get_queue() -> QueueSnapshot
get_order(order_id: str) -> OrderSnapshot
cancel_order(order_id: str) -> OrderSnapshot
```

支持的饮料：

| `item_id` | 检测标签 |
| --- | --- |
| `water` | `mineral_water` |
| `cola` | `coco_cola` |
| `oolong_tea` | `oolong_tea` |

`get_queue()` 返回当前活动订单和 FIFO 等待订单。每个订单快照都包含完整的
四阶段任务链。终态订单不在队列快照中，但进程退出前仍可用 `get_order()`
按 ID 查询。

`get_status()` 返回：

| Runner 状态 | 含义 |
| --- | --- |
| `NOT_STARTED` | 尚未执行 `start()` |
| `IDLE` | 已启动，正在等待订单 |
| `RUNNING` | 存在当前订单或等待订单 |
| `FAULTED` | 已发生致命故障并停止调度 |
| `STOPPED` | 已正常关闭 |

## 4. 状态和取消规则

订单状态：

```text
QUEUED / RUNNING / PAUSED / CANCELLING /
SUCCEEDED / FAILED / CANCELLED
```

任务状态：

```text
BLOCKED / QUEUED / RUNNING / PAUSED / CANCELLING /
SUCCEEDED / FAILED / CANCELLED / SKIPPED
```

- 第一次识别不到目标：订单 `FAILED(out_of_stock)`，后三阶段 `SKIPPED`。
- 第二次识别不到目标：抓取任务和订单进入 `PAUSED`。
- 正式抓取中，抓取前明确的 MvL 目标不可达也进入 `PAUSED`。
- 排队订单可以立即取消。
- 暂停订单由唯一 Worker 执行回初始位后取消。
- 正在正常执行的订单不能取消。
- 首版没有 `resume()`。

## 5. 两种运行时

### 正式硬件运行时

`create_hardware_runtime()` 连接：

- 正式机器人控制和监控端口。
- 测试配置指定的相机。
- 灵巧手。
- AGV。
- `TwoStagePickWorkflow`。

启动前检查机器人初始位、使能和报警状态，以及 AGV 是否位于抓取站点。

### 识别测试运行时

`create_recognition_test_runtime()` 连接：

- 默认 `192.168.110.77` 的模拟机器人控制和监控端口。
- 默认逻辑相机 `left`。
- `workflows.test_recognition_workflow.TestRecognitionWorkflow`。
- `orders.TestRecognitionOrders`。

此运行时不会导入、构造或连接 AGV，也不会创建灵巧手执行器。

测试订单的阶段行为：

1. 抓取：真实调用相机识别测试 Workflow。
2. 运送：模拟机器人进入运输姿态，等待 3 秒模拟 AGV。
3. 放置：模拟机器人执行放置动作，等待 3 秒，不操作灵巧手。
4. 复位：等待 3 秒模拟返回，模拟机器人回初始位。

当前识别测试 Workflow 不发送 `movel`/`movel_model`，因此不能用该页面验收
`target_unreachable`。

## 6. 启动测试页面

准备条件：

1. `192.168.110.77` 的模拟机器人可访问控制端口和监控端口。
2. 模拟机器人允许登录和上使能，并位于约定初始位。
3. `left` 相机已连接且没有被其他进程占用。
4. 模型、标签、相机参数和三个相机外参文件位于 `config/`。

启动：

```powershell
python -m taskrunner.test_ui
```

浏览器打开：

```text
http://127.0.0.1:8765
```

常用覆盖参数：

```powershell
python -m taskrunner.test_ui `
  --robot-ip 192.168.110.77 `
  --camera left `
  --port 8765 `
  --queue-capacity 10 `
  --stage-delay 3
```

页面只监听本机地址，不提供认证，也不应部署为生产服务。

## 7. 页面功能和数据边界

页面显示：

- Runner 生命周期和致命故障。
- 当前订单与四阶段任务链。
- FIFO 等待订单、排队位置和容量。
- 暂停原因、错误码和状态消息。
- 约 8 秒的终态结果提示。

页面允许：

- 提交三种饮料订单。
- 取消 `QUEUED` 订单。
- 取消 `PAUSED` 订单。

HTTP 适配层只调用 `get_status()`、`get_queue()`、`get_order()`、
`submit_beverage()`、`cancel_order()` 和 `shutdown()`。它不读取 `_orders`、
`_queue` 或其他私有字段。

浏览器每 500 ms 轮询一次。终态订单从主区域消失后，页面用已知 ID 调用一次
`get_order()` 并显示临时提示；刷新页面后不会保留。

## 8. 致命故障和退出

以下情况会让 Runner 进入 `FAULTED`：

- 模拟机器人掉使能。
- 控制器或驱动器错误码非零。
- 独立监控连接读取失败。
- 测试动作或暂停取消动作抛出异常。

发生故障后，测试运行时关闭相机和机器人连接，HTTP 页面继续运行并只读显示
故障，不能继续下单。

按 `Ctrl+C` 退出。如果仍有活动或等待订单，服务会拒绝退出；应先在页面取消
可取消订单，或等待当前订单结束。

## 9. 快速验收

1. 不摆放目标并下单：应显示 `FAILED(out_of_stock)`，后续阶段为 `SKIPPED`。
2. 目标连续两次可见：应观察四阶段依次执行并最终成功。
3. 第一次识别后在 0.5 秒内移走或遮挡目标：订单应进入 `PAUSED`。
4. 取消暂停订单：模拟机器人回初始位，订单变为 `CANCELLED`，下一单继续。
5. 连续下单：验证 FIFO、容量和排队取消。
6. 让模拟机器人掉使能：Runner 应显示 `FAULTED` 并停止接单。

## 10. 自动化测试

TaskRunner 测试不连接设备：

```powershell
python -m unittest discover -s taskrunner\tests -v
```

包含 Workflow 和 Backend 的相关测试：

```powershell
python -m pytest `
  taskrunner\tests `
  tests\test_two_stage_pick_workflow.py `
  tests\robot\test_kaanh_backend.py -q
```

## 11. 当前边界

- 不持久化订单或任务。
- 不提供历史列表。
- 不实现 `resume()`。
- 不允许取消正常运行中的订单。
- 不实现优先级或插队。
- 不实现咖啡订单。
- 测试页面不显示相机画面，也不提供直接设备控制。
