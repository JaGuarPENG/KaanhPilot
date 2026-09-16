"""使用集中配置比较 G305 滤波前后的点云。"""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from camera.contracts.errors import CameraError
from commands.setup import RobotSetup
from visualization.filter_comparison_viewer import FilterComparisonPointCloudViewer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 config 中的深度滤波配置进行点云对比")
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--max-field-m", type=float, default=2.0)
    parser.add_argument("--max-points", type=int, default=200_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds 必须为正数")
    camera = None
    viewer = None
    try:
        setup = RobotSetup(args.config_dir)
        camera = setup.setup_camera()
        if not camera.depth_processing.enabled:
            raise ValueError("请先在 config/camera/g305.json 启用至少一个深度滤波")
        camera.set_observation_mode("RAW_AND_FILTERED")
        camera.start()
        viewer = FilterComparisonPointCloudViewer(args.max_field_m, args.max_points)
        deadline, last_frame_id, displayed_frames = time.monotonic() + args.seconds, 0, 0
        while time.monotonic() < deadline:
            raw, filtered = camera.get_latest_filter_comparison_observations()
            if raw.frame_id != last_frame_id:
                last_frame_id = raw.frame_id
                displayed_frames += 1
                if not viewer.update(raw, filtered):
                    break
            time.sleep(0.005)
        print(f"displayed_frames: {displayed_frames}")
    except (CameraError, RuntimeError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"滤波对比失败: {type(error).__name__}: {error}") from error
    finally:
        if viewer is not None:
            viewer.close()
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
