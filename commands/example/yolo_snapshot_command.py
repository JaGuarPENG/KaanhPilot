"""使用集中配置完成一次 G305 + YOLO 单帧定位。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from camera.contracts.errors import CameraError
from commands.setup import RobotSetup
from perception.percept_structs import TargetPerceptionResult


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
    parser = argparse.ArgumentParser(description="使用 config 目录完成一次指定目标的 RGB-D 定位")
    parser.add_argument("--target", required=True)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    camera = None
    try:
        setup = RobotSetup(args.config_dir)
        config = setup.get_robot_config()
        camera = setup.setup_camera()
        detector = setup.setup_detector()
        camera.start()
        time.sleep(config.camera_warmup_seconds)
        observation = camera.get_latest_observation()
        if observation is None:
            raise RuntimeError("相机预热后没有观测")
        height, width = observation.rgb.shape[:2]
        detector.warmup(width, height)
        session = setup.setup_session(detector, args.target)
        print(json.dumps(result_to_dict(session.process(observation)), ensure_ascii=False, indent=2))
    except (CameraError, RuntimeError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"YOLO 单帧感知失败: {type(error).__name__}: {error}") from error
    finally:
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
