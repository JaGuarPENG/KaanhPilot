"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

from pathlib import Path
import time
from commands.setup import RobotConfig, RobotSetup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"

class YoloFollowerCommand:
    """G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

    def __init__(self, target):
        self.config_path: Path = DEFAULT_CONFIG_PATH
        self.target = target
        self.robot_setup = RobotSetup(DEFAULT_CONFIG_PATH)
        self.robot_config: RobotConfig = self.robot_setup.get_robot_config()
        self.robot = self.robot_setup.setup_robot()
        self.camera = self.robot_setup.setup_camera(0)
        self.detector = self.robot_setup.setup_detector()
        self.localization = self.robot_setup.setup_localizer()
        self.tracker = self.robot_setup.setup_tracker()
        self.warmup_seconds = self.robot_config.camera_warmup_seconds
        self.bridge = self.robot_setup.setup_bridge(self.robot)
        self.session = self.robot_setup.setup_session(self.detector, self.target)
        self.cam_transform = self.robot_setup.setup_camera_transform()

    
    def yolo_follower_main(self) -> None:
        try:
            if not self.robot.connect():
                raise RuntimeError("无法连接虚拟控制器")
            self.robot.login(
                str(self.robot_config.robot.user), 
                str(self.robot_config.robot.password))
            self.robot.set_tool(tool_id=1)
            self.robot.set_jog_coordinate()
            self.robot.manual_enable()
            print(f"连接到虚拟控制器")
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")
            self.session.warmup(observation=self.camera.get_latest_observation())
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            robot_viewer = self.robot_setup.start_robot_scene()
            result_viewer = self.robot_setup.start_result_viewer()
            self.bridge.start()
            last_frame_id = 0
            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    result = self.session.process(observation)
                    # 同一 observation/result 同时送往控制桥和可视化，保证标记来自同一帧。
                    # 眼在手外
                    transform_result = self.cam_transform.result2base(
                        result=result,
                        cam_index=self.camera._device_index,
                        rbt_pq=None
                    )
                    self.bridge.submit_perception(transform_result)
                    if result_viewer is not None:
                        result_viewer.submit(observation, result)
                # 用户在 RGB/点云显示器按 Q 或关闭窗口时，同样结束完整 follower 会话。
                if result_viewer is not None and result_viewer.is_closed:
                    break
                if robot_viewer is not None:
                    if not robot_viewer.update(self.bridge.latest_joints_rad, self.bridge.display_state):
                        break
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            if self.bridge is not None:
                self.bridge.stop()
            if result_viewer is not None:
                result_viewer.close()
            if robot_viewer is not None:
                robot_viewer.close()
            if self.camera is not None:
                self.camera.close()
            self.robot.close()


if __name__ == "__main__":
    command = YoloFollowerCommand(target="oolong_tea")
    command.yolo_follower_main()


