from __future__ import annotations

from dataclasses import dataclass
import math
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from taskrunner.runtime import (
    INITIAL_JOINTS_DEG,
    create_recognition_test_runtime,
    validate_robot_baseline,
    validate_startup_baseline,
)


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
    def test_recognition_runtime_baseline_does_not_require_agv(self) -> None:
        validate_robot_baseline(FakeRobotState())

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


class RecognitionRuntimeFactoryTests(unittest.TestCase):
    def test_factory_uses_simulated_robot_and_never_imports_agv_or_hand(self) -> None:
        robots = []

        class FakeRobot:
            def __init__(self, ip, port, udp_port, timeout):
                self.ip = ip
                self.port = port
                self.closed = False
                robots.append(self)

            def connect(self):
                return True

            def login(self, *_args):
                pass

            def set_jog_coordinate(self):
                pass

            def manual_enable(self):
                pass

            def set_pgm_vel(self, _value):
                pass

            def set_jog_vel(self, _value):
                pass

            def get_robot_state(self):
                return FakeRobotState()

            def close(self):
                self.closed = True

        class FakeCamera:
            def __init__(self):
                self.started = False
                self.closed = False

            def start(self):
                self.started = True

            def close(self):
                self.closed = True

        camera = FakeCamera()
        config = SimpleNamespace(
            robot=SimpleNamespace(
                control_port=5999,
                monitor_port=5888,
                udp_port=9998,
                timeout_s=1.0,
                user="Engineer",
                password="000000",
            ),
            localization=object(),
            tracker=object(),
        )

        class FakeSetup:
            def __init__(self, _config_dir):
                pass

            def get_robot_config(self):
                return config

            def get_camera_settings(self, name):
                self.camera_name = name
                return SimpleNamespace(warmup_seconds=0, extrinsic_index=1)

            def setup_camera(self, _name):
                return camera

            def setup_detector(self):
                return object()

            def setup_camera_transform(self):
                return object()

        class FakeSnapshot:
            def __init__(self, **_kwargs):
                self.initialized = False
                self.closed = False

            def initialize_resources(self):
                self.initialized = True

            def close(self):
                self.closed = True

        class FakeExecutor:
            def __init__(self, robot):
                self.robot = robot

        class FakeWorkflow:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeLocalizer:
            def __init__(self, *_args, **_kwargs):
                pass

        def module(name, **members):
            result = ModuleType(name)
            for key, value in members.items():
                setattr(result, key, value)
            return result

        fake_modules = {
            "commands.robot_commands": module(
                "commands.robot_commands", RobotCommandExecutor=FakeExecutor
            ),
            "commands.setup": module("commands.setup", RobotSetup=FakeSetup),
            "commands.snapshot": module("commands.snapshot", SnapShotCommand=FakeSnapshot),
            "perception.roi_localizer": module(
                "perception.roi_localizer", RoiPointCloudLocalizer=FakeLocalizer
            ),
            "robot.kaanh_backend": module(
                "robot.kaanh_backend", KaanhRobotBackend=FakeRobot
            ),
            "workflows.test_recognition_workflow": module(
                "workflows.test_recognition_workflow",
                TestRecognitionWorkflow=FakeWorkflow,
            ),
        }
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in {"robot.agv_backend", "commands.hand_commands"}:
                raise AssertionError(f"识别测试运行时不应导入 {name}")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, fake_modules), patch(
            "builtins.__import__", side_effect=guarded_import
        ):
            runtime = create_recognition_test_runtime(
                config_dir=SimpleNamespace(),
                robot_ip="192.168.110.77",
                stage_delay_s=0,
            )

        self.assertEqual([robot.ip for robot in robots], ["192.168.110.77"] * 2)
        self.assertEqual([robot.port for robot in robots], [5999, 5888])
        self.assertTrue(camera.started)
        self.assertFalse(hasattr(runtime, "agv"))
        runtime.close()
        self.assertTrue(camera.closed)
        self.assertTrue(all(robot.closed for robot in robots))

if __name__ == "__main__":
    unittest.main()
