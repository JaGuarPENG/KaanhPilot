# 模块化语音运行时

## 各模块只承担一个职责

| 模块 | 输入 → 输出 | 替换位置 |
| --- | --- | --- |
| `audio/source.py` | 系统麦克风 → 16 kHz 单声道 PCM16 帧 | 实现 `AudioSource` |
| `audio/vad.py` | PCM 帧 → 是否有人声 | 实现 `VoiceActivityDetector` |
| `audio/segmenter.py` | 帧与 VAD 判断 → 完整语音片段 | 纯状态机，不调用模型 |
| `wake/keyword.py` | 本地 ASR 文本 → 唤醒结果与剩余指令 | 实现 `TranscriptWakeDetector` |
| `wake/porcupine.py` | 连续 PCM 帧 → 唤醒事件 | 实现 `FrameWakeDetector` |
| `asr/` | WAV 等音频字节 → 文本 | 实现 `ASRProvider` |
| `runtime/listener.py` | 音频事件 → 唤醒/录音/处理状态 | 注入各模块，不依赖 FastAPI |
| `runtime/conversation.py` | 指令文本 → AgentResponse | 实现 `Conversation`，可连接远程服务 |
| `agent/` | 对话上下文 → 已验证动作计划与结果 | 无麦克风、唤醒器或 HTTP 依赖 |
| `llm/` | 对话消息 → 模型生成文本 | 实现 `LLMProvider` |
| `actions/` | 已验证动作 → 执行回执 | 实现 `ActionExecutor` |
| `bootstrap.py` | 配置 → 各服务实例 | 集中选择实现 |
| `api/` | HTTP / WebSocket → AgentResponse | 保留现有对外协议 |

`__init__.py` 保留旧版 `from voice_agent.agent import Agent` 等导入，HTTP 启动入口也仍为 `voice_agent.main:create_app`。

## 默认 ASR 唤醒流程

1. 麦克风持续采样；WebRTC VAD 判断每帧是否有人声。
2. 连续 60 ms 人声开始候选录音，同时保留最多约 300 ms 的句首音频。
3. 连续约 800 ms 无人声结束录音。短暂停顿不会立即切断句子。
4. 当前 `ASR_PROVIDER` 输出最终文字：Whisper 在断句后本地处理；讯飞默认在录音期间边录边传，断句后等待最终结果。设置 `XFYUN_ASR_STREAMING=false` 可恢复讯飞完整录音模式。
5. 待唤醒状态只接受**句首**的唤醒词。例如“不要说你好小助手”不会唤醒。
6. 唤醒词后已有指令时直接处理；只有唤醒词时等待下一句，最多等 8 秒。
7. 指令进入 Agent，继续使用既有 JSON 协议和模拟执行器。
8. 处理结束回到等待唤醒。

默认词支持识别文本中的标点，例如“你好，小助手，打开灯”。它做标准化后的精确匹配，不进行模糊或语义唤醒；ASR 把名字识别错时可能无法唤醒。可改用辨识度更高的唤醒词，或切换专用引擎。

## 静音与录音边界

“静音”在这里指 VAD 连续判断为**无人声**，不要求音量为零。风扇、键盘声或背景噪声是否被过滤，取决于麦克风和 VAD 实际效果。根据环境调节 `VAD_AGGRESSIVENESS`，根据讲话停顿习惯调节 `AUDIO_END_SILENCE_MS`。

- 断句阈值按完整帧计算，因此 30 ms 帧下，800 ms 阈值实际约为 810 ms。
- 语音短于 `AUDIO_MIN_SPEECH_MS` 会丢弃。
- 超过录音上限时整段丢弃，不执行可能截断的指令。
- 采集队列溢出或 PortAudio 报告丢帧时，清空录音并要求重新唤醒。
- 唤醒超时只约束“开始讲话”的等待时间，已经开始的句子可继续到静音或录音上限。
- ASR/Agent 处理期间暂停接收麦克风帧；处理后清理队列，不重放积压声音。
- 当前不自动保存录音文件。ASR 唤醒会识别待机时的人声片段，但不向 MiniMax 发送未唤醒文本；讯飞模式仍会把音频发送给讯飞云端。

## 运行与输出

```bash
python -m pip install -e '.[voice,xfyun,dev]'
```

在本地 `.env` 中至少设置：

```dotenv
ASR_PROVIDER=whisper
WAKE_PROVIDER=asr
WAKE_PHRASE=你好小助手
AUDIO_END_SILENCE_MS=800
WAKE_TIMEOUT_SECONDS=8
```

