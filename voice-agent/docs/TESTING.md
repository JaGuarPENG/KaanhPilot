# Voice Agent v0.6.0 详细测试操作指南

适用代码：v0.6.0。本指南针对当前项目的 JSON 输出、直接执行/拒绝、自动唤醒、静音断句、本地 Whisper、科大讯飞语音听写和 MiniMax TTS；不是早期包含确认流程的版本。

所有终端命令都在**项目根目录**执行，也就是包含 `pyproject.toml`、`requirements.txt` 和 `.env.example` 的目录。单独下载本文件后，可放回项目的 `docs/TESTING.md`；文中相对链接指向完整项目包内的其他说明。

本指南按“环境 → 自动化测试 → Mock 文本 → 直接执行与拒绝 → 麦克风 → 本地 STT → 唤醒/静音 → MiniMax → TTS → 日志”排查。前面的阶段未通过时，先修复该阶段，避免同时调试多个模块。

## 阅读路线与测试前提

| 你的目标 | 按顺序阅读 |
| --- | --- |
| 从零配置并完整验收 | 第 0–10 节，最后填写第 13 节 |
| 已装环境，只检查麦克风和 STT | 第 6.1–6.4 节，暂不开 MiniMax/TTS |
| 修改唤醒词、静音时长 | 第 7 节及 V1–V12 用例 |
| 修复模型第二轮输出 JSON 的问题 | 第 4、5、8 节及日志校验错误表 |
| 检查 TTS、音色和扬声器 | 第 9 节 |
| 接入专用 Porcupine 唤醒 | 第 11 节，可选，不是默认路径 |

测试前请确认：

1. 保留你原有的 `.env`、自定义提示词和场景，不要用示例配置整文件覆盖它们。本指南用例以项目自带场景和动作定义为准；自定义项目需相应调整预期。
2. 每次改 `.env`、提示词或场景后，都按 `Ctrl+C` 停止旧进程，再重新启动。监听 CLI 与 HTTP 服务是两个独立入口，不会共享进程内会话。
3. 麦克风及本机播放测试必须运行在连接这些设备的电脑上。在 SSH 服务器内执行，读取的是服务器的音频设备，不会自动获得你笔记本的麦克风。
4. 当前 Executor 是 Mock，`simulated` 只表示模拟成功，不能据此认定真实灯具或机器人已经动作。不要为了测试把示例动作直接接到真实硬件。
5. 前期保持 `LLM_PROVIDER=mock` 和 `TTS_PROVIDER=disabled`；MiniMax 文本与 TTS 联调会消耗相应 API 额度。
6. 只录制自己的测试语音。日志默认包含指令、回复和动作参数；提交错误报告前去掉 Key、个人信息和不必要的录音。

