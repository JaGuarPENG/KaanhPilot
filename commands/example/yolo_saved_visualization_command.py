"""使用集中模型和感知配置处理已保存的 RGB-D 观测。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from commands.setup import RobotSetup
from perception.percept_structs import TargetPerceptionResult
from perception.saved_observation import load_saved_observation
from visualization.perception_viewer import PerceptionPointCloudViewer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def result_to_dict(result: TargetPerceptionResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "target_id": result.target_id,
        "frame_id": result.frame_id,
        "capture_timestamp_ms": result.capture_timestamp_ms,
        "status": result.status.value,
        "consecutive_missing_frames": result.consecutive_missing_frames,
    }
    if result.detection is not None:
        payload["detection"] = {"confidence": result.detection.confidence, "bbox_xyxy": result.detection.bbox_xyxy}
    if result.localization is not None:
        payload["localization"] = {"target_point_camera_m": result.localization.target_point_camera_m, "valid_point_count": result.localization.valid_point_count}
    if result.timing is not None:
        payload["timing_ms"] = {"yolo": result.timing.yolo_ms, "tracking": result.timing.tracking_ms, "localization": result.timing.localization_ms, "process_total": result.timing.process_total_ms}
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 config 模型处理已保存的 RGB-D 观测")
    parser.add_argument("--target", required=True)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--cloud-npz", type=Path, required=True)
    parser.add_argument("--no-display", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    viewer = None
    try:
        setup = RobotSetup(args.config_dir)
        detector = setup.setup_detector()
        session = setup.setup_session(detector, args.target)
        result = session.process(load_saved_observation(args.image, args.cloud_npz))
        print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
        if not args.no_display:
            viewer = PerceptionPointCloudViewer("Saved YOLO ROI Filtered Point Cloud")
            while viewer.is_open:
                viewer.update(result)
                time.sleep(0.02)
    except (RuntimeError, ValueError, OSError, KeyError, FileNotFoundError) as error:
        raise SystemExit(f"YOLO 离线点云显示失败: {type(error).__name__}: {error}") from error
    finally:
        if viewer is not None:
            viewer.close()


if __name__ == "__main__":
    main()
