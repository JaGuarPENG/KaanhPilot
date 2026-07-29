"""不依赖 controller 的 G305 相机独立验证入口。"""

from __future__ import annotations

import argparse
import time

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.models import AlignmentMode, DepthProcessingConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini 305 RGB-D 相机 Phase 1 验证")
    parser.add_argument("--profile", choices=("1280", "848"), default="1280", help="1280=1280x800@30；848=848x480@60")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--device-index", type=int, default=0, help="多设备时使用的设备索引，默认第 0 台")
    parser.add_argument("--seconds", type=float, default=5.0, help="打印最新观测信息的持续秒数")
    parser.add_argument("--temporal-filter", action="store_true")
    parser.add_argument("--spatial-filter", action="store_true")
    parser.add_argument("--hole-filling", action="store_true")
    parser.add_argument("--spatial-magnitude", type=int, default=1)
    parser.add_argument("--spatial-alpha", type=float, default=0.5)
    parser.add_argument("--hole-filling-mode", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--minimum-depth-m", type=float)
    parser.add_argument("--maximum-depth-m", type=float)
    args = parser.parse_args()

    profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
    try:
        depth_processing = DepthProcessingConfig(
            temporal_enabled=args.temporal_filter,
            spatial_enabled=args.spatial_filter,
            hole_filling_enabled=args.hole_filling,
            spatial_magnitude=args.spatial_magnitude,
            spatial_alpha=args.spatial_alpha,
            hole_filling_mode=args.hole_filling_mode,
            minimum_depth_m=args.minimum_depth_m,
            maximum_depth_m=args.maximum_depth_m,
        )
    except ValueError as error:
        raise SystemExit(f"深度滤波参数无效：{error}") from error
    with OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=depth_processing) as camera:
        print(f"camera_id: {camera.camera_id}")
        print(f"actual_profile: {camera.actual_profile}")
        print(f"actual_alignment: {camera.actual_alignment_mode}")
        print(f"calibration: {camera.calibration}")
        print(f"depth_processing: {camera.depth_processing}")
        deadline = time.monotonic() + args.seconds
        last_frame_id = 0
        while time.monotonic() < deadline:
            observation = camera.get_latest_observation()
            if observation is not None and observation.frame_id != last_frame_id:
                last_frame_id = observation.frame_id
                # 用深度有效性统计点数；NaN 点不会被误计入。
                valid_points = int((observation.depth_m > 0.0).sum())
                print(f"frame={observation.frame_id}, timestamp={observation.capture_timestamp_ms} ms, rgb={observation.rgb.shape}, depth={observation.depth_m.shape}, points={valid_points}")
            time.sleep(0.05)


if __name__ == "__main__":
    main()
