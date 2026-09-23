from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from taskrunner.runtime import create_hardware_runtime, create_recognition_test_runtime


@dataclass
class FakeRobotState:
    activated: bool = True
    has_error: bool = False
    error_code: int | None = 0
    driver_error_codes: tuple[int, ...] = ()
    moving: bool = False


class HardwareRuntimeFactoryTests(unittest.TestCase):
    def make_runtime(self, *, fail_head: bool = False):
        robots = []
        cameras = {}
        agvs = []
        snapshots = []

        class FakeRobot:
            def __init__(self, port):
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
            def __init__(self, name):
                self.name = name
                self.started = False
                self.closed = False

            def start(self):
                if self.name == "head" and fail_head:
                    raise RuntimeError("head camera unavailable")
                self.started = True

            def close(self):
                self.closed = True

        class FakeAgv:
            def __init__(self, **_kwargs):
                self.closed = False
                agvs.append(self)

            def connect(self):
                return True

            def close(self):
                self.closed = True

        class FakeSetup:
            def __init__(self, _config_dir):
                pass

            def get_robot_config(self):
                return SimpleNamespace(
                    robot=SimpleNamespace(control_port=5999, monitor_port=5888,
                                          user="Engineer", password="000000"),
                    localization=object(), tracker=object(),
                )

            def setup_robot(self, port):
                return FakeRobot(port)

            def setup_camera(self, name):
                camera = FakeCamera(name)
                cameras[name] = camera
                return camera

            def get_camera_settings(self, name):
                return SimpleNamespace(warmup_seconds=0, extrinsic_index=1 if name == "left" else 0)

            def setup_detector(self):
                return object()

            def setup_camera_transform(self):
                return object()

        class FakeSnapshot:
            def __init__(self, **kwargs):
                self.camera = kwargs["camera"]
                self.closed = False
                snapshots.append(self)

            def initialize_resources(self):
                pass

            def close(self):
                self.closed = True

        class FakeExecutor:
            def __init__(self, _robot):
                pass

            def move_init_pose(self):
                pass

        class FakeHand:
            def __init__(self, _robot):
                pass

            def reinitialize(self, _hand_id):
                pass

            def prepare(self, _hand_id):
                pass

        class FakeWorkflow:
            def __init__(self, **_kwargs):
                pass

        class FakeLocalizer:
            def __init__(self, *_args, **_kwargs):
                pass

        def module(name, **members):
            result = ModuleType(name)
            for key, value in members.items():
                setattr(result, key, value)
            return result

        fake_modules = {
            "commands.hand_commands": module("commands.hand_commands", HandCommandExecutor=FakeHand),
            "commands.robot_commands": module("commands.robot_commands", RobotCommandExecutor=FakeExecutor),
            "commands.setup": module("commands.setup", RobotSetup=FakeSetup),
            "commands.snapshot": module("commands.snapshot", SnapShotCommand=FakeSnapshot),
            "perception.roi_localizer": module("perception.roi_localizer", RoiPointCloudLocalizer=FakeLocalizer),
            "robot.agv_backend": module("robot.agv_backend", AGVBackend=FakeAgv),
            "workflows.two_stage_pick_workflow": module(
                "workflows.two_stage_pick_workflow", TwoStagePickWorkflow=FakeWorkflow
            ),
        }
        with patch.dict(sys.modules, fake_modules):
            if fail_head:
                with self.assertRaisesRegex(RuntimeError, "head camera unavailable"):
                    create_hardware_runtime(config_dir=SimpleNamespace())
                runtime = None
            else:
                runtime = create_hardware_runtime(config_dir=SimpleNamespace())
        return runtime, robots, cameras, agvs, snapshots

    def test_starts_head_and_left_and_closes_both(self) -> None:
        runtime, robots, cameras, agvs, snapshots = self.make_runtime()
        self.assertEqual(set(cameras), {"left", "head"})
        self.assertTrue(all(camera.started for camera in cameras.values()))
        self.assertIs(runtime.camera, cameras["left"])
        self.assertIs(runtime.cameras["head"], cameras["head"])
        self.assertIs(snapshots[0].camera, cameras["left"])

        runtime.close()
        self.assertTrue(all(camera.closed for camera in cameras.values()))
        self.assertTrue(all(robot.closed for robot in robots))
        self.assertTrue(agvs[0].closed)
        self.assertTrue(snapshots[0].closed)

    def test_head_start_failure_closes_created_resources(self) -> None:
        _, robots, cameras, agvs, snapshots = self.make_runtime(fail_head=True)
        self.assertEqual(set(cameras), {"left", "head"})
        self.assertTrue(all(camera.closed for camera in cameras.values()))
        self.assertTrue(all(robot.closed for robot in robots))
        self.assertTrue(agvs[0].closed)
        self.assertEqual(snapshots, [])


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

            def move_init_pose(self):
                pass

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
