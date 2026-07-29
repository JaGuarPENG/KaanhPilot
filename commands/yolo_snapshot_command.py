"""单帧指定目标识别与 ROI 点云定位命令。"""

from __future__ import annotations

import argparse
import json
import time

from camera.contracts.errors import CameraError
from commands.yolo_command_support import add_common_arguments, build_camera, build_session, result_to_dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G305 单帧指定目标检测与 ROI 点云定位")
    add_common_arguments(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    camera = None
    try:
        session = build_session(args)
        camera = build_camera(args)
        camera.start()
        time.sleep(args.warmup_seconds)
        observation = camera.get_latest_observation()
        if observation is None:
            raise RuntimeError("相机启动后未获取到有效观测")
        # 单帧命令仍使用完整会话，以保证输出结构与实时命令一致。
        print(json.dumps(result_to_dict(session.process(observation)), ensure_ascii=False, indent=2))
    except (CameraError, RuntimeError, ValueError) as error:
        raise SystemExit(f"YOLO 单帧感知失败: {type(error).__name__}: {error}") from error
    finally:
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
