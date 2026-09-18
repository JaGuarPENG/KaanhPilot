"""厂商无关的相机公共契约。"""

from camera.contracts.errors import CameraError, CameraNotFoundError, CameraProfileError, CameraStateError, CameraStreamError, CameraTimeoutError
from camera.contracts.interface import Camera
from camera.contracts.cam_structs import RGBFrame, AlignedRGBDObservation, AlignmentMode, CameraCapabilities, CameraProfile, CameraState, DepthProcessingConfig, FilterParameterDescriptor, SensorCalibration

__all__ = [
    "RGBFrame", "AlignedRGBDObservation", "AlignmentMode", "Camera", "CameraCapabilities", "CameraError",
    "CameraNotFoundError", "CameraProfile", "CameraProfileError", "CameraState", "CameraStateError",
    "CameraStreamError", "CameraTimeoutError", "DepthProcessingConfig", "FilterParameterDescriptor", "SensorCalibration",
]
