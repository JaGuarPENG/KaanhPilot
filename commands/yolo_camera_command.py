"""基于集中配置运行实时 YOLO RGB-D 感知。"""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import time

import numpy as np

from camera.contracts.errors import CameraError
from commands.setup import RobotSetup
from commands.yolo_command_support import result_to_dict
from perception.percept_structs import TargetPerceptionResult


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class LatencyWindow:
    """保存最近 N 帧的主机侧感知耗时。"""

    def __init__(self, size: int = 100) -> None:
        self._values: deque[float] = deque(maxlen=size)

    def add(self, result: TargetPerceptionResult) -> None:
        if result.timing is not None:
            self._values.append(result.timing.process_total_ms)

    def summary(self) -> dict[str, float] | None:
        if not self._values:
            return None
        values = np.asarray(self._values, dtype=np.float64)
        return {
            "samples": float(len(values)),
            "mean": float(values.mean()),
            "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)),
            "p99": float(np.percentile(values, 99)),
            "max": float(values.max()),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于 config 目录的实时单目标 YOLO RGB-D 感知")
    parser.add_argument("--target", required=True, help="要持续处理的目标 ID，如 oolong_tea")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR, help="包含 robot、camera、model、perception 等子目录的配置根目录")
    parser.add_argument("--seconds", type=float, default=0.0, help="运行秒数；0 表示持续运行至 Ctrl+C")
    parser.add_argument("--no-display", action="store_true", help="覆盖配置，禁用感知结果显示窗口")
    parser.add_argument("--print-every", type=int, default=10, help="每处理多少帧输出一次 JSON 结果")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds < 0 or args.print_every < 1:
        raise SystemExit("seconds 不能为负数，print-every 必须为正数")

    camera = None
    viewer = None
    try:
        setup = RobotSetup(args.config_dir)
        config = setup.get_robot_config()

        camera = setup.setup_camera()
        detector = setup.setup_detector()

        camera.start()
        time.sleep(config.camera_warmup_seconds)
        observation = camera.get_latest_observation()
        if observation is None:
            raise RuntimeError("相机预热后没有可用于模型预热的观测")

        # 预热只调用模型，不创建或修改正式会话中的 tracker。
        image_height, image_width = observation.rgb.shape[:2]
        detector.warmup(image_width, image_height)
        session = setup.setup_session(detector, args.target)
        viewer = None if args.no_display else setup.start_result_viewer()

        deadline = None if args.seconds == 0 else time.monotonic() + args.seconds
        last_frame_id, processed_count = 0, 0
        latency = LatencyWindow()
        while deadline is None or time.monotonic() < deadline:
            observation = camera.get_latest_observation()
            if observation is None or observation.frame_id == last_frame_id:
                time.sleep(0.002)
                continue

            last_frame_id = observation.frame_id
            result = session.process(observation)
            processed_count += 1
            latency.add(result)
            if viewer is not None:
                viewer.submit(observation, result)
            if processed_count % args.print_every == 0:
                payload = result_to_dict(result)
                payload["process_window_ms"] = latency.summary()
                print(json.dumps(payload, ensure_ascii=False))
    except (CameraError, RuntimeError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"YOLO 实时感知失败: {type(error).__name__}: {error}") from error
    except KeyboardInterrupt:
        pass
    finally:
        if viewer is not None:
            viewer.close()
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
