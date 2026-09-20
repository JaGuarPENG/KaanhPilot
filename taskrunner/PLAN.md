# 饮料订单队列与任务链实施方案

## Summary

- 系统只维护一个全局 FIFO 订单队列；默认最多等待 10 个订单，当前执行订单不计入容量。
- 每个订单内部包含抓取、运送、放置、复位四个不可穿插的机器人任务，并以有序任务链管理，不建立第二个 FIFO 任务队列。
- 首版不持久化、不实现恢复、不接入 `agent_api.py`；提供 Python API 和独立 CLI 测试脚本。
- 订单是全局调度和取消单位；机器人任务是订单内部可追踪状态的执行单元。

## 状态与执行规则

订单内部固定任务链：

1. `PickTask`：执行 `TwoStagePickWorkflow`。
2. `TransportToDropoffTask`：进入运输姿态，AGV 到站点 5。
3. `PlaceTask`：进入放置姿态、接近、松手、退回运输姿态。
4. `ReturnAndResetTask`：AGV 回站点 4，手臂回初始位。

状态模型：

- 订单：`QUEUED / RUNNING / PAUSED / CANCELLING / SUCCEEDED / FAILED / CANCELLED`
- 子任务：`BLOCKED / QUEUED / RUNNING / PAUSED / CANCELLING / SUCCEEDED / FAILED / CANCELLED / SKIPPED`
- 执行器不单独保存 `PAUSED`；运行状态由当前订单和当前子任务推导。
- 订单只有在四步全部成功、AGV 回到站点 4、手臂回初始位后才 `SUCCEEDED`。
- 一个订单开始后独占机器人，未来优先级只能调整尚未开始的订单，不能插入活动订单的四步之间。

队列与任务链的关系：

- 全局 FIFO 中只存放等待执行的订单；Runner 另行持有一个当前订单。
- 每个订单保存固定顺序的四个子任务对象，但这些对象不进入第二个队列。
- `BLOCKED` 表示子任务因前置任务尚未成功而不具备执行资格，不表示 Worker 或设备发生故障。
- 当前子任务成功后，下一个子任务才从 `BLOCKED` 转为 `QUEUED`，随后由同一 Worker 执行。
- `SKIPPED` 表示订单已因前序失败或取消而终止，该子任务确定不会执行；它是终态，不能恢复为 `QUEUED`。

暂停规则：

- 第一次识别不到目标：`PickTask FAILED(out_of_stock)`，后三个 `BLOCKED` 子任务转为 `SKIPPED`，订单 `FAILED`，然后继续下一订单。
- 第二次识别不到目标：`PickTask PAUSED`，订单同步 `PAUSED`。
- 抓取前收到明确的 MvL“目标点不可达”响应：进入 `PAUSED`。
- 首版不提供 `resume`；暂停订单只能查询、取消或等待程序关闭。
- 成功抓取后不再产生任务级 `PAUSED`，导航、放置或复位异常均按致命故障处理。

取消规则：

- `QUEUED` 订单：立即 `CANCELLED`；`PickTask` 设为 `CANCELLED`，其余子任务设为 `SKIPPED`。
- `PAUSED` 订单：当前任务异步进入 `CANCELLING`，Worker 调用 `move_init_pose()`；成功后当前任务和订单均为 `CANCELLED`，其余 `BLOCKED` 子任务转为 `SKIPPED`；失败则触发致命退出。
- `RUNNING` 订单：拒绝取消。
- 已终态订单再次取消：不执行动作，返回现有快照。
- 取消接口统一使用 `order_id`，不允许调用方单独取消某个子任务。

## 核心接口与实现

所有任务队列相关实现放在 `taskrunner/` 包中；Workflow、Command 和设备 Backend 继续保留在现有分层目录，不移入 `taskrunner/`。

建议目录结构：

```text
taskrunner/
├── __init__.py
├── taskrunner_contracts.py  # 状态、暂停原因、请求与只读快照契约
├── errors.py          # 队列满、订单不存在、不可取消等应用错误
├── queue.py           # 有界 FIFO 订单队列
├── orders.py          # 订单实体、订单类型及其任务链执行逻辑
├── monitor.py         # 独立控制器状态监控
├── runner.py          # 单 Worker、状态流转和公共接口
├── runtime.py         # 真机测试入口的对象组装、启动基线和资源清理
├── cli.py             # 仅调用公共接口的轻量本地测试入口
├── tests/             # 假设备状态机与 CLI 单元测试
├── README.md          # 模块职责、公共接口和模拟/真机使用说明
└── PLAN.md
```

文件边界：

- `taskrunner_contracts.py` 只定义 TaskRunner 的稳定契约，包括 `OrderStatus`、`TaskStatus`、`PauseReason`、下单请求和只读快照；不存放视觉模型或设备状态。
- `orders.py` 集中存放订单实体、订单构造和各类订单的任务链逻辑；首版只有饮料订单，未来咖啡等新订单类型也在此扩展。
- `cli.py` 不定义业务状态、队列规则或设备操作，只解析命令、调用 `TaskRunner` 的现有公共接口并格式化输出。

`taskrunner.runner.TaskRunner` 提供：

```python
submit_beverage(item_id: str) -> OrderSnapshot
get_queue() -> QueueSnapshot
get_order(order_id: str) -> OrderSnapshot
cancel_order(order_id: str) -> OrderSnapshot
start() -> None
shutdown() -> None
```

接口约束：

