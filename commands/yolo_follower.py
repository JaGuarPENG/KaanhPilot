"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time



from planner.follower_bridge import FollowerBridge, FollowerBridgeConfig
from robot.kaanh_backend import KaanhRobotBackend
from visualization.follower_integration_viewer import FollowerIntegrationViewer
from visualization.perception_result_viewer import AsyncResultViewer 

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_30, G305_848X480_60
from camera.contracts.cam_structs import DepthProcessingConfig
from perception.percept_structs import LocalizationConfig
from perception.roi_localizer import RoiPointCloudLocalizer
from perception.session import TargetPerceptionSession
from perception.target_tracker import SingleTargetTracker, TrackerConfig
from yolo.detector import UltralyticsPtDetector
from yolo.labels import load_label_mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "yolo_follower.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "yolo" / "model" / "yolov8_0728.pt"
DEFAULT_LABELS_PATH = PROJECT_ROOT / "yolo" / "model" / "yolov8_0728.labels.yaml"



class YoloFollowerCommand:
    """G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

    def __init__(self, target, show_robot_viewer=True, show_vision_2D=True, show_vision_cloud=True):

        self.config_path: Path = DEFAULT_CONFIG_PATH

        self.config, self.raw_config = self._load_config(self.config_path)
        self.robot = KaanhRobotBackend(
            str(self.raw_config["robot_ip"]),
            int(self.raw_config.get("control_port", 5999)),
            int(self.raw_config.get("udp_port", 9998)),
            timeout=float(self.raw_config.get("timeout_s", 3.0))
        )
        self.depth_config = DepthProcessingConfig(
            temporal_enabled=True, 
            spatial_enabled=True, 
            hole_filling_enabled=True, 
            hole_filling_mode=1, 
            minimum_depth_m=float(self.raw_config.get("minimum_depth_m", 0.05)), 
            maximum_depth_m=float(self.raw_config.get("maximum_depth_m", 2.0)))

        if self.raw_config.get("profile") == "1280@30":
            self.profile = G305_1280X800_30
        elif self.raw_config.get("profile") == "848@60":
            self.profile = G305_848X480_60
        elif self.raw_config.get("profile") == "848@30":
            self.profile = G305_848X480_30
        else:
            raise ValueError(f"不支持的 profile 配置: {self.raw_config.get('profile')}") 
                       
        self.camera = OrbbecG305Camera(
            profile=self.profile, 
            device_index=0, 
            depth_processing=self.depth_config)

        self.warmup_seconds = float(self.raw_config.get("warmup_seconds", 1.0))
        self.target = target
        self.show_robot_viewer = show_robot_viewer
        self.show_vision_cloud = show_vision_cloud
        self.localization_config = LocalizationConfig(
            static_roi=None,
            roi_shrink_ratio=float(self.raw_config.get("roi_shrink_ratio", 0.10)),
            minimum_valid_points=int(self.raw_config.get("minimum_valid_points", 10)),
            depth_inlier_half_width_m=float(self.raw_config.get("depth_inlier_half_width_m", 0.05)),
        )
        self.tracker_config = TrackerConfig(
            maximum_missing_frames=int(self.raw_config.get("maximum_missing_frames", 5)),
            minimum_iou=float(self.raw_config.get("minimum_track_iou", 0.3)),
            maximum_center_distance_ratio=float(self.raw_config.get("maximum_center_distance_ratio", 0.2)),
        )
        self.bridge = FollowerBridge(self.robot, self.config)

    #TODO: 1.封装初始化流程，太tm麻烦了 2.现有的桥接器跟眼在手外相机参数耦合了，很不合理 3.现有的FollowerIntegrationViewer 代码依赖桥接器config，这有鸡毛道理？？ 4.现有的可视化部分代码需要进一步梳理，起码把命名整好点。5.现有的yolo_command_support很多参数依赖外部输入的args，这也不合理

    def _load_config(self, path: Path) -> tuple[FollowerBridgeConfig, dict[str, object]]:
        """加载 JSON 配置并构造经过严格校验的桥接器配置。"""
        data = json.loads(path.read_text(encoding="utf-8"))
        extrinsic = data["sim_base_from_camera"]
        config = FollowerBridgeConfig(
            tuple(extrinsic["translation_m"]), tuple(extrinsic["quaternion_xyzw"]),
            float(data.get("frequency_hz", 8.0)),
            float(data.get("approach_distance_m", 0.1)), float(data.get("hold_after_s", 0.5)), float(data.get("stop_after_s", 2.0)),
        )
        return config, data
    
    def yolo_follower_main(self) -> None:
        try:
            if not self.robot.connect():
                raise RuntimeError("无法连接虚拟控制器")
            self.robot.login(
                str(self.raw_config.get("user", "Engineer")), 
                str(self.raw_config.get("password", "")))
            self.robot.set_tool(tool_id=1)
            self.robot.set_jog_coordinate()
            self.robot.manual_enable()
            print(f"连接到虚拟控制器")
            self.camera.start()
            time.sleep(self.warmup_seconds)
            print(f"预热完成")
            labels = load_label_mapping(DEFAULT_LABELS_PATH)
            localization = RoiPointCloudLocalizer(self.localization_config,
                                                  collect_inspection=self.show_vision_cloud)
            detector = UltralyticsPtDetector(DEFAULT_MODEL_PATH, 
                                             labels, 
                                             confidence_threshold=0.45, 
                                             iou_threshold=0.7)
            tracker = SingleTargetTracker(self.tracker_config)
            session = TargetPerceptionSession(detector, 
                                              localization, 
                                              tracker, 
                                              self.target)
            session.warmup(observation=self.camera.get_latest_observation())
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")

            #TODO: viewer规范化
            if self.show_robot_viewer:
                robot_viewer = FollowerIntegrationViewer(self.config)
                time.sleep(0.5)  # 等待显示器线程启动
            result_viewer = AsyncResultViewer(show_point_cloud = self.show_vision_cloud)
            time.sleep(0.5)  # 等待显示器线程启动
            self.bridge.start()
            last_frame_id = 0
            while True:
                observation = self.camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    result = session.process(observation)
                    # 同一 observation/result 同时送往控制桥和可视化，保证标记来自同一帧。
                    self.bridge.submit_perception(result)
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
    command = YoloFollowerCommand(target="oolong_tea", show_robot_viewer=True, show_vision_2D=True, show_vision_cloud=True)
    command.yolo_follower_main()