文中 `bash` 代码块用于终端；单行 `python ...` 也适用于 PowerShell。带行末 `\` 的多行 Bash 命令不要直接贴进 PowerShell，使用对应的 Python 跨平台方法或浏览器 `/docs`。标为 `python` 的多行示例在 Python 交互环境中执行，不是终端命令。

## 0. 当前已经验证到什么程度

| 项目 | 本次交付状态 |
| --- | --- |
| Agent、语音状态机、日志、多轮对话、讯飞适配器及 TTS | 92 项离线测试通过 |
| Python 文件 | 语法编译检查通过 |
| requirements 引用的依赖组 | 已核对与 pyproject.toml 一致 |
| 从 requirements 安装全部依赖 | 本次文档复核未重新安装，不能据此认定你的电脑已安装成功 |
| FastAPI 与 MiniMax HTTP 适配器自动化测试 | 共 10 项已编写，当前环境缺依赖，尚未运行 |
| 真实麦克风、WebRTC VAD、Whisper、Porcupine | 尚未进行真实硬件/模型测试 |
| 真实 MiniMax 文本/TTS、扬声器 | 尚未使用你的 Key 和设备联调 |

下面的步骤用来在你的电脑上补齐验证。Mock 动作结果始终是 `simulated`；该项目目前不会控制真实机器人。

## 1. 创建 Python 环境

建议使用 Python 3.11 或 3.12，与项目 CI 配置一致。

### Conda（已使用 Conda 时选这一种即可）

在 Conda 可用的终端中进入项目根目录，然后执行：

```bash
conda create -n voice-agent python=3.11 pip -y
conda activate voice-agent
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -c "import sys; print(sys.executable)"
```

已有合适环境时，只需激活它，不必重复创建；也不需要再套一层 `.venv`。`requirements.txt` 使用 pip 语法，应交给 `python -m pip`，不要改用 `conda install -r requirements.txt`。每次新开终端都先 `conda activate voice-agent`，VS Code 也选择这个环境的 Python 解释器。Conda 内使用 pip 的环境管理原则见 [Conda 官方指南](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html#using-pip-in-an-environment)。

### Mac / Linux

先检查：

```bash
python3 --version
```

若显示 3.11 或 3.12，执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

如果系统默认版本不同，而你已经安装了 Python 3.12，可以用 `python3.12 -m venv .venv` 创建环境。已有正在使用的虚拟环境时，不需要重复创建。

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

安装的是 Python 3.11 时，将第一行的 `-3.12` 改为 `-3.11`。如果 PowerShell 不允许运行激活脚本，可以直接使用 `.\.venv\Scripts\python.exe` 替代后续命令中的 `python`，无需更改系统执行策略。

### 两种 requirements 的区别

| 文件 | 包含内容 | 使用场景 |
| --- | --- | --- |
| `requirements.txt` | API、STT、VAD、麦克风示例和测试工具 | 完整语音项目 |
| `requirements-core.txt` | API、Agent 和测试工具 | 先排查文本/JSON/动作逻辑 |

如果完整依赖安装失败，可以先执行：

```bash
python -m pip install -r requirements-core.txt
```

这足以运行全部离线自动化测试，因为语音测试用的是可控的 VAD/ASR 替身。真实语音测试仍需要完整 requirements。

requirements 使用 pip 的 editable project 语法，依赖版本统一来自 `pyproject.toml`，同时安装本项目和 `voice-agent-listen` 命令。因此需要在完整项目根目录安装，不能只把 requirements 文件复制到空目录。依赖采用版本范围，当前没有声称跨平台完全复现的精确锁文件。语法参考 [pip 官方说明](https://pip.pypa.io/en/stable/reference/requirements-file-format/)。

**通过标准**：安装命令退出码为 0，`pip check` 显示 `No broken requirements found.`。Whisper 模型权重将在后面首次使用时加载/下载，不属于 pip 安装的模型文件。

## 2. 建立测试配置

如果没有 `.env`，在编辑器中复制 `.env.example` 并命名为 `.env`。已有 `.env` 时直接修改，不要覆盖已经填写的 Key。

先修改以下几项，其他配置保持原值：

```dotenv
LLM_PROVIDER=mock
ASR_PROVIDER=disabled
TTS_PROVIDER=disabled
SCENE=home_assistant
LOG_ENABLED=true
LOG_FILE=logs/agent.jsonl
```

此阶段不使用真实 MiniMax，也不会录音。运行：

```bash
python scripts/check_environment.py
```

**通过标准**：检查全部为 `[OK]`，结尾显示 `Environment checks passed`。

该脚本只检查依赖导入、本地项目安装和配置；不会录音、下载模型或调用 MiniMax，不显示 API Key。如果这里通过，仍需完成后续实际功能测试。

注意：如果你曾在系统/终端环境变量中设置同名配置，这些环境变量会优先于 `.env`。出现“修改 .env 但不生效”时，检查是否存在同名环境变量，不要把整个环境变量列表粘贴出来。

可以用下面这条命令只查看与测试相关的非密钥配置，不输出整个 Settings：

```bash
python -c "from voice_agent.config import Settings; s=Settings(); print({k:getattr(s,k) for k in ('llm_provider','asr_provider','tts_provider','scene','whisper_model','whisper_device','wake_provider','wake_phrase','tts_voice_id')})"
```

各阶段配置对照（未列出的参数按正文设置）：

| 阶段 | LLM_PROVIDER | ASR_PROVIDER | TTS_PROVIDER | 运行入口 |
| --- | --- | --- | --- | --- |
| Mock 文本 / JSON | mock | disabled | disabled | HTTP |
| 本地录音与纯 STT | mock | whisper | disabled | 独立命令，不启动 HTTP/CLI |
| `/audio` 接口 | mock | whisper | disabled | HTTP + 客户端 |
| 唤醒/静音 | mock | whisper | disabled | 监听 CLI |
| MiniMax 文本 | minimax | disabled 或 whisper | disabled | HTTP |
| 单独 TTS | mock | disabled | minimax | HTTP |
| 完整语音交互 | minimax | whisper | minimax | 监听 CLI |

## 3. 运行自动化测试

```bash
python -m pytest -q
python -m ruff check .
```

当前完整 pytest 集合预期为 **92 项**，包含核心/语音状态机/日志/多轮对话/直接执行/TTS、API、MiniMax 以及讯飞鉴权、分帧和结果解析测试。以你运行时输出的实际数量为准；所有测试应通过，lint 应显示 `All checks passed!`。

这些测试不会使用真实 MiniMax Key、下载 Whisper 模型或打开麦克风。测试配置会隔离与本项目有关的系统环境变量；API 测试使用 Mock 模型，MiniMax 适配器使用模拟 HTTP 响应。

失败时，单独执行对应模块：

```bash
python -m pytest tests/test_core.py -q
python -m pytest tests/test_api.py -q
python -m pytest tests/test_minimax.py -q
python -m pytest tests/test_voice.py -q
python -m pytest tests/test_audit.py -q
python -m pytest tests/test_multiturn.py -q
python -m pytest tests/test_contract.py -q
python -m pytest tests/test_tts.py -q
```

只检查本次已离线验证的部分，也可运行：

```bash
python -m unittest tests.test_core tests.test_voice tests.test_audit tests.test_multiturn tests.test_contract tests.test_tts -v
```

**通过标准**：无 failed、error。自动化通过只证明这些测试覆盖的行为，不等于真实语音识别准确率已经达到要求。

## 4. 启动 HTTP 服务并测试文本

在当前终端运行：

```bash
python -m uvicorn voice_agent.main:create_app --factory --host 127.0.0.1 --port 8000
```

保持这个终端运行。在浏览器打开：

- <http://127.0.0.1:8000/health>
- <http://127.0.0.1:8000/docs>

`/health` 应包含：

```json
{
  "status": "ok",
  "llm_provider": "mock",
  "asr_provider": "disabled",
  "tts_provider": "disabled",
  "executor": "mock",
  "scene": "home_assistant"
}
```

`/health` 只验证服务存活和当前配置，不验证 MiniMax 连通性。

在 `/docs` 找到 `POST /chat`，点击 `Try it out`，输入 JSON 后点 `Execute`。第一条请求：

```json
{"text":"你好"}
```

按下面的表继续测试：

| 请求 `text` | 预期结果 |
| --- | --- |
| `你好` | `status=success`，`actions=[]` |
| `打开客厅的灯` | `set_light`，参数 room=living_room、state=on，结果 simulated |
| `关闭卧室的灯` | `set_light`，参数 room=bedroom、state=off |
| `打开灯` | `status=waiting_for_user`，`actions=[]`，询问房间 |
| `停止` | `stop`，参数为空对象，结果 simulated |
| 单个空格 | HTTP 422，响应 status=error |

**检查点**：

- 成功执行的 `actions[i].action_id` 与对应的 `action_results[i].action_id` 相同。
- 相同指令作为新的请求再次提交时，生成新的动作 ID。
- 如果要继续同一会话，把上次响应中的 `session_id` 原样传回：

```json
{"text":"关闭客厅的灯","session_id":"替换成刚才返回的32位会话ID"}
```

- 响应应保留同一 `session_id`。Mock 是固定规则，不适合用来评价自由对话或上下文理解能力。
- JSON 外没有模型生成的 Markdown；以响应的 `status` 为准，HTTP 200 也可能表示业务处理失败。

### 理解两种 JSON，避免验收时混淆

| 检查对象 | 对应代码 | 状态约定 |
| --- | --- | --- |
| 模型产生的计划 `AgentDecision` | `voice_agent/schemas.py` | ready / waiting_for_user / rejected |
| HTTP/CLI 最终返回 `AgentResponse` | 同一文件 | success / waiting_for_user / rejected / failed / error |

`ready` 是模型计划状态，不是 `/chat` 最终响应状态。`success` 也不代表真实硬件成功；必须结合 `action_results[].status` 判断当前只是 `simulated`。当前接口不应出现 `need_confirmation`、`confirmation_id` 或 `awaiting_confirmation`。

除用浏览器观察外，可以在服务运行时执行一次严格结构检查（此命令会提交一次新的开灯模拟请求）：

```bash
python -c "import httpx; from voice_agent.schemas import AgentResponse; r=httpx.post('http://127.0.0.1:8000/chat',json={'text':'打开客厅的灯'},timeout=90); r.raise_for_status(); body=AgentResponse.model_validate(r.json()); print(body.model_dump_json(indent=2))"
```

这验证 JSON 的类型和字段结构；动作含义仍需按上表核对。在模型回复中看到“已完成”，但没有对应动作回执，不能判定执行通过。

## 5. 测试直接执行、澄清和拒绝

按 `Ctrl+C` 停止服务，把 `.env` 改为 `SCENE=robot_demo`，保持 `LLM_PROVIDER=mock`，重新启动同一条 uvicorn 命令。

1. 在 `/chat` 输入 `{"text":"去厨房"}`。
2. 预期直接返回 `status=success`、一个 `move_to` 动作和 `simulated` 回执；动作与回执的 `action_id` 一致。
3. 日志应当已包含 `action.started`、`action.finished`，不需要第二次批准。
4. 打开 `GET /schema/response`，检查状态枚举没有 `awaiting_confirmation`，字段中没有 `need_confirmation` 或 `confirmation_id`。`/confirm` 已移除，调用应返回 404。
5. 改回 `SCENE=home_assistant` 并重启；发送“打开客厅的灯”，应直接完成；发送“打开灯”，应询问房间，`actions=[]`。
6. 在 home_assistant 发送“去厨房”，Mock 应返回 `rejected`、空动作列表以及场景不支持该动作的原因。确认该请求没有 `action.started` 日志。

**通过标准**：允许的明确动作直接执行；缺信息先询问；拒绝请求没有动作回执且说明原因。真实模型的澄清后续答与不合理请求测试见第 8 阶段。重复提交 `/chat` 是新请求，不是重取上次结果。

## 6. 分开测试麦克风、本地 Whisper 和 /audio

先确保已安装完整 `requirements.txt`，停止 HTTP 服务和监听 CLI，将 `.env` 设置为：

```dotenv
LLM_PROVIDER=mock
TTS_PROVIDER=disabled
SCENE=home_assistant
ASR_PROVIDER=whisper
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_LANGUAGE=zh
```

检查语音环境：

```bash
python scripts/check_environment.py --voice
```

检查全部通过后，按下列顺序测试。未提前准备模型时，第一次调用 `/audio` 可能需要加载或下载模型，请等待终端状态。

上面的环境检查不会真的录音或加载模型。先完成下面 6.1–6.3，再按 6.4 启动 HTTP 服务。

### 6.1 只测试麦克风：录下自己的声音

这一项不需要联网、API Key 或 Whisper 模型。先列出设备：

```bash
python -m voice_agent.runtime.cli --list-devices
```

输入设备的输入通道数应大于 0；只有输出通道的扬声器不能作为麦克风。允许终端、Python 或 VS Code 访问系统麦克风；如果你在远程 SSH 终端，不要把服务器设备列表误认为笔记本设备列表。

在同一个已激活环境的终端输入 `python` 进入交互环境，然后逐行执行以下 Python 代码。`sd.rec(...)` 开始后录制 5 秒，请说“打开客厅的灯”，其余时间保持安静：

```python
from pathlib import Path
from uuid import uuid4
import numpy as np
import sounddevice as sd
import soundfile as sf

