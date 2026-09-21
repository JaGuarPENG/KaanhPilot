"""TaskRunner 正式硬件与识别测试环境的依赖组装。

本模块是组合根，不保存订单状态。正式运行时连接机器人、相机、灵巧手和
AGV；识别测试运行时连接测试相机与模拟机器人，明确不创建 AGV 或灵巧手。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import threading
import time
from typing import Callable, Iterable

from taskrunner.monitor import ControllerMonitor
from taskrunner.orders import HardwareBeverageOrderActions, TestRecognitionOrders
from taskrunner.runner import TaskRunner


INITIAL_JOINTS_DEG = (
    35.851, -70.067, -84.049, -72.301, -20.318, -54.485, -0.76,
    -31.083, 65.342, 91.197, 66.982, 13.656, 60.622, -38.768,
    0.0, 10.0, 45.0, -55.0, 0.0, 0.0,
)


def validate_robot_baseline(
    robot_state,
    *,
    initial_joints_deg: tuple[float, ...] = INITIAL_JOINTS_DEG,
    joint_tolerance_deg: float = 2.0,
) -> None:
    """验证机器人可安全开始执行任务，不包含任何 AGV 条件。"""

    if not math.isfinite(joint_tolerance_deg) or joint_tolerance_deg <= 0:
        raise ValueError("joint_tolerance_deg 必须大于 0")
    if not initial_joints_deg or not all(math.isfinite(v) for v in initial_joints_deg):
        raise ValueError("initial_joints_deg 必须包含有限数值")
    if robot_state is None:
        raise RuntimeError("无法读取机器人启动状态")
    if not bool(getattr(robot_state, "activated", False)):
        raise RuntimeError("机器人尚未使能")
    error_code = getattr(robot_state, "error_code", None)
    if bool(getattr(robot_state, "has_error", False)) or error_code not in (None, 0):
        raise RuntimeError(f"机器人存在控制器错误，错误码={error_code}")
    driver_codes = tuple(getattr(robot_state, "driver_error_codes", ()) or ())
    nonzero_driver_codes = tuple(code for code in driver_codes if code != 0)
    if nonzero_driver_codes:
        raise RuntimeError(f"机器人存在驱动器错误，错误码={nonzero_driver_codes}")
    if bool(getattr(robot_state, "moving", False)):
        raise RuntimeError("机器人仍在运动，不能启动任务队列")

    actual = getattr(robot_state, "actual_joints_deg", None)
    try:
        actual_joints = tuple(float(value) for value in actual)
    except (TypeError, ValueError, OverflowError) as error:
        raise RuntimeError("无法读取机器人实际关节角") from error
    if len(actual_joints) != len(initial_joints_deg) or not all(
        math.isfinite(value) for value in actual_joints
    ):
        raise RuntimeError(
            f"机器人实际关节角必须包含 {len(initial_joints_deg)} 个有限数值"
        )
    deviations = tuple(
        abs(actual_value - expected_value)
        for actual_value, expected_value in zip(actual_joints, initial_joints_deg)
    )
    if max(deviations, default=0.0) > joint_tolerance_deg:
        worst_index = max(range(len(deviations)), key=deviations.__getitem__)
        raise RuntimeError(
            "机器人不在约定初始位："
            f"关节 {worst_index + 1} 偏差 {deviations[worst_index]:.3f}°，"
            f"允许偏差 {joint_tolerance_deg:.3f}°"
        )


def validate_startup_baseline(
    robot_state,
    agv_state,
    *,
    initial_joints_deg: tuple[float, ...] = INITIAL_JOINTS_DEG,
    joint_tolerance_deg: float = 2.0,
    pickup_station_id: int = 4,
) -> None:
    """验证正式硬件运行时的机器人基线和 AGV 起始站点。"""

    validate_robot_baseline(
        robot_state,
        initial_joints_deg=initial_joints_deg,
        joint_tolerance_deg=joint_tolerance_deg,
    )
    if agv_state is None:
        raise RuntimeError("无法读取 AGV 启动状态")
    terminal_station = getattr(agv_state, "terminal_station", None)
    if terminal_station != pickup_station_id:
        raise RuntimeError(
            f"AGV 必须停在站点 {pickup_station_id}，当前终到站点={terminal_station}"
        )


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
    joint_tolerance_deg: float = 2.0,
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

        def readiness_check() -> None:
            validate_startup_baseline(
                robot.get_robot_state(),
                agv.get_state(),
                joint_tolerance_deg=joint_tolerance_deg,
            )

        runner = TaskRunner(
            actions,
            queue_capacity=queue_capacity,
            monitor=monitor,
            readiness_check=readiness_check,
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
    joint_tolerance_deg: float = 2.0,
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
        # pick_workflow = TestRecognitionWorkflow(
        #     robot=robot,
        #     robot_executor=robot_executor,
        #     snapshot_command=snapshot_command,
        # )
        actions = TestRecognitionOrders(
            # pick_workflow=pick_workflow,
            robot_executor=robot_executor,
            stage_delay_s=stage_delay_s,
        )
        monitor = ControllerMonitor(monitor_robot.get_robot_state, interval_s=monitor_interval_s)
        robot_executor.move_init_pose()

        def readiness_check() -> None:
            return  # 测试环境不验证机器人基线，避免模拟器与真实机器人差异导致的误报。
            # validate_robot_baseline(
            #     robot.get_robot_state(),
            #     joint_tolerance_deg=joint_tolerance_deg,
            # )

        runner = TaskRunner(
            actions,
            queue_capacity=queue_capacity,
            monitor=monitor,
            readiness_check=readiness_check,
            on_fatal=on_fatal,
        )
        return RecognitionTestRuntime(runner, robot, monitor_robot, camera, snapshot_command)
    except Exception:
        _close_resources((snapshot_command, camera, monitor_robot, robot))
        raise