- `item_id` 首版只允许 `water`、`cola`、`oolong_tea`，内部映射到对应检测标签。
- `get_queue()` 只返回活动订单和 FIFO 等待订单。
- 终态订单保留在内存中直到进程退出，可按 `order_id` 查询，但不提供历史列表。
- 队列满时抛出明确的 `QueueFullError`。
- 暂不实现 `request_id` 幂等、HTTP API、磁盘存储和任务恢复。
- 快照返回不可变副本，外部不能直接修改 Runner 内部状态。
- 队列通过抽象接口封装，首版为 FIFO；以后可替换优先级实现。

改造 [two_stage_pick_workflow.py](C:/Users/11051/Desktop/KaanhPilot/workflows/two_stage_pick_workflow.py)：

- 用结构化结果区分成功、无库存和暂停。
- 记录暂停原因与阶段，例如 `second_detection_failed`、`target_unreachable`。
- 只把抓取前明确的目标点不可达转换为暂停；其他异常继续向上抛出。
- 第一次识别失败返回 `out_of_stock`，不再使用含义模糊的整数返回码。

改造 [kaanh_backend.py](C:/Users/11051/Desktop/KaanhPilot/robot/kaanh_backend.py) 及执行器：

- 将控制器明确的目标点不可达响应转换为专用 `TargetUnreachableError`。
- 控制器错误、掉使能、连接失败和响应不确定不能伪装成目标点不可达。
- `move_init_pose()`、运输位和放置位动作必须传播底层失败，不再静默返回成功。
- AGV 任务必须检查 `NavigationResult.success`；失败触发致命流程。

机器人监控：

- 使用独立监控端口连接，避免与控制命令共享 WebSocket。
- 完成登录、手动使能和启动基线检查后再启动监控线程。
- 首次监控读取失败、`activated == False`、控制器错误码或任一驱动错误码非零，立即停止 Runner 并调用注入的 `on_fatal(error)`。
- AGV 因障碍进入自身暂停状态时，订单仍保持 `RUNNING`；AGV 自己恢复导航。
- `on_fatal` 由宿主负责清理资源和退出进程；Runner 不使用强制进程退出。

启动基线：

- AGV 必须位于站点 4。
- 机器人已使能且无控制器/驱动错误。
- 手臂位于约定初始关节位，默认允许 2° 可配置误差。
- 不满足基线时拒绝启动第一条订单。

通过 `python -m taskrunner.cli` 启动模拟设备的独立测试入口；显式增加
`--real` 才连接真实机器人、相机和 AGV，避免误操作真实设备。入口支持：

```text
submit water
queue
status <order_id>
cancel <order_id>
quit
```

CLI 是长时运行的交互式提示符，不是启动时只执行一次的命令。启动参数只用于选择运行配置；进入提示符后，可以在同一进程中持续执行多次 `submit`、`queue`、`status` 和 `cancel`。Runner 的 Worker 和控制器监控线程在后台持续运行，CLI 输入不得阻塞任务执行。只有输入 `quit`、收到终止信号或发生致命故障时才退出。

CLI 只使用 `submit_beverage()`、`get_queue()`、`get_order()`、`cancel_order()` 和 `shutdown()`；不为测试界面新增专用的业务 API、事件订阅、健康模型或底层设备命令。`status` 和 `queue` 直接展示现有快照，`quit` 只调用 Runner 本来就需要的生命周期关闭能力。

CLI 负责本地测试时的对象组装，复用现有 `RobotSetup`、Workflow、Command 和 Backend，不要求这些模块增加 CLI 分支。发生致命故障时清理资源并以非零状态退出。`agent_api.py` 不在本次改动范围内，后续前端工程师只需适配上述 Runner 接口。

## Test Plan

自动化测试使用假机器人、假相机和假 AGV，覆盖：

- FIFO 顺序、10 个等待订单上限和满队列拒绝。
- 同一订单四个子任务连续执行，订单之间不穿插，也不存在第二个任务 FIFO。
- 新订单创建时 `PickTask` 为 `QUEUED`，其余三个任务为 `BLOCKED`。
- 前序任务成功后仅解锁紧邻的下一个任务。
- 完整成功后才标记订单 `SUCCEEDED`。
- 第一次识别失败产生 `out_of_stock`，后三个 `BLOCKED` 任务转为 `SKIPPED`。
- 第二次识别失败和抓取前目标不可达进入 `PAUSED`。
- 暂停订单异步取消：`PAUSED → CANCELLING → CANCELLED`。
- 暂停取消返回初始位失败时触发 `on_fatal`。
- 排队订单取消、运行订单拒绝取消、终态订单重复取消。
- 暂停期间可以继续接收订单，但后续订单保持等待。
- 抓取成功后的导航、放置和复位异常触发致命退出。
- 监控首次断连、掉使能、控制器错误和驱动错误触发致命退出。
- AGV 遇障碍期间订单保持 `RUNNING`。
- `get_queue()` 不返回终态历史，`get_order()` 在当前进程内仍可查询终态。
- CLI 使用假设备完成下单、查询、取消和退出流程。

真机验收依次验证：无库存、二次识别暂停、暂停取消回初始位、完整取送循环、明确目标点不可达，以及控制器掉使能后的程序退出。

## Assumptions

- 首版只支持水、可乐和乌龙茶，不实现咖啡订单。
- 暂停只发生在物体尚未抓住且 AGV 仍位于站点 4 的阶段。
- 从所有首版暂停点直接调用现有 `move_init_pose()` 已被视为安全恢复路径。
- 放置任务首版可复用现有命令实现，未来封装为独立 Workflow 时不改变订单和任务链模型。
- 实施时同步更新 `CONTEXT.md`、架构和项目计划，记录订单、子任务、暂停和取消的最终语义。
