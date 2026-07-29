"""打印当前 G305 和 pyorbbecsdk 实际支持的官方滤波参数。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_60
from camera.contracts.errors import CameraError
from camera.contracts.models import AlignmentMode, DepthProcessingConfig


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="打印 Gemini 305 官方深度滤波器参数 schema")
    parser.add_argument("--profile", choices=("1280", "848"), default="848")
    parser.add_argument("--alignment", choices=tuple(mode.value for mode in AlignmentMode), default=AlignmentMode.AUTO.value)
    parser.add_argument("--device-index", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    profile = G305_1280X800_30 if args.profile == "1280" else G305_848X480_60
    # 全部创建一次，才能枚举时域、空间、破洞和阈值四类官方滤波器的能力。
    processing = DepthProcessingConfig(
        temporal_enabled=True,
        spatial_enabled=True,
        hole_filling_enabled=True,
        minimum_depth_m=0.15,
        maximum_depth_m=2.0,
    )
    camera = OrbbecG305Camera(profile, AlignmentMode(args.alignment), args.device_index, depth_processing=processing)
    try:
        camera.start()
        descriptors = camera.get_depth_filter_parameter_schemas()
        print(json.dumps([asdict(item) for item in descriptors], ensure_ascii=False, indent=2))
    except CameraError as error:
        print(f"[Filter Schema] 相机失败：{type(error).__name__}: {error}")
        raise SystemExit(1) from error
    finally:
        camera.close()


if __name__ == "__main__":
    main()
