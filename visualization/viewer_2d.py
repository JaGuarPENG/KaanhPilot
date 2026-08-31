
from __future__ import annotations

import queue
import threading

import numpy as np

from camera.contracts.cam_structs import AlignedRGBDObservation
from perception.percept_structs import TargetPerceptionResult
from visualization.rgb_overlay import draw_target_perception


class Viewer2D:
    """独立窗口线程：显示变慢或被关闭均不会阻塞感知后端。

    队列容量恒为一帧，显示端落后时主动丢弃旧画面；后台始终处理最新相机帧。
    """

    def __init__(self) -> None:
        self._frames: queue.Queue[tuple[AlignedRGBDObservation, TargetPerceptionResult|None]] = queue.Queue(maxsize=1)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="2D Viewer", daemon=True)
        self._thread.start()

    def update(self, observation: AlignedRGBDObservation, result: TargetPerceptionResult|None) -> None:
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
            print("[2D Viewer] 无法启动 RGB 显示窗口: 未安装 opencv-python")
            return
        window_name = "2D Viewer"
        try:
            while not self._closed.is_set():
                try:
                    observation, result = self._frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                canvas = np.ascontiguousarray(observation.rgb[..., ::-1].copy())
                if result is not None:
                    draw_target_perception(cv2, canvas, result)
                cv2.imshow(window_name, canvas)
                # 关闭窗口只停止显示线程，不向后端发送停止信号。
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    self._closed.set()
        finally:
            try:
                cv2.destroyWindow(window_name)
            except Exception:
                pass