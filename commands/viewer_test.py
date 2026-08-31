"""2D可视化测试入口。"""

from __future__ import annotations

from pathlib import Path
import time
import numpy as np
from planner.camera_transform import TransformResult, CameraPoseInBase
from robot.robot_state import RobotState
from commands.setup import RobotConfig, RobotSetup
from visualization.viewer_2d import Viewer2D
from visualization.viewer_3d import Viewer3D
from visualization.viewer_robot import ViewerRobot, ViewerRobotState, SceneMarker, SceneCoordinateFrame

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"

class ViewerCommand:
    """G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

    def __init__(self, target: str = "oolong_tea"):
        self.config_path: Path = DEFAULT_CONFIG_PATH
        self.target = target
        self.robot_setup = RobotSetup(DEFAULT_CONFIG_PATH)
        self.robot_config: RobotConfig = self.robot_setup.get_robot_config()
        # self.robot = self.robot_setup.setup_robot()
        self.robot_monitor = self.robot_setup.setup_robot(port=5888)
        self.camera = self.robot_setup.setup_camera(0)
        self.detector = self.robot_setup.setup_detector()
        self.localization = self.robot_setup.setup_localizer()
        self.tracker = self.robot_setup.setup_tracker()
        self.warmup_seconds = self.robot_config.camera_warmup_seconds
        # self.bridge = self.robot_setup.setup_bridge(self.robot)
        self.session = self.robot_setup.setup_session(self.detector, self.target)
        self.cam_transform = self.robot_setup.setup_camera_transform()

    
    def viewer_2d_test(self) -> None:
        try:
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")

            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            
            viewer2d = Viewer2D()
            last_frame_id = 0
            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    viewer2d.update(observation, None)
                # 用户在 RGB/点云显示器按 Q 或关闭窗口时，同样结束完整 follower 会话。
                if viewer2d is not None and viewer2d.is_closed:
                    break
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            if viewer2d is not None:
                viewer2d.close()
            if self.camera is not None:
                self.camera.close()

        
    def viewer_3d_test(self) -> None:
        try:
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")
            self.session.warmup(observation=self.camera.get_latest_observation())
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            viewer2d = Viewer2D()
            viewer3d = Viewer3D()
            last_frame_id = 0
            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    result = self.session.process(observation)
                    # 同一 observation/result 同时送往控制桥和可视化，保证标记来自同一帧。
                    # 眼在手外
                    if viewer2d is not None:
                        viewer2d.update(observation, result)

                    if viewer3d is not None:
                        viewer3d.update(result)

                # 用户在 RGB/点云显示器按 Q 或关闭窗口时，同样结束完整 follower 会话。
                if viewer2d is not None and viewer2d.is_closed:
                    break
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            if viewer2d is not None:
                viewer2d.close()
            if viewer3d is not None:
                viewer3d.close()
            if self.camera is not None:
                self.camera.close()

    def viewer_all_test(self) -> None:
        try:
            if not self.robot_monitor.connect():
                raise RuntimeError("无法连接机器人 5888 监控端口")
            print(f"连接到机器人 5888 监控端口")
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")
            self.session.warmup(observation=self.camera.get_latest_observation())
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            viewer2d = Viewer2D()
            viewer3d = Viewer3D()
            viewer_robot = ViewerRobot()
            
            last_frame_id = 0
            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    robot_state = self.robot_monitor.get_robot_state()
                    rbt_pq = None if robot_state is None else robot_state.tcp_pq
                    result = self.session.process(observation)
                    transform_result = self.cam_transform.result2base(
                        result=result,
                        cam_index=1,
                        rbt_pq=rbt_pq
                    )

                    if viewer2d is not None:
                        viewer2d.update(observation, result)

                    if viewer3d is not None:
                        viewer3d.update(result)

                    if viewer_robot is not None:
                        viewer_robot.update(self._build_viewer_state(robot_state, transform_result))

                if viewer2d is not None and viewer2d.is_closed:
                    break
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass
        finally:
            if viewer2d is not None:
                viewer2d.close()
            if viewer3d is not None:
                viewer3d.close()
            if self.camera is not None:
                self.camera.close() 
            if viewer_robot is not None:
                viewer_robot.close()
            if self.robot_monitor is not None:
                self.robot_monitor.close()

    def _build_viewer_state(self, state: RobotState, result: TransformResult) -> ViewerRobotState:
        """将 5888 原始机器人状态与手眼外参适配为 Viewer 的通用输入。"""
        joints_rad = None
        if state.has_joints:
            # 控制器返回关节角为度，Robotics Toolbox 使用弧度。
            joints_rad = tuple(float(value) for value in np.deg2rad(state.joints_deg))

        markers: tuple[SceneMarker, ...] = ()
        coordinate_frames: tuple[SceneCoordinateFrame, ...] = ()
        if state.has_tcp_pq:
            tcp_pq = state.tcp_pq
            tcp_position_m = tuple(float(value) / 1000.0 for value in tcp_pq[:3])
            tcp_quaternion = tuple(float(value) for value in tcp_pq[3:])
            if result.target_point_base_m is not None:
                target_position_m = tuple(float(value) for value in result.target_point_base_m)
            else:
                target_position_m = (0.0, 0.0, 0.0)

            camera_pose = self.cam_transform.camera2base(cam_index=1, rbt_pq=tcp_pq)
            camera_position_m = camera_pose.position_m
            camera_quaternion = camera_pose.quaternion_xyzw


            coordinate_frames = (
                SceneCoordinateFrame("tcp", tcp_position_m, tcp_quaternion, 0.1),
                SceneCoordinateFrame("eye_in_hand_camera", camera_position_m, camera_quaternion, 0.1),
            )
            markers = (
                SceneMarker("tcp_origin", tcp_position_m, "TCP", "#0ff180", 55.0),
                SceneMarker("camera_origin", camera_position_m, "camera", "#3498db", 55.0),
                SceneMarker("target", target_position_m, "target", "#cc2e50", 65.0),
            )

        status = state.robot_status or "unknown"
        error = state.error_code if state.error_code is not None else 0
        return ViewerRobotState(
            joints_rad=joints_rad,
            markers=markers,
            coordinate_frames=coordinate_frames,
            status_text=f"robot monitor | status={status} | error={error}",
        )

if __name__ == "__main__":
    command = ViewerCommand(target="oolong_tea")
    command.viewer_3d_test()

