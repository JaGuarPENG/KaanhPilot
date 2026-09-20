"""两种相机共用的后台采集与帧交接模块，让预览不被深度处理拖住

用后台线程持续从SDK读取完整RGBD数据，生成RGBFrame的彩色预览图供get_latest_color_frame()读取
用一个槽位保存待处理的 CapturedFrames，其中包含原始 SDK 帧集和对应的 RGBFrame
新帧到来时替换尚未处理的旧帧，避免队列积压，已经在处理的帧不受影响。
"""
from dataclasses import dataclass
import threading

from camera.contracts.cam_structs import RGBFrame
from camera.contracts.errors import CameraTimeoutError


@dataclass(frozen=True)
class CapturedFrames:
    frames: object
    color: RGBFrame


class LatestCapture:
    def __init__(self, read, make_color, publish):
        self._read, self._make_color, self._publish = read, make_color, publish
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._pending = None
        self._error = None
        self.thread = threading.Thread(target=self._run, name='camera-rgb-capture', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                frames = self._read()
                if frames is None:
                    continue
                color = self._make_color(frames)
                with self._condition:
                    if self._stop.is_set():
                        break
                    self._pending = CapturedFrames(frames, color)
                    self._publish(color)
                    self._condition.notify_all()
        except Exception as error:
            with self._condition:
                self._error = error
                self._pending = None
                self._publish(None)
                self._condition.notify_all()

    def get(self, timeout):
        with self._condition:
            self._condition.wait_for(
                lambda: self._pending is not None or self._error is not None or self._stop.is_set(), timeout)
            if self._error is not None:
                raise self._error
            packet, self._pending = self._pending, None
            return packet

    def close(self, timeout):
        self._stop.set()
        with self._condition:
            self._pending = None
            self._condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise CameraTimeoutError('RGB capture thread did not stop; pipeline is still in use')
