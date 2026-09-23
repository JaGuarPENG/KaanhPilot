from __future__ import annotations

from dataclasses import dataclass
import threading
import unittest

from taskrunner.errors import ControllerFaultError, ControllerMonitorError
from taskrunner.monitor import ControllerMonitor


@dataclass
class FakeRobotState:
    activated: bool = True
    has_error: bool = False
    error_code: int | None = 0
    driver_error_codes: tuple[int, ...] = ()


class ControllerMonitorTests(unittest.TestCase):
    def _capture_fault(self, reader) -> Exception:
        event = threading.Event()
        captured: list[Exception] = []
        monitor = ControllerMonitor(reader, interval_s=0.001)

        def on_fault(error: Exception) -> None:
            captured.append(error)
            event.set()

        monitor.start(on_fault)
        self.assertTrue(event.wait(1.0), "监控没有报告故障")
        monitor.stop()
        return captured[0]

    def test_read_failure_is_monitor_error(self) -> None:
        def fail_reader():
            raise OSError("connection lost")

        error = self._capture_fault(fail_reader)
        self.assertIsInstance(error, ControllerMonitorError)

    def test_disabled_robot_is_fatal(self) -> None:
        error = self._capture_fault(lambda: FakeRobotState(activated=False))
        self.assertIsInstance(error, ControllerFaultError)

    def test_controller_error_is_fatal(self) -> None:
        error = self._capture_fault(
            lambda: FakeRobotState(has_error=True, error_code=42)
        )
        self.assertIsInstance(error, ControllerFaultError)

    def test_driver_error_is_fatal(self) -> None:
        error = self._capture_fault(
            lambda: FakeRobotState(driver_error_codes=(0, 17, 0))
        )
        self.assertIsInstance(error, ControllerFaultError)


if __name__ == "__main__":
    unittest.main()
