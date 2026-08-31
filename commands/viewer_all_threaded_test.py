"""多线程综合可视化测试入口。

本文件基于 ``commands/viewer_test.py`` 的 ``viewer_all_test`` 改写，但不修改
旧文件。执行上下文如下：

    主线程                 ：只刷新 Robotics Toolbox 的 ViewerRobot（约 20 Hz）
    robot-monitor 线程     ：只读取 5888 状态（约 50 Hz）
    perception-worker 线程：相机新帧、YOLO、点云定位、2D/Open3D 显示

这样 YOLO 推理或一次较慢的 5888 网络响应不会阻塞 PyPlot 的鼠标事件，因此
机器人窗口可以更流畅地拖动和旋转。

本指令只连接 5888；不会连接 5999，也不会下发机器人运动或 follower 指令。
所有运行参数都在下方“用户配置区”修改，无需命令行参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time

import numpy as np

from commands.setup import RobotConfig, RobotSetup
from planner.camera_transform import CameraTransform, TransformResult
from robot.robot_state import RobotState
from visualization.viewer_2d import Viewer2D
from visualization.viewer_3d import Viewer3D
from visualization.viewer_robot import (
    SceneCoordinateFrame,
    SceneMarker,
    ViewerRobot,
    ViewerRobotConfig,
    ViewerRobotState,
)


# ============================================================================
# 用户配置区
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# 眼在手相机编号及希望识别的类别。当前手眼外参使用 eye_in_hand_cam1.json。
CAMERA_INDEX = 0
TARGET_ID = "oolong_tea"

# 两个后台线程的频率。机器人状态读取可以比三维窗口刷新更快；窗口只显示
# 最近状态，不会积压旧数据。
MONITOR_PERIOD_S = 0.02       # 50 Hz：5888 状态读取
PERCEPTION_IDLE_S = 0.002     # 没有新相机帧时的等待时间
ROBOT_VIEWER_HZ = 20.0        # 主线程 PyPlot 刷新频率

# 是否开启 2D 和 ROI 点云窗口。它们均不在 Robot Viewer 主线程中绘制。
SHOW_2D_VIEWER = True
SHOW_ROI_POINT_CLOUD = True

ROBOT_VIEWER_CONFIG = ViewerRobotConfig(
    title="Threaded Robot / Eye-in-hand Viewer | 5888 monitor only",
    x_limits_m=(-1.0, 1.5),
    y_limits_m=(-1.0, 1.0),
    z_limits_m=(0.0, 1.5),
)


@dataclass
class SharedDebugData:
    """两个后台线程向主线程发布的最新快照。

    这里始终覆盖旧数据而不排队：调试显示应优先展示“最新状态”，不能因为
    YOLO 或渲染变慢而产生延迟越来越大的历史帧队列。
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    robot_state: RobotState | None = None
    transform_result: TransformResult | None = None
    worker_error: BaseException | None = None

    def publish_robot_state(self, state: RobotState) -> None:
        with self.lock:
            self.robot_state = state

    def snapshot_robot_state(self) -> RobotState | None:
        with self.lock:
            return self.robot_state

    def publish_transform_result(self, result: TransformResult | None) -> None:
        with self.lock:
            self.transform_result = result

    def snapshot_for_viewer(self) -> tuple[RobotState | None, TransformResult | None, BaseException | None]:
        with self.lock:
            return self.robot_state, self.transform_result, self.worker_error

    def publish_error(self, error: BaseException) -> None:
        with self.lock:
            self.worker_error = error


