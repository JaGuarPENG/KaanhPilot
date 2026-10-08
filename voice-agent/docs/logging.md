# JSONL 日志

程序默认把持久日志写入**当前运行目录**下的 `logs/agent.jsonl`。

如果在项目根目录执行 `uvicorn` 或 `voice-agent-listen`，实际位置就是项目中的 `logs/agent.jsonl`。`logs/` 已加入 `.gitignore`，不会意外提交到 GitHub。

每一行是一个独立 JSON 对象。例如（ID 已缩短）：

```json
{"request_id":"b861...","session_id":"9a15...","action_id":"7764...","type":"move_to","parameters":{"location":"kitchen"},"timestamp":"2026-09-02T10:25:12.148+00:00","event":"action.started","run_id":"1317...","pid":4281,"sequence":7}
{"request_id":"b861...","session_id":"9a15...","action_id":"7764...","type":"move_to","status":"simulated","details":{"executor":"mock","parameters":{"location":"kitchen"}},"timestamp":"2026-09-02T10:25:12.150+00:00","event":"action.finished","run_id":"1317...","pid":4281,"sequence":8}
```

日志记录的是 UTC 时间。`request_id` 关联一次请求，`session_id` 关联对话，`action_id` 关联动作与执行结果。`run_id` 标识一次程序运行，`sequence` 是该运行实例内递增的事件编号。

## 记录内容

| 事件 | 含义 |
| --- | --- |
| `request.received` | 收到文本或语音指令 |
| `model.error` / `model.validation_failed` | 模型调用或 JSON/动作校验失败 |
| `plan.created` | 已验证的动作计划和参数 |
| `action.started` | 动作开始执行 |
| `action.finished` | 动作的 simulated、failed 或 skipped 回执 |
| `response` | 最终 AgentResponse，包含 success、waiting_for_user、rejected 等结果 |
| `voice.*` | 唤醒、开始/结束说话、指令和音频错误等生命周期事件 |
| `tts.finished` / `tts.failed` | HTTP TTS 合成结果，不记录音频内容 |
| `voice.tts_start` / `voice.tts_finished` / `voice.tts_error` | 自动监听中的合成与播放状态 |
| `http.*` | 请求格式、HTTP 或内部错误 |
| `service.created` / `service.stopped` | 服务进程开始和正常停止 |

未包含唤醒词的背景语音识别文本仍会被丢弃，不写入日志。原始音频也不会写入日志文件。

### 模型校验错误

`model.validation_failed` 包含 `attempt`（1 或 2）、`failure_kind` 和 `validation_errors`。校验错误包含字段位置、错误类型和原因，省略 Pydantic 的输入值、上下文和文档 URL；不记录模型原始候选输出。失败候选仅用于当前请求的一次修复尝试，不进入持久对话历史。

| failure_kind | 含义 | 排查方向 |
| --- | --- | --- |
| `json_syntax` | 内容无法解析成 JSON | 自然语言、Markdown、缺少引号或括号 |
| `decision_schema` | JSON 不符合 AgentDecision | 多余字段、缺字段、错误的 status 或字段类型 |
| `action_parameters` | 动作参数不符合定义 | 例如 pick_object 使用不支持的物品或错误参数名 |
| `unsupported_action` | 动作未注册或当前场景不允许 | 检查 registry 与场景 available_actions |

同一个请求两次校验都失败时返回 `status=error`，不执行任何候选动作。`model.error` 是另一类模型调用错误，不应当作 JSON 语法错误处理。

拒绝或缺少信息的请求没有 `action.started` 事件；动作允许且参数明确时，在当前请求中直接产生执行日志。

## 配置

`.env.example` 提供以下默认配置：

```dotenv
LOG_ENABLED=true
LOG_FILE=logs/agent.jsonl
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
LOG_INCLUDE_TEXT=true
```

- `LOG_FILE` 可使用相对路径或绝对路径。相对路径以启动命令的当前目录为基准。
- 活动文件达到约 10 MiB 后依次轮转为 `agent.jsonl.1` 至 `agent.jsonl.5`，最多占用约 60 MiB。
- 设置 `LOG_INCLUDE_TEXT=false` 后，不保存请求 `text`、回复 `reply` 和语音 `transcript`；动作参数中的业务字符串仍会保留。
- 设置 `LOG_ENABLED=false` 可完全关闭文件日志。
- 配置中的 MiniMax Key、Porcupine Key，以及名称包含 password、secret、token、authorization、api_key、access_key 的字段会被替换为 `[REDACTED]`。

HTTP 服务和语音监听 CLI 是两个独立进程。如果同时运行，请为它们设置不同的 `LOG_FILE`，避免两个进程同时轮转同一文件。

## 查看与筛选

实时查看：

```bash
tail -f logs/agent.jsonl
```

查找一个动作的全部事件：

```bash
python - <<'PY'
import json

target = "把实际的action_id放在这里"
with open("logs/agent.jsonl", encoding="utf-8") as file:
    for line in file:
        row = json.loads(line)
        if row.get("action_id") == target:
            print(json.dumps(row, ensure_ascii=False, indent=2))
PY
```

轮转文件需要从 `agent.jsonl.5` 到当前 `agent.jsonl` 一起读取，才能覆盖完整保留范围。日志超过轮转保留范围后会被删除，它不是永久归档数据库。

日志写入失败时程序会输出一条警告并继续运行，避免动作已经执行后因为日志错误而诱发客户端重试。此时应检查目录权限和磁盘空间。文件日志用于追踪和调试；当前不会从日志中恢复对话会话。
