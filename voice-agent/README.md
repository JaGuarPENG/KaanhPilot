# MiniMax Voice Agent · v0.6

讯飞 AIKit 本地唤醒已支持 `WAKE_PROVIDER=xfyun`，复用现有讯飞应用凭证，
待机音频在本地检测，唤醒后再进入云端流式 STT。配置与 SDK 授权说明见
[讯飞本地唤醒](docs/xfyun-wake.md)。

## 从主项目启动语音助手

Windows 用户安装依赖并配置 `voice-agent/.env` 后，可以直接双击根目录的
`Start-Voice-Agent.bat`。语音启动脚本优先使用根目录 `.venv\Scripts\python.exe`，
不存在时激活 Conda 的 `pyagent` 环境，不再回退到系统 Python。依赖需要安装到选中的环境。
脚本从 `CONDA_EXE` 定位 Conda，未找到时使用本机的 `D:\anaconda3\condabin\conda.bat`；
其他机器可设置 `KAANH_CONDA_BAT` 指定 `conda.bat` 完整路径（值不带引号）。
传入 `--check-env` 可仅查看解释器而不启动服务。旧的 `VOICE_AGENT_PYTHON` 和
`VOICE_AGENT_CONDA_ENV` 不再改变解释器选择顺序。
退出或启动失败后窗口会保留，便于查看提示。
该批处理直接启动监听模块，不依赖 `start_voice_agent.py`。

在 KaanhPilot 根目录安装依赖，并配置 `voice-agent/.env` 后运行：

```bash
python -m pip install -r requirements.txt
python start_voice_agent.py
```

根目录启动脚本使用当前 Python 环境，自动定位 `voice-agent`，从该目录读取
`.env`、写入相对路径日志，并透传监听程序参数。不需要安装 editable 项目或
`voice-agent-listen` 命令。按 `Ctrl+C` 停止。

```bash
python start_voice_agent.py --list-devices
python start_voice_agent.py --device 1 --output-device 3
```

需要启用 `ASR_PROVIDER=xfyun` 或 `whisper`；播放回复需设置
`TTS_PROVIDER=minimax` 并配置相应凭证。使用 TaskRunner 下单时先单独启动主项目后端，
该脚本只启动语音持续监听。

## 接入 KaanhPilot 商品订单

新增可选 `TaskRunnerExecutor`，通过与商品卡片相同的 `POST /api/orders`
提交订单，不创建第二个 Runner，不直接控制机器人。默认仍为 Mock。

先按主项目原有方式启动后端，再在 **voice-agent/.env** 中设置：

```dotenv
LLM_PROVIDER=minimax
MINIMAX_API_KEY=你的密钥
SCENE=kaanh_agent
EXECUTOR_PROVIDER=taskrunner
TASKRUNNER_BASE_URL=http://127.0.0.1:9090
TASKRUNNER_TIMEOUT_SECONDS=8
```

后端在其他机器或端口运行时修改 `TASKRUNNER_BASE_URL`。这会提交真实后端订单，
是否驱动硬件由后端运行模式决定。`EXECUTOR_PROVIDER=taskrunner` 必须搭配仅开放
`submit_order` 动作的场景（如 `SCENE=kaanh_agent`），场景名称、角色和背景可自定义；
改回 `mock` 可仅模拟下单。Mock LLM 仍只有原有演示规则，
自然语言下单需要上述 MiniMax 配置。

HTTP 服务与 `voice-agent-listen` 共用该配置。在 voice-agent 目录启动：

```bash
uvicorn voice_agent.main:create_app --factory --host 127.0.0.1 --port 8000
```

向 `/chat` 发送 `{"text":"给我一瓶可乐"}` 即可验证文本入口；持续语音模式另按下文
配置 ASR 后运行 `voice-agent-listen`，说“你好小助手，给我一瓶可乐”。不需要同时启动
语音 HTTP 服务。收到的 `actions` 已处理，调用方不要再据此提交订单。

当前每轮只提交一件商品：矿泉水 `water`、可乐 `cola`、乌龙茶 `oolong_tea`。
模型通过 `submit_order` 动作提供 `parameters.item_id`；服务端限制该动作每批只能有一个。
商品不明确时追问；多件请求由场景规则要求用户明确本次先下一件什么商品。
暂不接入取消、停止、订单查询或其他商品，不使用页面中其他商品到矿泉水的演示映射。