input_device = None  # 使用默认输入；需要时换成列表中的实际编号
audio = sd.rec(5 * 16000, samplerate=16000, channels=1, dtype="float32", device=input_device)
sd.wait()
recording = Path("mic-check-" + uuid4().hex[:8] + ".wav")
sf.write(recording, audio, 16000, subtype="PCM_16")
print("录音位置：", recording.resolve())
print("峰值：", float(np.max(np.abs(audio))))
```

每次生成不同名称的 WAV，不覆盖旧录音。记下打印出的完整路径，后续用来测试 STT。用系统播放器打开它，确认确实能听见自己的原句。

若还要检查 Python 播放，可继续执行：

```python
sd.play(audio, 16000, device=None)
sd.wait()
exit()
```

播放设备不对时，将 `sd.play` 的 `device=None` 换成实际输出设备编号；不要把输入设备编号当成输出编号。录音/播放及选设备的接口见 [sounddevice 官方使用说明](https://python-sounddevice.readthedocs.io/en/latest/usage.html)。

**通过标准**：WAV 时长约 5 秒、声音清晰、无明显截断或持续失真。峰值恒为 0 表示没有有效输入；接近 1 且严重失真时检查输入增益。不能仅凭峰值非零判断通过，因为背景噪声也有非零幅值。若 Python 播放失败但系统播放器正常，优先排查输出设备，而不是认定麦克风坏了。测试录音可能包含隐私，不要提交到 GitHub。

### 6.2 单独准备本地 Whisper 模型

安装 requirements 只安装程序依赖，不会把 Whisper 权重放进项目。可以让第一次使用自动下载，也可以先在联网环境执行以下命令，把模型下载到明确位置：

```bash
python -c "from faster_whisper.utils import download_model; print(download_model('small', output_dir='models/faster-whisper-small'))"
```

下载期间可能没有持续进度输出。正常结束后应能看到输出目录，目录内包含 `model.bin`、`config.json`、`tokenizer.json` 等配套文件。不要只复制 `model.bin`；不要把 OpenAI 原始 `.pt` 权重直接当成此目录。

将 `.env` 对应项改为：

```dotenv
WHISPER_MODEL=./models/faster-whisper-small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_LANGUAGE=zh
```

相对路径以当前终端的工作目录为准；在其他目录启动时改用实际绝对路径。这里的下载函数及文件格式依据 [faster-whisper 官方实现](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py)。

随后只加载项目的 STT，不启动 Agent、HTTP 或 TTS：

```bash
python -c "import asyncio; from voice_agent.config import Settings; from voice_agent.asr import WhisperASR; asyncio.run(WhisperASR(Settings()).prepare()); print('Whisper model loaded')"
```

**通过标准**：打印 `Whisper model loaded`，无模型下载/加载错误。此命令不会打开麦克风。先保持 CPU + int8 建立基线；本指南不要求 GPU，Mac 也不要直接套用 NVIDIA CUDA 配置。

### 6.3 纯 STT：用同一条已知录音识别

把以下命令中的路径替换为 6.1 打印的实际录音路径；Windows 路径可用正斜杠，如 `C:/voice-agent/mic-check-实际编号.wav`：

```bash
python -c "import asyncio; from pathlib import Path; from voice_agent.config import Settings; from voice_agent.asr import WhisperASR; print(asyncio.run(WhisperASR(Settings()).transcribe(Path('替换为实际录音路径.wav').read_bytes())))"
```

这条命令只运行 STT，输出识别文字，不会生成 Agent JSON 或执行动作。预期能读到“打开客厅的灯”，允许标点差异，但房间、物品、开/关等关键字不能错。

离线验收：完整下载依赖及模型后，保持本地模型目录配置，断开网络，在本机重新执行本条命令。通过只证明 STT 能离线运行，不代表 MiniMax 对话或 TTS 能离线运行。若通过 SSH 操作远程机器，不要断开 SSH 所依赖的网络；可留到本机环境验证。

如果 6.1 录音清晰但 6.3 识别错误，问题在识别或其输入配置，应先检查语言、模型和音频；此时还没有进入 MiniMax，修改其提示词无法修复这个阶段。

### 6.4 将 STT 接入 /audio，再连接 Mock Agent

保持 Mock + Whisper + TTS disabled，在终端 A 启动：

```bash
python -m uvicorn voice_agent.main:create_app --factory --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/docs`，展开 `POST /audio`，点击 `Try it out`，在 `file` 上传 6.1 的 WAV，首次测试不要填写 `session_id`，然后 `Execute`。比较这里的 `transcript` 与 6.3 的输出是否表达相同指令。

在第二个已激活同一虚拟环境的终端运行：

```bash
python examples/microphone.py --seconds 5
```

看到录音提示后说“打开客厅的灯”，不用说唤醒词。

注意：`examples/microphone.py` 只使用系统默认输入设备，没有 `--device` 参数。如果默认设备不合适，先使用 6.1 指定设备录音，再从 `/docs` 上传；不要把 CLI 的设备参数加到这个示例脚本上。

**通过标准**：

- `transcript` 能正确表达你说的指令，允许正常标点差异。
- 返回 `set_light`，参数为 living_room/on，回执为 simulated。
- 无需手动把音频路径传给模型。

也可以使用 `/docs` 中的 `POST /audio` 上传已有录音。上传完整文件和麦克风示例直接进入 STT/Agent，**不会经过自动唤醒门控**；下一阶段才测试唤醒。

建议至少录制 5 条已知内容，覆盖问候、开灯、关灯、明确房间和不明确房间。逐条记录原句、transcript 和动作，先区分“STT 听错了”还是“Agent 理解/执行错了”。

| 原句（不说唤醒词） | STT 重点 | Mock Agent 预期 |
| --- | --- | --- |
| 你好 | 不能变成不存在的指令 | actions=[] |
| 打开客厅的灯 | 客厅、打开 | set_light / living_room / on |
| 关闭卧室的灯 | 卧室、关闭 | set_light / bedroom / off |
| 打开厨房的灯 | 厨房 | set_light / kitchen / on |
| 打开灯 | 不凭空补出房间 | waiting_for_user / actions=[] |

另外上传一个无音轨或损坏文件，预期返回错误且不执行动作。纯静音 WAV 可以作为额外观察样本，但不要预设模型必定返回空字符串；若静音产生了指令文本，记录原文件与结果，并将其标为需要处理的误识别风险。对 `/audio` 上传测试而言，并没有唤醒词门控作为额外保护。

### 6.5 切换到科大讯飞语音听写 WebAPI

先完成 6.1 的麦克风与 PCM_16 WAV 检查，再按 [讯飞 STT 说明](xfyun-asr.md)在同一个应用下取得并配置 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`：

