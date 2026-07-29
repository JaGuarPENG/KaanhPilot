"""YOLO 实时与单帧命令共用的构造、参数和结果序列化辅助函数。"""

from __future__ import annotations

import argparse
from pathlib import Path

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.cam_structs import AlignmentMode, DepthProcessingConfig
from perception.percept_struct import LocalizationConfig, PixelROI, TargetPerceptionResult, Workspace3D
from perception.roi_localizer import RoiPointCloudLocalizer
from perception.session import TargetPerceptionSession
from perception.target_tracker import SingleTargetTracker, TrackerConfig
from yolo.detector import UltralyticsPtDetector
from yolo.labels import load_label_mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = PROJECT_ROOT / "yolo" / "model" / "yolov8_0728.pt"
DEFAULT_LABELS_PATH = PROJECT_ROOT / "yolo" / "model" / "yolov8_0728.labels.yaml"


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """增加相机、模型、单目标追踪和用户过滤范围参数。"""
    parser.add_argument("--target", required=True, help="要持续处理的目标，如 mineral_water")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--confidence", type=float, default=0.45)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--profile", choices=("1280", "848"), default="848")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--warmup-seconds", type=float, default=2.0)
    parser.add_argument("--minimum-depth-m", type=float, default=0.05, help="用户指定的最小有效深度（米）")
    parser.add_argument("--maximum-depth-m", type=float, default=2.0, help="用户指定的最大有效深度（米）")
    parser.add_argument("--static-roi", type=int, nargs=4, metavar=("X_MIN", "Y_MIN", "X_MAX", "Y_MAX"), help="用户指定静态 2D ROI 的 xyxy 像素范围")
    parser.add_argument("--workspace-min", type=float, nargs=3, metavar=("X", "Y", "Z"), help="相机坐标系 3D 工作空间最小值（米）")
    parser.add_argument("--workspace-max", type=float, nargs=3, metavar=("X", "Y", "Z"), help="相机坐标系 3D 工作空间最大值（米）")
    parser.add_argument("--roi-shrink-ratio", type=float, default=0.10, help="检测框向内收缩比例，默认 0.10")
    parser.add_argument("--minimum-valid-points", type=int, default=30)
    parser.add_argument("--depth-inlier-half-width-m", type=float, default=0.05)
    parser.add_argument("--maximum-missing-frames", type=int, default=5)
    parser.add_argument("--minimum-track-iou", type=float, default=0.15)
    parser.add_argument("--maximum-center-distance-ratio", type=float, default=0.20)


def build_camera(args: argparse.Namespace) -> OrbbecG305Camera:
    """按命令参数创建相机；只使用既有 Camera 公共接口，不修改相机模块。"""
    profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
    processing = DepthProcessingConfig(minimum_depth_m=args.minimum_depth_m, maximum_depth_m=args.maximum_depth_m)
    return OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=processing)


def build_session(args: argparse.Namespace, collect_inspection: bool = False) -> TargetPerceptionSession:
    """为一个用户指定 target 创建模型、定位器与锁定式追踪器。"""
    if args.warmup_seconds < 0:
        raise ValueError("warmup-seconds 不能为负数")
    workspace = None
    if (args.workspace_min is None) != (args.workspace_max is None):
        raise ValueError("workspace-min 和 workspace-max 必须同时指定")
    if args.workspace_min is not None:
        workspace = Workspace3D(tuple(args.workspace_min), tuple(args.workspace_max))
    static_roi = PixelROI(*args.static_roi) if args.static_roi is not None else None
    localization = RoiPointCloudLocalizer(LocalizationConfig(
        minimum_depth_m=args.minimum_depth_m,
        maximum_depth_m=args.maximum_depth_m,
        static_roi=static_roi,
        workspace=workspace,
        roi_shrink_ratio=args.roi_shrink_ratio,
        minimum_valid_points=args.minimum_valid_points,
        depth_inlier_half_width_m=args.depth_inlier_half_width_m,
    ), collect_inspection=collect_inspection)
    detector = UltralyticsPtDetector(args.model, load_label_mapping(args.labels), args.confidence, args.iou)
    tracker = SingleTargetTracker(TrackerConfig(args.maximum_missing_frames, args.minimum_track_iou, args.maximum_center_distance_ratio))
    return TargetPerceptionSession(detector, localization, tracker, args.target)


def result_to_dict(result: TargetPerceptionResult) -> dict[str, object]:
    """把枚举和 dataclass 显式转为稳定的 JSON 结果，便于后续机器人模块调用。"""
    payload: dict[str, object] = {
        "target_id": result.target_id,
        "frame_id": result.frame_id,
        "capture_timestamp_ms": result.capture_timestamp_ms,
        "status": result.status.value,
        "consecutive_missing_frames": result.consecutive_missing_frames,
    }
    if result.detection is not None:
        payload["detection"] = {"confidence": result.detection.confidence, "bbox_xyxy": result.detection.bbox_xyxy, "center_pixel": result.detection.center_pixel}
    if result.localization is not None:
        payload["localization"] = {
            "target_point_camera_m": result.localization.target_point_camera_m,
            "roi_xyxy": None if result.localization.roi is None else (result.localization.roi.x_min, result.localization.roi.y_min, result.localization.roi.x_max, result.localization.roi.y_max),
            "roi_point_count": result.localization.roi_point_count,
            "valid_point_count": result.localization.valid_point_count,
            "depth_median_m": result.localization.depth_median_m,
            "depth_spread_m": result.localization.depth_spread_m,
            "inspection_point_count": None if result.localization.inspection is None else len(result.localization.inspection.points_m),
        }
    if result.timing is not None:
        payload["timing_ms"] = {
            "yolo": result.timing.yolo_ms,
            "tracking": result.timing.tracking_ms,
            "localization": result.timing.localization_ms,
            "process_total": result.timing.process_total_ms,
        }
    return payload
