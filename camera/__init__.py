"""可替换 RGB-D 相机能力的顶层命名空间。

通用消费者应从 camera.contracts 导入接口和数据契约；厂商实现只能从
camera.adapters.<vendor>.<module> 显式导入，避免把 SDK 依赖扩散到调用方。
"""

from camera.contracts import (
    RGBFrame,
    AlignedRGBDObservation,
    AlignmentMode,
    Camera,
    CameraCapabilities,
    CameraError,
    CameraNotFoundError,
    CameraProfile,
    CameraProfileError,
    CameraState,
    CameraStateError,
    CameraStreamError,
    CameraTimeoutError,
    DepthProcessingConfig,
    FilterParameterDescriptor,
    SensorCalibration,
)

__all__ = [
    "RGBFrame", "AlignedRGBDObservation", "AlignmentMode", "Camera", "CameraCapabilities", "CameraError",
    "CameraNotFoundError", "CameraProfile", "CameraProfileError", "CameraState", "CameraStateError",
    "CameraStreamError", "CameraTimeoutError", "DepthProcessingConfig", "FilterParameterDescriptor", "SensorCalibration",
]
