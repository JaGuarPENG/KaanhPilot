"""单目标跨帧关联：首次获取后绝不因置信度波动自动切换到同类其他物体。"""

from __future__ import annotations

from dataclasses import dataclass

from yolo.contracts.yolo_structs import Detection


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    """跨帧识别单一目标参数配置。

    当画面中出现多个同类别物体时，追踪器会优先选择与上一帧已锁定目标
    位置最接近的候选框，避免识别目标在不同物体之间跳转。

    参数：
    - maximum_missing_frames: 连续多少帧没有找到原目标后，判定目标已丢失。
    - minimum_iou: 当前候选框与上一帧目标框至少需要重叠多少，才认为可能是同一目标。
    - maximum_center_distance_ratio: 即使两个框没有重叠，只要中心点距离不超过
      图像对角线的这个比例，仍允许认为是同一目标。
    """

    maximum_missing_frames: int = 5
    minimum_iou: float = 0.15
    maximum_center_distance_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.maximum_missing_frames < 1:
            raise ValueError("最大丢失帧数必须至少为 1")
        if not 0.0 <= self.minimum_iou <= 1.0 or self.maximum_center_distance_ratio < 0.0:
            raise ValueError("追踪阈值无效")


class SingleTargetTracker:
    """跨帧锁定单一目标的追踪器"""

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
        """选择当前输入帧的目标检测结果，并更新追踪器状态。

        输入参数表：
        - candidates: 输入帧所有同类别检测结果。
        - image_width: 输入帧图像宽度。
        - image_height: 输入帧图像高度。

        输出参数表：
        - selected: 当前帧选定的目标检测结果；如果没有选定目标则为 None。
        - acquired: 判断是否获取到目标, 每一帧都会判断。
        - lost: 判断目标是否丢失， 只有在连续多帧没有选定目标时才会返回 True。
        
        """
        # 1. 如果没有上一帧目标，选择置信度最高的候选框作为新目标。
        if self._last_detection is None:
            if not candidates:
                # 从未获取过目标时只是正常 no_match；target_lost 只描述已锁定实例的丢失。
                self._missing_frames = 0
                acquired = False
                lost = False
                return None, acquired, lost
            selected = max(candidates, key=lambda candidate: candidate.confidence)
            self._last_detection, self._missing_frames = selected, 0
            # acquired=True 表示首次锁定了一个新目标
            acquired = True
            lost = False
            return selected, acquired, lost
        # 2. 如果有上一帧目标，尝试在当前帧候选框中找到与之最关联的目标。
        associated = [candidate for candidate in candidates 
                      if self._is_associated(self._last_detection, candidate, image_width, image_height)]
        if not associated:
            # 没有任何候选框与上一帧目标关联，增加丢失帧计数，直到达到最大丢失帧数。
            self._missing_frames += 1
            acquired = False
            lost = self._missing_frames >= self._config.maximum_missing_frames
            return None, acquired, lost
        # 3. 如果有多个候选框与上一帧目标关联，选择与上一帧目标最接近的候选框作为当前帧目标。
        selected = max(associated, key=lambda candidate: (self._iou(self._last_detection, candidate), candidate.confidence))
        self._last_detection, self._missing_frames = selected, 0
        acquired = False
        lost = False
        return selected, acquired, lost

    @staticmethod
    def _iou(left: Detection, right: Detection) -> float:
        """计算两个检测框的交并比（IoU）。"""
        x_min, y_min = max(left.x_min, right.x_min), max(left.y_min, right.y_min)
        x_max, y_max = min(left.x_max, right.x_max), min(left.y_max, right.y_max)
        intersection = max(0, x_max - x_min) * max(0, y_max - y_min)
        union = (left.x_max - left.x_min) * (left.y_max - left.y_min) + (right.x_max - right.x_min) * (right.y_max - right.y_min) - intersection
        return intersection / union if union else 0.0

    def _is_associated(self, previous: Detection, candidate: Detection, image_width: int, image_height: int) -> bool:
        """判断当前帧候选框是否与上一帧目标关联。
        关联条件：
        1. IoU >= minimum_iou
        2. 或者中心点距离 <= maximum_center_distance_ratio * 图像对角线长度
        """

        if self._iou(previous, candidate) >= self._config.minimum_iou:
            return True
        previous_center, candidate_center = previous.center_pixel, candidate.center_pixel
        dx = (previous_center[0] - candidate_center[0]) / image_width
        dy = (previous_center[1] - candidate_center[1]) / image_height
        return (dx * dx + dy * dy) ** 0.5 <= self._config.maximum_center_distance_ratio