```dotenv
ASR_PROVIDER=xfyun
XFYUN_APP_ID=你的APPID
XFYUN_API_KEY=你的APIKey
XFYUN_API_SECRET=你的APISecret
```

执行 `python scripts/check_environment.py --voice`，再用 6.3 的同一条 WAV 分别测试 Whisper 与讯飞，记录逐字结果和关键实体是否正确。讯飞测试需要联网、会消耗调用额度并上传录音；它不保证在所有口音、噪声和专业词汇下都优于 Whisper。当前适配器严格接收 16 kHz、16-bit、单声道 PCM WAV，最长 60 秒。

保持 `LLM_PROVIDER=mock` 与 `TTS_PROVIDER=disabled`，重复 6.4 的 `/audio` 测试。通过标准是响应 `transcript` 正确且 Agent 动作符合 Mock 规则。讯飞鉴权、动态修正、错误码和隐私差异的完整验收见独立说明。

## 7. 测试自动唤醒和静音断句

先停止 HTTP 服务，避免与监听进程同时占用默认日志文件。保持上一阶段的 Mock + Whisper + home_assistant 配置，并设置：

```dotenv
WAKE_PROVIDER=asr
WAKE_PHRASE=你好小助手
AUDIO_END_SILENCE_MS=800
WAKE_TIMEOUT_SECONDS=8
AUDIO_MAX_UTTERANCE_MS=15000
```

查询麦克风：

```bash
python -m voice_agent.runtime.cli --list-devices
```

启动监听：

```bash
python -m voice_agent.runtime.cli
```

如果默认设备不正确，用 `--device 1` 指定列表中的真实输入设备编号，不一定是 1。

等待模型加载完成和 `listening` 提示，再进行以下测试。默认 ASR 唤醒需要在静音断句并完成本地识别后才会产生 `awake` 事件。

