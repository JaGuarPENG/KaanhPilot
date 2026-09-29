# 科大讯飞语音听写 WebAPI 接入说明

项目 v0.6.0 支持两个 STT Provider：本地 `whisper` 和云端 `xfyun`。两者不会同时识别同一段录音；由 `.env` 的 `ASR_PROVIDER` 选择。讯飞识别会将唤醒检测阶段及命令阶段的语音发送到讯飞云端，因此不再是离线 STT。

## 持续监听的实时上传

`voice-agent-listen` 在 `ASR_PROVIDER=xfyun` 时默认边录边传，配置为
`XFYUN_ASR_STREAMING=true`。改为 `false` 可恢复先完整录音再识别的旧路径。
原来的三项讯飞密钥、模型配置及启动命令不变；无需修改 TaskRunner。

VAD 判断开始说话后建立一次 WebSocket 会话，先发送句首预录音，再持续发送
16 kHz 单声道 PCM16。适配器将麦克风的 10/20/30 ms 帧重组为 40 ms 音频块，
发送与接收并行；静音断句后发送结束标记，只把最终修正文本交给 Agent。
不会根据中间识别结果下单。连接建立和预录音仍有开销，静音等待仍然存在。

待发送队列最多缓存约 3 秒音频，网络积压超限、音频丢帧、录音过长、短噪声、
识别失败或服务端提前结束都会丢弃本次指令并关闭识别会话，不自动重新提交。
退出监听时取消尚未结束的识别任务并关闭连接。

默认 ASR 唤醒仍会把待机语句发往讯飞，只有最终文本以唤醒词开头才进入 Agent；
Porcupine 模式则唤醒后才开启识别。短噪声可能已上传部分音频，但不会触发动作。
LLM/TTS 处理期间仍为半双工，不能打断播报。

`POST /audio` 和 `examples/microphone.py` 仍使用完整录音上传；Whisper、TTS、
LLM JSON 协议均不受此开关影响。实时输入接口为
`transcribe_stream(AsyncIterable[bytes]) -> str`，输入必须为 16 kHz 单声道 PCM16。

离线回归：`python -m pytest tests/test_streaming_asr.py tests/test_xfyun.py tests/test_voice.py -q`。
测试验证录音结束前发包、分帧无损、最终修正、连接取消和异常行为，不能替代真实网络及麦克风延迟实测。

官方接口文档：<https://www.xfyun.cn/doc/asr/voicedictation/API.html>

## 1. 开通服务并取得密钥

在讯飞开放平台创建应用，为同一个应用开通“语音听写（流式版）”，从控制台取得三项值：`APPID`、`APIKey`、`APISecret`。它们不是 MiniMax Key，不能混用。若启用了 IP 白名单，需要加入运行 Voice Agent 的设备所使用的公网 IP；家庭网络公网 IP 变化时需同步更新，或在控制台按你的安全要求关闭白名单。

把项目根目录 `.env` 中对应条目填写为：

```dotenv
ASR_PROVIDER=xfyun

XFYUN_APP_ID=你的APPID
XFYUN_API_KEY=你的APIKey
XFYUN_API_SECRET=你的APISecret
XFYUN_ASR_URL=wss://iat-api.xfyun.cn/v2/iat
XFYUN_ASR_LANGUAGE=zh_cn
XFYUN_ASR_DOMAIN=iat
XFYUN_ASR_ACCENT=mandarin
XFYUN_ASR_DYNAMIC_CORRECTION=true
XFYUN_ASR_TIMEOUT_SECONDS=75
```

真实 `.env` 已被 `.gitignore` 排除。不要把它上传 GitHub、发到聊天或放进公开的 EXE/ZIP；`.env.example` 只能保留空值。APPID、APIKey、APISecret 必须来自开通了语音听写服务的同一个应用。

参数含义：

| 配置 | 作用 |
| --- | --- |
| `ASR_PROVIDER` | `xfyun` 使用讯飞；改回 `whisper` 使用本地模型 |
| `XFYUN_ASR_URL` | 中英文推荐接口；一般无需修改 |
| `XFYUN_ASR_LANGUAGE` | `zh_cn` 为中文并支持简单英文 |
| `XFYUN_ASR_DOMAIN` | `iat` 为日常用语；其他领域必须先获得授权 |
| `XFYUN_ASR_ACCENT` | `mandarin` 为普通话；其他方言需先开通 |
| `XFYUN_ASR_DYNAMIC_CORRECTION` | 中文开启 `wpgs` 动态修正，代码会处理替换结果 |
| `XFYUN_ASR_TIMEOUT_SECONDS` | 单次 WebSocket 会话的客户端超时 |

