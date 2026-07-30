"""G305 官方滤波前后点云的 60 秒双窗口诊断命令。"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.errors import CameraError
from camera.contracts.cam_structs import AlignmentMode, DepthProcessingConfig
from visualization.filter_comparison_viewer import FilterComparisonPointCloudViewer


@dataclass(frozen=True, slots=True)
class FilterComparisonResult:
    """一次滤波对比任务的结束状态。"""

    success: bool
    reason: str
    displayed_frames: int
    elapsed_s: float
    error_message: str | None = None


class CameraFilterComparisonCommand:
    """打开一个 G305 并在两个 Open3D 窗口中同步比较原始与过滤后点云。"""

    def __init__(self, camera: OrbbecG305Camera, duration_s: float = 60.0, max_field_m: float = 2.0, max_points: int = 200_000) -> None:
        if duration_s <= 0.0:
            raise ValueError("持续时间必须为正数")
        if not camera.depth_processing.enabled:
            raise ValueError("滤波对比命令要求至少启用一项深度处理")
        self._camera = camera
        self._duration_s = duration_s
        self._max_field_m = max_field_m
        self._max_points = max_points

    def run(self) -> FilterComparisonResult:
        started_at = time.monotonic()
        reason, error_message, displayed_frames, last_frame_id = "completed", None, 0, 0
        viewer: FilterComparisonPointCloudViewer | None = None
        try:
            self._camera.start()
            viewer = FilterComparisonPointCloudViewer(self._max_field_m, self._max_points)
            deadline = started_at + self._duration_s
            while time.monotonic() < deadline:
                pair = self._camera.get_latest_filter_comparison_observations()
                if pair is not None:
                    raw, filtered = pair
                    if raw.frame_id != last_frame_id:
                        last_frame_id = raw.frame_id
                        displayed_frames += 1
                        if not viewer.update(raw, filtered):
                            reason = "user_closed_window"
                            break
                time.sleep(0.005)
        except CameraError as error:
            reason = "camera_failed"
            error_message = f"{type(error).__name__}: {error}"
            print(f"[Filter Comparison] 相机失败：{error_message}")
        except Exception as error:
            reason = "command_failed"
            error_message = f"{type(error).__name__}: {error}"
            print(f"[Filter Comparison] 命令失败：{error_message}")
        finally:
            if viewer is not None:
                viewer.close()
            self._camera.close()
        return FilterComparisonResult(error_message is None, reason, displayed_frames, time.monotonic() - started_at, error_message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini 305 官方滤波前后点云双窗口比较")
    parser.add_argument("--profile", choices=("1280", "848"), default="848")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--max-field-m", type=float, default=2.0)
    parser.add_argument("--max-points", type=int, default=200_000)
    parser.add_argument("--minimum-depth-m", type=float, default=0.05)
    parser.add_argument("--maximum-depth-m", type=float, default=2.0)
    parser.add_argument("--no-temporal-filter", action="store_true")
    parser.add_argument("--no-spatial-filter", action="store_true")
    parser.add_argument("--no-hole-filling", action="store_true")
    parser.add_argument("--spatial-magnitude", type=int, default=1, help="空间滤波迭代次数，范围 1-5")
    parser.add_argument("--spatial-alpha", type=float, default=0.5, help="空间滤波当前像素权重，范围 0.1-1.0")
    parser.add_argument("--hole-filling-mode", type=int, choices=(0, 1, 2), default=1, help="0=TOP，1=NEAREST，2=FAREST")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        processing = DepthProcessingConfig(
            temporal_enabled=not args.no_temporal_filter,
            spatial_enabled=not args.no_spatial_filter,
            hole_filling_enabled=not args.no_hole_filling,
            spatial_magnitude=args.spatial_magnitude,
            spatial_alpha=args.spatial_alpha,
            hole_filling_mode=args.hole_filling_mode,
            minimum_depth_m=args.minimum_depth_m,
            maximum_depth_m=args.maximum_depth_m,
        )
    except ValueError as error:
        raise SystemExit(f"深度滤波参数无效：{error}") from error
    profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
    camera = OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=processing)
    result = CameraFilterComparisonCommand(camera, args.seconds, args.max_field_m, args.max_points).run()
    print("\n=== Filter comparison result ===")
    print(f"success: {result.success}\nreason: {result.reason}\ndisplayed_frames: {result.displayed_frames}\nelapsed_s: {result.elapsed_s:.3f}")
    if result.error_message is not None:
        print(f"error: {result.error_message}")


if __name__ == "__main__":
    main()
