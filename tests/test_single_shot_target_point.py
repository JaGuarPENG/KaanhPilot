import unittest
import tempfile
from pathlib import Path

import numpy as np

from camera.adapters.orbbec.profiles import G305_848X480_60
from camera.contracts.cam_structs import (
    AlignedRGBDObservation,
    AlignmentMode,
    CameraDistortion,
    CameraIntrinsics,
    RigidTransform,
    SensorCalibration,
)
from perception.percept_structs import LocalizationResult
from planner.camera_transform import TransformResult
from commands.snapshot import (
    SnapShotCommand,
    TargetPointUnavailableError,
)
from yolo.contracts.yolo_structs import Detection, FrameDetectionResult


def make_observation(frame_id):
    intrinsics = CameraIntrinsics(4, 4, 100.0, 100.0, 2.0, 2.0)
    distortion = CameraDistortion(0.0, 0.0, 0.0, 0.0, 0.0)
    calibration = SensorCalibration(
        intrinsics,
        intrinsics,
        distortion,
        distortion,
        RigidTransform(np.eye(3), np.zeros(3)),
    )
    cloud = np.zeros((4, 4, 3), dtype=np.float32)
    cloud[..., 2] = 1.0
    return AlignedRGBDObservation(
        frame_id,
        frame_id * 100,
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.ones((4, 4), dtype=np.float32),
        cloud,
        G305_848X480_60,
        AlignmentMode.SOFTWARE,
        calibration,
    )


class FakeCamera:
    def __init__(self, observation):
        self.observation = observation
        self.close_calls = 0

    def get_latest_observation(self):
        return self.observation

    def close(self):
        self.close_calls += 1


class FakeDetector:
    target_ids = ("oolong_tea",)

    def __init__(self, with_detection=True):
        self.with_detection = with_detection
        self.detect_calls = 0
        self.warmup_calls = []

    def warmup(self, image_width, image_height):
        self.warmup_calls.append((image_width, image_height))

    def detect(self, observation, target_id):
        self.detect_calls += 1
        detections = ()
        if self.with_detection:
            detections = (
                Detection(
                    0,
                    target_id,
                    0.9,
                    0,
                    0,
                    4,
                    4,
                    observation.frame_id,
                    observation.capture_timestamp_ms,
                ),
            )
        return FrameDetectionResult(
            target_id,
            observation.frame_id,
            observation.capture_timestamp_ms,
            4,
            4,
            detections,
        )


class FakeMultipleDetector(FakeDetector):
    def detect(self, observation, target_id):
        self.detect_calls += 1
        return FrameDetectionResult(
            target_id,
            observation.frame_id,
            observation.capture_timestamp_ms,
            4,
            4,
            (
                Detection(
                    0,
                    target_id,
                    0.99,
                    0,
                    0,
                    2,
                    4,
                    observation.frame_id,
                    observation.capture_timestamp_ms,
                ),
                Detection(
                    0,
                    target_id,
                    0.60,
                    2,
                    0,
                    4,
                    4,
                    observation.frame_id,
                    observation.capture_timestamp_ms,
                ),
            ),
        )


class FakeLocalizer:
    def localize(self, observation, detection):
        return LocalizationResult(
            detection=detection,
            target_point_camera_m=(0.1, 0.2, 1.0),
            roi=None,
            roi_point_count=16,
            valid_point_count=16,
            depth_median_m=1.0,
            depth_spread_m=0.0,
        )


class FakeNoPointLocalizer:
    def localize(self, observation, detection):
        return LocalizationResult(
            detection=detection,
            target_point_camera_m=None,
            roi=None,
            roi_point_count=16,
            valid_point_count=0,
            depth_median_m=None,
            depth_spread_m=None,
        )


class FakeMultipleLocalizer:
    def localize(self, observation, detection):
        point = (0.8, 0.0, 1.0) if detection.x_min == 0 else (0.1, 0.0, 1.0)
        return LocalizationResult(
            detection=detection,
            target_point_camera_m=point,
            roi=None,
            roi_point_count=8,
            valid_point_count=8,
            depth_median_m=point[2],
            depth_spread_m=0.0,
        )


