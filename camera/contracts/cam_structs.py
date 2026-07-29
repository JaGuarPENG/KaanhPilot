"""厂商无关的 RGB-D 相机数据契约。

所有数组均使用 NumPy；观测发布前会设为只读，避免消费者在共享最新帧缓冲中修改数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from numpy.typing import NDArray


class AlignmentMode(str, Enum):
    """深度向彩色像素坐标系注册时采用的实现方式。"""

    HARDWARE = "hardware"
    SOFTWARE = "software"
    AUTO = "auto"


class CameraState(str, Enum):
    """相机流生命周期状态。FAILED 是终止状态，不会自动重连。"""

    STOPPED = "stopped"
    STARTING = "starting"
    STREAMING = "streaming"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class DepthProcessingConfig:
    """面向效果的可选深度处理配置，距离单位统一为米。

    该配置不暴露任意厂商 SDK 类型。适配器必须报告无法支持的配置，不能静默忽略。
    默认实例保持所有处理关闭，确保与已有未经滤波的 RGB-D 行为一致。

    参数表及说明：
    - temporal_enabled: 是否启用时域滤波（去除随机噪点）
    - spatial_enabled: 是否启用空间滤波（去除孤立点）
    - hole_filling_enabled: 是否启用孔洞填充（插值缺失点）
    - spatial_magnitude: 空间滤波迭代次数，范围 1-5，越大越平滑但越慢，默认 1
    - spatial_alpha: 空间滤波当前像素权重，范围 0.1-1.0，越大越平滑但越慢，默认 0.5
    - hole_filling_mode: 孔洞填充模式，0=TOP（优先填充近处点）、1=NEAREST（优先填充最近点）、2=FAREST（优先填充远处点），默认 0
    - minimum_depth_m: 可选的最小深度阈值，低于该值的点会被视为无效点，必须同时指定最大深度阈值
    - maximum_depth_m: 可选的最大深度阈值，高于该值的点会被视为无效点，必须同时指定最小深度阈值
    """

    #TODO: 后续考虑兼容RealSense SDK的深度处理参数，增加更多可配置项
    temporal_enabled: bool = False
    spatial_enabled: bool = False
    hole_filling_enabled: bool = False
    spatial_magnitude: int = 1
    spatial_alpha: float = 0.5
    hole_filling_mode: int = 0
    minimum_depth_m: float | None = None
    maximum_depth_m: float | None = None

    def __post_init__(self) -> None:
        if (self.minimum_depth_m is None) != (self.maximum_depth_m is None):
            raise ValueError("深度阈值必须同时指定最小和最大距离")
        if self.minimum_depth_m is not None:
            if self.minimum_depth_m < 0.0 or self.maximum_depth_m is None or self.maximum_depth_m <= self.minimum_depth_m:
                raise ValueError("深度阈值必须满足 0 <= minimum_depth_m < maximum_depth_m")
        if isinstance(self.spatial_magnitude, bool) or not isinstance(self.spatial_magnitude, int) or not 1 <= self.spatial_magnitude <= 5:
            raise ValueError("spatial_magnitude 必须是 1 到 5 的整数")
        if not 0.1 <= self.spatial_alpha <= 1.0:
            raise ValueError("spatial_alpha 必须在 0.1 到 1.0 之间")
        if isinstance(self.hole_filling_mode, bool) or not isinstance(self.hole_filling_mode, int) or self.hole_filling_mode not in (0, 1, 2):
            raise ValueError("hole_filling_mode 必须是 0（TOP）、1（NEAREST）或 2（FAREST）")

    @property
    def enabled(self) -> bool:
        """是否至少启用了一项会改变深度数据的处理。"""
        return self.temporal_enabled or self.spatial_enabled or self.hole_filling_enabled or self.minimum_depth_m is not None
    
    def filter_status(self) -> str:
        """返回一个简短的字符串，描述当前深度处理配置的启用状态。"""
        status = []
        if self.temporal_enabled:
            status.append("temporal")
        if self.spatial_enabled:
            status.append(f"spatial(mag={self.spatial_magnitude},alpha={self.spatial_alpha})")
        if self.hole_filling_enabled:
            status.append(f"hole_filling(mode={self.hole_filling_mode})")
        if self.minimum_depth_m is not None and self.maximum_depth_m is not None:
            status.append(f"depth_range({self.minimum_depth_m:.2f}-{self.maximum_depth_m:.2f}m)")
        return ", ".join(status) if status else "none"


@dataclass(frozen=True, slots=True)
class FilterParameterDescriptor:
    """一个厂商滤波器可配置项的可打印描述。

    该对象只描述参数能力，不要求所有未来相机适配器支持同名参数。
    取值使用 SDK 返回的原始标量或字符串化结果，便于诊断命令序列化为 JSON。
    """

    filter_name: str
    name: str
    description: str
    value_type: str
    default_value: str | bool | int | float | None
    minimum: str | bool | int | float | None
    maximum: str | bool | int | float | None
    step: str | bool | int | float | None


@dataclass(frozen=True, slots=True)
class CameraProfile:
    """明确请求的一对彩色/深度视频流参数。
    
    参数表：
    - color_width: 彩色图像宽度（像素）
    - color_height: 彩色图像高度（像素）
    - color_fps: 彩色图像帧率（帧/秒）
    - color_format: 彩色图像格式（字符串）
    - depth_width: 深度图像宽度（像素）
    - depth_height: 深度图像高度（像素）
    - depth_fps: 深度图像帧率（帧/秒）
    - depth_format: 深度图像格式（字符串）

    """

    color_width: int
    color_height: int
    color_fps: int
    color_format: str
    depth_width: int
    depth_height: int
    depth_fps: int
    depth_format: str

    def __post_init__(self) -> None:
        if min(self.color_width, self.color_height, self.color_fps, self.depth_width, self.depth_height, self.depth_fps) <= 0:
            raise ValueError("图像尺寸和帧率必须为正数")


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    """针孔相机内参；坐标单位为像素。"""

    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass(frozen=True, slots=True)
class CameraDistortion:
    """镜头径向和切向畸变系数。"""

    k1: float
    k2: float
    k3: float
    p1: float
    p2: float


@dataclass(frozen=True, slots=True)
class RigidTransform:
    """从深度相机光学坐标系到彩色相机光学坐标系的外参，平移单位为米。"""

    rotation: NDArray[np.float64]
    translation_m: NDArray[np.float64]

    def __post_init__(self) -> None:
        rotation = np.asarray(self.rotation, dtype=np.float64)
        translation = np.asarray(self.translation_m, dtype=np.float64)
        if rotation.shape != (3, 3) or translation.shape != (3,):
            raise ValueError("外参旋转矩阵必须为 3x3，平移向量必须有 3 个元素")
        rotation.setflags(write=False)
        translation.setflags(write=False)
        object.__setattr__(self, "rotation", rotation)
        object.__setattr__(self, "translation_m", translation)


@dataclass(frozen=True, slots=True)
class SensorCalibration:
    """当前实际 Profile 对应的传感器标定数据。
    
    参数表：
    - rgb_intrinsics: 彩色相机内参
    - depth_intrinsics: 深度相机内参
    - rgb_distortion: 彩色相机畸变系数
    - depth_distortion: 深度相机畸变系数
    - depth_to_rgb: 深度相机到彩色相机的外参
    """

    rgb_intrinsics: CameraIntrinsics
    depth_intrinsics: CameraIntrinsics
    rgb_distortion: CameraDistortion
    depth_distortion: CameraDistortion
    depth_to_rgb: RigidTransform


@dataclass(frozen=True, slots=True)
class CameraCapabilities:
    """适配器静态声明的能力；实际可用 Profile 仍取决于当前设备。"""
    camera_id: str
    supported_profiles: tuple[CameraProfile, ...]
    software_alignment_profiles: tuple[CameraProfile, ...]
    hardware_alignment_profiles: tuple[CameraProfile, ...]


@dataclass(frozen=True, slots=True)
class AlignedRGBDObservation:
    """同一时刻的不可变对齐 RGB-D 观测。

    depth_m 与 point_cloud_m 均在 RGB 像素空间中排列。点云坐标在
    camera_optical_frame（即彩色相机光学坐标系）中，非法点为 NaN。

    默认 point_cloud_m[v, u] 与 rgb[v, u] 和 depth_m[v, u] 对齐

    参数表：
    - frame_id: 观测帧 ID，从 1 开始递增
    - capture_timestamp_ms: 观测采集时间戳（毫秒）
    - rgb: 彩色图像，H x W x 3，uint8
    - depth_m: 对齐深度图，H x W，float32，单位为米
    - point_cloud_m: 有组织点云，H x W x 3，float32，单位为米
    - profile: 当前实际 Profile
    - alignment_mode: 当前实际对齐模式
    - calibration: 当前实际传感器标定数据
    - depth_processing: 当前实际深度处理配置
    """

    frame_id: int
    capture_timestamp_ms: int
    rgb: NDArray[np.uint8]
    depth_m: NDArray[np.float32]
    point_cloud_m: NDArray[np.float32]
    profile: CameraProfile
    alignment_mode: AlignmentMode
    calibration: SensorCalibration
    depth_processing: DepthProcessingConfig = field(default_factory=DepthProcessingConfig)

    def __post_init__(self) -> None:
        rgb = np.asarray(self.rgb, dtype=np.uint8)
        depth_m = np.asarray(self.depth_m, dtype=np.float32)
        point_cloud_m = np.asarray(self.point_cloud_m, dtype=np.float32)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("RGB 图像必须为 H x W x 3")
        if depth_m.shape != rgb.shape[:2]:
            raise ValueError("对齐深度图必须与 RGB 图像尺寸一致")
        if point_cloud_m.shape != (*rgb.shape[:2], 3):
            raise ValueError("有组织点云必须为 H x W x 3")
        if self.frame_id < 1 or self.capture_timestamp_ms < 0:
            raise ValueError("frame_id 必须从 1 开始，采集时间戳不可为负")
        # 只读标志能防止最新帧消费者意外篡改共享观测。
        for array in (rgb, depth_m, point_cloud_m):
            array.setflags(write=False)
        object.__setattr__(self, "rgb", rgb)
        object.__setattr__(self, "depth_m", depth_m)
        object.__setattr__(self, "point_cloud_m", point_cloud_m)


if __name__ == "__main__":
    print("这里定义厂商无关的数据契约；请运行 camera.adapters.orbbec.profiles 查看 G305 Profile。")
