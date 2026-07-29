"""YOLO 检测结果的稳定数据结构，不暴露 Ultralytics 的 Results 对象。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Detection:
    """基于单帧图像的YOLO检测结果

    bbox 使用原始 RGB 图像的 ``xyxy`` 像素坐标。右下角采用切片上界语义，
    因此可直接用于 ``point_cloud_m[y_min:y_max, x_min:x_max]``。

    参数表：
    - model_class_id: 模型输出的类别索引。
    - target_id: 识别目标的唯一标识符。
    - confidence: 检测置信度，范围 [0, 1]。
    - x_min, y_min, x_max, y_max: 检测框的 xyxy 像素坐标。
    - frame_id: 检测结果来源的 RGB-D 观测的 frame_id。
    - capture_timestamp_ms: 检测结果来源的 RGB-D 观测的采集时间戳（毫秒）。

    属性
    -------
    bbox_xyxy : tuple[int, int, int, int]
        检测框的 xyxy 像素坐标。
    center_pixel : tuple[float, float]
        检测框的中心像素坐标。  

    """

    model_class_id: int
    target_id: str
    confidence: float
    x_min: int
    y_min: int
    x_max: int
    y_max: int
    frame_id: int
    capture_timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target_id 不能为空")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须位于 [0, 1]")
        if self.x_min < 0 or self.y_min < 0 or self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("bbox 必须是有效的非负 xyxy 像素区域")
        if self.frame_id < 1 or self.capture_timestamp_ms < 0:
            raise ValueError("来源帧信息无效")

    @property
    def bbox_xyxy(self) -> tuple[int, int, int, int]:
        return self.x_min, self.y_min, self.x_max, self.y_max

    @property
    def center_pixel(self) -> tuple[float, float]:
        return (self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0


@dataclass(frozen=True, slots=True)
class FrameDetectionResult:
    """一个指定目标在一帧中的全部候选检测结果。
    
    参数表：
    - target_id: 识别目标的唯一标识符。
    - frame_id: 检测结果来源的 RGB-D 观测的 frame_id
    - capture_timestamp_ms: 检测结果来源的 RGB-D 观测的采集时间戳（毫秒）。
    - image_width, image_height: 检测结果来源的 RGB 图像的尺寸。
    - detections: 检测结果列表；如果未检测到目标则为空元组。
    """

    target_id: str
    frame_id: int
    capture_timestamp_ms: int
    image_width: int
    image_height: int
    detections: tuple[Detection, ...]

    def __post_init__(self) -> None:
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("图像尺寸必须为正数")
        for detection in self.detections:
            if detection.target_id != self.target_id:
                raise ValueError("识别目标id与要求的 target_id 不一致")
            if detection.frame_id != self.frame_id:
                raise ValueError("检测框必须与结果属于同一 frame_id")
