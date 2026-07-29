"""将保存的 RGB 图片和同名 NPZ 点云组合为离线感知观测。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class SavedRGBDObservation:
    """离线快照的 RGB、组织点云和同帧身份信息。

    现有 NPZ 不保存完整传感器标定；ROI 点云定位和 YOLO 推理均不使用标定，
    因而可安全地复放这些字段。该类型不能用于未来的手眼标定或坐标变换。
    """

    frame_id: int
    capture_timestamp_ms: int
    rgb: NDArray[np.uint8]
    point_cloud_m: NDArray[np.float32]

    def __post_init__(self) -> None:
        rgb = np.asarray(self.rgb, dtype=np.uint8)
        cloud = np.asarray(self.point_cloud_m, dtype=np.float32)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or cloud.shape != (*rgb.shape[:2], 3):
            raise ValueError("快照 RGB 与有组织点云尺寸不匹配")
        if self.frame_id < 1 or self.capture_timestamp_ms < 0:
            raise ValueError("快照帧身份信息无效")
        rgb.setflags(write=False)
        cloud.setflags(write=False)
        object.__setattr__(self, "rgb", rgb)
        object.__setattr__(self, "point_cloud_m", cloud)


def load_saved_observation(image_path: Path, cloud_npz_path: Path) -> SavedRGBDObservation:
    """读取 PNG 作为 YOLO 输入，并读取同名 NPZ 作为点云来源。

    PNG 和 NPZ 各自承担唯一职责：检测框从 PNG 像素坐标产生，点云从 NPZ
    按相同 ``(v, u)`` 像素位置裁剪。文件名和尺寸不一致时拒绝执行，避免
    把一张图片的 ROI 错用于另一帧点云。
    """
    if image_path.stem != cloud_npz_path.stem:
        raise ValueError(f"图片与点云文件必须同名: {image_path.name} / {cloud_npz_path.name}")
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"无法读取 RGB 图片: {image_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    with np.load(cloud_npz_path, allow_pickle=False) as data:
        required = {"rgb", "point_cloud_m", "metadata_json"}
        missing = required - set(data.files)
        if missing:
            raise ValueError(f"快照缺少字段: {', '.join(sorted(missing))}")
        metadata = json.loads(str(data["metadata_json"].item()))
        cloud = data["point_cloud_m"].copy()
    if cloud.shape[:2] != rgb.shape[:2]:
        raise ValueError(f"图片尺寸 {rgb.shape[:2]} 与点云尺寸 {cloud.shape[:2]} 不一致")
    return SavedRGBDObservation(int(metadata["frame_id"]), int(metadata["capture_timestamp_ms"]), rgb, cloud)
