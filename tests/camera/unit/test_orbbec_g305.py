"""G305 中无需连接硬件即可验证的纯计算逻辑。"""

import unittest

import numpy as np

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_848X480_60
from camera.contracts.models import CameraIntrinsics


class OrganizedPointCloudTests(unittest.TestCase):
    def test_deprojection_uses_meters_and_nan_for_invalid_depth(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60)
        cloud = camera._build_organized_point_cloud(
            np.array([[1.0, 0.0], [2.0, 3.0]], dtype=np.float32),
            CameraIntrinsics(2, 2, 1.0, 1.0, 0.0, 0.0),
        )
        np.testing.assert_allclose(cloud[0, 0], [0.0, 0.0, 1.0])
        self.assertTrue(np.isnan(cloud[0, 1]).all())
        np.testing.assert_allclose(cloud[1, 0], [0.0, 2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
