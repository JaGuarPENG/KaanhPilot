"""拍摄一次 G305 RGB 图像和过滤后点云快照。"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.errors import CameraError
from camera.contracts.cam_structs import AlignmentMode, DepthProcessingConfig
from camera.storage.snapshot import SavedSnapshot, save_observation_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="保存一次 Gemini 305 RGB 图片和过滤后点云快照")
    parser.add_argument("--profile", choices=("1280", "848"), default="848")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--temporal-filter", action="store_true")
    parser.add_argument("--spatial-filter", action="store_true")
    parser.add_argument("--hole-filling", action="store_true")
    parser.add_argument("--spatial-magnitude", type=int, default=1)
    parser.add_argument("--spatial-alpha", type=float, default=0.5)
    parser.add_argument("--hole-filling-mode", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--minimum-depth-m", type=float)
    parser.add_argument("--maximum-depth-m", type=float)
    parser.add_argument("--warmup-seconds", type=float, default=2.0, help="自动曝光和时域滤波预热时间，默认 2 秒")
    parser.add_argument("--save-root", type=Path, default=PROJECT_ROOT, help="保存根目录，默认当前 test_follower 项目根目录")
    return parser.parse_args()


def capture_once(camera: OrbbecG305Camera, root_dir: Path, warmup_seconds: float = 2.0) -> SavedSnapshot:
    """启动相机、等待自动曝光收敛后保存一次过滤后观测并关闭相机。"""
    if warmup_seconds < 0.0:
        raise ValueError("预热时间不能为负数")
    try:
        camera.start()
        # 首帧通常还未完成自动曝光；时域滤波也需要连续帧才能发挥作用。
        time.sleep(warmup_seconds)
        observation = camera.get_latest_observation()
        if observation is None:
            raise RuntimeError("相机启动成功但未发布首个观测")
        return save_observation_snapshot(observation, camera.camera_id, root_dir)
    finally:
        camera.close()


def main() -> None:
    args = _parse_args()
    try:
        processing = DepthProcessingConfig(
            temporal_enabled=args.temporal_filter,
            spatial_enabled=args.spatial_filter,
            hole_filling_enabled=args.hole_filling,
            spatial_magnitude=args.spatial_magnitude,
            spatial_alpha=args.spatial_alpha,
            hole_filling_mode=args.hole_filling_mode,
            minimum_depth_m=args.minimum_depth_m,
            maximum_depth_m=args.maximum_depth_m,
        )
        profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
        camera = OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=processing)
        saved = capture_once(camera, args.save_root.resolve(), args.warmup_seconds)
    except (CameraError, ValueError, RuntimeError) as error:
        print(f"[Camera Snapshot] 失败：{type(error).__name__}: {error}")
        raise SystemExit(1) from error
    print(f"图片：{saved.image_path}")
    print(f"过滤后完整点云数据：{saved.cloud_npz_path}")
    print(f"CloudCompare 点云：{saved.cloud_ply_path}")


if __name__ == "__main__":
    main()
