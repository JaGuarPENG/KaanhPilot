from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import (
    G305_1280X800_30,
    G305_848X480_30,
    G305_848X480_60,
)
from camera.adapters.realsense.d435 import RealSenseD435Camera
from camera.adapters.realsense.profiles import (
    D435_640X480_30, D435_1280X720_6,
)
from camera.contracts.interface import Camera
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
from robot.kaanh_backend import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_MONITOR_PORT,
    DEFAULT_UDP_PORT,
    KaanhRobotBackend,
)
from yolo.detector import UltralyticsPtDetector, Detector
from yolo.labels import load_label_mapping
from planner.follower_bridge import FollowerBridge, FollowerBridgeConfig
from perception.session import TargetPerceptionSession
from planner.camera_transform import CameraTransform, RobotCameraExtrinsic
from planner.target_position_filter import TargetPositionFilter


CAMERA_ADAPTERS: dict[str, type[Camera]] = {
    "orbbec_g305": OrbbecG305Camera,
    "realsense_d435": RealSenseD435Camera,
}
CAMERA_PROFILES: dict[str, dict[str, CameraProfile]] = {
    "orbbec_g305": {
        "1280@30": G305_1280X800_30,
        "848@30": G305_848X480_30,
        "848@60": G305_848X480_60,
    },
    "realsense_d435": {
        "640@30": D435_640X480_30,
        "1280@6": D435_1280X720_6,
    },
}
CAMERA_SETTINGS_FILES = {"orbbec_g305": "g305.json", "realsense_d435": "d435.json"}

@dataclass(frozen=True, slots=True)
class RobotConnectionSettings:
    """机器人连接参数
    
    参数表：
    - ip: 机器人 IP 地址
    - monitor_port: WebSocket 监控端口 (默认 5888)
    - control_port: WebSocket 控制端口 (默认 5999)
    - udp_port: UDP 端口 (默认 9998)
    - user: 登录用户名 (默认 "Engineer")
    - password: 登录密码 (默认 000000)
    - timeout_s: 连接超时时间 (默认 5.0)
    """

    ip: str
    monitor_port: int
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
class CameraSettings:
    """一台逻辑相机的独立配置；设备索引与机器人外参编号互不关联。"""

    camera_type: str
    device_index: int
    profile: CameraProfile
    alignment: AlignmentMode
    depth_processing: DepthProcessingConfig
    warmup_seconds: float
    extrinsic_index: int


