"""独立真机测试入口的依赖组装、启动基线和资源生命周期。

本模块是组合根，不包含订单状态规则。它复用项目既有的 RobotSetup、
Workflow、Command 和 Backend，把这些对象注入 TaskRunner。正式宿主未来也
可以参考这里的组装方式，但无需依赖 CLI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import threading
import time
from typing import Callable

from taskrunner.monitor import ControllerMonitor
from taskrunner.orders import HardwareBeverageOrderActions
from taskrunner.runner import TaskRunner


INITIAL_JOINTS_DEG = (
    # 与 RobotCommandExecutor.move_init_pose() 相同的 20 轴约定初始位。
    35.851,
    -70.067,
    -84.049,
    -72.301,
    -20.318,
    -54.485,
    -0.76,
    -31.083,
    65.342,
    91.197,
    66.982,
    13.656,
    60.622,
    -38.768,
    0.0,
    10.0,
    45.0,
    -55.0,
    0.0,
    0.0,
)


def validate_startup_baseline(
    robot_state,
    agv_state,
    *,
    initial_joints_deg: tuple[float, ...] = INITIAL_JOINTS_DEG,
    joint_tolerance_deg: float = 2.0,
    pickup_station_id: int = 4,
) -> None:
    """验证真机启动基线，不满足时拒绝启动 Worker。

    检查内容包括：机器人已使能且无控制器/驱动报警、机器人静止、20 个
    实际关节角位于初始位容差内，以及 AGV 已停在抓取站点。
    """

    if not math.isfinite(joint_tolerance_deg) or joint_tolerance_deg <= 0:
        raise ValueError("joint_tolerance_deg 必须大于 0")
    if not initial_joints_deg or not all(
        math.isfinite(value) for value in initial_joints_deg
    ):
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

    if agv_state is None:
        raise RuntimeError("无法读取 AGV 启动状态")
    terminal_station = getattr(agv_state, "terminal_station", None)
    if terminal_station != pickup_station_id:
        raise RuntimeError(
            f"AGV 必须停在站点 {pickup_station_id}，当前终到站点={terminal_station}"
        )


@dataclass(slots=True)
class HardwareRuntime:
    """真机 Runner 及其所有外部资源的生命周期容器。

    ``TaskRunner`` 本身不拥有硬件连接，所以宿主结束时必须额外调用
    ``close()``。该方法是幂等的，方便正常退出和异常清理共用。
    """

    runner: TaskRunner
    robot: object
    monitor_robot: object
    camera: object
    snapshot_command: object
    agv: object
    _closed: bool = field(default=False, init=False)
    _close_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def close(self) -> None:
        """按依赖反向顺序尽力关闭资源，可安全重复调用。"""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        for resource in (
            self.snapshot_command,
            self.camera,
            self.agv,
            self.monitor_robot,
            self.robot,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    print(f"[TaskRunner] 资源关闭失败: {error}")


def create_hardware_runtime(
    *,
    config_dir: Path,
    agv_ip: str = "192.168.110.93",
    agv_port: int = 9201,
    agv_device_id: int = 1,
    queue_capacity: int = 10,
    joint_tolerance_deg: float = 2.0,
    monitor_interval_s: float = 0.02,
    on_fatal: Callable[[Exception], None] | None = None,
) -> HardwareRuntime:
    """连接并组装真实机器人、监控端口、相机、检测器和 AGV。

    本函数完成资源创建和登录/使能，但不会启动 Runner；调用方拿到返回值后
    再调用 ``runtime.runner.start()``，此时会执行最终启动基线检查。
    任一步骤失败都会关闭已创建资源后重新抛出异常。
    """

    # 延迟导入真机依赖：普通业务导入 taskrunner 或运行模拟 CLI 时，不应加载
    # 相机 SDK、YOLO、Modbus 等重量级/设备相关模块。
    from commands.hand_commands import HandCommandExecutor
    from commands.robot_commands import RobotCommandExecutor
    from commands.setup import RobotSetup
    from commands.snapshot import SnapShotCommand
    from perception.roi_localizer import RoiPointCloudLocalizer
    from robot.agv_backend import AGVBackend
    from workflows.two_stage_pick_workflow import TwoStagePickWorkflow

    setup = RobotSetup(config_dir)
    config = setup.get_robot_config()
    robot = setup.setup_robot(config.robot.control_port)
    monitor_robot = setup.setup_robot(config.robot.monitor_port)
    camera = setup.setup_camera(0)
    agv = AGVBackend(
        ip=agv_ip,
        port=agv_port,
        device_id=agv_device_id,
        timeout=3.0,
    )
    snapshot_command = None

    try:
        # 控制端口只发送动作；监控端口只轮询状态，避免两个用途争用同一
        # WebSocket 的请求/响应顺序。
        if not robot.connect():
            raise RuntimeError("无法连接机器人控制端口")
        robot.login(config.robot.user, config.robot.password)
        robot.set_jog_coordinate()
        robot.manual_enable()
        robot.set_pgm_vel(70)
        robot.set_jog_vel(70)

        if not monitor_robot.connect():
            raise RuntimeError("无法连接机器人监控端口")
        monitor_robot.login(config.robot.user, config.robot.password)

        if not agv.connect():
            raise RuntimeError("无法连接 AGV 控制器")

        camera.start()
        time.sleep(config.camera_warmup_seconds)
        detector = setup.setup_detector()
        localizer = RoiPointCloudLocalizer(
            config.localization,
            collect_inspection=False,
        )
        snapshot_command = SnapShotCommand(
            robot=robot,
            camera=camera,
            detector=detector,
            localizer=localizer,
            camera_transform=setup.setup_camera_transform(),
            tracker_config=config.tracker,
            show_yolo_result=False,
            show_point_cloud_result=False,
            is_save=True,
        )
        snapshot_command.initialize_resources()

        # 现有执行器和 Workflow 保持原有分层，TaskRunner 只通过 actions
        # 适配器看到统一的四阶段任务接口。
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
        monitor = ControllerMonitor(
            monitor_robot.get_robot_state,
            interval_s=monitor_interval_s,
        )

        def readiness_check() -> None:
            # 每次 Runner 首次启动时读取最新状态，而不是使用组装阶段的旧值。
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
        return HardwareRuntime(
            runner=runner,
            robot=robot,
            monitor_robot=monitor_robot,
            camera=camera,
            snapshot_command=snapshot_command,
            agv=agv,
        )
    except Exception:
        # 组装可能在任意设备步骤失败；只清理已经成功创建的对象，并保留原始
        # 异常供上层显示，避免初始化失败留下相机或网络连接。
        for resource in (snapshot_command, camera, agv, monitor_robot, robot):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise
