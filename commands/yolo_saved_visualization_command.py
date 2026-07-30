"""离线以保存的 PNG 检测 ROI，并使用同名 NPZ 点云计算抓取点。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from commands.yolo_command_support import add_common_arguments, build_session, result_to_dict
from perception.saved_observation import load_saved_observation
from visualization.perception_viewer import PerceptionPointCloudViewer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线用 PNG 检测指定目标，再用同名 NPZ 点云显示最终 ROI 与抓取点")
    add_common_arguments(parser)
    parser.add_argument("--image", type=Path, default=Path("save/pic/20260730_102822_267313.png"), help="YOLO 输入图片，如 save/pic/1.png")
    parser.add_argument("--cloud-npz", type=Path, default=Path("save/cloud/20260730_102822_267313.npz"), help="与图片同名的点云，如 save/cloud/1.npz")
    parser.add_argument("--no-display", action="store_true", help="只输出 JSON 和耗时，不打开 Open3D 窗口")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    viewer = None
    try:
        # YOLO 仅消费 image；NPZ 只提供与该图像同像素坐标系的有组织点云。
        observation = load_saved_observation(args.image, args.cloud_npz)
        # 复用实时路径的同一会话，确保离线和实时的模型、ROI 过滤完全一致。
        session = build_session(args, collect_inspection=True)
        result = session.process(observation)
        print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
        if args.no_display:
            return
        viewer = PerceptionPointCloudViewer("Saved YOLO ROI Filtered Point Cloud")
        while viewer.is_open:
            viewer.update(result)
            time.sleep(0.02)
    except (RuntimeError, ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"YOLO 离线点云显示失败: {type(error).__name__}: {error}") from error
    except KeyboardInterrupt:
        pass
    finally:
        if viewer is not None:
            viewer.close()


if __name__ == "__main__":
    main()
