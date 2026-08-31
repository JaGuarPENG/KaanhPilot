"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

from pathlib import Path
import time
from commands.setup import RobotConfig, RobotSetup
from visualization.viewer_2d import Viewer2D
from visualization.viewer_3d import Viewer3D
from planner.target_position_filter import TargetPositionFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"

class YoloFollowerCommand:
    """G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

    def __init__(self, target):
        self.config_path: Path = DEFAULT_CONFIG_PATH
        self.target = target
        self.robot_setup = RobotSetup(DEFAULT_CONFIG_PATH)
        self.robot_config: RobotConfig = self.robot_setup.get_robot_config()
        self.robot = self.robot_setup.setup_robot(5999)
        self.camera = self.robot_setup.setup_camera(0)
        self.detector = self.robot_setup.setup_detector()
        self.localization = self.robot_setup.setup_localizer()
        self.tracker = self.robot_setup.setup_tracker()
        self.warmup_seconds = self.robot_config.camera_warmup_seconds
        self.position_filter = TargetPositionFilter(alpha=0.1, jump_threshold_m=0.1, dead_zone_m=0.002)
        self.bridge = self.robot_setup.setup_bridge(self.robot, position_filter=self.position_filter)
        self.session = self.robot_setup.setup_session(self.detector, self.target)
        self.cam_transform = self.robot_setup.setup_camera_transform()

    
    def yolo_follower_main(self) -> None:
        try:
            if not self.robot.connect():
                raise RuntimeError("无法连接虚拟控制器")
            self.robot.login(
                str(self.robot_config.robot.user), 
                str(self.robot_config.robot.password))
            # self.robot.set_tool(tool_id=1)
            self.robot.set_jog_coordinate()
            self.robot.manual_enable()
            self.robot.set_pgm_vel(20)
            print(f"连接到虚拟控制器")
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")
            self.session.warmup(observation=self.camera.get_latest_observation())
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            viewer2d = Viewer2D()
            viewer3d = Viewer3D()
            self.bridge.start()
            last_frame_id = 0

            # 等待 bridge 后台线程完成首次 get_robot_state()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                robot_state = self.bridge.state
                if robot_state is not None and robot_state.has_tcp_pq:
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError("follower 启动后未能获取有效 TCP pq")

            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    result = self.session.process(observation)
                    robot_state = self.bridge.state
                    rbt_pq = (
                        robot_state.tcp_pq
                        if robot_state is not None and robot_state.has_tcp_pq
                        else None
                    )

                    if rbt_pq is None:
                        # 本帧不进行眼在手坐标转换，等待下一帧
                        continue

                    # 同一 observation/result 同时送往控制桥和可视化，保证标记来自同一帧。

                    transform_result = self.cam_transform.result2base(
                        result=result,
                        cam_index=1,
                        rbt_pq=rbt_pq
                    )
                    # 将相机获得的目标位置进行处理，测试使得 follower 目标点在相机的偏置距离处，避免脱离视野
                    self.bridge.submit_perception(transform_result)
                    state = self.bridge.display_state
                    print(f"[Bridge] 状态: {state.status}, 原始点: {state.raw_point_m}, 滤波点: {state.filtered_point_m}")

                    if viewer2d is not None:
                        viewer2d.update(observation, result)

                    if viewer3d is not None:
                        viewer3d.update(result)
 
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            if self.bridge is not None:
                self.bridge.stop()
            if viewer2d is not None:
                viewer2d.close()
            if viewer3d is not None:
                viewer3d.close()
            if self.camera is not None:
                self.camera.close()
            self.robot.close()


if __name__ == "__main__":
    command = YoloFollowerCommand(target="oolong_tea")
    command.yolo_follower_main()


