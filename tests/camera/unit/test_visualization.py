"""点云显示坐标变换的无窗口单元测试。"""

import unittest

import numpy as np

from camera.visualization.rgbd_viewer import ObservationVisualizer


class PointCloudDisplayCoordinatesTests(unittest.TestCase):
    def test_optical_frame_is_rotated_for_open3d_display_only(self) -> None:
        camera_points = np.array([[1.0, 2.0, 3.0], [-1.0, -2.0, 0.5]], dtype=np.float32)
        displayed = ObservationVisualizer._to_display_coordinates(camera_points)
        np.testing.assert_allclose(displayed, [[1.0, -2.0, -3.0], [-1.0, 2.0, -0.5]])


if __name__ == "__main__":
    unittest.main()