class FakeTransform:
    def result2base(self, result, cam_index, rbt_pq):
        return TransformResult(
            target_id=result.target_id,
            frame_id=result.frame_id,
            capture_timestamp_ms=result.capture_timestamp_ms,
            status=result.status,
            target_point_base_m=(0.4, 0.5, 0.6),
        )


class FakeIdentityTransform:
    def result2base(self, result, cam_index, rbt_pq):
        return TransformResult(
            target_id=result.target_id,
            frame_id=result.frame_id,
            capture_timestamp_ms=result.capture_timestamp_ms,
            status=result.status,
            target_point_base_m=result.localization.target_point_camera_m,
        )


class FakeModelState:
    tcp_pq = [100.0, 200.0, 300.0, 0.0, 0.0, 0.0, 1.0]


class FakeRobotState:
    has_error = False
    moving = False

    def get_model(self, model_id):
        return FakeModelState()


class FakeRobot:
    def get_robot_state(self):
        return FakeRobotState()


class Config:
    from perception.target_tracker import TrackerConfig

    tracker = TrackerConfig()


def initialized_command(detector, localizer=None, transform=None):
    camera = FakeCamera(make_observation(2))
    command = SnapShotCommand(
        FakeRobot(),
        camera,
        detector,
        localizer or FakeLocalizer(),
        transform or FakeTransform(),
        Config.tracker,
    )
    command._last_frame_id = 1
    command._initialized = True
    return command


