"""感知层的领域结果。它只依赖统一的相机观测和检测结果。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from yolo.contracts.yolo_structs import Detection


class TargetStatus(str, Enum):
    """单目标会话在每个输入帧结束后的状态。"""

    TARGET_ACQUIRED = "target_acquired"
    TARGET_TRACKED = "target_tracked"
    NO_MATCH = "no_match"
    NO_TARGET_POINT = "no_target_point"
    TARGET_LOST = "target_lost"


@dataclass(frozen=True, slots=True)
class PixelROI:
    """静态允许区域，采用右下角为切片上界的 xyxy 像素坐标。"""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    def __post_init__(self) -> None:
        if self.x_min < 0 or self.y_min < 0 or self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("静态 2D ROI 必须是有效的非负 xyxy 区域")

    def intersect(self, other: "PixelROI") -> "PixelROI | None":
        x_min, y_min = max(self.x_min, other.x_min), max(self.y_min, other.y_min)
        x_max, y_max = min(self.x_max, other.x_max), min(self.y_max, other.y_max)
        return PixelROI(x_min, y_min, x_max, y_max) if x_max > x_min and y_max > y_min else None


@dataclass(frozen=True, slots=True)
class Workspace3D:
    """相机光学坐标系中的轴对齐工作空间，单位为米。"""

    minimum_m: tuple[float, float, float]
    maximum_m: tuple[float, float, float]

    def __post_init__(self) -> None:
        if any(low >= high for low, high in zip(self.minimum_m, self.maximum_m)):
            raise ValueError("3D 工作空间的每个最小值必须小于最大值")


@dataclass(frozen=True, slots=True)
class LocalizationConfig:
    """ROI 点云过滤参数，所有物理范围由命令调用者显式指定。
    
    参数表：
    - minimum_depth_m: 可选的最小深度（米），如果未指定则不限制。
    - maximum_depth_m: 可选的最大深度（米），如果未指定则不限制。
    - static_roi: 可选的静态像素 ROI，所有检测框必须在此 ROI 内部才能进行点云定位。
    - workspace: 可选的 3D 工作空间，所有点云必须在此工作空间内才能进行点云定位。
    - roi_shrink_ratio: ROI 收缩比例，默认 0.10，表示在检测框周围收缩 10% 的 ROI 用于点云定位。
    - minimum_valid_points: 最小有效点数，默认 30，表示在 ROI 内至少需要 30 个有效点才能进行点云定位。
    - depth_inlier_half_width_m: 深度内点窗口，默认 0.05 米，表示在 ROI 内的点云深度必须在目标深度的 ±0.05 米范围内才能被认为是有效点。

    """

    minimum_depth_m: float | None = None
    maximum_depth_m: float | None = None
    static_roi: PixelROI | None = None
    workspace: Workspace3D | None = None
    roi_shrink_ratio: float = 0.10
    minimum_valid_points: int = 30
    depth_inlier_half_width_m: float = 0.05

    def __post_init__(self) -> None:
        if (self.minimum_depth_m is None) != (self.maximum_depth_m is None):
            raise ValueError("最小和最大深度必须同时指定")
        if self.minimum_depth_m is not None and (self.minimum_depth_m < 0 or self.maximum_depth_m is None or self.maximum_depth_m <= self.minimum_depth_m):
            raise ValueError("深度范围必须满足 0 <= minimum < maximum")
        if not 0.0 <= self.roi_shrink_ratio < 0.5:
            raise ValueError("ROI 收缩比例必须位于 [0, 0.5)")
        if self.minimum_valid_points < 1 or self.depth_inlier_half_width_m <= 0.0:
            raise ValueError("最小有效点数和深度内点窗口必须为正数")


@dataclass(frozen=True, slots=True)
class PointCloudInspection:
    """仅用于显示和离线诊断的最终 ROI 点云。

    ``points_m`` 是已经通过 NaN、深度范围、3D 工作空间和深度中位数
    过滤的最终内点；抓取点正是由这些点计算得到。生产接口只使用
    ``LocalizationResult.target_point_camera_m``，不会依赖该调试数据。
    """

    points_m: NDArray[np.float32]
    colors_rgb: NDArray[np.uint8]

    def __post_init__(self) -> None:
        points = np.asarray(self.points_m, dtype=np.float32)
        colors = np.asarray(self.colors_rgb, dtype=np.uint8)
        if points.ndim != 2 or points.shape[1] != 3 or colors.shape != points.shape:
            raise ValueError("诊断点云和颜色必须均为 N x 3 数组")
        points.setflags(write=False)
        colors.setflags(write=False)
        object.__setattr__(self, "points_m", points)
        object.__setattr__(self, "colors_rgb", colors)


@dataclass(frozen=True, slots=True)
class ProcessingTiming:
    """同一帧的主机侧处理耗时，单位均为毫秒。

    这些数值以 ``time.perf_counter_ns`` 测量，描述 Python 进程从拿到一帧
    到输出结果的计算耗时；设备采集时间与主机时钟不同源，不能据此推导
    相机曝光到结果的绝对端到端延迟。
    """

    yolo_ms: float
    tracking_ms: float
    localization_ms: float
    process_total_ms: float


@dataclass(frozen=True, slots=True)
class LocalizationResult:
    """对 ROI 内点云的定位结果；目标点始终在 camera_optical_frame、单位米。
    
    参数表：
    - detection: 检测结果
    - target_point_camera_m: 目标点在相机光学坐标系中的三维坐标，单位为米；如果无法定位则为 None。
    - roi: 用于点云定位的有效像素 ROI；如果无法定位则为 None。
    - roi_point_count: ROI 内的点云总数。
    - valid_point_count: ROI 内的有效点云数量。
    - depth_median_m: ROI 内有效点云的深度中位数，单位为米；如果无法定位则为 None。
    - depth_spread_m: ROI 内有效点云的深度分布，单位为米；如果无法定位则为 None。
    """

    detection: Detection
    target_point_camera_m: tuple[float, float, float] | None
    roi: PixelROI | None
    roi_point_count: int
    valid_point_count: int
    depth_median_m: float | None
    depth_spread_m: float | None
    inspection: PointCloudInspection | None = None

    @property
    def has_target_point(self) -> bool:
        return self.target_point_camera_m is not None


@dataclass(frozen=True, slots=True)
class TargetPerceptionResult:
    """单次识别会话的结果，包含检测和定位信息。
    
    参数表：
    - target_id: 目标 ID
    - frame_id: 帧 ID
    - capture_timestamp_ms: 捕获时间戳（毫秒）
    - status: 目标状态
    - detection: 检测结果；如果未检测到目标则为 None。
    - localization: 定位结果；如果未定位到目标则为 None。
    - consecutive_missing_frames: 连续未检测到目标的帧数。
    """

    target_id: str
    frame_id: int
    capture_timestamp_ms: int
    status: TargetStatus
    detection: Detection | None
    localization: LocalizationResult | None
    consecutive_missing_frames: int
    timing: ProcessingTiming | None = None