@dataclass(frozen=True, slots=True)
class RobotConfig:
    target_id: str | None
    robot: RobotConnectionSettings
    cameras: dict[str, CameraSettings]
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
    
    def setup_robot(self, port: int | None = None) -> KaanhRobotBackend:
        """根据配置创建并返回 KaanhRobotBackend 实例。"""
        return KaanhRobotBackend(
            str(self._robot_config.robot.ip),
            int(self._robot_config.robot.control_port) if port is None else port,
            int(self._robot_config.robot.udp_port),
            timeout=float(self._robot_config.robot.timeout_s)
        )
    
    def get_camera_settings(self, camera_name: str) -> CameraSettings:
        """按逻辑名称查询相机配置，名称不等于 SDK 设备索引。"""
        if not isinstance(camera_name, str) or camera_name not in self._robot_config.cameras:
            available = ", ".join(self._robot_config.cameras)
            raise ValueError(f"未知相机名称 {camera_name!r}；可选名称: {available}")
        return self._robot_config.cameras[camera_name]

    def setup_camera(self, camera_name: str) -> Camera:
        """创建未启动的独立适配器；同一物理相机应只创建一次并共享实例。"""
        settings = self.get_camera_settings(camera_name)
        adapter = CAMERA_ADAPTERS[settings.camera_type]
        return adapter(
            profile=settings.profile,
            alignment_mode=settings.alignment,
            device_index=settings.device_index,
            depth_processing=settings.depth_processing,
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
        cameras = RobotSetup._load_camera_settings(config_dir / "camera")
        model_data = RobotSetup._read_json(config_dir / "model" / "model_config.json")
        perception_data = RobotSetup._read_json(config_dir / "perception" / "perception_config.json")
        # 重新规范化相机外参数据，确保每个相机都有对应的外参
        extrinsic_data_head = RobotSetup._read_json(config_dir / "calibration" / "eye_in_hand_realsense_head.json")
        extrinsic_data_left = RobotSetup._read_json(config_dir / "calibration" / "eye_in_hand_orbec_left.json")
        extrinsic_data_right = RobotSetup._read_json(config_dir / "calibration" / "eye_in_hand_orbec_right.json")

        cam_extrinsic = RobotCameraExtrinsic(
            cam_0_extrinsic=extrinsic_data_head.get("camera_pose_in_end"),
            cam_1_extrinsic=extrinsic_data_left.get("camera_pose_in_end"),
            cam_2_extrinsic=extrinsic_data_right.get("camera_pose_in_end"),
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
                monitor_port=int(robot_data.get("monitor_port", DEFAULT_MONITOR_PORT)),
                control_port=int(robot_data.get("control_port", DEFAULT_CONTROL_PORT)),
                udp_port=int(robot_data.get("udp_port", DEFAULT_UDP_PORT)),
                user=str(robot_data.get("user", "Engineer")),
                password=str(robot_data.get("password", "")),
                timeout_s=float(robot_data.get("timeout_s", 5.0)),
            ),
            cameras=cameras,
            localization=localization,
            tracker=tracker,
            bridge=bridge,
            model_path=config_dir/ Path(model_data.get("model_path", "model/yolo_0915.pt")),
            labels_path=config_dir/ Path(model_data.get("labels_path", "model/yolo_0915_labels.yaml")),
            model_confidence=float(model_data.get("confidence", 0.5)),
            model_iou=float(model_data.get("iou", 0.7)),
            viewer=ViewerSettings(
                show_robot=bool(perception_data.get("show_robot", False)),
                show_result_2D=bool(perception_data.get("show_2d", False)),
                show_result_3D=bool(perception_data.get("show_3d", False)),
            ),
            cam_extrinsic=cam_extrinsic
        )

    @staticmethod
    def _load_camera_settings(camera_dir: Path) -> dict[str, CameraSettings]:
        """读取名称→设备映射，再按设备型号解析相应的参数文件和 Profile 表。"""
        camera_dir = camera_dir.resolve()
        registry = RobotSetup._read_json(camera_dir / "cameras.json")
        if not registry:
            raise ValueError("cameras.json 必须至少配置一台相机")
        cameras = {}
        for name, entry in registry.items():
            if not name.strip() or not isinstance(entry, dict):
                raise ValueError(f"相机 {name!r} 的配置必须是对象，且名称不能为空")
            camera_type = entry.get("type")
            if not isinstance(camera_type, str) or camera_type not in CAMERA_ADAPTERS:
                raise ValueError(f"相机 {name!r} 的类型不受支持: {camera_type!r}")
            filename = entry.get("settings_file", CAMERA_SETTINGS_FILES[camera_type])
            if not isinstance(filename, str) or not filename:
                raise ValueError(f"相机 {name!r} 的 settings_file 必须为非空文件名")
            settings_path = (camera_dir / filename).resolve()
            if not settings_path.is_relative_to(camera_dir):
                raise ValueError(f"相机 {name!r} 的 settings_file 必须位于 config/camera 中")
            data = RobotSetup._read_json(settings_path)
            profile_name = data.get("profile")
            profiles = CAMERA_PROFILES[camera_type]
            if not isinstance(profile_name, str) or profile_name not in profiles:
                raise ValueError(f"相机 {name!r} 不支持 {camera_type} profile: {profile_name!r}；可选: {', '.join(profiles)}")
            device_index = entry.get("device_index", 0)
            extrinsic_index = entry.get("extrinsic_index")
            if type(device_index) is not int or device_index < 0:
                raise ValueError(f"相机 {name!r} 的 device_index 必须为非负整数")
            if type(extrinsic_index) is not int or extrinsic_index not in (0, 1, 2):
                raise ValueError(f"相机 {name!r} 的 extrinsic_index 必须为 0、1 或 2")
            warmup = float(data.get("warmup_seconds", 1.0))
            if not math.isfinite(warmup) or warmup < 0:
                raise ValueError(f"相机 {name!r} 的 warmup_seconds 必须为有限非负数")
            alignment = AlignmentMode(data.get("alignment", "auto"))
            for flag in ("temporal_enabled", "spatial_enabled", "hole_filling_enabled"):
                if type(data.get(flag, False)) is not bool:
                    raise ValueError(f"相机 {name!r} 的 {flag} 必须为 JSON 布尔值")
            minimum = data.get("minimum_depth_m")
            maximum = data.get("maximum_depth_m")
            if any(value is not None and not math.isfinite(float(value)) for value in (minimum, maximum)):
                raise ValueError(f"相机 {name!r} 的深度阈值必须为有限数值")
            processing = DepthProcessingConfig(
                temporal_enabled=data.get("temporal_enabled", False),
                spatial_enabled=data.get("spatial_enabled", False),
                hole_filling_enabled=data.get("hole_filling_enabled", False),
                spatial_magnitude=data.get("spatial_magnitude", 1),
                spatial_alpha=float(data.get("spatial_alpha", 0.5)),
                hole_filling_mode=data.get("hole_filling_mode", 1),
                minimum_depth_m=None if minimum is None else float(minimum),
                maximum_depth_m=None if maximum is None else float(maximum),
            )
            profile = profiles[profile_name]
            if camera_type == "realsense_d435":
                if alignment == AlignmentMode.HARDWARE:
                    raise ValueError(f"相机 {name!r}: D435 只支持 software 或 auto 对齐")
                if processing.hole_filling_enabled and processing.hole_filling_mode == 0:
                    raise ValueError(f"相机 {name!r}: D435 孔洞填充模式必须选择 1 或 2")
            elif alignment == AlignmentMode.HARDWARE and profile != G305_848X480_30:
                raise ValueError(f"相机 {name!r}: G305 仅 848@30 支持硬件对齐")
            cameras[name] = CameraSettings(
                camera_type=camera_type, device_index=device_index, profile=profile,
                alignment=alignment, depth_processing=processing, warmup_seconds=warmup,
                extrinsic_index=extrinsic_index,
            )
        return cameras

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