class SingleShotTargetPointTests(unittest.TestCase):
    def test_initialize_uses_injected_dependencies(self):
        camera = FakeCamera(make_observation(2))
        detector = FakeDetector()
        localizer = FakeLocalizer()
        transform = FakeTransform()
        command = SnapShotCommand(
            FakeRobot(),
            camera,
            detector,
            localizer,
            transform,
            Config.tracker,
        )

        command.initialize_resources()

        self.assertTrue(command.is_initialized)
        self.assertIs(command._detector, detector)
        self.assertIs(command._localizer, localizer)
        self.assertIs(command._camera_transform, transform)
        self.assertEqual(detector.warmup_calls, [(4, 4)])

    def test_capture_returns_camera_and_base_target_point_from_one_inference(self):
        detector = FakeDetector()
        command = initialized_command(detector)

        target_point = command.capture_once("oolong_tea")

        self.assertEqual(target_point.target_id, "oolong_tea")
        self.assertEqual(target_point.frame_id, 2)
        self.assertEqual(target_point.target_point_camera_m, (0.1, 0.2, 1.0))
        self.assertEqual(target_point.target_point_base_m, (0.4, 0.5, 0.6))
        self.assertEqual(detector.detect_calls, 1)

    def test_capture_returns_none_when_yolo_does_not_select_target(self):
        command = initialized_command(FakeDetector(with_detection=False))

        target_point = command.capture_once("oolong_tea")

        self.assertIsNone(target_point)

    def test_reference_match_prefers_nearest_candidate_over_higher_confidence(self):
        detector = FakeMultipleDetector()
        command = initialized_command(
            detector,
            FakeMultipleLocalizer(),
            FakeIdentityTransform(),
        )

        target_point = command.capture_once(
            "oolong_tea",
            reference_point_base_m=(0.11, 0.0, 1.0),
            maximum_match_distance_m=0.06,
        )

        self.assertEqual(target_point.target_point_base_m, (0.1, 0.0, 1.0))
        self.assertEqual(target_point.detection_confidence, 0.60)
        self.assertEqual(detector.detect_calls, 1)

    def test_capture_without_reference_keeps_highest_confidence_behavior(self):
        detector = FakeMultipleDetector()
        command = initialized_command(
            detector,
            FakeMultipleLocalizer(),
            FakeIdentityTransform(),
        )

        target_point = command.capture_once("oolong_tea")

        self.assertEqual(target_point.target_point_base_m, (0.8, 0.0, 1.0))
        self.assertEqual(target_point.detection_confidence, 0.99)
        self.assertEqual(detector.detect_calls, 1)

    def test_reference_match_returns_none_when_nearest_candidate_is_too_far(self):
        command = initialized_command(
            FakeMultipleDetector(),
            FakeMultipleLocalizer(),
            FakeIdentityTransform(),
        )

        target_point = command.capture_once(
            "oolong_tea",
            reference_point_base_m=(0.0, 0.0, 0.0),
            maximum_match_distance_m=0.06,
        )

        self.assertIsNone(target_point)

    def test_reference_match_keeps_error_when_all_candidates_have_no_3d_point(self):
        command = initialized_command(
            FakeMultipleDetector(),
            FakeNoPointLocalizer(),
            FakeIdentityTransform(),
        )

        with self.assertRaisesRegex(TargetPointUnavailableError, "没有有效三维点"):
            command.capture_once(
                "oolong_tea",
                reference_point_base_m=(0.1, 0.0, 1.0),
                maximum_match_distance_m=0.06,
            )

    def test_reference_match_parameters_must_be_valid_and_provided_together(self):
        invalid_arguments = (
            {"reference_point_base_m": (0.1, 0.2, 0.3)},
            {"maximum_match_distance_m": 0.06},
            {
                "reference_point_base_m": (0.1, 0.2),
                "maximum_match_distance_m": 0.06,
            },
            {
                "reference_point_base_m": (0.1, 0.2, float("nan")),
                "maximum_match_distance_m": 0.06,
            },
            {
                "reference_point_base_m": (0.1, 0.2, 0.3),
                "maximum_match_distance_m": 0.0,
            },
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                command = initialized_command(FakeDetector())
                with self.assertRaises(ValueError):
                    command.capture_once("oolong_tea", **arguments)

    def test_capture_keeps_error_when_detected_target_has_no_3d_point(self):
        command = initialized_command(FakeDetector(), FakeNoPointLocalizer())

        with self.assertRaisesRegex(TargetPointUnavailableError, "没有有效三维点"):
            command.capture_once("oolong_tea")

    def test_failed_detection_is_saved_for_debugging_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            command = initialized_command(FakeDetector(with_detection=False))
            command._is_save = True
            command._save_root = Path(directory)

            target_point = command.capture_once("oolong_tea")

            self.assertIsNone(target_point)
            save_directory = command.last_save_directory
            self.assertIsNotNone(save_directory)
            payload = (save_directory / "result.json").read_text(encoding="utf-8")
            self.assertIn('"status": "no_match"', payload)
            self.assertIn('"target_point": null', payload)

    def test_capture_rejects_moving_robot(self):
        command = initialized_command(FakeDetector())
        command._robot.get_robot_state = lambda: type(
            "MovingState",
            (),
            {"has_error": False, "moving": True},
        )()

        with self.assertRaisesRegex(RuntimeError, "仍在运动"):
            command.capture_once("oolong_tea")

    def test_close_does_not_close_external_camera(self):
        command = initialized_command(FakeDetector())
        camera = command._camera

        command.close()

        self.assertEqual(camera.close_calls, 0)
        self.assertFalse(command.is_initialized)

    def test_is_save_writes_replayable_debug_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            command = initialized_command(FakeDetector())
            command._is_save = True
            command._save_root = Path(directory)

            command.capture_once("oolong_tea")

            save_directory = command.last_save_directory
            self.assertIsNotNone(save_directory)
            self.assertTrue((save_directory / "rgb.png").is_file())
            self.assertTrue((save_directory / "yolo_result.png").is_file())
            self.assertTrue((save_directory / "observation_and_target_cloud.npz").is_file())
            self.assertTrue((save_directory / "result.json").is_file())


if __name__ == "__main__":
    unittest.main()
