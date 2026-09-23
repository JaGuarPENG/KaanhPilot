"""通过独立控制器连接持续监控机器人致命状态。"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from taskrunner.errors import ControllerFaultError, ControllerMonitorError


class RobotStateReader(Protocol):
    """返回最新 ``RobotState`` 的可调用对象协议。"""

    def __call__(self): ...


class ControllerMonitor:
    """在后台线程中持续校验控制器状态。

    本类不拥有控制器连接，也不负责退出进程。发现故障后只调用一次
    ``on_fault``；连接清理和进程退出由宿主负责。
    """

    def __init__(self, state_reader: RobotStateReader, interval_s: float = 0.02) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s 必须大于 0")
        self._state_reader = state_reader
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_fault: Callable[[Exception], None]) -> None:
        """启动守护监控线程；重复启动活动线程会被拒绝。"""

        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("控制器监控已经启动")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(on_fault,),
            name="taskrunner-controller-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """请求监控线程停止，并在非监控线程中等待其退出。"""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _run(self, on_fault: Callable[[Exception], None]) -> None:
        """监控循环；读取失败和状态故障统一进入致命回调。"""

        try:
            while not self._stop_event.is_set():
                try:
                    state = self._state_reader()
                except Exception as error:
                    raise ControllerMonitorError(
                        f"读取机器人监控状态失败: {error}"
                    ) from error
                self._validate_state(state)
                self._stop_event.wait(self._interval_s)
        except Exception as error:
            if not self._stop_event.is_set():
                self._stop_event.set()
                on_fault(error)

    @staticmethod
    def _validate_state(state) -> None:
        """把控制器状态中的致命条件转换为可分类异常。"""

        if state is None:
            raise ControllerMonitorError("机器人监控返回空状态")
        if not bool(getattr(state, "activated", False)):
            raise ControllerFaultError("机器人已掉使能")
        error_code = getattr(state, "error_code", None)
        driver_codes = tuple(getattr(state, "driver_error_codes", ()) or ())
        if bool(getattr(state, "has_error", False)) or error_code not in (None, 0):
            raise ControllerFaultError(f"控制器错误，错误码={error_code}")
        nonzero_driver_codes = tuple(code for code in driver_codes if code != 0)
        if nonzero_driver_codes:
            raise ControllerFaultError(f"驱动器错误码={nonzero_driver_codes}")
