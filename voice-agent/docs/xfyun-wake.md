# 讯飞 AIKit 本地唤醒（Windows x64）

在 `.env` 设置 `WAKE_PROVIDER=xfyun`。当前本机配置已切换到该模式，词表为“小佳”。
麦克风保持由现有 `MicrophoneSource` 管理，本地唤醒模块不另外打开麦克风。
待机音频只送本地 AIKit；命中后进入现有 STT → Agent/TaskRunner → TTS 流程。
STT 仍是讯飞云端流式识别，TTS 仍使用原配置。语音 HTTP API 不依赖本地唤醒。

## SDK 与授权

SDK 当前位于 `voice_agent/resources/xfyun_wake/`，其下有一份解压目录：
`windows_ivw_e867a88f2_v1.0.13_v2.2.15-rc6`。
程序自动识别唯一解压目录，也可以把 `XFYUN_WAKE_SDK_DIR` 指向该解压目录。
它读取 `libs/64/AEE_lib.dll`、同目录能力 DLL，以及 `bin/resource/ivw70/IVW_*`。
SDK 原始文件不需要移动或覆盖，32 位库不会被加载。需要 Windows x64、64 位 Python，
以及 DLL 所需的 Visual C++ 运行库。

本地唤醒复用 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`（本次用户确认同一应用）。
该应用必须已开通 AIKit 离线唤醒 `e867a88f2` 并有有效设备授权。
SDK 首次初始化可能联网激活并消耗设备授权额度；并非只要开通云端听写就能使用。
程序不复制其他机器的授权缓存，工作目录和 SDK 日志保存在 `logs/xfyun_wake`。

## 配置

```dotenv
WAKE_PROVIDER=xfyun
WAKE_PHRASE=小佳
XFYUN_WAKE_SDK_DIR=voice_agent/resources/xfyun_wake
XFYUN_WAKE_KEYWORD_PATH=voice_agent/resources/xfyun_wake/keyword.txt
XFYUN_WAKE_WORK_DIR=logs/xfyun_wake
XFYUN_WAKE_THRESHOLD="0 0:999"
```

这些 SDK 路径相对于 `voice-agent` 根目录解析，与启动终端所在目录无关；也支持绝对路径。
`keyword.txt` 是 UTF-8 唤醒词表，当前为 `小佳;`。SDK 自带示例词表“小白小白”不会被使用。
修改词表后重启。讯飞模式按词表剥离 STT 文本中的唤醒前缀；仅识别出词尾时也不会提交给 Agent。
`WAKE_PHRASE` 仍供 ASR/Porcupine 模式使用，建议设为相同名称以方便切换。
门限字符串沿用 SDK 示例，`0 0:999` 对应第 0 份词表的第 0 个词；多词门限需按 SDK 文档配置。

## 启动与验证

配置原有讯飞凭证后，继续使用根目录 `Start-Voice-Agent.bat`，或在 `voice-agent` 中执行：

```powershell
python -m voice_agent.runtime.cli
```

终端先显示“正在初始化讯飞本地唤醒 SDK……”，成功后出现 `listening`；
说“小佳”应出现 `awake`，然后说指令。说“小佳，给我一瓶可乐”时，程序回放
`AUDIO_PRE_ROLL_MS`（默认 300 ms）本地缓存到录音流程，以减少异步唤醒延迟导致的截断。
这段缓存只在命中后进入 STT，并不保证任意语速下连说都不截断；建议先测试分句，
再按真实麦克风表现验证连说及调整缓存。唤醒词本身被正确识别后会剥离，不直接提交给 Agent。

命中后结束本次 SDK 会话，指令录音及 TTS 期间不继续检测；回到待机后重新开会话。
退出、初始化中途失败时释放会话、构造器、词表、引擎与 SDK。
本进程只支持一个 AIKit 唤醒实例。SDK 错误打印阶段和错误码，不打印凭证或原始 SDK 错误正文；
运行中引擎故障会停止监听，不会每个音频帧重复重试或偷偷切回云端唤醒。

回退为原来的文字匹配唤醒：设置 `WAKE_PROVIDER=asr` 并重启。Porcupine 分支仍保留。

离线测试（模拟 SDK，不连接麦克风、授权服务或机器人）：

```powershell
python -m pytest tests/test_xfyun_wake.py tests/test_voice.py tests/test_streaming_asr.py --asyncio-mode=auto -q
```

真实 DLL 的加载和导出符号已检查；真实授权、唤醒准确率和声学连说效果需要本机启动验证。
