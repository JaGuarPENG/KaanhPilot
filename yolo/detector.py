"""Ultralytics .pt 检测器适配器。"""

from __future__ import annotations

from math import ceil, floor
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from camera.contracts.cam_structs import AlignedRGBDObservation
from yolo.contracts.yolo_structs import Detection, FrameDetectionResult
from yolo.labels import LabelMapping


class Detector(Protocol):
    """可替换检测后端的统一接口，未来 ONNX/TensorRT 复用此接口。"""

    @property
    def target_ids(self) -> tuple[str, ...]: ...

    def detect(self, observation: AlignedRGBDObservation, target_id: str) -> FrameDetectionResult: ...

    def warmup(self, image_width: int, image_height: int) -> None: ...


class UltralyticsPtDetector(Detector):
    """将当前 ``.pt`` 模型的原始输出转换为项目 Detection。Ultralytics 仅在构造时导入，使不安装推理依赖的单元测试仍可运行。

    输入参数表：
    - model_path: YOLO .pt 模型文件路径。
    - labels: 目标 ID 与 class_id 映射。
    - confidence_threshold: 置信度阈值，范围 [0, 1]。
    - iou_threshold: NMS IoU 阈值，范围 [0, 1]。

    """

    def __init__(self, model_path: Path, labels: LabelMapping, confidence_threshold: float = 0.45, iou_threshold: float = 0.7) -> None:
        if not 0.0 <= confidence_threshold <= 1.0 or not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("YOLO 置信度和 IoU 阈值必须位于 [0, 1]")
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError("未安装 ultralytics；请在运行环境中安装后再加载 .pt 模型") from error

        self._model = YOLO(str(model_path))
        self._labels = labels
        self._confidence_threshold = confidence_threshold
        self._iou_threshold = iou_threshold
        raw_names = self._model.names
        self._model_names = {int(class_id): str(name) for class_id, name in dict(raw_names).items()}
        self._labels.validate_model_names(self._model_names)

    @property
    def target_ids(self) -> tuple[str, ...]:
        return self._labels.target_ids

    def detect(self, observation: AlignedRGBDObservation, target_id: str) -> FrameDetectionResult:
        """ 对单帧 RGB 图像进行 YOLO 推理，并将输出转换为 Detection。

        输入参数表：
        - observation: 相机契约的 RGBD 观测。
        - target_id: 仅返回与此目标 ID 匹配的检测框。

        输出参数表：
        - FrameDetectionResult: 包含所有匹配目标的检测框列表。

        """
        class_id = self._labels.class_id_for_target(self._model_names, target_id)
        # 相机契约规定 rgb 为 RGB；Ultralytics 对 ndarray 输入沿用 OpenCV 的 BGR 约定，
        # 故在适配边界转换一次，输出框仍然是原始 RGB 图像坐标系。
        bgr = np.ascontiguousarray(observation.rgb[..., ::-1])
        prediction = self._model.predict(
            source=bgr,
            classes=[class_id],
            conf=self._confidence_threshold,
            iou=self._iou_threshold,
            verbose=False,
        )[0]
        height, width = observation.rgb.shape[:2]
        detections: list[Detection] = []
        boxes = prediction.boxes
        if boxes is not None:
            for xyxy, confidence, detected_class_id in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(), boxes.cls.cpu().numpy()):
                x_min = max(0, floor(float(xyxy[0])))
                y_min = max(0, floor(float(xyxy[1])))
                x_max = min(width, ceil(float(xyxy[2])))
                y_max = min(height, ceil(float(xyxy[3])))
                # 极小或越界候选框无法形成有效 ROI，不能传入点云定位层。
                if x_max <= x_min or y_max <= y_min:
                    continue
                detections.append(Detection(int(detected_class_id), target_id, float(confidence), x_min, y_min, x_max, y_max, observation.frame_id, observation.capture_timestamp_ms))
        return FrameDetectionResult(target_id, observation.frame_id, observation.capture_timestamp_ms, width, height, tuple(detections))
    

    def warmup(self, image_width: int, image_height: int) -> None:
        """用实际相机尺寸的空 RGB 图触发推理后端的首次初始化。"""
        if image_width <= 0 or image_height <= 0:
            raise ValueError("预热图像宽高必须为正数")

        rgb = np.zeros((image_height, image_width, 3), dtype=np.uint8)
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        self._model.predict(
            source=bgr,
            conf=self._confidence_threshold,
            iou=self._iou_threshold,
            verbose=False,
        )