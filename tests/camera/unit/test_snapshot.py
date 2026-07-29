"""相机快照存储的无硬件单元测试。"""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from camera.adapters.orbbec.profiles import G305_848X480_60
from camera.contracts.models import (
    AlignedRGBDObservation,
    AlignmentMode,
    CameraDistortion,
    CameraIntrinsics,
    DepthProcessingConfig,
    RigidTransform,
    SensorCalibration,
)
from camera.storage.snapshot import save_observation_snapshot


def make_calibration() -> SensorCalibration:
    intrinsics = CameraIntrinsics(2, 2, 100.0, 100.0, 0.5, 0.5)
    distortion = CameraDistortion(0.0, 0.0, 0.0, 0.0, 0.0)
    return SensorCalibration(intrinsics, intrinsics, distortion, distortion, RigidTransform(np.eye(3), np.zeros(3)))


class SnapshotStorageTests(unittest.TestCase):
    def test_png_and_filtered_organized_cloud_share_timestamp_name(self) -> None:
        processing = DepthProcessingConfig(spatial_enabled=True, spatial_magnitude=2, spatial_alpha=0.6)
        rgb = np.array([[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [128, 128, 128]]], dtype=np.uint8)
        cloud = np.arange(12, dtype=np.float32).reshape(2, 2, 3)
        observation = AlignedRGBDObservation(1, 1234, rgb, np.ones((2, 2), dtype=np.float32), cloud, G305_848X480_60, AlignmentMode.SOFTWARE, make_calibration(), processing)
        with tempfile.TemporaryDirectory() as directory:
            saved = save_observation_snapshot(observation, "test-camera", Path(directory))
            self.assertEqual(saved.image_path.stem, saved.cloud_npz_path.stem)
            self.assertEqual(saved.image_path.stem, saved.cloud_ply_path.stem)
            self.assertTrue(saved.image_path.is_file())
            self.assertTrue(saved.cloud_npz_path.is_file())
            self.assertTrue(saved.cloud_ply_path.is_file())
            # PNG 以 BGR 写出，读取后转换为 RGB 应与公共观测完全相同。
            read_rgb = cv2.cvtColor(cv2.imread(str(saved.image_path)), cv2.COLOR_BGR2RGB)
            np.testing.assert_array_equal(read_rgb, rgb)
            with np.load(saved.cloud_npz_path) as data:
                np.testing.assert_array_equal(data["point_cloud_m"], cloud)
                self.assertEqual(json.loads(str(data["metadata_json"]))["depth_processing"]["spatial_magnitude"], 2)
            ply_header = saved.cloud_ply_path.read_bytes().split(b"end_header\n", maxsplit=1)[0].decode("ascii")
            self.assertIn("format binary_little_endian 1.0", ply_header)
            self.assertIn("element vertex 4", ply_header)


if __name__ == "__main__":
    unittest.main()
