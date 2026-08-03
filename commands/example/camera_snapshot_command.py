"""使用集中配置保存一次 G305 RGB-D 快照。"""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.contracts.errors import CameraError
from camera.storage.snapshot import SavedSnapshot, save_observation_snapshot
from commands.setup import RobotSetup


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 config 目录保存一次 G305 RGB-D 快照")
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--save-root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args()


def capture_once(camera: OrbbecG305Camera, root_dir: Path, warmup_seconds: float) -> SavedSnapshot:
    try:
        camera.start()
        time.sleep(warmup_seconds)
        observation = camera.get_latest_observation()
        if observation is None:
            raise RuntimeError("相机预热后没有观测")
        return save_observation_snapshot(observation, camera.camera_id, root_dir)
    finally:
        camera.close()


def main() -> None:
    args = parse_args()
    try:
        setup = RobotSetup(args.config_dir)
        config = setup.get_robot_config()
        saved = capture_once(setup.setup_camera(), args.save_root.resolve(), config.camera_warmup_seconds)
    except (CameraError, RuntimeError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"相机快照失败: {type(error).__name__}: {error}") from error
    print(f"图片: {saved.image_path}")
    print(f"点云数据: {saved.cloud_npz_path}")
    print(f"CloudCompare 点云: {saved.cloud_ply_path}")


if __name__ == "__main__":
    main()
