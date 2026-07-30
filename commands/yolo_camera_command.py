"""实时单目标 YOLO + 点云定位联调命令。"""

from __future__ import annotations

import argparse
from collections import deque
import json
import queue
import threading
import time

import numpy as np

from camera.contracts.errors import CameraError
from camera.contracts.cam_structs import AlignedRGBDObservation
from commands.yolo_command_support import add_common_arguments, build_camera, build_session, result_to_dict
from perception.percept_structs import TargetPerceptionResult
from visualization.perception_viewer import PerceptionPointCloudViewer
from visualization.rgb_overlay import draw_target_perception


class AsyncResultViewer:
    """独立窗口线程：显示变慢或被关闭均不会阻塞感知后端。

    队列容量恒为一帧，显示端落后时主动丢弃旧画面；后台始终处理最新相机帧。
    """

    def __init__(self, show_point_cloud: bool) -> None:
        self._frames: queue.Queue[tuple[AlignedRGBDObservation, TargetPerceptionResult]] = queue.Queue(maxsize=1)
        self._show_point_cloud = show_point_cloud
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="yolo-result-viewer", daemon=True)
        self._thread.start()

    def submit(self, observation: AlignedRGBDObservation, result: TargetPerceptionResult) -> None:
        if self._closed.is_set():
            return
        try:
            self._frames.put_nowait((observation, result))
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait((observation, result))
            except queue.Full:
                pass

    def close(self) -> None:
        self._closed.set()
        self._thread.join(timeout=1.0)

    @property
    def is_closed(self) -> bool:
        """显示窗口是否已由用户关闭；集成命令据此统一结束测试会话。"""
        return self._closed.is_set()

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            # 显示能力是可选诊断功能；缺少 OpenCV 时感知后端仍继续输出结果。
            self._closed.set()
            return
        window_name = "YOLO Target Perception"
        cloud_viewer = None
        try:
            if self._show_point_cloud:
                try:
                    cloud_viewer = PerceptionPointCloudViewer()
                except RuntimeError as error:
                    # 点云窗口不可用时仍保留 RGB 和 JSON 输出。
                    print(f"[YOLO Viewer] 无法启动点云窗口: {error}")
            while not self._closed.is_set():
                try:
                    observation, result = self._frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                canvas = np.ascontiguousarray(observation.rgb[..., ::-1].copy())
                draw_target_perception(cv2, canvas, result)
                cv2.imshow(window_name, canvas)
                if cloud_viewer is not None and not cloud_viewer.update(result):
                    cloud_viewer.close()
                    cloud_viewer = None
                # 关闭窗口只停止显示线程，不向后端发送停止信号。
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    self._closed.set()
        finally:
            if cloud_viewer is not None:
                cloud_viewer.close()
            try:
                cv2.destroyWindow(window_name)
            except Exception:
                pass


class LatencyWindow:
    """保存最近 N 帧主机侧处理耗时，用于观察实时性能尾延迟。"""

    def __init__(self, size: int = 100) -> None:
        self._values: deque[float] = deque(maxlen=size)

    def add(self, result: TargetPerceptionResult) -> None:
        if result.timing is not None:
            self._values.append(result.timing.process_total_ms)

    def summary(self) -> dict[str, float] | None:
        if not self._values:
            return None
        values = np.asarray(self._values, dtype=np.float64)
        return {"samples": float(len(values)), "mean": float(values.mean()), "p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95)), "p99": float(np.percentile(values, 99)), "max": float(values.max())}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G305 实时指定目标检测、追踪与 ROI 点云定位")
    add_common_arguments(parser)
    parser.add_argument("--seconds", type=float, default=0.0, help="运行秒数；0 表示持续运行至 Ctrl+C")
    parser.add_argument("--no-display", action="store_true", help="禁用异步 OpenCV 显示窗口")
    parser.add_argument("--show-point-cloud", action="store_true", help="在独立显示线程中显示最终 ROI 过滤点云和红色抓取点")
    parser.add_argument("--print-every", type=int, default=10, help="每隔多少处理帧输出一次 JSON 结果")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds < 0 or args.print_every < 1:
        raise SystemExit("seconds 不能为负数，print-every 必须为正数")
    camera = None
    viewer = None
    try:
        session = build_session(args, collect_inspection=args.show_point_cloud)
        camera = build_camera(args)
        camera.start()
        time.sleep(args.warmup_seconds)
        viewer = None if args.no_display else AsyncResultViewer(args.show_point_cloud)
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
    except (CameraError, RuntimeError, ValueError) as error:
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