class EyeInHandSceneAdapter:
    """将机器人状态、手眼变换和目标点适配为 ViewerRobot 的通用场景输入。

    ViewerRobot 自身不依赖 ``RobotState`` 或 ``CameraTransform``；这些业务
    依赖集中在此适配器中。后续 follower 调试命令可复用同一模式。
    """

    def __init__(self, camera_transform: CameraTransform, camera_index: int) -> None:
        self._camera_transform = camera_transform
        self._camera_index = camera_index

    def build(
        self,
        robot_state: RobotState | None,
        transform_result: TransformResult | None,
    ) -> ViewerRobotState:
        if robot_state is None or robot_state.raw is None:
            return ViewerRobotState(status_text="5888 monitor: waiting for robot state")

        joints_rad = None
        if robot_state.has_joints:
            joints_rad = tuple(float(value) for value in np.deg2rad(robot_state.joints_deg))

        markers: list[SceneMarker] = []
        coordinate_frames: list[SceneCoordinateFrame] = []
        if robot_state.has_tcp_pq:
            tcp_pq = robot_state.tcp_pq
            assert tcp_pq is not None
            tcp_position_m = tuple(float(value) / 1000.0 for value in tcp_pq[:3])
            tcp_quaternion = tuple(float(value) for value in tcp_pq[3:])
            camera_pose = self._camera_transform.camera2base(1, tcp_pq)

            coordinate_frames.extend((
                SceneCoordinateFrame("tcp", tcp_position_m, tcp_quaternion, 0.10),
                SceneCoordinateFrame(
                    "eye_in_hand_camera",
                    camera_pose.position_m,
                    camera_pose.quaternion_xyzw,
                    0.12,
                ),
            ))
            markers.extend((
                SceneMarker("tcp_origin", tcp_position_m, "TCP", "#f1c40f", 55.0),
                SceneMarker("camera_origin", camera_pose.position_m, "camera", "#3498db", 55.0),
            ))

        # 目标丢失或尚未产生有效基座系结果时，不传 target 图元；ViewerRobot
        # 会自动隐藏上一帧 target，而不是把假点画在世界原点。
        if transform_result is not None and transform_result.target_point_base_m is not None:
            markers.append(
                SceneMarker(
                    "target",
                    transform_result.target_point_base_m,
                    "target",
                    "#cc2e50",
                    65.0,
                )
            )

        status = robot_state.robot_status or "unknown"
        error = robot_state.error_code if robot_state.error_code is not None else 0
        return ViewerRobotState(
            joints_rad=joints_rad,
            markers=tuple(markers),
            coordinate_frames=tuple(coordinate_frames),
            status_text=f"5888 monitor | status={status} | error={error}",
        )


