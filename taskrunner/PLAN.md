# 饮料订单队列与可视化测试方案

## 核心模型

- 一个有界 FIFO 只保存等待订单；当前订单不占用等待容量。
- 一个 Worker 串行执行订单，不允许两个订单的阶段互相穿插。
- 每个饮料订单固定包含 `Pick`、`TransportToDropoff`、`Place`、
  `ReturnAndReset` 四阶段任务链，不建立第二个任务 FIFO。
- `BLOCKED` 表示等待前序成功，`SKIPPED` 表示前序失败或订单取消后不再执行。
- 状态全部保存在内存中，进程退出后不恢复。

## 执行规则

- 第一次识别失败：`FAILED(out_of_stock)`，后续阶段 `SKIPPED`，继续下一单。
- 第二次识别失败或正式抓取前明确目标不可达：订单 `PAUSED`。
- 暂停订单阻塞后续队列；首版不支持恢复，只能查询或取消。
- 排队订单立即取消；暂停订单由 Worker 回初始位后取消；运行订单拒绝取消。
- 控制器报警、掉使能、监控断连及抓取后的动作错误均为致命故障。
- Runner 生命周期为 `NOT_STARTED / IDLE / RUNNING / FAULTED / STOPPED`。

## 公共接口

```python
start() -> None
shutdown() -> None
get_status() -> RunnerStatusSnapshot
submit_beverage(item_id: str) -> OrderSnapshot
get_queue() -> QueueSnapshot
get_order(order_id: str) -> OrderSnapshot
cancel_order(order_id: str) -> OrderSnapshot
```

外部只能读取不可变快照，不能直接访问或修改 Runner 的订单字典和队列。

## 运行时

`create_hardware_runtime()` 是完整正式路径，连接机器人、监控、相机、灵巧手和
AGV，使用 `TwoStagePickWorkflow` 与 `HardwareBeverageOrderActions`。

`create_recognition_test_runtime()` 是可视化测试页专用路径：

- 连接 `192.168.110.77` 模拟机器人和 `left` 测试相机。
- 使用 `workflows.test_recognition_workflow.TestRecognitionWorkflow`。
- 使用 `orders.TestRecognitionOrders` 执行四阶段。
- 运输、放置和返回阶段在模拟机器人执行动作，并用 3 秒等待模拟 AGV。
- 不导入、不创建、不连接 AGV 或灵巧手。
- 仅验证机器人使能、报警、静止和初始关节位，不检查 AGV 站点。

## 可视化测试页

`python -m taskrunner.test_ui` 启动本地测试服务。页面显示 Runner 状态、当前
订单、FIFO 队列和每个订单的四阶段任务链，支持下单以及取消排队/暂停订单。

HTTP 层只映射公共接口：

```text
GET  /api/status
GET  /api/queue
GET  /api/orders/{id}
POST /api/orders
POST /api/orders/{id}/cancel
```

页面每 500 ms 轮询；终态订单不进入历史列表，只显示约 8 秒结果提示。发生
致命故障时关闭设备资源并保留只读页面。测试页不属于正式 `frontend/`。

## 测试与边界

自动测试覆盖状态机、FIFO、容量、取消、暂停、致命故障、Runner 状态快照、
测试动作适配器、启动基线和 HTTP 公共接口映射。

首版不包含持久化、历史列表、恢复、运行中取消、优先级、咖啡订单、相机预览
和直接设备控制。识别测试 Workflow 不发送 MvL，因此不能通过测试页验收
`target_unreachable`。
