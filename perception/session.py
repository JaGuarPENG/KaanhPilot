"""面向一个指定目标的检测、关联和 ROI 定位会话。"""

from __future__ import annotations

import time

from camera.contracts.cam_structs import AlignedRGBDObservation
from perception.percept_structs import ProcessingTiming, TargetPerceptionResult, TargetStatus
from perception.roi_localizer import RoiPointCloudLocalizer
from perception.target_tracker import SingleTargetTracker
from yolo.detector import Detector


class TargetPerceptionSession:
    """一次完整的单目标识别与定位任务。
    
    输入参数表：
    - detector: YOLO 检测器
    - localizer: ROI 点云定位器
    - tracker: 单目标追踪器
    - target_id: 要持续处理的目标 ID

    返回结果表：
    - TargetPerceptionResult: 每帧的处理结果，包含检测、追踪和定位信息，以及处理耗时统计
    """

    def __init__(self, detector: Detector, localizer: RoiPointCloudLocalizer, tracker: SingleTargetTracker, target_id: str) -> None:
        if target_id not in detector.target_ids:
            raise ValueError(f"当前模型不支持目标 {target_id!r}；可用目标: {', '.join(detector.target_ids)}")
        self._detector = detector
        self._localizer = localizer
        self._tracker = tracker
        self._target_id = target_id

    def warmup(self, observation: AlignedRGBDObservation) -> None:
        """使用一帧图像进行模型前向推理，加载权重和 CUDA 内核，避免首次推理延迟。"""
        height, width = observation.rgb.shape[:2]
        self._detector.warmup(width, height)

    def process(self, observation: AlignedRGBDObservation) -> TargetPerceptionResult:
        started_ns = time.perf_counter_ns()
        detection_result = self._detector.detect(observation, self._target_id)
        after_yolo_ns = time.perf_counter_ns()
        selected, acquired, lost = self._tracker.select(detection_result.detections, detection_result.image_width, detection_result.image_height)
        after_tracking_ns = time.perf_counter_ns()
        if selected is None:
            status = TargetStatus.TARGET_LOST if lost else TargetStatus.NO_MATCH
            return TargetPerceptionResult(self._target_id, observation.frame_id, observation.capture_timestamp_ms, status, None, None, self._tracker.missing_frames, self._timing(started_ns, after_yolo_ns, after_tracking_ns, after_tracking_ns))

        localization = self._localizer.localize(observation, selected)
        finished_ns = time.perf_counter_ns()
        if not localization.has_target_point:
            status = TargetStatus.NO_TARGET_POINT
        else:
            status = TargetStatus.TARGET_ACQUIRED if acquired else TargetStatus.TARGET_TRACKED
        return TargetPerceptionResult(self._target_id, observation.frame_id, observation.capture_timestamp_ms, status, selected, localization, self._tracker.missing_frames, self._timing(started_ns, after_yolo_ns, after_tracking_ns, finished_ns))
    

    @staticmethod
    def _timing(started_ns: int, after_yolo_ns: int, after_tracking_ns: int, finished_ns: int) -> ProcessingTiming:
        """统一转换为毫秒，确保三个阶段之和可与总耗时进行比较。"""
        to_ms = lambda value: value / 1_000_000.0
        return ProcessingTiming(to_ms(after_yolo_ns - started_ns), to_ms(after_tracking_ns - after_yolo_ns), to_ms(finished_ns - after_tracking_ns), to_ms(finished_ns - started_ns))
    


