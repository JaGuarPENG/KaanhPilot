"""TaskRunner 识别测试环境的本地可视化入口。

HTTP 适配层只调用 TaskRunner 公共接口；它不读取队列、订单或 Worker 的
私有字段。页面只用于本地联调，不是正式产品前端或生产级设备网关。
"""

from __future__ import annotations

import argparse
from dataclasses import fields, is_dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

from taskrunner.errors import (
    OrderNotCancellableError,
    QueueFullError,
    RunnerBusyError,
    RunnerNotStartedError,
    RunnerStoppedError,
    TaskRunnerError,
    UnknownOrderError,
    UnsupportedBeverageError,
)
from taskrunner.runner import TaskRunner
from taskrunner.runtime import RecognitionTestRuntime, create_recognition_test_runtime


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = Path(__file__).resolve().parent / "ui"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def to_jsonable(value):
    """递归转换只读契约，保留枚举值和 Unix 时间戳。"""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def _error_status(error: Exception) -> int:
    if isinstance(error, UnknownOrderError):
        return 404
    if isinstance(error, (QueueFullError, OrderNotCancellableError)):
        return 409
    if isinstance(
        error,
        (UnsupportedBeverageError, RunnerNotStartedError, RunnerStoppedError, ValueError),
    ):
        return 400
    if isinstance(error, TaskRunnerError):
        return 409
    return 500


def handler_for(runner: TaskRunner):
    """创建只持有 TaskRunner 公共对象的请求处理器。"""

    class TaskRunnerRequestHandler(BaseHTTPRequestHandler):
        server_version = "TaskRunnerTestUI/1.0"

        def log_message(self, format_string: str, *args) -> None:
            print(f"[TaskRunner UI] {self.address_string()} - {format_string % args}")

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path == "/api/status":
                    return self._send_json(200, runner.get_status())
                if path == "/api/queue":
                    return self._send_json(200, runner.get_queue())
                if path.startswith("/api/orders/"):
                    order_id = unquote(path.removeprefix("/api/orders/"))
                    if not order_id or "/" in order_id:
                        return self._send_error_json(404, "接口不存在")
                    return self._send_json(200, runner.get_order(order_id))
                if path.startswith("/api/"):
                    return self._send_error_json(404, "接口不存在")
                static = STATIC_FILES.get(path)
                if static is None:
                    return self._send_error_json(404, "页面资源不存在")
                filename, content_type = static
                data = (UI_ROOT / filename).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except Exception as error:
                self._handle_exception(error)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path == "/api/orders":
                    body = self._read_json_body()
                    item_id = body.get("item_id") if isinstance(body, dict) else None
                    return self._send_json(201, runner.submit_beverage(item_id))
                prefix = "/api/orders/"
                suffix = "/cancel"
                if path.startswith(prefix) and path.endswith(suffix):
                    order_id = unquote(path[len(prefix) : -len(suffix)])
                    if not order_id or "/" in order_id:
                        return self._send_error_json(404, "接口不存在")
                    return self._send_json(200, runner.cancel_order(order_id))
                return self._send_error_json(404, "接口不存在")
            except Exception as error:
                self._handle_exception(error)

        def _read_json_body(self):
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("请求缺少 Content-Length")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise ValueError("Content-Length 无效") from error
            if length < 0 or length > 64 * 1024:
                raise ValueError("请求体大小无效")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("请求体必须是 UTF-8 JSON") from error

        def _handle_exception(self, error: Exception) -> None:
            status = _error_status(error)
            message = str(error) if status < 500 else "服务器内部错误"
            if status >= 500:
                print(f"[TaskRunner UI] 未处理异常: {error!r}")
            self._send_error_json(status, message, type(error).__name__)

        def _send_error_json(
            self,
            status: int,
            message: str,
            error_type: str | None = None,
        ) -> None:
            self._send_json(
                status,
                {"error": {"type": error_type or "HTTPError", "message": message}},
            )

        def _send_json(self, status: int, value) -> None:
            data = json.dumps(to_jsonable(value), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return TaskRunnerRequestHandler


class TaskRunnerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TaskRunner 识别测试可视化页面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--robot-ip", default="192.168.110.77")
    parser.add_argument("--camera", default="left")
    parser.add_argument("--queue-capacity", type=int, default=10)
    parser.add_argument("--joint-tolerance-deg", type=float, default=2.0)
    parser.add_argument("--monitor-interval", type=float, default=0.02)
    parser.add_argument("--stage-delay", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_holder: dict[str, RecognitionTestRuntime] = {}

    def on_fatal(error: Exception) -> None:
        print(f"[TaskRunner UI] Runner 致命故障: {error}")
        runtime = runtime_holder.get("runtime")
        if runtime is not None:
            threading.Thread(
                target=runtime.close,
                name="taskrunner-fault-cleanup",
                daemon=True,
            ).start()

    runtime = create_recognition_test_runtime(
        config_dir=args.config_dir,
        robot_ip=args.robot_ip,
        camera_name=args.camera,
        queue_capacity=args.queue_capacity,
        joint_tolerance_deg=args.joint_tolerance_deg,
        monitor_interval_s=args.monitor_interval,
        stage_delay_s=args.stage_delay,
        on_fatal=on_fatal,
    )
    runtime_holder["runtime"] = runtime
    server = TaskRunnerHTTPServer((args.host, args.port), handler_for(runtime.runner))

    try:
        runtime.runner.start()
        print(f"TaskRunner 测试页面: http://{args.host}:{server.server_port}")
        print("页面仅连接测试相机和模拟机器人；按 Ctrl+C 尝试安全退出。")
        while True:
            try:
                server.serve_forever(poll_interval=0.25)
                break
            except KeyboardInterrupt:
                try:
                    runtime.runner.shutdown()
                except RunnerBusyError as error:
                    print(f"无法退出: {error}；请在页面取消订单或等待完成。")
                    continue
                break
    finally:
        server.server_close()
        try:
            runtime.runner.shutdown()
        except RunnerBusyError:
            # 只有非预期服务器异常会走到这里；仍关闭外部资源，避免遗留连接。
            print("[TaskRunner UI] 服务异常结束，正在关闭测试资源。")
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