接口 URL 必须为无账号、无查询参数的 `wss` 地址。程序会根据当前 UTC 时间、APIKey 和 APISecret 生成短期 HMAC-SHA256 鉴权 URL；不要手工生成或保存鉴权 URL。系统时间与标准时间相差超过约 5 分钟可能鉴权失败。

## 2. 安装与检查

完整安装：

```bash
python -m pip install -r requirements.txt
python -m pip check
python scripts/check_environment.py --voice
```

只在已有核心项目中补装讯飞 WebSocket 客户端：

```bash
python -m pip install -e '.[xfyun]'
```

环境检查只验证依赖、配置是否存在和 URL 结构，不连接讯飞，也不会显示密钥。通过后重启正在运行的 HTTP 服务或监听 CLI。

## 3. 先测试同一条 WAV

讯飞接口要求 16 kHz、16-bit、单声道 PCM。当前适配器接受带 WAV 文件头的这一格式，移除文件头后按 1280 字节一帧、约 40 ms 间隔发送。自动监听产生的音频已符合要求；`/audio` 上传的 MP3、M4A、立体声、8 kHz、44.1 kHz、压缩 WAV 会被明确拒绝，不会静默转码。

准备一段不超过 60 秒的 PCM_16 WAV，然后仅测试 STT：

```bash
python -c "import asyncio; from pathlib import Path; from voice_agent.config import Settings; from voice_agent.asr import XFYunASR; print(asyncio.run(XFYunASR(Settings()).transcribe(Path('你的16k单声道录音.wav').read_bytes())))"
```

这一步会联网并消耗讯飞调用额度，但不会调用 MiniMax 或执行动作。预期直接打印识别文字。

也可以启动 HTTP 服务后，在 `/docs` 的 `POST /audio` 上传同一个 WAV：

```bash
python -m uvicorn voice_agent.main:create_app --factory --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/docs>。先保持 `LLM_PROVIDER=mock`、`TTS_PROVIDER=disabled`，以免把 STT、模型理解和 TTS 问题混在一起。`/audio` 不要求唤醒词；检查响应中的 `transcript`。

## 4. 测试持续监听

```bash
python -m voice_agent.runtime.cli --list-devices
python -m voice_agent.runtime.cli
```

默认 `WAKE_PROVIDER=asr` 时，待机语句也需要先上传讯飞识别，识别结果以唤醒词开头才会继续。因此背景对话可能消耗调用额度，并会被发送至云端。若希望待机阶段不上传完整语句，可另行配置本地 Porcupine 唤醒；唤醒后的命令仍会发送讯飞。

建议依次测试：

1. “你好小助手，打开客厅的灯。”——应一次完成唤醒和命令识别。
2. 只说“你好小助手”，再说“关闭卧室的灯。”——应进入 armed 后识别第二句。
3. 不说唤醒词直接发出指令——不应进入 Agent，但 ASR Provider 仍会收到这段音频。
4. 将 `XFYUN_ASR_DYNAMIC_CORRECTION=false` 重启，再用相同录音比较结果；普通追加结果也受支持。

## 5. 切回本地 Whisper

无需删除讯飞配置，只改：

```dotenv
ASR_PROVIDER=whisper
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_LANGUAGE=zh
```

重启后语音不再发送讯飞。建议保留 Whisper 作为断网或讯飞额度不足时的手动回退。当前版本不会在云端失败时自动转用 Whisper，以避免同一段用户语音未经明确配置又被第二个 Provider 处理，也避免掩盖额度和鉴权故障。

## 6. 常见错误

| 现象 | 检查内容 |
| --- | --- |
| 启动提示三项凭证缺失 | `.env` 中三项都要填写，名称必须完全一致，修改后重启 |
| 401 / HMAC signature does not match | APIKey/APISecret、接口地址、本机 UTC 时间 |
| 403 / valid date | 自动同步系统时间；允许偏差约 5 分钟 |
| 403 / IP address is not allowed | 控制台 IP 白名单与当前公网 IP |
| 10005 | APPID 是否正确、同一应用是否开通语音听写 |
| 10043 | 上传音频格式；当前项目要求 PCM_16 WAV |
| 11200/11201/11202/11203 | 权限、套餐额度、流控或授权有效期 |
| 超时或无法连接 | 网络、DNS、防火墙、系统时间和 `wss` 访问 |
| transcript 正确但动作错误 | MiniMax/Mock、场景 YAML、提示词或动作定义，不是 STT |

程序会保留讯飞错误码与简短错误说明，同时从错误消息和审计日志的密钥集合中脱敏三项凭证。真实识别效果、账户权限、额度、网络和硬件仍需在你的设备上验收。
