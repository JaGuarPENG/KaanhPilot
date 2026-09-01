from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import (
    G305_1280X800_30,
    G305_848X480_30,
    G305_848X480_60,
)
from camera.contracts.cam_structs import AlignmentMode, DepthProcessingConfig
from camera.contracts.cam_structs import CameraProfile
from perception.percept_structs import LocalizationConfig
from perception.target_tracker import  TrackerConfig
from perception.target_tracker import SingleTargetTracker
from perception.roi_localizer import RoiPointCloudLocalizer
from planner.follower_bridge import  FollowerBridgeConfig
from visualization.viewer_2d import Viewer2D
from visualization.viewer_3d import Viewer3D
from visualization.viewer_robot_origin import ViewerRobot
from robot.kaanh_backend import KaanhRobotBackend
from yolo.detector import UltralyticsPtDetector, Detector
from yolo.labels import load_label_mapping
from planner.follower_bridge import FollowerBridge, FollowerBridgeConfig
from perception.session import TargetPerceptionSession
from planner.camera_transform import CameraTransform, RobotCameraExtrinsic
from planner.target_position_filter import TargetPositionFilter

@dataclass(frozen=True, slots=True)
class RobotConnectionSettings:
    """机器人连接参数
    
    参数表：
    - ip: 机器人 IP 地址
    - control_port: WebSocket 控制端口 (默认 5999)
    - udp_port: UDP 端口 (默认 9998)
    - user: 登录用户名 (默认 "Engineer")
    - password: 登录密码 (默认 000000)
    - timeout_s: 连接超时时间 (默认 5.0)
    """

    ip: str
    control_port: int
    udp_port: int
    user: str
    password: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class ViewerSettings:
    show_robot: bool
    show_result_2D: bool
    show_result_3D: bool



@dataclass(frozen=True, slots=True)
class RobotConfig:
    target_id: str | None
    robot: RobotConnectionSettings
    profile: CameraProfile
    alignment: AlignmentMode
    depth_processing: DepthProcessingConfig
    camera_warmup_seconds: float
    localization: LocalizationConfig
    tracker: TrackerConfig
    bridge: FollowerBridgeConfig
    model_path: Path
    labels_path: Path
    model_confidence: float
    model_iou: float
    viewer: ViewerSettings
    cam_extrinsic: RobotCameraExtrinsic


