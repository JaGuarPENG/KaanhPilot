"""单目标跨帧关联：首次获取后绝不因置信度波动自动切换到同类其他物体。"""

from __future__ import annotations

from dataclasses import dataclass

from yolo.contracts.yolo_structs import Detection


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    """同类候选框重新关联的阈值。"""

    maximum_missing_frames: int = 5
    minimum_iou: float = 0.15
    maximum_center_distance_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.maximum_missing_frames < 1:
            raise ValueError("最大丢失帧数必须至少为 1")
        if not 0.0 <= self.minimum_iou <= 1.0 or self.maximum_center_distance_ratio < 0.0:
            raise ValueError("追踪阈值无效")


class SingleTargetTracker:
    """保存一个已锁定目标的二维状态；不做运动控制，也不平滑三维坐标。"""

    def __init__(self, config: TrackerConfig) -> None:
        self._config = config
        self._last_detection: Detection | None = None
        self._missing_frames = 0

    @property
    def is_locked(self) -> bool:
        return self._last_detection is not None

    @property
    def missing_frames(self) -> int:
        return self._missing_frames

    def select(self, candidates: tuple[Detection, ...], image_width: int, image_height: int) -> tuple[Detection | None, bool, bool]:
        """返回 ``(检测框, 是否首次获取, 是否已达到丢失阈值)``。"""
        if self._last_detection is None:
            if not candidates:
                # 从未获取过目标时只是正常 no_match；target_lost 只描述已锁定实例的丢失。
                self._missing_frames = 0
                return None, False, False
            selected = max(candidates, key=lambda candidate: candidate.confidence)
            self._last_detection, self._missing_frames = selected, 0
            return selected, True, False

        associated = [candidate for candidate in candidates if self._is_associated(self._last_detection, candidate, image_width, image_height)]
        if not associated:
            self._missing_frames += 1
            return None, False, self._missing_frames >= self._config.maximum_missing_frames

        # IoU 是主排序，置信度只作为相同关联质量下的次级规则，避免目标之间跳转。
        selected = max(associated, key=lambda candidate: (self._iou(self._last_detection, candidate), candidate.confidence))
        self._last_detection, self._missing_frames = selected, 0
        return selected, False, False

    @staticmethod
    def _iou(left: Detection, right: Detection) -> float:
        x_min, y_min = max(left.x_min, right.x_min), max(left.y_min, right.y_min)
        x_max, y_max = min(left.x_max, right.x_max), min(left.y_max, right.y_max)
        intersection = max(0, x_max - x_min) * max(0, y_max - y_min)
        union = (left.x_max - left.x_min) * (left.y_max - left.y_min) + (right.x_max - right.x_min) * (right.y_max - right.y_min) - intersection
        return intersection / union if union else 0.0

    def _is_associated(self, previous: Detection, candidate: Detection, image_width: int, image_height: int) -> bool:
        if self._iou(previous, candidate) >= self._config.minimum_iou:
            return True
        previous_center, candidate_center = previous.center_pixel, candidate.center_pixel
        dx = (previous_center[0] - candidate_center[0]) / image_width
        dy = (previous_center[1] - candidate_center[1]) / image_height
        return (dx * dx + dy * dy) ** 0.5 <= self._config.maximum_center_distance_ratio
