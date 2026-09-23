# 前端直接接入 TaskRunner

## iPad 界面（方案 N）

- 点单页采用浅灰背景，按饮料、零食、咖啡的小标题分组，每行两款无边框商品。
- 「点单 / 订单」底部导航常驻，点单时保留四段细线订单进度。页面可滚动并为底栏、安全区预留空间。
- 订单页包含当前订单、可展开的等待队列、默认折叠的本页历史订单，以及头部主视角和左右手小视角。
- 队列及历史条目可打开详情；查看详情不改变正在执行订单的进度跟踪。
- 维护入口打开补货与库存测试弹窗，保持原有运行模式及忙碌状态限制。
- 下单不会自动切页。咖啡、零食仍使用原 itemTasks 映射，实际执行物品不同的说明在点单摘要中保留。
- 页面继续使用项目已有商品图、咖啡插画和相机示意资源；正式运行时显示已启动的头部与左手相机，未接入的右手相机保留示意图。测试运行时仅显示左手相机。

浏览器回归（需要 Node、Playwright 和本机 Edge；Playwright 可通过 NODE_PATH 指向环境已有依赖）：

```powershell
node --test frontend/tests/test_ipad_ui.cjs frontend/tests/test_progress_follow.cjs frontend/tests/test_queue_model.cjs frontend/tests/test_status_contract.cjs
```

这些浏览器检查通过隔离的模拟 HTTP 接口验证真实页面，不连接机器人。覆盖下单映射、取消、订单详情、断线、提交不明恢复、维护入口和不同视口下的底栏布局。设置 `UI_SCREENSHOTS` 为输出目录可保存 1024×1536 竖屏截图；本次预览位于 `frontend/preview/`。

本次验证：Python 前端测试 23 项通过；Node 非浏览器测试 10 项通过。
浏览器回归需要本机安装 Playwright，本环境未运行该项。

## 启动与接口

唯一 Python 启动入口为根目录 `python launcher.py`。`frontend/Start-Frontend.bat`
也转到这个入口。读取 `config/launcher/launcher_config.json`，不再使用前端独立配置。

启动器会自动打印本机网页与 iPad 访问地址。`host: "0.0.0.0"` 时枚举本机局域网 IPv4，
多个网卡会显示多个候选地址；iPad 连接同一局域网后，用 Safari 打开对应地址。
若监听 `127.0.0.1` 则只提示本机访问，不会打印无法访问的局域网链接。
地址检测失败不阻止服务启动；网页需等待设备初始化完成后才能访问。

- `dry_run: true`：通过 `create_recognition_test_runtime()` 连接模拟机器人服务器，并初始化左手相机和识别资源，不连接 AGV 和灵巧手。
- `dry_run: false`：通过现有 `create_hardware_runtime()` 连接机器人、头部和左手相机、灵巧手和 AGV；右手相机尚未接入。
  右手相机接入并由运行时加入 `cameras` 后，页面会自动改为实时画面，不需改页面配置。
- `simulation_robot_ip`：可选的模拟控制器 IP，未设置时沿用 runtime 默认值 `192.168.110.77`。
- `stage_delay`：可选的测试阶段等待秒数，未设置时沿用 runtime 默认值 3；`queue_capacity` 默认 10。
- `host`、`port`、`python_executable` 和 AGV 配置继续由根启动入口读取。页面无需口令，旧配置中的 `api_token` 不再使用。

页面 → `frontend/server.py` → Runner 公共方法。HTTP 层不保存订单、不实现第二套调度。
`taskrunner/`（含 `test_ui.py`）和其他后端实现不需要修改。

| 接口 | 行为 |
|---|---|
| POST /api/orders，`{"item_id":"water"}` | submit_beverage |
| GET /api/status | get_status 原始快照 |
| GET /api/queue | get_queue 原始快照 |
| GET /api/orders/{id} | get_order 原始快照 |
| POST /api/orders/{id}/cancel | cancel_order |
| GET /api/info | 运行模式、`cameras: {head, left, right}` 各视角可用性、测试注入能力与进程会话标识 |
| GET /api/cameras/{head,left,right}/frame.jpg | 返回运行时已启动相机的 JPEG 单帧；未接入的相机返回 503，页面显示示意图 |
| POST /api/test/inventory | 仅单元测试注入纯软件动作时可用；正常启动的两种模式均不提供 |

前端轮询队列，并继续查询已离队的订单，取得最终完成、失败或无库存结果。
四阶段显示直接读取 `tasks[].status`：抓取、运输、放置、返回复位。
排队/暂停订单可取消，运行中的普通动作按 Runner 规则不提供取消。

## 新商品映射

修改 `frontend/dist/config.js` 中的 `itemTasks`。目前三种饮料使用自己的 ID，
其余商品映射 `water`。例如 Runner 日后注册了 `latte`，将 `latte:'water'`
改为 `latte:'latte'`。HTTP 接口不需要改动。新增卡片仍需在 HTML 中提供匹配的 `data-item`。
别名只由发起订单的页面记住，其他页面显示 Runner 返回的实际物品。

## 简化后的边界

- 不持久化订单，也无请求幂等去重。进程重启丢失 Runner 订单，刷新页面清空本页历史。
- 提交超时不会自动重发。页面要求核对队列及设备结果后才能继续下单。
- 售罄提示来自本页观察到的 `failed / out_of_stock` 结果，并作用于映射到同一物品的卡片。
  页面加载前已结束的订单不会被枚举，因此这不是跨页面共享库存数据库。
- “设为待检测”仅清除本页售罄提示；下一单由 Runner 重新检测。
- 原纯软件 `frontend/simulation.py` 仅供自动化测试，不再用于项目启动；模拟控制器模式隐藏演示库存设置面板。
- dry_run 和真实模式使用相同队列及状态机；直接调用 `runtime.py` 的工厂，`test_ui.py` 不参与启动。
- 当前 `create_recognition_test_runtime()` 中识别 Workflow 的构造与调用被注释，取物阶段直接返回模拟成功。此次保持该逻辑不变，因此它目前不能验证真实识别缺货；运输、放置、复位会按现有实现向模拟控制器发动作指令。

要使用模拟控制器，将统一启动配置中的 `dry_run` 设为 `true`。可选增加
`"simulation_robot_ip": "192.168.110.77"`。它使用现有机器人配置中的控制/监控端口及登录配置，
也需要左手相机和识别依赖可用；连接失败会报错，不会退回纯演示或切换真实机器人。

验证：`python -m unittest discover -s frontend/tests -p "test_*.py" -v`；
`node frontend/tests/test_queue_model.cjs`；`node frontend/tests/test_status_contract.cjs`。