成功提交返回 `status=success`、`action_results[0].status=accepted`，
回执 `details` 包含 `order_id`、`item_id`、`order_status`。
“订单已受理”只表示提交成功，执行进度仍由现有商品页面展示。
后端明确返回 4xx 时，动作结果为 `failed`；超时、连接异常、5xx 或无效回执时为
`unknown`，公共 `status=failed`，回复要求核对队列，**不会自动重试**。
后端没有幂等接口，用户再次发起下单仍可能重复，结果不确定时需先人工核对。
TTS 若已开启，会朗读此处理结果；播报失败不会重新提交订单。

下文家庭助手与机器人演示仍使用模拟执行器。

一个可自定义场景的 Python 对话 Agent：**语音 → 文字 → MiniMax → JSON → 模拟动作执行**。

所有 Agent 接口均返回结构化 JSON，包含 `reply`、`intent`、`actions`、`status`。默认使用 Mock 模型和 Mock Executor，无需 API Key 即可测试文本链路。真实语音识别是可选依赖，MiniMax Key 由使用者在本地配置。

## 环境安装与完整测试

完整语音环境：`python -m pip install -r requirements.txt`。
只验证文本/API：`python -m pip install -r requirements-core.txt`。

安装后运行 `python scripts/check_environment.py`，测试语音前用 `--voice` 检查语音依赖。详细安装命令、Mock/直接执行与拒绝/STT/唤醒/静音/MiniMax/日志测试用例及通过标准见 [完整测试指南](docs/TESTING.md)。

requirements 复用 pyproject.toml 的依赖声明并安装本项目，需在项目根目录运行。

## 已实现

- 持久化 JSONL 日志：请求、动作计划、执行结果和语音事件，自动轮转。

- 模块化音频监听：麦克风、VAD、断句器、唤醒器、ASR、对话适配器各自独立。
- 自动语音唤醒、静音自动结束录音、唤醒超时、录音长度限制及丢帧恢复。
- 默认中文唤醒词“你好小助手”，可替换为其他短语；另有可选 Porcupine 适配器。

- FastAPI：文本 `/chat`、音频上传 `/audio`、文本 WebSocket `/ws`。
- MiniMax Chat Completions 适配器，模型、地址、超时均可配置。
- 模块化 MiniMax TTS、独立 `/speech` WAV 接口和自动监听回复播放。
- Pydantic 严格 JSON 校验；模型输出不合规时重试修正一次，仍失败则不执行动作。
- YAML 场景、参数类型校验、动作允许列表、按顺序模拟执行。
- 允许且参数明确的动作直接执行；缺少信息时澄清，无法执行或不合理的请求返回拒绝原因。
- 会话隔离、有限历史、会话过期、同一会话请求串行处理。
- 可选 faster-whisper 本地 ASR、科大讯飞语音听写 WebAPI 与麦克风录音示例。
- 中文说明、测试和 GitHub Actions 配置。

当前采用**先生成完整 JSON 计划，再执行已验证动作**。讯飞持续监听已支持边录边传（`XFYUN_ASR_STREAMING=true`，默认开启）；上传录音和 Whisper 保持完整音频识别。MiniMax 本地 TTS 支持边收边播（`TTS_STREAMING=true`，默认开启）。尚未实现根据每一步观察结果继续规划的多轮 Tool Calling Loop、ROS2 或 MQTT。设备执行可通过上文的 TaskRunner 下单接口接入。执行失败会停止后续动作，不会自动重新规划。

## 1. 快速启动（Python 3.11 / 3.12）

在解压后的 `voice-agent` 目录中运行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-core.txt
cp .env.example .env
uvicorn voice_agent.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell 中把激活命令替换为 `.venv\Scripts\Activate.ps1`，复制命令替换为 `Copy-Item .env.example .env`。

打开 <http://127.0.0.1:8000/docs> 使用接口文档，或在另一个终端运行：

```bash
curl http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"打开客厅的灯"}'
```

返回示意（ID 每次不同）：

```json
{
  "reply": "模拟动作已完成，未控制真实设备。",
  "intent": "control_light",
  "actions": [
    {
      "action_id": "77645932ea56491f9376b916c3aabb22",
      "type": "set_light",
      "parameters": {"room": "living_room", "state": "on"}
    }
  ],
  "status": "success",
  "session_id": "2985e27c93b643549eb36b2b0995c72c",
  "action_results": [
    {
      "action_id": "77645932ea56491f9376b916c3aabb22",
      "type": "set_light",
      "status": "simulated",
      "details": {
        "executor": "mock",
        "parameters": {"room": "living_room", "state": "on"}
      }
    }
  ],
  "transcript": null
}
```

