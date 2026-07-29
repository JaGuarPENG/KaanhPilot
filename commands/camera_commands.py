"""十秒相机流命令：仅通过 Camera 接口读取与可视化最新 RGB-D 观测。"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.errors import CameraError
from camera.contracts.interface import Camera
from camera.contracts.models import AlignmentMode, CameraProfile, DepthProcessingConfig
from camera.visualization.rgbd_viewer import ObservationVisualizer


@dataclass(frozen=True, slots=True)
class CameraStreamViewResult:
    """命令最终状态，后续 controller 或任务编排器可直接消费。"""

    success: bool
    reason: str
    displayed_frames: int
    elapsed_s: float
    actual_profile: CameraProfile | None
    actual_alignment: AlignmentMode | None
    depth_processing: DepthProcessingConfig
    error_message: str | None = None


class CameraStreamViewerCommand:
    """启动相机，持续显示最新帧；不访问 pyorbbecsdk，也不修改 controller。"""

    def __init__(
        self,
        camera: Camera,
        duration_s: float = 10.0,
        show_point_cloud: bool = True,
        max_field_m: float = 2.0,
        max_points: int = 200_000,
    ) -> None:
        if duration_s <= 0.0:
            raise ValueError("流持续时间必须为正数")
        self._camera = camera
        self._duration_s = duration_s
        self._show_point_cloud = show_point_cloud
        self._max_field_m = max_field_m
        self._max_points = max_points

    def run(self) -> CameraStreamViewResult:
        """运行到超时、窗口关闭或相机失败；无论结果如何都会清理资源。"""
        started_at = time.monotonic()
        displayed_frames, last_frame_id = 0, 0
        reason, error_message = "completed", None
        visualizer: ObservationVisualizer | None = None
        try:
            self._camera.start()
            visualizer = ObservationVisualizer(self._show_point_cloud, self._max_field_m, self._max_points)
            deadline = started_at + self._duration_s
            while time.monotonic() < deadline:
                observation = self._camera.get_latest_observation()
                if observation is not None and observation.frame_id != last_frame_id:
                    last_frame_id = observation.frame_id
                    displayed_frames += 1
                    if not visualizer.update(observation):
                        reason = "user_closed_window"
                        break
                # 只处理最新帧，既避免积压旧观测，也避免空循环占满 CPU。
                time.sleep(0.005)
        except CameraError as error:
            reason = "camera_failed"
            error_message = f"{type(error).__name__}: {error}"
            print(f"[Camera Command] 相机失败：{error_message}")
        except Exception as error:
            reason = "command_failed"
            error_message = f"{type(error).__name__}: {error}"
            print(f"[Camera Command] 可视化失败：{error_message}")
        finally:
            if visualizer is not None:
                visualizer.close()
            self._camera.close()

        return CameraStreamViewResult(
            success=error_message is None,
            reason=reason,
            displayed_frames=displayed_frames,
            elapsed_s=time.monotonic() - started_at,
            actual_profile=getattr(self._camera, "actual_profile", None),
            actual_alignment=getattr(self._camera, "actual_alignment_mode", None),
            depth_processing=getattr(self._camera, "depth_processing", DepthProcessingConfig()),
            error_message=error_message,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini 305 RGB-D 与点云可视化命令")
    parser.add_argument("--profile", choices=("1280", "848"), default="848")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--max-depth-m", type=float, default=2.0)
    parser.add_argument("--max-points", type=int, default=200_000)
    parser.add_argument("--no-point-cloud", action="store_true")
    parser.add_argument("--temporal-filter", action="store_true", help="启用奥比中光官方时域滤波")
    parser.add_argument("--spatial-filter", action="store_true", help="启用奥比中光官方空间滤波")
    parser.add_argument("--hole-filling", action="store_true", help="启用奥比中光官方深度破洞修补")
    parser.add_argument("--spatial-magnitude", type=int, default=1, help="空间滤波迭代次数，范围 1-5")
    parser.add_argument("--spatial-alpha", type=float, default=0.5, help="空间滤波当前像素权重，范围 0.1-1.0")
    parser.add_argument("--hole-filling-mode", type=int, choices=(0, 1, 2), default=0, help="0=TOP，1=NEAREST，2=FAREST")
    parser.add_argument("--minimum-depth-m", type=float, help="启用阈值滤波的最小深度，单位米")
    parser.add_argument("--maximum-depth-m", type=float, help="启用阈值滤波的最大深度，单位米")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
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
    profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
    camera = OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=depth_processing)
    result = CameraStreamViewerCommand(camera, args.seconds, not args.no_point_cloud, args.max_field_m, args.max_points).run()
    print("\n=== Camera stream result ===")
    print(f"success: {result.success}\nreason: {result.reason}\ndisplayed_frames: {result.displayed_frames}")
    print(f"elapsed_s: {result.elapsed_s:.3f}\nactual_profile: {result.actual_profile}\nactual_alignment: {result.actual_alignment}")
    print(f"depth_processing: {result.depth_processing}")
    if result.error_message:
        print(f"error: {result.error_message}")


if __name__ == "__main__":
    main()
