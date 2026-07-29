"""相机数据契约的无硬件单元测试。"""

import unittest

import numpy as np

from camera.contracts.models import (
    AlignedRGBDObservation,
    AlignmentMode,
    CameraDistortion,
    CameraIntrinsics,
    RigidTransform,
    SensorCalibration,
)
from camera.adapters.orbbec.profiles import G305_1280X800_30


def calibration() -> SensorCalibration:
    intrinsics = CameraIntrinsics(2, 2, 100.0, 100.0, 0.5, 0.5)
    distortion = CameraDistortion(0.0, 0.0, 0.0, 0.0, 0.0)
    transform = RigidTransform(np.eye(3), np.zeros(3))
    return SensorCalibration(intrinsics, intrinsics, distortion, distortion, transform)


class ObservationContractTests(unittest.TestCase):
    def test_observation_arrays_are_read_only(self) -> None:
        observation = AlignedRGBDObservation(
            1,
            100,
            np.zeros((2, 2, 3), dtype=np.uint8),
            np.ones((2, 2), dtype=np.float32),
            np.ones((2, 2, 3), dtype=np.float32),
            G305_1280X800_30,
            AlignmentMode.SOFTWARE,
            calibration(),
        )
        self.assertFalse(observation.rgb.flags.writeable)
        self.assertFalse(observation.depth_m.flags.writeable)
        self.assertFalse(observation.point_cloud_m.flags.writeable)

    def test_invalid_point_cloud_shape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AlignedRGBDObservation(
                1, 100, np.zeros((2, 2, 3), dtype=np.uint8), np.ones((2, 2), dtype=np.float32),
                np.ones((2, 2), dtype=np.float32), G305_1280X800_30, AlignmentMode.SOFTWARE, calibration(),
            )


if __name__ == "__main__":
    unittest.main()