class RobotSetup:
    """从 config 目录读取运行参数，并集中构造各模块所需配置。"""

    def __init__(self, config_dir: Path) -> None:
        self._robot_config = self._load_config(config_dir)

    def get_robot_config(self) -> RobotConfig: 
        """获取机器人配置。"""
        return self._robot_config
    
    def setup_robot(self, port: int|None) -> KaanhRobotBackend:
        """根据配置创建并返回 KaanhRobotBackend 实例。"""
        return KaanhRobotBackend(
            str(self._robot_config.robot.ip),
            int(self._robot_config.robot.control_port) if port is None else port,
            int(self._robot_config.robot.udp_port),
            timeout=float(self._robot_config.robot.timeout_s)
        )
    
    def setup_camera(self, device_index: int = 0) -> OrbbecG305Camera:
        """根据配置创建并返回 OrbbecG305Camera 实例。"""
        return OrbbecG305Camera(
            profile=self._robot_config.profile,
            device_index=device_index,
            depth_processing=self._robot_config.depth_processing
        )
    
    def setup_localizer(self) -> RoiPointCloudLocalizer:
        """根据配置创建并返回 RoiPointCloudLocalizer 实例。"""
        return RoiPointCloudLocalizer(self._robot_config.localization)
    
    def setup_detector(self) -> UltralyticsPtDetector:
        """根据配置创建并返回 UltralyticsPtDetector 实例。"""
        labels = load_label_mapping(self._robot_config.labels_path)
        return UltralyticsPtDetector(
            self._robot_config.model_path,
            labels,
            confidence_threshold=self._robot_config.model_confidence,
            iou_threshold=self._robot_config.model_iou
        )
    
    def setup_tracker(self) -> SingleTargetTracker:
        """根据配置创建并返回 SingleTargetTracker 实例。"""
        return SingleTargetTracker(self._robot_config.tracker)
    
    def setup_session(
        self,
        detector: Detector,
        target_id: str,
    ) -> TargetPerceptionSession:
        localizer = RoiPointCloudLocalizer(
            self._robot_config.localization,
            collect_inspection=self._robot_config.viewer.show_result_3D,
        )
        tracker = SingleTargetTracker(self._robot_config.tracker)
        return TargetPerceptionSession(detector, localizer, tracker, target_id)

    def setup_bridge(self, robot: KaanhRobotBackend, position_filter: TargetPositionFilter | None) -> FollowerBridge:
        return FollowerBridge(robot, self._robot_config.bridge, position_filter=position_filter)

    def start_2d_viewer(self) -> Viewer2D | None:
        if not self._robot_config.viewer.show_result_2D:
            print("[Setup] 配置文件中未启用视觉2D可视化窗口")
            return None
        return Viewer2D()
    
    def start_3d_viewer(self) -> Viewer3D | None:
        if not self._robot_config.viewer.show_result_3D:
            print("[Setup] 配置文件中未启用视觉3D可视化窗口")
            return None
        return Viewer3D()
    
    def start_robot_viewer(self) -> ViewerRobot | None:
        if not self._robot_config.viewer.show_robot:
            print("[Setup] 配置文件中未启用机器人可视化窗口")
            return None
        return ViewerRobot()
    
    
    def setup_camera_transform(self) -> CameraTransform | None:
        """根据配置创建并返回 CameraTransform 实例。"""
        return CameraTransform(
            cam_extrinsic=self._robot_config.cam_extrinsic
        )
    

    @staticmethod
    def _load_config(config_dir: Path) -> RobotConfig:
        """读取 config 下各模块文件，并组合成统一的运行时配置。"""
        config_dir = config_dir.resolve()
        robot_data = RobotSetup._read_json(config_dir / "robot" / "robot_config.json")
        camera_data = RobotSetup._read_json(config_dir / "camera" / "g305.json")
        model_data = RobotSetup._read_json(config_dir / "model" / "model_config.json")
        perception_data = RobotSetup._read_json(config_dir / "perception" / "perception_config.json")
        calibration_data = RobotSetup._read_json(config_dir / "calibration" / "eye_to_hand_cam0.json")

        extrinsic_data_1 = RobotSetup._read_json(config_dir / "calibration" / "eye_to_hand_cam0.json")
        extrinsic_data_2 = RobotSetup._read_json(config_dir / "calibration" / "eye_in_hand_cam1.json")
        extrinsic_data_3 = RobotSetup._read_json(config_dir / "calibration" / "eye_in_hand_cam2.json")

        profiles = {
            "1280@30": G305_1280X800_30,
            "848@60": G305_848X480_60,
            "848@30": G305_848X480_30,
        }
        profile_name = str(camera_data.get("profile", ""))
        try:
            profile = profiles[profile_name]
        except KeyError as error:
            raise ValueError(f"不支持的 G305 profile: {profile_name!r}") from error

        extrinsic = calibration_data.get("camera_pose_in_base")
        if not isinstance(extrinsic, dict):
            raise ValueError("eye_to_hand_cam0.json 必须包含 camera_pose_in_base 对象")

        translation = tuple(float(value) for value in extrinsic["translation_m"])
        quaternion = tuple(float(value) for value in extrinsic["quaternion_xyzw"])
        if len(translation) != 3 or len(quaternion) != 4:
            raise ValueError("camera_pose_in_base 必须包含 3 个原点坐标值和 4 个旋转四元数值")
        
        cam_extrinsic = RobotCameraExtrinsic(
            cam_0_extrinsic=extrinsic_data_1.get("camera_pose_in_base"),
            cam_1_extrinsic=extrinsic_data_2.get("camera_pose_in_end"),
            cam_2_extrinsic=extrinsic_data_3.get("camera_pose_in_end"),
        )

        depth_processing = DepthProcessingConfig(
            temporal_enabled=bool(camera_data.get("temporal_enabled", False)),
            spatial_enabled=bool(camera_data.get("spatial_enabled", False)),
            hole_filling_enabled=bool(camera_data.get("hole_filling_enabled", False)),
            spatial_magnitude=int(camera_data.get("spatial_magnitude", 1)),
            spatial_alpha=float(camera_data.get("spatial_alpha", 0.5)),
            hole_filling_mode=int(camera_data.get("hole_filling_mode", 1)),
            minimum_depth_m=float(camera_data["minimum_depth_m"]),
            maximum_depth_m=float(camera_data["maximum_depth_m"]),
        )
        localization = LocalizationConfig(
            roi_shrink_ratio=float(perception_data.get("roi_shrink_ratio", 0.10)),
            minimum_valid_points=int(perception_data.get("minimum_valid_points", 30)),
            depth_inlier_half_width_m=float(perception_data.get("depth_inlier_half_width_m", 0.05)),
        )
        tracker = TrackerConfig(
            maximum_missing_frames=int(perception_data.get("maximum_missing_frames", 5)),
            minimum_iou=float(perception_data.get("minimum_track_iou", 0.15)),
            maximum_center_distance_ratio=float(perception_data.get("maximum_center_distance_ratio", 0.20)),
        )
        bridge = FollowerBridgeConfig(
            frequency_hz=float(perception_data.get("frequency_hz", 100.0)),
            approach_distance_m=float(perception_data.get("approach_distance_m", 0.1)),
            hold_after_s=float(perception_data.get("hold_after_s", 0.5)),
            stop_after_s=float(perception_data.get("stop_after_s", 2.0)),
        )

        return RobotConfig(
            target_id=None,
            robot=RobotConnectionSettings(
                ip=str(robot_data["robot_ip"]),
                control_port=int(robot_data.get("control_port", 5999)),
                udp_port=int(robot_data.get("udp_port", 9998)),
                user=str(robot_data.get("user", "Engineer")),
                password=str(robot_data.get("password", "")),
                timeout_s=float(robot_data.get("timeout_s", 5.0)),
            ),
            profile=profile,
            alignment=AlignmentMode(str(camera_data.get("alignment", AlignmentMode.AUTO.value))),
            depth_processing=depth_processing,
            camera_warmup_seconds=float(camera_data.get("warmup_seconds", 1.0)),
            localization=localization,
            tracker=tracker,
            bridge=bridge,
            model_path=config_dir/ Path(model_data.get("model_path", "model/yolov8_0728.pt")),
            labels_path=config_dir/ Path(model_data.get("labels_path", "model/yolov8_0728.labels.yaml")),
            model_confidence=float(model_data.get("confidence", 0.45)),
            model_iou=float(model_data.get("iou", 0.7)),
            viewer=ViewerSettings(
                show_robot=bool(perception_data.get("show_robot", False)),
                show_result_2D=bool(perception_data.get("show_2d", False)),
                show_result_3D=bool(perception_data.get("show_3d", False)),
            ),
            cam_extrinsic=cam_extrinsic
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        if not path.is_file():
            raise FileNotFoundError(f"缺少配置文件: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as error:
            raise ValueError(f"JSON 配置格式错误: {path}") from error
        if not isinstance(data, dict):
            raise ValueError(f"JSON 配置根节点必须是对象: {path}")
        return data