class ThreadedViewerAllCommand:
    """将状态监控与感知从 Robot Viewer 主线程剥离的综合测试命令。"""

    def __init__(self) -> None:
        self._setup = RobotSetup(CONFIG_DIR)
        self._config: RobotConfig = self._setup.get_robot_config()
        self._robot_monitor = self._setup.setup_robot(port=5888)
        self._camera = self._setup.setup_camera(CAMERA_INDEX)
        detector = self._setup.setup_detector()
        self._session = self._setup.setup_session(detector, TARGET_ID)
        self._camera_transform = self._setup.setup_camera_transform()
        self._scene_adapter = EyeInHandSceneAdapter(self._camera_transform, CAMERA_INDEX)

        self._stop_event = threading.Event()
        self._shared = SharedDebugData()
        self._monitor_thread: threading.Thread | None = None
        self._perception_thread: threading.Thread | None = None

    def run(self) -> None:
        """启动两个后台线程，并在主线程运行 Robot Viewer。"""
        viewer_robot: ViewerRobot | None = None
        viewer_2d: Viewer2D | None = None
        try:
            if not self._robot_monitor.connect():
                raise RuntimeError("无法连接机器人 5888 监控端口")
            self._robot_monitor.login(
                self._config.robot.user,
                self._config.robot.password,
            )
            print("[5888监控] 已连接；不会向机器人下发运动指令。")

            self._camera.start()
            time.sleep(self._config.camera_warmup_seconds)
            observation = self._camera.get_latest_observation()
            if observation is None:
                raise RuntimeError("相机预热后没有可用于模型预热的观测")
            self._session.warmup(observation)
            print(f"[感知] 已连接眼在手相机 {CAMERA_INDEX}，YOLO 预热完成。")

            if SHOW_2D_VIEWER:
                viewer_2d = Viewer2D()
            viewer_robot = ViewerRobot(ROBOT_VIEWER_CONFIG)

            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="robot-monitor-5888",
                daemon=True,
            )
            self._perception_thread = threading.Thread(
                target=self._perception_loop,
                args=(viewer_2d,),
                name="camera-yolo-perception",
                daemon=True,
            )
            self._monitor_thread.start()
            self._perception_thread.start()

            # 主线程不等待网络或 YOLO，只以固定频率绘制最新快照，从而保持
            # PyPlot 的鼠标拖动、缩放和旋转响应。
            display_period_s = 1.0 / ROBOT_VIEWER_HZ
            next_display_at = time.monotonic()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_display_at:
                    robot_state, transform_result, worker_error = self._shared.snapshot_for_viewer()
                    if worker_error is not None:
                        raise RuntimeError("后台线程异常") from worker_error
                    if not viewer_robot.update(self._scene_adapter.build(robot_state, transform_result)):
                        break
                    # 若一次绘制耗时过长，直接跳过过期刷新时刻，不能在下一轮
                    # 连续补画多帧而导致窗口交互进一步变慢。
                    next_display_at = now + display_period_s
                if viewer_2d is not None and viewer_2d.is_closed:
                    break
                time.sleep(0.002)
        except KeyboardInterrupt:
            print("\n[结束] 收到 Ctrl+C。")
        finally:
            self._stop_event.set()
            if self._monitor_thread is not None:
                self._monitor_thread.join(timeout=2.0)
            if self._perception_thread is not None:
                self._perception_thread.join(timeout=2.0)
            if viewer_2d is not None:
                viewer_2d.close()
            if viewer_robot is not None:
                viewer_robot.close()
            self._camera.close()
            self._robot_monitor.close()
            print("[结束] 已关闭监控、感知和全部显示窗口。")

    def _monitor_loop(self) -> None:
        """后台线程 1：独占 5888 WebSocket，持续发布最新 RobotState。"""
        try:
            while not self._stop_event.is_set():
                state = self._robot_monitor.get_robot_state()
                self._shared.publish_robot_state(state)
                self._stop_event.wait(MONITOR_PERIOD_S)
        except BaseException as error:
            self._shared.publish_error(error)
            self._stop_event.set()

    def _perception_loop(self, viewer_2d: Viewer2D | None) -> None:
        """后台线程 2：处理最新相机帧，并在本线程维护 Open3D 点云窗口。"""
        viewer_3d: Viewer3D | None = None
        last_frame_id = 0
        try:
            if SHOW_ROI_POINT_CLOUD:
                # Open3D 要求 create/update/destroy 在同一线程，因此在这里创建。
                viewer_3d = Viewer3D()
            while not self._stop_event.is_set():
                observation = self._camera.get_latest_observation()
                if observation is None or observation.frame_id == last_frame_id:
                    self._stop_event.wait(PERCEPTION_IDLE_S)
                    continue

                last_frame_id = observation.frame_id
                # 在推理前拿一个状态快照；它比“推理完成后再读取状态”更接近
                # 当前相机帧时刻，适合眼在手目标点转换。
                state_at_capture = self._shared.snapshot_robot_state()
                result = self._session.process(observation)

                transform_result: TransformResult | None = None
                if state_at_capture is not None and state_at_capture.has_tcp_pq:
                    assert state_at_capture.tcp_pq is not None
                    transform_result = self._camera_transform.result2base(
                        result=result,
                        cam_index=CAMERA_INDEX,
                        rbt_pq=state_at_capture.tcp_pq,
                    )
                self._shared.publish_transform_result(transform_result)

                # Viewer2D 内部有容量为一帧的线程安全队列，感知线程可直接提交。
                if viewer_2d is not None:
                    viewer_2d.update(observation, result)
                if viewer_3d is not None and not viewer_3d.update(result):
                    self._stop_event.set()
                    break
        except BaseException as error:
            self._shared.publish_error(error)
            self._stop_event.set()
        finally:
            if viewer_3d is not None:
                viewer_3d.close()


if __name__ == "__main__":
    ThreadedViewerAllCommand().run()