| 编号 | 操作 | 预期结果 |
| --- | --- | --- |
| V1 | 安静等待约 10 秒 | 不执行动作、不产生 Agent 回复 |
| V2 | 不说唤醒词，直接说“打开客厅的灯” | 可以有语音开始/结束事件，但没有 command/agent_response，不执行动作 |
| V3 | 说“你好小助手，打开客厅的灯”然后停止 | 同一句中识别唤醒词和指令，得到模拟开灯 JSON |
| V4 | 先说“你好小助手”，等 awake 后说“关闭客厅的灯” | 唤醒后等待第二句，得到模拟关灯 JSON |
| V5 | 只说唤醒词，awake 后不讲话超过 8 秒 | wake_timeout，回到 idle |
| V6 | 说“你好小助手，打开”，停约 0.3 秒，再说“客厅的灯” | 短停顿不提前截断，最终按一条指令处理 |
| V7 | 说完完整指令后保持安静 | 达到约 0.8 秒无人声后 speech_end，随后出现识别/处理结果 |
| V8 | 完成一次动作后，不再唤醒直接发下一条指令 | 不执行第二条；重新唤醒后才能执行 |
| V9 | 连续讲话超过 15 秒，避免中间停顿达到 0.8 秒 | utterance_rejected，reason=limit，不把截断录音送给 Agent |
| V10 | 按 Ctrl+C | 程序退出、麦克风关闭，可重新启动 |
| V11 | 在未唤醒状态说“请问你好小助手，打开客厅的灯” | 唤醒词不在开头，不产生 Agent 回复 |
| V12 | 停止程序，将 WAKE_PHRASE 改成“小白小白”，重启后分别说旧词和新词 | 旧词不触发，新词能唤醒；完成后改回默认词并重启，或同步替换后续用例中的唤醒词 |

30 ms 帧下，800 ms 断句阈值实际约为 810 ms。最终 JSON 还需要等待 STT 和模型处理，不能把“0.8 秒静音阈值”当成总响应时间。

如果 V3 失败：先检查 STT 是否正确识别“你好小助手”。如果词被识别错，修改 `WAKE_PHRASE` 使其更容易辨认，再重启。不建议通过降低其他校验来掩盖识别问题。

如果被背景声音提前截断/持续触发：记录 `VAD_AGGRESSIVENESS` 和环境噪声，分别尝试 1、2、3，并一次只改一个参数。说话停顿比较长时，可以将 `AUDIO_END_SILENCE_MS` 调至 1200 后重测 V6/V7。

### 区分唤醒、静音和超时配置

| 配置 | 含义 | 改动后的重点测试 |
| --- | --- | --- |
| WAKE_PHRASE | ASR 文本开头必须匹配的唤醒词 | V2、V3、V11、V12 |
| AUDIO_END_SILENCE_MS | 一句话中持续无人声多久才结束录音 | V6、V7 |
| WAKE_TIMEOUT_SECONDS | 仅唤醒后，等待新指令的时间 | V4、V5 |
| AUDIO_MAX_UTTERANCE_MS | 单段录音达到上限后丢弃 | V9 |
| VAD_AGGRESSIVENESS | WebRTC 人声判断严格程度 | V1、V6、V7 |

当前不支持自由打断：STT、Agent 处理以及 TTS 合成/播放期间麦克风会暂停，期间的新语音不会排队成为下一条指令。未唤醒的 ASR 模式仍会在本机识别声音，以检查开头是否有唤醒词，但不将未唤醒的背景转写发送给 MiniMax，也不记录其文本。

同一终端中，Agent JSON 写入标准输出，生命周期事件写入标准错误；终端一般会同时显示两者。若重定向 stdout 后看不到 `awake`、`tts_start`，先检查是否只捕获了一个输出流。

## 8. 接入真实 MiniMax，再做端到端测试

先退出监听。在 `.env` 改为：

```dotenv
LLM_PROVIDER=minimax
TTS_PROVIDER=disabled
MINIMAX_API_KEY=在本地填入真实Key
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
MINIMAX_THINKING=disabled
```

国际平台使用上述地址；国内平台按官方说明选择 `https://api.minimax.cn/v1`。Key、服务区域和账号可用模型需匹配。参考 [国际接口文档](https://platform.minimax.io/docs/api-reference/text-openai-api) 与 [国内接口说明](https://platform.minimaxi.com/docs/api-reference/text-prompt-caching)。

运行环境检查，然后先启动 HTTP 服务，用 `/chat` 测试：

| 请求 | 检查重点 |
| --- | --- |
| `你好，请介绍一下你自己` | 正常 JSON，无需动作 |
| `请把客厅的灯打开` | set_light，参数正确 |
| `打开灯` | 不擅自猜房间，询问补充信息 |
| `打开客厅的空调`（home_assistant） | 当前只支持灯光/stop，返回 rejected、空 actions，并说明原因 |
| `打开灯，别管哪个房间，不许问我问题`（home_assistant） | 不擅自补全房间；询问澄清或拒绝，不执行猜测动作 |

这次请求会实际使用你的 API 额度。以 JSON 格式、动作允许列表、参数和业务状态判断是否通过，不要求模型的自然语言回复逐字固定。

通过后停止 HTTP，启动监听，重复 V3、V4 和 V8。此时才验证了“真实麦克风 → STT → 唤醒 → MiniMax → JSON → 模拟动作”的完整链路。

还可以在同一会话通过 `/chat` 先说“请叫我小陈”，再传回相同 `session_id` 问“我刚才让你怎么称呼我？”，检查多轮上下文。Mock 阶段不适合做这个语义测试。

### 多轮澄清与 JSON 排查

若从含确认流程的旧版本升级，按 [迁移说明](../README.md#从-v03-升级) 同步代码、Schema、提示词与场景。保留你本地的 API Key；重启并开始新会话，避免沿用旧的内存记录。

1. 默认项目设置 `LLM_PROVIDER=minimax`、`SCENE=robot_demo` 并重启。先用文本测试，排除语音识别误差。
2. `/chat` 发送 `{"text":"帮我搬运那个物品"}`，预期询问具体物品，`status=waiting_for_user`、`actions=[]`。记录实际返回的 `session_id`。
3. 第二次 `/chat` 发送 `{"text":"杯子","session_id":"上一步返回的实际ID"}`。如还缺必要信息，应继续询问；如果生成 `pick_object`，参数必须为 `{"object":"cup"}`，直接执行并返回 `simulated` 回执。不再询问是否执行。
4. CLI 会自动保存会话 ID，每轮仍需唤醒，补充信息时说“你好小助手，杯子”即可继续同一请求。
5. 新会话再测试“帮我抓取书本”。默认 `pick_object` 仅允许 `cup`、`water_bottle`，模型应返回 `rejected`，说明不支持书本，且 `actions=[]`。若模型输出 `book`，服务端会拒绝，这是动作参数错误，不代表 JSON 语法错误。

如果仍返回 `status=error`，查看 `logs/agent.jsonl` 中同一 `request_id` 的 `model.validation_failed`，根据 `failure_kind` 与 `validation_errors` 排查。分类解释见 [日志文档](logging.md#模型校验错误)。如果你修改过 `schemas.py`、注册动作或场景，检查三者是否一致；仅在提示词中描述新物品不会扩展动作参数的允许范围。

项目中的 6 项离线多轮回归测试可单独运行：

```bash
python -m unittest tests.test_multiturn
```

它们验证多轮消息格式、直接执行与错误处理，使用模拟返回值；不能保证真实 MiniMax 每次都生成合格输出。真实测试失败时，保留原始用户输入、第二轮回答、响应和校验错误信息用于定位。

把上述澄清用例用 3 个新会话各做一遍，记录成功次数。第二轮必须沿用当前那一组的真实 `session_id`，不能使用示例 ID 或其他会话 ID。`waiting_for_user` 是补充缺失信息，不是询问“是否批准执行”；明确且支持的请求不应再出现批准步骤。

## 9. 测试 MiniMax TTS 与扬声器

先测试 TTS API，不使用麦克风和真实文本模型。在 `.env` 中设置：

```dotenv
LLM_PROVIDER=mock
ASR_PROVIDER=disabled
SCENE=home_assistant
TTS_PROVIDER=minimax
MINIMAX_API_KEY=在本地填入真实Key
TTS_MODEL=speech-2.8-turbo
TTS_VOICE_ID="Chinese (Mandarin)_Gentleman"
TTS_LANGUAGE_BOOST=Chinese
```

`TTS_BASE_URL` 留空时复用 `MINIMAX_BASE_URL`。运行环境检查并启动 HTTP 服务：

```bash
python scripts/check_environment.py
python -m uvicorn voice_agent.main:create_app --factory --host 127.0.0.1 --port 8000
```

### 9.1 只检查合成请求，不使用麦克风

另开已激活环境的终端，执行以下跨平台命令。它先检查 HTTP 状态和音频类型，再保存一个带随机后缀的文件，不把错误 JSON 当作 WAV：

```bash
python -c "import httpx; from pathlib import Path; from uuid import uuid4; r=httpx.post('http://127.0.0.1:8000/speech',json={'text':'你好，我是你的语音助手。'},timeout=90); print(r.status_code,r.headers.get('content-type')); r.raise_for_status(); assert r.headers.get('content-type','').startswith('audio/wav'); p=Path('tts-check-'+uuid4().hex[:8]+'.wav'); p.write_bytes(r.content); print(p.resolve())"
```

Mac/Linux 也可用 curl（若已有 `agent-reply.wav` 请先改用另一个输出文件名）：

```bash
curl --fail --show-error http://127.0.0.1:8000/speech \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，我是你的语音助手。"}' \
  --output agent-reply.wav
```

预期 HTTP 200、`Content-Type: audio/wav`，文件可以正常播放。该请求会实际消耗 MiniMax TTS 额度。失败时检查 Key 与服务区域是否匹配、模型和音色是否对该账号开放。

`/speech` 只合成传入的 text，不调用 Agent、不执行动作，也不会自己启动服务器扬声器。`/chat` 和 `/audio` 仍返回 JSON；HTTP 客户端需把响应中的 `reply` 另行发给 `/speech` 才会得到语音。

### 9.2 检查项目播放器

先在系统播放器打开 WAV。然后将下面路径改为 9.1 打印的真实文件路径，调用项目自己的播放器：

```bash
python -c "import asyncio; from pathlib import Path; from voice_agent.tts import WavPlayer,SynthesizedAudio; asyncio.run(WavPlayer().play(SynthesizedAudio(Path('替换为实际TTS文件路径.wav').read_bytes())))"
```

输出设备不对时，将 `WavPlayer()` 改成 `WavPlayer(device=3)`，其中 3 换成实际输出设备编号。此步骤不再调用 MiniMax，不产生第二次合成费用。

**通过标准**：能听见完整文本，速度和音高正常。系统播放器正常而此命令失败，优先检查 sounddevice/PortAudio/设备；两者都无法播放，先检查文件是否是有效 WAV。

### 9.3 监听模式自动朗读回复

停止 HTTP 服务，将 `.env` 的 `ASR_PROVIDER` 改回 `whisper`，保持 `LLM_PROVIDER=mock` 和 `TTS_PROVIDER=minimax`，确认 `SCENE=home_assistant`、`WAKE_PROVIDER=asr`，运行：

```bash
python -m voice_agent.runtime.cli --list-devices
python -m voice_agent.runtime.cli
```

说“你好小助手，打开客厅的灯”。预期先输出 `agent_response`，然后看到 `tts_start`、听到 `reply`，最后出现 `tts_finished`。如果默认扬声器错误，使用 `--output-device 设备编号`。播放期间麦克风应保持暂停，结束后重新进入等待唤醒。

| 编号 | 操作 | 通过标准 |
| --- | --- | --- |
| T1 | 唤醒并说“打开客厅的灯” | 一次模拟动作、一次 JSON 回复、一次语音朗读 |
| T2 | 唤醒并说“打开灯” | 空 actions，朗读澄清问题，不执行开灯 |
| T3 | 唤醒并说“去厨房”（home_assistant） | rejected，朗读拒绝理由，无执行事件 |
| T4 | 播放期间保持安静 | 不被自己的合成声音再次唤醒，不产生第二次动作 |
| T5 | 播放完后重新唤醒并说新指令 | 正常进入下一轮，没有卡在 processing |
| T6 | 改 TTS_VOICE_ID 后重启并重复同一句话 | 使用账号支持的新音色，JSON 与动作行为不变 |

T4 建议先戴耳机测试，再测试外放。当前通过暂停收音降低自唤醒风险，并没有实现回声消除；如果外放尾音仍触发下一轮，请记录，不应把一次成功当作已经完全解决回声问题。

再把 `TTS_VOICE_ID` 临时改成明显无效的值并重启，只用于失败测试。预期动作仍产生一次 `simulated` 回执，随后出现 `tts_error`，不能再次执行动作。测完立即恢复正确音色；不要反复发送付费失败请求。完整配置与边界见 [TTS 说明](tts.md)。

### 9.4 最终端到端验收

停止 CLI，将 `LLM_PROVIDER=minimax`、`ASR_PROVIDER=whisper`、`TTS_PROVIDER=minimax` 同时开启，再启动 CLI。重复明确开灯、澄清房间、拒绝不支持设备这三类用例。

**通过标准**：转写正确、JSON 合法、明确动作只执行一次、缺信息或拒绝时不执行、语音朗读与实际 `reply` 对应、播放完成后能重新唤醒。检查 `reply` 是否承认只是模拟执行；把 Mock 结果说成“真实设备已经完成”应判为回复质量问题。

本版本采用非流式合成：需先等待完整语音返回，再播放。模型、音色参数与音频返回方式见 [MiniMax 同步 TTS 官方文档](https://platform.minimax.io/docs/api-reference/speech-t2a-http)。

## 10. 验证文件日志

在项目根目录打开 `logs/agent.jsonl`，或另开终端查看：

```bash
tail -f logs/agent.jsonl
```

Windows PowerShell：

```powershell
Get-Content .\logs\agent.jsonl -Wait -Tail 20
```

检查以下内容：

1. 每一行都能解析为 JSON，中文可读。
2. 开灯请求能找到 request.received、plan.created、action.started、action.finished、response。
3. 同一动作的 action.started 与 action.finished 具有相同 action_id。
4. waiting_for_user 和 rejected 请求没有 action.started；明确开灯请求在当前轮就有执行记录。
5. 语音监听有 voice.awake、voice.speech_end 等事件；未唤醒的背景语音没有 transcript 文本日志。
6. 退出再启动程序后，旧日志仍在，新运行的 run_id 不同，文件继续追加。

日志行使用 UTC 时间。可以在终端解析当前活动日志，并只打印最后 20 条的事件及关联 ID：

```bash
python -c "import json; from pathlib import Path; p=Path('logs/agent.jsonl'); rows=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]; print('JSONL rows:',len(rows)); print(*[(x.get('event'),x.get('request_id'),x.get('action_id')) for x in rows[-20:]],sep='\n')"
```

将路径改成实际 `LOG_FILE`。解析成功只代表日志格式正确，还需核对事件是否齐全。需要查历史记录时同时查看轮转备份；当前活动文件不包含所有历史。

### JSON 错误不是同一种错误

| 日志字段/事件 | 说明 | 下一步 |
| --- | --- | --- |
| model.error | 模型调用失败，不一定拿到有效输出 | 检查网络、账号权限、额度和 API 配置 |
| json_syntax | 无法解析成 JSON | 检查提示词、是否出现 Markdown 或自然语言外壳 |
| decision_schema | JSON 可以解析，但计划字段不合约定 | 检查旧确认字段、状态值和字段类型 |
| action_parameters | 动作名存在，但参数无效 | 检查对象/房间枚举和参数名 |
| unsupported_action | 未注册或场景不允许 | 同时检查 registry 与 available_actions |

后四项是 `model.validation_failed.failure_kind` 的取值。查看同一 `request_id` 下的 `attempt` 和 `validation_errors`，不要只看最后一句“模型错误”。程序有一次校验修复机会；两次候选都不合法时返回 error，不执行候选动作。日志默认不会保留模型原始失败输出。

会话存在内存中，日志是追踪记录，不是自动恢复数据库。重启后拿旧 `session_id` 请求可能失败；请新建会话，不要删除日志来“修复”会话。

测试轮转时使用独立文件，避免替换原日志：

```dotenv
LOG_FILE=logs/rotation-test.jsonl
LOG_MAX_BYTES=1024
LOG_BACKUP_COUNT=2
```

重启后发送数次开灯指令，应出现 `.1`、`.2` 备份，文件数量最多 3 个。完成后恢复原配置并重启。详细事件说明见项目中的 `docs/logging.md`。

## 11. 可选：测试 Porcupine

默认 requirements 不包含这个可选引擎。需要使用时另外安装：

```bash
python -m pip install -e '.[porcupine]'
```

在 `.env` 中设置（示例路径必须换成实际路径）：

```dotenv
WAKE_PROVIDER=porcupine
PORCUPINE_ACCESS_KEY=在本地填写
PORCUPINE_KEYWORD_PATH=/实际路径/keyword.ppn
PORCUPINE_MODEL_PATH=/实际路径/porcupine_params_zh.pv
WAKE_SENSITIVITY=0.5
```

填写 AccessKey、匹配运行平台的 `.ppn` 和匹配语言的 `.pv` 路径。实际唤醒词由 `.ppn` 决定，仅改 `WAKE_PHRASE` 不能替换该声学唤醒词。运行 `python scripts/check_environment.py --voice`，再启动监听。

先说模型对应的唤醒词，等待 awake，再说指令。当前专用引擎模式不承诺“唤醒词和指令同一句”的行为，V3 只适用于默认 ASR 唤醒方式。

## 12. 常见失败如何定位

| 现象 | 先检查 |
| --- | --- |
| requirements 提示找不到本地项目 | 是否在含 pyproject.toml 的根目录执行 |
| ModuleNotFoundError | 终端是否使用了安装依赖的同一个 Python/虚拟环境 |
| pip 安装原生音频库失败 | 使用 Python 3.11/3.12；记录失败包名、平台和架构，不要盲目更换全部依赖版本 |
| sounddevice 无法导入或加载 PortAudio | 系统音频运行库；Linux 可能需要安装系统 PortAudio，重新运行环境检查 |
| voice-agent-listen 找不到命令 | 使用 `python -m voice_agent.runtime.cli`；确认 editable 项目已安装 |
| 麦克风没有输入 | 系统麦克风权限、真实输入设备编号、其他程序是否独占设备 |
| SSH 中看不到笔记本麦克风 | 脚本运行在服务器；把设备测试放回连接麦克风的本机 |
| examples/microphone.py 不接受 --device | 该示例没有此参数；按 6.1 指定设备录音，然后从 /docs 上传 |
| Invalid sample rate / 输入格式不支持 | 按 6.1 验证选定设备是否支持 16 kHz 单声道；不要只改采样率数字而不做重采样 |
| Whisper 卡在加载 | 模型下载连接/本地模型目录；首次加载尚未完成时不要开始讲话 |
| 说话后没有唤醒 | 先做第 6 阶段单独 STT，确认唤醒词识别是否正确 |
| 每句都被截断 | 静音阈值过短或 VAD 误判；调整一个参数后重测 |
| 迟迟不结束录音 | 噪声被判为人声，检查 VAD 和麦克风环境；录音上限会终止并丢弃 |
| `/health` 正常但 `/chat` 模型错误 | health 不访问外部模型；核对 Key、地址、账号模型权限、额度和网络 |
| `/speech` 返回 502 | 核对 TTS Key、区域、模型、音色和额度；查看 tts.failed 日志 |
| `/speech` 返回 503 | 当前 TTS_PROVIDER=disabled；启用并重启 |
| `/speech` 返回 422 | 请求体应为非空 text，长度不超过 4000 字符，且无额外字段 |
| 保存了 .wav 但无法播放 | 检查 HTTP 状态和 Content-Type，避免把错误 JSON 保存为音频 |
| 有 tts_finished 但听不到声音 | 检查系统输出设备和音量，使用 --output-device 指定扬声器 |
| 出现 voice.tts_error | 区分云端合成失败和本地播放失败；动作不会因此自动重试 |
| 播放期间说话无响应 | 当前收音暂停、不支持语音打断；等结束后重新唤醒 |
| 一直要求“是否确认执行” | 检查是否沿用旧提示词或旧版本代码；模型补问缺失房间不属于执行确认 |
| status=error 但 HTTP 是 200 | 本项目业务错误在 JSON 中表达，读取 reply 并检查对应日志事件 |
| API 测试通过但真实语音失败 | 自动化使用的是语音替身；继续检查设备、原生库及模型 |
| 修改 .env 不生效 | 重启对应进程；检查同名系统环境变量是否覆盖配置 |
| 日志不在项目目录 | LOG_FILE 相对启动命令的当前目录；可以改为绝对路径 |
| 旧 session_id 无效 | 会话重启后不会恢复，或已过期；创建新会话 |

不要为了消除错误而删除 Schema 校验、放宽场景动作允许列表或让客户端自动重试整条动作请求。模型/设备出错和操作是否已执行应分开判断。

## 13. 记录测试结论

建议每阶段记一行，方便区分已验证和未验证部分：

| 阶段 | 是否通过 | 失败用例/错误 | 操作系统与 Python 版本 | 备注 |
| --- | --- | --- | --- | --- |
| 环境安装与 pip check | 待测 | | | |
| pytest 与 lint | 待测 | | | |
| Mock 文本/JSON | 待测 | | | |
| 直接执行/澄清/拒绝 | 待测 | | | |
| 麦克风录音及回放 | 待测 | | | |
| 纯 STT / 本地模型离线运行 | 待测 | | | |
| STT 录音识别 | 待测 | | | |
| 唤醒/静音 V1–V12 | 待测 | | | |
| 真实 MiniMax 文本 | 待测 | | | |
| 完整语音链路 | 待测 | | | |
| TTS 合成与扬声器 | 待测 | | | |
| 日志追加与轮转 | 待测 | | | |

反馈失败时，提供阶段编号、执行命令、完整错误类型和去掉 Key 的相关日志即可。不要把 API Key 或完整 `.env` 发出来。

### 建议的阶段性验收规则（本项目测试目标，不是模型准确率承诺）

- 自动化：完整 92 项测试全通过，ruff 无错误。
- JSON 与动作：结构符合 Schema；允许的明确动作直接执行；拒绝/澄清没有执行事件；不出现旧确认字段。
- 麦克风与 STT：5 条固定录音能听清；转写中的房间、物品、开关意图正确。不要求标点完全一致。
- 唤醒：V2、V3、V4 各测试 5 次并记录成功次数；V1 可延长为 2 分钟观察误触发。少量样本通过不代表真实环境零误唤醒。
- TTS：T1–T6 有记录；音色可切换；合成或播放失败后能够继续唤醒，不重复动作。
- 多轮：3 个独立会话的“缺信息→补充信息”测试无 JSON 格式失败；按各会话正确传递 session_id。

### 记录响应时间

模型预热前后的时间分开记录。项目没有逐 token、STT 内部或首音频到达时间的专门计时指标，不要从一条总耗时推断具体瓶颈。

| 时间项 | 记录方法 | 含义 |
| --- | --- | --- |
| 用户停说到 speech_end | 人工观察或录屏 | 静音判断与帧处理，不包含后续全部耗时 |
| voice.speech_end 到 voice.agent_response | 同一轮日志时间差 | STT、唤醒判断和 Agent 处理的合计近似值 |
| voice.tts_start 到 voice.tts_finished | 同一轮日志时间差 | 合成等待与完整播放的总时间，不是首音频延迟 |
| 用户停说到实际听见回复 | 秒表或录屏 | 用户感受到的完整等待时间 |

### 错误反馈模板

```text
项目版本：v0.6.0（或你当前实际版本）
测试阶段/用例编号：
操作系统与 Python 版本：
运行位置：本机 / SSH 服务器
运行入口：HTTP / 自动监听 CLI / 独立 STT
模型及设备：Whisper 模型、cpu/cuda、麦克风/扬声器编号
场景及唤醒方式：
LLM_PROVIDER / ASR_PROVIDER / TTS_PROVIDER：
用户原句与 transcript（去掉隐私信息）：
预期结果：
实际 JSON / 错误类型：
session_id / request_id / action_id（相关时填写）：
相关日志（去掉 Key 与个人信息）：
是否每次复现、重复次数：
```

## 14. 修改配置后的最小回归清单

| 修改位置 | 作用 | 至少重测 |
| --- | --- | --- |
| `.env` 的 WHISPER_* | 本地 STT 模型、设备、语言 | 6.2、6.3、V3 |
| `.env` 的 WAKE_PHRASE | ASR 唤醒词 | V2、V3、V11、V12 |
| `.env` 的 AUDIO_* / VAD_* | 人声判断、分段 | V1、V6、V7、V9 |
| `.env` 的 TTS_VOICE_ID / TTS_MODEL | 合成音色、模型 | 9.1、9.2、T1、T4、T6 |
| `voice_agent/prompts/system.txt` | Agent 行为规则 | 第 8 节多轮、澄清、拒绝及 JSON |
| `voice_agent/scenes/*.yaml` | 场景角色、规则与动作允许列表 | 第 5 节及第 8 节 |
| `voice_agent/actions/registry.py` | 动作名称及参数约束 | 自动化测试、支持/不支持参数、对应场景用例 |
| `voice_agent/schemas.py` | 计划和响应格式 | 自动化测试、/schema/response、多轮 JSON |

`.env.example` 只是模板，运行时应编辑 `.env`。测试完成后恢复你准备长期使用的配置，关闭本次测试服务；保留有用的脱敏日志，避免把 Key、模型权重或私人录音提交到仓库。若失败复现需要原始录音，请先征得录音者同意并妥善保管。
