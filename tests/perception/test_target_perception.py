"""不依赖真实相机或 .pt 模型的单目标感知单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from camera.adapters.orbbec.profiles import G305_848X480_60
from camera.contracts.cam_structs import AlignedRGBDObservation, AlignmentMode, CameraDistortion, CameraIntrinsics, RigidTransform, SensorCalibration
from perception.percept_structs import LocalizationConfig, TargetStatus
from perception.roi_localizer import RoiPointCloudLocalizer
from perception.saved_observation import load_saved_observation
from perception.session import TargetPerceptionSession
from perception.target_tracker import SingleTargetTracker, TrackerConfig
from yolo.contracts.yolo_structs import Detection, FrameDetectionResult
from yolo.labels import LabelMapping


def make_observation(frame_id: int = 1) -> AlignedRGBDObservation:
    """创建 10x10 的确定性有组织点云，坐标单位为米。"""
    intrinsics = CameraIntrinsics(10, 10, 100.0, 100.0, 5.0, 5.0)
    distortion = CameraDistortion(0.0, 0.0, 0.0, 0.0, 0.0)
    calibration = SensorCalibration(intrinsics, intrinsics, distortion, distortion, RigidTransform(np.eye(3), np.zeros(3)))
    cloud = np.zeros((10, 10, 3), dtype=np.float32)
    cloud[..., 2] = 1.0
    cloud[..., 0] = 0.1
    cloud[..., 1] = -0.1
    return AlignedRGBDObservation(frame_id, frame_id * 100, np.zeros((10, 10, 3), dtype=np.uint8), np.ones((10, 10), dtype=np.float32), cloud, G305_848X480_60, AlignmentMode.SOFTWARE, calibration)


def detection(frame_id: int, x_min: int = 2, y_min: int = 2, x_max: int = 8, y_max: int = 8, confidence: float = 0.9) -> Detection:
    return Detection(0, "mineral_water", confidence, x_min, y_min, x_max, y_max, frame_id, frame_id * 100)


class FakeDetector:
    """用预设候选框模拟检测器，确保测试只验证感知职责。"""

    target_ids = ("mineral_water",)

    def __init__(self, candidates_by_frame: dict[int, tuple[Detection, ...]]) -> None:
        self._candidates_by_frame = candidates_by_frame

    def detect(self, observation: AlignedRGBDObservation, target_id: str) -> FrameDetectionResult:
        return FrameDetectionResult(target_id, observation.frame_id, observation.capture_timestamp_ms, observation.rgb.shape[1], observation.rgb.shape[0], self._candidates_by_frame.get(observation.frame_id, ()))


class LabelMappingTests(unittest.TestCase):
    def test_target_uses_model_label_not_fixed_class_index(self) -> None:
        mapping = LabelMapping({"mineral_water": "mineral_water", "oolong_tea": "oolong_tea", "coco_cola": "coco_cola"})
        model_names = {8: "coco_cola", 3: "mineral_water", 5: "oolong_tea"}
        self.assertEqual(mapping.class_id_for_target(model_names, "mineral_water"), 3)


class RoiLocalizerTests(unittest.TestCase):
    def test_median_target_point_ignores_nan_and_depth_outlier(self) -> None:
        observation = make_observation()
        cloud = observation.point_cloud_m.copy()
        cloud.setflags(write=True)
        cloud[2, 2] = np.array([np.nan, np.nan, np.nan], dtype=np.float32)
        cloud[3, 3, 2] = 1.8
        # 构造新观测以保持不可变数组契约，不修改共享相机观测。
        observation = AlignedRGBDObservation(1, 100, observation.rgb, observation.depth_m, cloud, observation.profile, observation.alignment_mode, observation.calibration)
        result = RoiPointCloudLocalizer(LocalizationConfig(minimum_valid_points=5, depth_inlier_half_width_m=0.05), collect_inspection=True).localize(observation, detection(1))
        self.assertTrue(result.has_target_point)
        self.assertEqual(result.target_point_camera_m, (0.10000000149011612, -0.10000000149011612, 1.0))
        self.assertEqual(result.valid_point_count, 34)
        self.assertIsNotNone(result.inspection)
        self.assertEqual(len(result.inspection.points_m), 34)


class SavedObservationTests(unittest.TestCase):
    def test_png_is_used_as_yolo_input_and_same_name_npz_supplies_cloud(self) -> None:
        observation = make_observation(7)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path, cloud_path = root / "snapshot.png", root / "snapshot.npz"
            # PNG 故意写入非零颜色，验证不会偷用 NPZ 内嵌的旧 RGB。
            image_rgb = np.full_like(observation.rgb, (10, 20, 30))
            import cv2
            self.assertTrue(cv2.imwrite(str(image_path), cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)))
            np.savez_compressed(cloud_path, rgb=observation.rgb, point_cloud_m=observation.point_cloud_m, metadata_json=np.asarray(json.dumps({"frame_id": 7, "capture_timestamp_ms": 700})))
            loaded = load_saved_observation(image_path, cloud_path)
        self.assertEqual((loaded.frame_id, loaded.capture_timestamp_ms), (7, 700))
        np.testing.assert_array_equal(loaded.rgb, image_rgb)
        np.testing.assert_array_equal(loaded.point_cloud_m, observation.point_cloud_m)


class TargetSessionTests(unittest.TestCase):
    def test_never_acquired_target_is_no_match_not_target_lost(self) -> None:
        session = TargetPerceptionSession(FakeDetector({}), RoiPointCloudLocalizer(LocalizationConfig()), SingleTargetTracker(TrackerConfig(maximum_missing_frames=1)), "mineral_water")
        self.assertEqual(session.process(make_observation()).status, TargetStatus.NO_MATCH)

    def test_tracker_keeps_associated_target_instead_of_higher_confidence_peer(self) -> None:
        # 首个 ROI 足够大，可同时覆盖追踪关联和点云定位两条路径。
        first = detection(1, 1, 1, 9, 9, 0.6)
        associated = detection(2, 1, 1, 9, 9, 0.5)
        other = detection(2, 6, 6, 10, 10, 0.99)
        session = TargetPerceptionSession(FakeDetector({1: (first,), 2: (associated, other)}), RoiPointCloudLocalizer(LocalizationConfig()), SingleTargetTracker(TrackerConfig()), "mineral_water")
        self.assertEqual(session.process(make_observation(1)).status, TargetStatus.TARGET_ACQUIRED)
        tracked = session.process(make_observation(2))
        self.assertEqual(tracked.status, TargetStatus.TARGET_TRACKED)
        self.assertEqual(tracked.detection, associated)
        self.assertIsNotNone(tracked.timing)
        self.assertGreaterEqual(tracked.timing.process_total_ms, tracked.timing.yolo_ms)


if __name__ == "__main__":
    unittest.main()