`success` 表示本次服务处理成功；`action_results[].status=simulated` 明确表示只做模拟。没有真实设备动作。

继续同一对话时，在 `/chat` 请求中传回 `session_id`。不传则创建新会话。Mock 只支持少量规则示例，没有通用自然语言理解能力。

## 2. 接入 MiniMax

修改本地 `.env`：

```dotenv
LLM_PROVIDER=minimax
MINIMAX_API_KEY=在本地填写你的APIKey
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
MINIMAX_THINKING=disabled
```

重启服务。按你账号可用的模型及服务区域调整配置，Key 与 API 地址应属于同一服务环境。`.gitignore` 已排除真实 `.env`，不要把 Key 写入代码或提交到 GitHub。

本项目直接发送 HTTP 请求，使用 JSON Schema 提示词和本地校验来约束模型输出，**没有依赖未核实的 `response_format=json_schema` 保证**。不能通过校验的输出会转换成错误 JSON，动作不会执行。`reasoning_split=true` 用于把推理内容与结果分开。

默认模型、地址及 thinking 设置依据 [MiniMax 官方兼容接口文档](https://platform.minimax.io/docs/api-reference/text-openai-api)。该接口不接收音频输入，语音由独立 ASR 模块处理。也可参考 [Chat Completions 参数说明](https://platform.minimax.io/docs/api-reference/text-chat-openai)。

## 3. 语音输入

本地 Whisper 与麦克风依赖：

```bash
python -m pip install -e '.[asr,microphone]'
```

修改 `.env` 并重启服务：

```dotenv
ASR_PROVIDER=whisper
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_LANGUAGE=zh
```

CPU 配置适合先在 Mac、Windows 或 Linux 上验证功能。首次调用可能下载模型，需要相应网络连接。`WHISPER_MODEL` 也可以指向已下载的本地模型目录。NVIDIA GPU 可配置 `cuda` 与 `float16`，其运行库要求见 [faster-whisper 官方说明](https://github.com/SYSTRAN/faster-whisper)。

上传一段本地录音：

```bash
curl http://127.0.0.1:8000/audio -F 'file=@voice.wav'
```

或者在运行服务的电脑上录制 5 秒麦克风声音：

```bash
python examples/microphone.py --seconds 5
```

麦克风示例需要系统允许终端访问麦克风。它将声音作为 WAV 发送给本机服务；识别出的文本进入 Agent，出现在响应的 `transcript` 字段中。默认限制上传文件 10 MiB、解码后的音频 60 秒。长音频在解码后检查时长，这不是针对任意不可信文件的资源沙箱。

也可以使用科大讯飞语音听写（流式版）WebAPI。完整安装已包含 WebSocket 客户端；在 `.env` 中配置同一讯飞应用的三项密钥：

```dotenv
ASR_PROVIDER=xfyun
XFYUN_APP_ID=你的APPID
XFYUN_API_KEY=你的APIKey
XFYUN_API_SECRET=你的APISecret
XFYUN_ASR_URL=wss://iat-api.xfyun.cn/v2/iat
```

讯飞模式把 16 kHz 单声道录音发送到云端，最长 60 秒，不是本地或离线识别。详细配置、隐私区别、格式限制、动态修正和测试步骤见 [讯飞 STT 说明](docs/xfyun-asr.md)。识别准确率需用你的实际麦克风、口音、环境和词汇测试，切换 Provider 本身不保证所有场景都更准确。

## 自动唤醒与静音断句（新增）

完整说明与模块接口见 [docs/voice-runtime.md](docs/voice-runtime.md)。自动监听在麦克风所在电脑上运行，直接调用同一套 Agent 服务，不需要先启动 HTTP 服务：

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，把 `ASR_PROVIDER` 改成 `whisper` 或 `xfyun` 并完成对应配置，然后运行：

```bash
voice-agent-listen
```

启动后等待本地模型加载完成、终端出现 `listening`，再说：

> 你好小助手，打开客厅的灯。

或者先说“你好小助手”，等 `awake` 提示后，再说“打开客厅的灯”。约 0.8 秒未检测到人声后自动断句、处理并输出 JSON；完成后回到等待唤醒。无需按录音按钮或设定固定录音时长。

默认参数：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `WAKE_PROVIDER` | `asr` | 当前 ASR 识别句子后匹配唤醒词 |
| `WAKE_PHRASE` | `你好小助手` | 可以自定义的中文唤醒词 |
| `AUDIO_END_SILENCE_MS` | `800` | 连续无语音多久自动结束 |
| `WAKE_TIMEOUT_SECONDS` | `8` | 唤醒后未开始说话的等待时间 |
| `AUDIO_PRE_ROLL_MS` | `300` | 保留句首，减少开头被截断 |
| `AUDIO_MIN_SPEECH_MS` | `240` | 丢弃太短的语音段 |
| `AUDIO_MAX_UTTERANCE_MS` | `15000` | 录音过长则丢弃，避免执行截断指令 |
| `VAD_AGGRESSIVENESS` | `2` | 0–3，越大越倾向过滤非语音 |

默认 ASR 唤醒会在一次静音断句并识别后才判断是否唤醒，因此有 ASR 延迟与待机开销；它不是专用低功耗热词模型。Whisper 在本地处理，讯飞模式会把待机语句也发送到讯飞云端。没有唤醒词的识别结果会被丢弃，不写日志，也不会发给 MiniMax。

需要专用唤醒引擎时，改用 `WAKE_PROVIDER=porcupine` 并提供匹配平台的 `.ppn`、语言 `.pv` 和 AccessKey。该模式按帧检测唤醒词，唤醒前不运行 ASR；需要先说唤醒词，等 `awake` 后再说指令。详见 [配置说明](docs/voice-runtime.md#专用唤醒引擎)。

当前为单轮唤醒、半双工：讯飞流式模式在录音期间同时识别；断句后等待最终识别结果及 Agent/TTS 处理期间暂停采集并清除积压音频，完成后再次说唤醒词开启下一轮。暂不支持处理期间打断、连续免唤醒多轮或回声消除。默认输出是 JSON 文本，不自动播放语音。

### MiniMax TTS 与自动播放

设置 `TTS_PROVIDER=minimax` 后，持续监听 CLI 默认把每次 Agent 返回的 `reply` 合成为流式 PCM，边接收边通过扬声器播放；`TTS_STREAMING=false` 可恢复完整 WAV 合成后播放。合成和播放期间麦克风保持暂停。HTTP 客户端可以把 `/chat` 或 `/audio` 返回的 `reply` 发送给 `/speech`，得到完整 `audio/wav`。模型、音色、语速、输出设备、独立测试方法和失败行为见 [TTS 说明](docs/tts.md)。默认关闭 TTS，不影响原有文本、STT 或动作流程。

## 4. 自定义场景与动作

修改 `voice_agent/scenes/home_assistant.yaml`，或者复制为新文件并设置 `SCENE=文件名`（不含 `.yaml`）。场景修改后重启服务。

### 用 TXT 编辑提示词

通用系统提示词保存在 **`voice_agent/prompts/system.txt`**，可以用 VS Code 或文本编辑器直接修改，用 UTF-8 编码保存。这里适合详细描述所有场景共同遵守的规则、交流要求和处理步骤；场景特有的角色、环境和规则仍放在对应 YAML 中。

`voice_agent/scene.py` 中的 `build_prompt()` 每轮请求读取这个 TXT，然后自动附加 `SCENE`、`ACTION_PARAMETER_SCHEMAS` 和 `DECISION_SCHEMA`。TXT 内的花括号按原文保留，不做模板替换；无需手动复制动作定义或完整 JSON Schema。提示词不会替代服务端的 JSON、参数和动作允许列表校验。

使用本项目的 `requirements.txt` 可编辑安装时，保存 TXT 后下一轮请求即读取新内容，不需要重启；正在处理的请求不受影响。测试新提示词时建议使用新会话，避免旧对话历史影响结果。修改 YAML 或 `.env` 仍需重启。TXT 不能为空、不能删除；文件读取失败会报错，不会静默回退到旧提示词。

要验证 MiniMax 对新规则的响应，设置 `LLM_PROVIDER=minimax` 并配置 API Key；Mock 模型不会按提示词生成真实回复。

多轮对话的历史分为模型决策与服务端结果：`assistant` 历史只包含符合 `AgentDecision` 的 JSON；实际处理状态和动作回执作为单独的 `SERVER_OBSERVATION` 上下文提供，避免模型模仿公共接口的额外字段或 `success` 等状态。每个会话保留最近 10 轮完整记录。JSON/动作校验失败时，仅重试一次，并提供被拒绝的候选输出及具体校验错误；两次失败都不会执行动作。复测方法见 [多轮澄清与 JSON 排查](docs/TESTING.md#多轮澄清与-json-排查)。

| 文件 | 用途 |
| --- | --- |
| `voice_agent/prompts/system.txt` | 可直接编辑的通用系统提示词 |
| `voice_agent/scenes/*.yaml` | 角色、规则和允许动作 |
| `voice_agent/actions/` | 动作名称与参数模型、Mock Executor |
| `voice_agent/scene.py` | 场景加载、提示词构造 |
| `voice_agent/llm/` | MiniMax / Mock 模型适配器 |
| `voice_agent/asr/` | ASR 接口、本地 Whisper 与科大讯飞 WebAPI 实现 |
| `voice_agent/tts/` | MiniMax TTS、合成结果、流式 PCM / WAV 播放和输出组合 |
| `voice_agent/schemas.py` | 输入、决策、输出 JSON 协议 |
| `voice_agent/agent/orchestrator.py` | 校验、拒绝、计划及执行编排 |
| `voice_agent/bootstrap.py` | 组装 LLM、ASR、场景和执行器 |
| `voice_agent/audio/` | 麦克风、VAD、PCM 编码和断句器 |
| `voice_agent/wake/` | 中文关键词匹配与可选专用引擎 |
| `voice_agent/runtime/` | 持续监听状态机、对话适配和 CLI |
| `voice_agent/observability/` | JSONL 日志、轮转和敏感字段处理 |
| `voice_agent/memory.py` | 内存会话与历史 |
| `voice_agent/api/app.py` | HTTP / WebSocket 接口（main.py 保留兼容入口） |
| `examples/microphone.py` | 本地麦克风录音并上传 |
| `tests/` | 核心逻辑、API 与 MiniMax 适配器测试 |
| `schemas/agent-response.schema.json` | 可供前端或其他系统使用的输出 Schema |
| `schemas/agent-decision.schema.json` | MiniMax 应输出的决策 Schema |
| `scripts/export_schemas.py` | 从 Python 定义重新导出两份 Schema |

内置动作：

| 动作 | 参数 | 默认可用场景 |
| --- | --- | --- |
| `set_light` | `room`: living_room / bedroom / kitchen；`state`: on / off | home_assistant |
| `move_to` | `location`: living_room / bedroom / kitchen / user / workspace | robot_demo |
| `pick_object` | `object`: water_bottle / cup | robot_demo |
| `look_at` | `target`: 非空文本 | robot_demo |
| `stop` | `{}` | 两种场景 |

要增加新动作，需要在 `actions/registry.py` 增加参数模型并注册，随后加入场景的 `available_actions`。模型无法自行增加动作或执行任意代码。真实设备适配器尚未包含；新增设备执行器时还需要定义实际成功/失败回执、超时、幂等、急停及用户权限机制，并调整输出协议中的模拟状态。

## 5. 直接执行、澄清和拒绝

- 参数明确、属于当前场景 `available_actions` 且通过校验的动作：模型输出 `ready`，服务端立即按顺序执行，完成后返回 `success` 或 `failed`。
- 缺少物品、房间等必要信息：返回 `waiting_for_user`，`actions=[]`；用户补充后沿用同一会话继续处理。
- 能力范围之外或不合理的请求：模型返回 `rejected`、`actions=[]`，`reply` 解释原因。服务端不会执行拒绝响应。

例如，`SCENE=robot_demo` 下发送“去厨房”会直接返回模拟执行回执；`SCENE=home_assistant` 下发送同一句话，Mock 会说明当前场景不支持该动作并返回 `rejected`。场景规则的语义由模型理解；服务端强制检查动作允许列表、参数和 JSON 协议。模型若仍生成非法动作，校验与一次修复都失败后返回 `error`，说明失败原因。

`actions` 是已处理的动作计划记录，`action_results` 是服务端回执。客户端只展示结果，不要再根据 `actions` 重复执行。重复提交 `/chat` 会被视为一条新的请求；当前没有请求级幂等键。

### 从 v0.3 升级

这是一次接口调整，更新后重启并开始新会话：

1. 更新 Python 代码、TXT 提示词和导出的 Schema；合并自定义业务规则时删除原有的等待批准步骤。
2. 从所有自定义场景 YAML 删除 `confirmation_actions`（包括空列表），否则严格的场景校验会提示多余字段。
3. 删除 `.env` 中不再使用的 `CONFIRMATION_TTL_SECONDS`；保留本地 API Key 和其他配置。
4. 客户端删除 `need_confirmation`、`confirmation_id`、`awaiting_confirmation` 分支和 `/confirm` 调用；新增处理 `rejected`。
5. 如修改过 `schemas.py`，合并新协议后运行 `python scripts/export_schemas.py`，同步决策与最终响应的 JSON Schema。

## 6. 接口协议

| 接口 | 输入 / 输出 |
| --- | --- |
| `GET /health` | 配置与进程存活状态，不验证外部 MiniMax 连通性 |
| `GET /schema/response` | AgentResponse JSON Schema |
| `POST /chat` | `{"text":"...","session_id":null}` → AgentResponse |
| `POST /audio` | multipart `file` 和可选 `session_id` → AgentResponse |
| `POST /speech` | `{"text":"Agent回复"}` → `audio/wav` |
| `WS /ws` | 文本 JSON ChatRequest → 完整 JSON AgentResponse |

WebSocket 示例消息：`{"text":"打开客厅的灯"}`。同一连接会记住最近的会话 ID。这个接口一次返回完整结果，不传二进制音频，不做 token 流式输出。

| 返回状态 | 含义 |
| --- | --- |
| `success` | 对话完成，或全部模拟动作完成 |
| `waiting_for_user` | 需要补充信息，不执行动作 |
| `rejected` | 请求无法执行或不合理，reply 说明原因，actions 为空 |
| `failed` | 动作执行失败，剩余动作跳过 |
| `error` | 模型、协议、会话或请求发生错误 |

`status=ready` 只出现在内部模型决策中，公共接口返回处理后的状态。业务错误也可能使用 HTTP 200，调用方需检查 JSON 的 `status`；HTTP 请求格式错误使用 422，音频为空或过大使用 413。

## 持久日志

程序默认把请求、动作计划和执行结果写入 `logs/agent.jsonl`，每行是一个独立 JSON 对象。文件达到约 10 MiB 后自动轮转，保留 5 个备份。事件与字段说明、敏感字段处理和筛选命令见 [JSONL 日志说明](docs/logging.md)。

重启不会清除已有日志，但日志不会自动恢复 Agent 的对话状态。若旧版本已创建 `.env`，无需重新复制文件，直接添加或修改 `LOG_FILE=logs/agent.jsonl` 等配置即可；未填写时采用上述默认值。

## 7. 测试与当前验证情况

```bash
python -m pip install -r requirements-core.txt
pytest -q
ruff check .
```

核心测试也可以单独运行，只需要 Pydantic 和 PyYAML：

```bash
python -m unittest tests.test_core tests.test_voice tests.test_audit tests.test_multiturn tests.test_contract -v
```

本次交付已通过 **92 项离线测试**，包含 Agent、音频状态机、API、MiniMax、TTS，以及讯飞鉴权签名、PCM WAV 校验、分帧、动态修正合并、错误码和密钥脱敏。语音测试使用可控 PCM 帧及模拟服务，不代表真实麦克风、账号、网络或扬声器效果。

没有使用你的 MiniMax/讯飞密钥或麦克风做端到端验证，也未实测 WebRTC VAD、Whisper、讯飞识别准确率和 Porcupine。GitHub Actions 已配置为运行不需要真实密钥的检查。

## 8. 放入 GitHub

在 [GitHub 新建仓库](https://github.com/new)，建议名称 `voice-agent`。如果你要使用现有 `xca161/voice-agent`，请确认自己可以打开它且已授权相关 GitHub 连接访问。

新建时选择你希望的可见性，并保持空仓库（不自动生成 README 或其他文件）。然后在本项目目录运行：

```bash
git init -b main
git add .
git commit -m "Initialize MiniMax voice agent with JSON contract"
git remote add origin https://github.com/xca161/voice-agent.git
git push -u origin main
```

命令使用已核实的账号名 `xca161`；如果仓库属于其他账号或组织，请替换远程地址。GitHub 身份认证使用你本机已登录的 Git 客户端，不把访问令牌粘贴到代码或文档中。

当前服务面向单人本地开发：没有登录认证，内存状态不跨进程、不跨重启共享，因此保持单 worker 并绑定 `127.0.0.1`。以后要部署供多用户使用时，需要增加认证、共享存储及请求限额。
