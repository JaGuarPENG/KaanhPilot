"""TaskRunner 正式硬件与识别测试环境的依赖组装。

本模块是组合根，不保存订单状态。正式运行时连接机器人、相机、灵巧手和
AGV；识别测试运行时连接测试相机与模拟机器人，明确不创建 AGV 或灵巧手。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Callable, Iterable

from taskrunner.monitor import ControllerMonitor
from taskrunner.orders import HardwareBeverageOrderActions, TestRecognitionOrders
from taskrunner.runner import TaskRunner


def _close_resources(resources: Iterable[object | None]) -> None:
    """按调用方给出的反向依赖顺序尽力关闭资源。"""

    for resource in resources:
        close = getattr(resource, "close", None)
        if callable(close):
            try:
                close()
            except Exception as error:
                print(f"[TaskRunner] 资源关闭失败: {error}")


@dataclass(slots=True)
class HardwareRuntime:
    """正式 Runner 及其机器人、相机、监控和 AGV 资源。"""

    runner: TaskRunner
    robot: object
    monitor_robot: object
    camera: object
    snapshot_command: object
    agv: object
    _closed: bool = field(default=False, init=False)
    _close_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        _close_resources(
            (self.snapshot_command, self.camera, self.agv, self.monitor_robot, self.robot)
        )


@dataclass(slots=True)
class RecognitionTestRuntime:
    """识别测试 Runner 及其模拟机器人和测试相机资源。"""

    runner: TaskRunner
    robot: object
    monitor_robot: object
    camera: object
    snapshot_command: object
    _closed: bool = field(default=False, init=False)
    _close_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def close(self) -> None:
        """关闭测试实际创建的资源；这里不存在 AGV 或灵巧手资源。"""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        _close_resources((self.snapshot_command, self.camera, self.monitor_robot, self.robot))


def _connect_robot(robot, config, *, label: str, enable: bool) -> None:
    if not robot.connect():
        raise RuntimeError(f"无法连接机器人{label}")
    robot.login(config.robot.user, config.robot.password)
    if enable:
        robot.set_jog_coordinate()
        robot.manual_enable()
        robot.set_pgm_vel(70)
        robot.set_jog_vel(70)


def create_hardware_runtime(
    *,
    config_dir: Path,
    camera_name: str = "left",
    agv_ip: str = "192.168.110.93",
    agv_port: int = 9201,
    agv_device_id: int = 1,
    queue_capacity: int = 10,
    monitor_interval_s: float = 0.02,
    on_fatal: Callable[[Exception], None] | None = None,
) -> HardwareRuntime:
    """组装正式机器人、相机、灵巧手、AGV 和完整抓取 Workflow。"""

    from commands.hand_commands import HandCommandExecutor
    from commands.robot_commands import RobotCommandExecutor
    from commands.setup import RobotSetup
    from commands.snapshot import SnapShotCommand
    from perception.roi_localizer import RoiPointCloudLocalizer
    from robot.agv_backend import AGVBackend
    from workflows.two_stage_pick_workflow import TwoStagePickWorkflow

    setup = RobotSetup(config_dir)
    config = setup.get_robot_config()
    camera_settings = setup.get_camera_settings(camera_name)
    robot = setup.setup_robot(config.robot.control_port)
    monitor_robot = setup.setup_robot(config.robot.monitor_port)
    camera = setup.setup_camera(camera_name)
    agv = AGVBackend(ip=agv_ip, port=agv_port, device_id=agv_device_id, timeout=3.0)
    snapshot_command = None

    try:
        _connect_robot(robot, config, label="控制端口", enable=True)
        _connect_robot(monitor_robot, config, label="监控端口", enable=False)
        if not agv.connect():
            raise RuntimeError("无法连接 AGV 控制器")
        camera.start()
        time.sleep(camera_settings.warmup_seconds)
        detector = setup.setup_detector()
        localizer = RoiPointCloudLocalizer(config.localization, collect_inspection=False)
        snapshot_command = SnapShotCommand(
            robot=robot,
            camera=camera,
            detector=detector,
            localizer=localizer,
            camera_transform=setup.setup_camera_transform(),
            tracker_config=config.tracker,
            camera_extrinsic_index=camera_settings.extrinsic_index,
            show_yolo_result=False,
            show_point_cloud_result=False,
            is_save=True,
        )
        snapshot_command.initialize_resources()

        robot_executor = RobotCommandExecutor(robot)
        hand_executor = HandCommandExecutor(robot)
        hand_executor.reinitialize(15)
        hand_executor.prepare(15)
        pick_workflow = TwoStagePickWorkflow(
            robot=robot,
            robot_executor=robot_executor,
            hand_executor=hand_executor,
            snapshot_command=snapshot_command,
        )
        actions = HardwareBeverageOrderActions(
            pick_workflow=pick_workflow,
            robot_executor=robot_executor,
            hand_executor=hand_executor,
            agv=agv,
        )
        monitor = ControllerMonitor(monitor_robot.get_robot_state, interval_s=monitor_interval_s)

        runner = TaskRunner(
            actions,
            queue_capacity=queue_capacity,
            monitor=monitor,
            on_fatal=on_fatal,
        )
        return HardwareRuntime(runner, robot, monitor_robot, camera, snapshot_command, agv)
    except Exception:
        _close_resources((snapshot_command, camera, agv, monitor_robot, robot))
        raise


def create_recognition_test_runtime(
    *,
    config_dir: Path,
    robot_ip: str = "192.168.110.77",
    camera_name: str = "left",
    queue_capacity: int = 10,
    monitor_interval_s: float = 0.02,
    stage_delay_s: float = 3.0,
    on_fatal: Callable[[Exception], None] | None = None,
) -> RecognitionTestRuntime:
    """组装测试相机、模拟机器人和 ``TestRecognitionOrders``。

    此路径不会导入或构造 AGV Backend 和 HandCommandExecutor。机器人连接
    仍使用正式 Backend，但目标 IP 默认为模拟控制器 ``192.168.110.77``。
    """

    from commands.robot_commands import RobotCommandExecutor
    from commands.setup import RobotSetup
    from commands.snapshot import SnapShotCommand
    from perception.roi_localizer import RoiPointCloudLocalizer
    from robot.kaanh_backend import KaanhRobotBackend
    from workflows.test_recognition_workflow import TestRecognitionWorkflow

    setup = RobotSetup(config_dir)
    config = setup.get_robot_config()
    camera_settings = setup.get_camera_settings(camera_name)
    robot = KaanhRobotBackend(
        robot_ip,
        config.robot.control_port,
        config.robot.udp_port,
        timeout=config.robot.timeout_s,
    )
    monitor_robot = KaanhRobotBackend(
        robot_ip,
        config.robot.monitor_port,
        config.robot.udp_port,
        timeout=config.robot.timeout_s,
    )
    camera = setup.setup_camera(camera_name)
    snapshot_command = None

    try:
        _connect_robot(robot, config, label="模拟控制端口", enable=True)
        _connect_robot(monitor_robot, config, label="模拟监控端口", enable=False)
        camera.start()
        time.sleep(camera_settings.warmup_seconds)
        detector = setup.setup_detector()
        localizer = RoiPointCloudLocalizer(config.localization, collect_inspection=True)
        snapshot_command = SnapShotCommand(
            robot=robot,
            camera=camera,
            detector=detector,
            localizer=localizer,
            camera_transform=setup.setup_camera_transform(),
            tracker_config=config.tracker,
            camera_extrinsic_index=camera_settings.extrinsic_index,
            show_yolo_result=True,
            show_point_cloud_result=True,
            is_save=False,
        )
        snapshot_command.initialize_resources()

        robot_executor = RobotCommandExecutor(robot)
        pick_workflow = TestRecognitionWorkflow(
            robot=robot,
            robot_executor=robot_executor,
            snapshot_command=snapshot_command,
        )
        actions = TestRecognitionOrders(
            pick_workflow=pick_workflow,
            robot_executor=robot_executor,
            stage_delay_s=stage_delay_s,
        )
        monitor = ControllerMonitor(monitor_robot.get_robot_state, interval_s=monitor_interval_s)
        robot_executor.move_init_pose()

        runner = TaskRunner(
            actions,
            queue_capacity=queue_capacity,
            monitor=monitor,
            on_fatal=on_fatal,
        )
        return RecognitionTestRuntime(runner, robot, monitor_robot, camera, snapshot_command)
    except Exception:
        _close_resources((snapshot_command, camera, monitor_robot, robot))
        raise
