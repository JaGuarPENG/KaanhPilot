# MiniMax TTS 与本地播放

TTS 模块把 Agent 的 `reply` 转成语音。持续监听程序默认通过 HTTP SSE 接收 PCM 并边收边播，`/speech` 仍返回完整 WAV。它与 Agent、STT 和动作执行解耦：`voice_agent/tts/minimax.py` 只调用 MiniMax，`voice_agent/tts/playback.py` 只负责本机播放，`StreamingSpeechOutput` 或 `SpeechOutput` 组合两者。

## 配置

在项目根目录的 `.env` 中设置：

```dotenv
MINIMAX_API_KEY=你的Key
MINIMAX_BASE_URL=https://api.minimax.io/v1

TTS_PROVIDER=minimax
TTS_STREAMING=true
TTS_BASE_URL=
TTS_MODEL=speech-2.8-turbo
TTS_VOICE_ID="Chinese (Mandarin)_Gentleman"
TTS_LANGUAGE_BOOST=Chinese
TTS_SPEED=1.0
TTS_VOLUME=1.0
TTS_PITCH=0
TTS_SAMPLE_RATE=32000
TTS_BITRATE=128000
TTS_TIMEOUT_SECONDS=60
TTS_MAX_AUDIO_BYTES=10485760
```

`TTS_BASE_URL` 留空时复用 `MINIMAX_BASE_URL`，因此 Key、文本模型和 TTS 默认位于同一服务区域。国内平台可按账号区域把基础地址配置为 `https://api.minimax.cn/v1`，并选用该账号可用的音色。即使 `LLM_PROVIDER=mock`，使用 MiniMax TTS 仍必须提供 `MINIMAX_API_KEY`，并会消耗 TTS API 额度。

默认使用 `speech-2.8-turbo` 以降低对话等待时间，输出为单声道。MiniMax HTTP 接口为 `POST /v1/t2a_v2`，支持流式 PCM 和非流式 WAV，文本上限小于 10,000 字符；本项目的 `/speech` 进一步限制为 4,000 字符，与 Agent `reply` 上限一致。参考 [HTTP TTS 接口](https://platform.minimax.io/docs/api-reference/speech-t2a-http) 和 [系统音色列表](https://platform.minimax.io/docs/faq/system-voice-id)。

## 单独测试合成

先保持 `LLM_PROVIDER=mock`，设置 `TTS_PROVIDER=minimax`，然后运行：

```bash
python scripts/check_environment.py
uvicorn voice_agent.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

另开终端请求语音并保存：

```bash
curl http://127.0.0.1:8000/speech \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，我是你的语音助手。"}' \
  --output agent-reply.wav
```

正常响应的 `Content-Type` 是 `audio/wav`，`agent-reply.wav` 应可用系统播放器打开。也可以在 `http://127.0.0.1:8000/docs` 测试 `/speech`，但浏览器接口页面不一定自动播放二进制响应。

HTTP 对话仍返回 JSON。客户端的正确流程是先调用 `/chat` 或 `/audio`，读取 `reply`，再按需调用 `/speech`。不要把 WAV 塞入 Agent JSON，也不要根据 `actions` 再执行一次动作。

## 自动监听并播放回复

安装完整语音依赖，并同时启用任一 STT 与 TTS：

```dotenv
ASR_PROVIDER=whisper
TTS_PROVIDER=minimax
```

使用科大讯飞 STT 时将第一项改为 `ASR_PROVIDER=xfyun`，并按 [讯飞 STT 说明](xfyun-asr.md)填写三项凭证。

查看输入和输出设备：

```bash
python -m voice_agent.runtime.cli --list-devices
```

启动默认设备：

```bash
python -m voice_agent.runtime.cli
```

或分别指定设备编号：

```bash
python -m voice_agent.runtime.cli --device 1 --output-device 3
```

监听程序输出 `agent_response` 后依次产生 `tts_start`、`tts_finished`。默认 `TTS_STREAMING=true`：收到首段 PCM 后即开始播放，后续音频通过有界队列持续送入同一个输出设备；请求排除最终聚合音频，避免整段重复播放。设为 `false` 可恢复先合成完整 WAV 再播放。该开关只影响持续监听 CLI，不改变 `/speech`。

合成与播放期间麦克风保持暂停，收到流结束并等待输出设备播放完缓冲音频后才恢复等待唤醒，减少扬声器声音再次触发 Agent 的风险。当前没有回声消除，外放尾音或环境回声仍可能影响下一轮，使用耳机更容易测试。

如果合成或播放失败，会产生 `tts_error` 并恢复等待唤醒。动作可能已经在 TTS 之前完成，所以播放失败不会撤销动作，也不会自动重试整条指令。排查 `logs/agent.jsonl` 中的 `tts.failed`、`voice.tts_error`，以及输出设备、音量、Key、区域、模型和音色配置。

## 当前边界

- 流式 TTS 接收完整 Agent `reply` 后开始合成；尚未接入 LLM 文本增量，也不支持用户说话打断播放。
- `/speech` 返回音频本身，不会永久保存 WAV；只有你用 `curl --output` 或客户端自行保存时才写文件。
- HTTP 服务当前没有用户认证，仅适合绑定 `127.0.0.1` 本地测试；公开部署前必须增加鉴权和调用限额，避免他人消耗 API 额度。
- 流式 PCM 播放依赖 `sounddevice` 和可用的系统输出设备；完整 WAV 播放还使用 `soundfile`。仅调用 HTTP `/speech` 不需要本机扬声器。
