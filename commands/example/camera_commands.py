"""使用集中配置查看 G305 RGB-D 流。"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from camera.contracts.errors import CameraError
from commands.setup import RobotSetup
from visualization.camera_viewer import ObservationVisualizer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 config 目录查看 G305 RGB-D 流")
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--max-field-m", type=float, default=2.0)
    parser.add_argument("--max-points", type=int, default=200_000)
    parser.add_argument("--no-point-cloud", action="store_true")
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
        camera.start()
        viewer = ObservationVisualizer(not args.no_point_cloud, args.max_field_m, args.max_points)
        deadline, last_frame_id, displayed_frames = time.monotonic() + args.seconds, 0, 0
        while time.monotonic() < deadline:
            observation = camera.get_latest_observation()
            if observation is not None and observation.frame_id != last_frame_id:
                last_frame_id = observation.frame_id
                displayed_frames += 1
                if not viewer.update(observation):
                    break
            time.sleep(0.005)
        print(f"displayed_frames: {displayed_frames}")
    except (CameraError, RuntimeError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"相机流查看失败: {type(error).__name__}: {error}") from error
    finally:
        if viewer is not None:
            viewer.close()
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
