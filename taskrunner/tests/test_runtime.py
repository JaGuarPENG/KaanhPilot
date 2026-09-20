from __future__ import annotations

from dataclasses import dataclass
import math
import unittest

from taskrunner.runtime import INITIAL_JOINTS_DEG, validate_startup_baseline


@dataclass
class FakeRobotState:
    activated: bool = True
    has_error: bool = False
    error_code: int | None = 0
    driver_error_codes: tuple[int, ...] = ()
    moving: bool = False
    actual_joints_deg: tuple[float, ...] = INITIAL_JOINTS_DEG


@dataclass
class FakeAgvState:
    terminal_station: int = 4


class StartupBaselineTests(unittest.TestCase):
    def test_valid_baseline(self) -> None:
        validate_startup_baseline(FakeRobotState(), FakeAgvState())

    def test_rejects_joint_outside_tolerance(self) -> None:
        joints = list(INITIAL_JOINTS_DEG)
        joints[3] += 2.1
        with self.assertRaisesRegex(RuntimeError, "不在约定初始位"):
            validate_startup_baseline(
                FakeRobotState(actual_joints_deg=tuple(joints)),
                FakeAgvState(),
            )

    def test_rejects_disabled_or_faulted_robot(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "尚未使能"):
            validate_startup_baseline(
                FakeRobotState(activated=False),
                FakeAgvState(),
            )
        with self.assertRaisesRegex(RuntimeError, "控制器错误"):
            validate_startup_baseline(
                FakeRobotState(has_error=True, error_code=8),
                FakeAgvState(),
            )
        with self.assertRaisesRegex(RuntimeError, "驱动器错误"):
            validate_startup_baseline(
                FakeRobotState(driver_error_codes=(0, 12)),
                FakeAgvState(),
            )

    def test_rejects_wrong_agv_station(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "站点 4"):
            validate_startup_baseline(FakeRobotState(), FakeAgvState(5))

    def test_rejects_nonfinite_tolerance(self) -> None:
        with self.assertRaises(ValueError):
            validate_startup_baseline(
                FakeRobotState(),
                FakeAgvState(),
                joint_tolerance_deg=math.nan,
            )


if __name__ == "__main__":
    unittest.main()
