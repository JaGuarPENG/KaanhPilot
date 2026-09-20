"""TaskRunner 的长时运行交互式测试入口。

CLI 只调用 TaskRunner 已有公共能力，不向核心模块添加调试专用状态或命令。
默认使用模拟动作；只有显式传入 ``--real`` 才会连接真实设备。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import queue
import shlex
import sys
import threading
import time
from typing import Callable, TextIO

from taskrunner.errors import RunnerBusyError, TaskRunnerError
from taskrunner.runner import TaskRunner
from taskrunner.runtime import HardwareRuntime, create_hardware_runtime
from taskrunner.taskrunner_contracts import RobotTaskType, TaskExecutionResult


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SimulatedActions:
    """CLI 专用的最小模拟动作，不向核心 Runner 注入测试分支。"""

    def __init__(self, task_delay_s: float = 0.1) -> None:
        self._task_delay_s = task_delay_s

    def execute(
        self, task_type: RobotTaskType, *, item_id: str, target_id: str
    ) -> TaskExecutionResult:
        if self._task_delay_s > 0:
            time.sleep(self._task_delay_s)
        return TaskExecutionResult.succeeded(
            f"模拟完成 {task_type.value}: {item_id} -> {target_id}"
        )

    def cancel_paused_pick(self) -> None:
        return None


def _json_default(value):
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    raise TypeError(f"无法序列化 {type(value).__name__}")


def format_snapshot(snapshot) -> str:
    """把不可变快照格式化成便于人工检查的中文兼容 JSON。"""

    return json.dumps(
        asdict(snapshot),
        ensure_ascii=False,
        indent=2,
        default=_json_default,
    )


class CommandLoop:
    """解析一行命令，并把它映射到 TaskRunner 公共接口。"""

    def __init__(self, runner: TaskRunner, output: TextIO = sys.stdout) -> None:
        self._runner = runner
        self._output = output

    def handle(self, line: str) -> bool:
        """执行一行命令；仅当 ``quit`` 成功关闭 Runner 时返回 ``False``。"""

        try:
            parts = shlex.split(line)
        except ValueError as error:
            self._write(f"命令格式错误: {error}")
            return True
        if not parts:
            return True

        command = parts[0].lower()
        try:
            if command == "help":
                self._write(
                    "命令: submit <water|cola|oolong_tea> | queue | "
                    "status <order_id> | cancel <order_id> | quit | help"
                )
            elif command == "submit" and len(parts) == 2:
                self._write(format_snapshot(self._runner.submit_beverage(parts[1])))
            elif command == "queue" and len(parts) == 1:
                self._write(format_snapshot(self._runner.get_queue()))
            elif command == "status" and len(parts) == 2:
                self._write(format_snapshot(self._runner.get_order(parts[1])))
            elif command == "cancel" and len(parts) == 2:
                self._write(format_snapshot(self._runner.cancel_order(parts[1])))
            elif command == "quit" and len(parts) == 1:
                self._runner.shutdown()
                return False
            else:
                self._write("未知命令或参数数量错误；输入 help 查看用法")
        except (TaskRunnerError, ValueError) as error:
            self._write(f"操作失败: {error}")
        return True

    def _write(self, message: str) -> None:
        print(message, file=self._output, flush=True)


def _start_input_reader(
    lines: queue.Queue[str | None],
    input_func: Callable[[str], str],
) -> threading.Thread:
    """在守护线程中阻塞读取终端，避免输入等待阻塞故障退出检查。"""

    def read_forever() -> None:
        while True:
            try:
                lines.put(input_func("taskrunner> "))
            except EOFError:
                lines.put(None)
                return

    thread = threading.Thread(
        target=read_forever,
        name="taskrunner-cli-input",
        daemon=True,
    )
    thread.start()
    return thread


def run_interactive(
    runner: TaskRunner,
    *,
    fatal_event: threading.Event,
    fatal_errors: list[Exception],
    input_func: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
) -> int:
    """持续处理终端命令，直到安全退出、EOF 或致命故障。"""

    loop = CommandLoop(runner, output)
    lines: queue.Queue[str | None] = queue.Queue()
    _start_input_reader(lines, input_func)
    print("TaskRunner 已启动；输入 help 查看命令。", file=output, flush=True)
    eof_received = False

    while not fatal_event.is_set():
        if eof_received:
            # EOF 后不再接收命令，但也不能粗暴关闭正在运动的机器人；等待
            # 当前订单自然结束，直到 shutdown 不再报告 busy。
            try:
                runner.shutdown()
                return 0
            except RunnerBusyError:
                time.sleep(0.1)
                continue
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            eof_received = True
            print("输入已结束，等待当前订单安全完成后退出。", file=output, flush=True)
            continue
        if not loop.handle(line):
            return 0

    error = fatal_errors[0] if fatal_errors else "未知致命故障"
    print(f"TaskRunner 因致命故障停止: {error}", file=output, flush=True)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """定义启动配置；交互命令本身在进程启动后持续输入。"""

    parser = argparse.ArgumentParser(description="TaskRunner 交互式测试入口")
    parser.add_argument(
        "--real",
        action="store_true",
        help="连接真实机器人、相机和 AGV；默认使用模拟动作",
    )
    parser.add_argument("--queue-capacity", type=int, default=10)
    parser.add_argument("--fake-task-delay", type=float, default=0.1)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--agv-ip", default="192.168.110.93")
    parser.add_argument("--agv-port", type=int, default=9201)
    parser.add_argument("--agv-device-id", type=int, default=1)
    parser.add_argument("--joint-tolerance-deg", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    """组装模拟或真机 Runner，启动交互循环并统一清理资源。"""

    args = build_parser().parse_args(argv)
    fatal_event = threading.Event()
    fatal_errors: list[Exception] = []

    def on_fatal(error: Exception) -> None:
        fatal_errors.append(error)
        fatal_event.set()

    hardware_runtime: HardwareRuntime | None = None
    if args.real:
        hardware_runtime = create_hardware_runtime(
            config_dir=args.config_dir,
            agv_ip=args.agv_ip,
            agv_port=args.agv_port,
            agv_device_id=args.agv_device_id,
            queue_capacity=args.queue_capacity,
            joint_tolerance_deg=args.joint_tolerance_deg,
            on_fatal=on_fatal,
        )
        runner = hardware_runtime.runner
    else:
        if args.fake_task_delay < 0:
            raise ValueError("--fake-task-delay 不能为负数")
        runner = TaskRunner(
            SimulatedActions(args.fake_task_delay),
            queue_capacity=args.queue_capacity,
            on_fatal=on_fatal,
        )

    try:
        runner.start()
        return run_interactive(
            runner,
            fatal_event=fatal_event,
            fatal_errors=fatal_errors,
        )
    finally:
        try:
            runner.shutdown()
        except RunnerBusyError:
            pass
        if hardware_runtime is not None:
            hardware_runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