要使用科大讯飞，将 `ASR_PROVIDER=xfyun` 并填写 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`。详情见 [讯飞 STT 说明](xfyun-asr.md)。

选择 `LLM_PROVIDER=mock` 可先验证语音和 JSON 动作链；选择 `minimax` 时另外填写自己的 Key。启动：

```bash
voice-agent-listen
```

或使用 Python 模块入口：

```bash
python -m voice_agent.runtime.cli
```

查询和选择麦克风：

```bash
voice-agent-listen --list-devices
voice-agent-listen --device 1
```

需要终端麦克风权限；部分 Linux 环境还需要系统 PortAudio 运行库。Whisper 模式先加载本地模型再打开麦克风；讯飞模式先检查三项密钥和 WebSocket 地址。Whisper 首次下载模型可能较慢，讯飞每次识别都需要网络。

正式 Agent JSON 每条占一行，输出到 **stdout**；`listening`、`awake`、`speech_start`、`speech_end`、`wake_timeout`、`error` 等事件输出到 **stderr**。这些事件也会写入配置的 JSONL 日志；详见 [日志说明](logging.md)。可另外单独保存响应：

```bash
voice-agent-listen > responses.jsonl
```

按 `Ctrl+C` 退出会关闭麦克风、释放唤醒引擎并关闭模型 HTTP 客户端。

## 专用唤醒引擎

已支持讯飞 AIKit Windows x64 本地唤醒：设置 `WAKE_PROVIDER=xfyun`，
复用现有麦克风音频，待机不调用云端 STT。SDK 路径、授权、词表与连说缓冲说明见
[讯飞本地唤醒](xfyun-wake.md)。原有 ASR 和 Porcupine 模式仍可选。

可选 Porcupine 适配器把 10/20/30 ms 的输入帧重新拼接为引擎要求的帧大小。它在待唤醒状态逐帧检测，检测前不运行 Whisper。

安装：

```bash
python -m pip install -e '.[voice,porcupine]'
```

准备 Picovoice AccessKey、与你操作系统匹配的关键词 `.ppn`、同语言 `.pv` 模型后配置：

```dotenv
WAKE_PROVIDER=porcupine
PORCUPINE_ACCESS_KEY=在本地填写
PORCUPINE_KEYWORD_PATH=/absolute/path/keyword.ppn
PORCUPINE_MODEL_PATH=/absolute/path/porcupine_params_zh.pv
WAKE_SENSITIVITY=0.5
```

这些模型和 Key 没有包含在项目包中。中文关键词需使用对应的中文资源；不要混用英文参数模型。该模式下实际唤醒词由 `.ppn` 决定，单独修改 `WAKE_PHRASE` 不会改变声学模型。

当前专用引擎适配器在唤醒触发处丢弃唤醒片段，因此采用**先说唤醒词，等 awake 后再说指令**的交互。若要把唤醒和指令连在同一句，请使用默认 ASR 唤醒模式。

参数、模型准备和 AccessKey 要求参考 [Porcupine 官方 Python SDK](https://github.com/Picovoice/porcupine#python)。

## 对话与直接执行

监听 CLI 与 HTTP 服务是两种独立运行入口，分别持有自己的进程内会话，不能跨进程共用会话 ID。

CLI 保持最近会话，每条新指令仍需重新唤醒。明确且允许的动作在当前轮直接执行；缺少信息时返回 `waiting_for_user`，重新唤醒并补充即可继续；无法执行或不合理的请求返回 `rejected` 和原因。`LocalConversation` 只负责会话延续，不再截获批准或取消口令。

当前只运行 Mock Executor。语音唤醒不等于身份认证；如果以后对接真实设备，应按设备要求增加独立的授权和保护。

## 验证范围

```bash
python -m unittest tests.test_core tests.test_voice tests.test_tts -v
```

92 项离线测试已通过，包括讯飞鉴权、分帧、动态修正解析和密钥脱敏。真实麦克风、WebRTC/Whisper/讯飞账号/Porcupine、扬声器和 MiniMax 调用尚未端到端实测。

实现参数参考：[WebRTC VAD 帧要求](https://github.com/wiseman/py-webrtcvad#how-to-use-it)、[预编译 VAD 包](https://github.com/daanzu/py-webrtcvad-wheels)、[sounddevice RawInputStream](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html)。
