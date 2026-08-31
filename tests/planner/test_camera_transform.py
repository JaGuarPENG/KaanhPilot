from __future__ import annotations

import unittest

from perception.percept_structs import (
    LocalizationResult,
    TargetPerceptionResult,
    TargetStatus,
)
from planner.camera_transform import (
    CameraTransform,
    RobotCameraExtrinsic,
    TransformResult,
)
from yolo.contracts.yolo_structs import Detection


IDENTITY_QUATERNION = (0.0, 0.0, 0.0, 1.0)


def make_detection() -> Detection:
    return Detection(0, "target", 0.9, 0, 0, 10, 10, 1, 100)


def make_result(
    status: TargetStatus,
    point_camera_m: tuple[float, float, float] | None,
) -> TargetPerceptionResult:
    detection = make_detection()
    localization = (
        None
        if point_camera_m is None
        else LocalizationResult(detection, point_camera_m, None, 1, 1, 1.0, 0.0)
    )
    return TargetPerceptionResult(
        "target",
        1,
        100,
        status,
        detection if localization is not None else None,
        localization,
        0,
    )


class CameraTransformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transform = CameraTransform(
            RobotCameraExtrinsic(
                cam_0_extrinsic={
                    "translation_m": (1.0, 2.0, 3.0),
                    "quaternion_xyzw": IDENTITY_QUATERNION,
                },
                cam_1_extrinsic={
                    "translation_m": (0.1, 0.2, 0.3),
                    "quaternion_xyzw": IDENTITY_QUATERNION,
                },
                cam_2_extrinsic={
                    "translation_m": (0.0, 0.0, 0.0),
                    "quaternion_xyzw": IDENTITY_QUATERNION,
                },
            )
        )

    def test_external_camera_converts_camera_point_to_base(self) -> None:
        result = self.transform.result2base(
            make_result(TargetStatus.TARGET_ACQUIRED, (1.0, 2.0, 3.0)),
            cam_index=0,
            rbt_pq=None,
        )

        self.assertEqual(result.target_point_base_m, (2.0, 4.0, 6.0))
        self.assertEqual(result.status, TargetStatus.TARGET_ACQUIRED)

    def test_eye_in_hand_composes_end_and_base_transforms(self) -> None:
        result = self.transform.result2base(
            make_result(TargetStatus.TARGET_TRACKED, (1.0, 2.0, 3.0)),
            cam_index=1,
            rbt_pq=[1000.0, 2000.0, 3000.0, *IDENTITY_QUATERNION],
        )

        self.assertEqual(result.target_point_base_m, (2.1, 4.2, 6.3))

    def test_no_target_status_returns_empty_base_point_without_tcp_pose(self) -> None:
        result = self.transform.result2base(
            make_result(TargetStatus.TARGET_LOST, None),
            cam_index=1,
            rbt_pq=None,
        )

        self.assertIsNone(result.target_point_base_m)
        self.assertEqual(result.status, TargetStatus.TARGET_LOST)

    def test_eye_in_hand_target_requires_tcp_pose(self) -> None:
        with self.assertRaises(ValueError):
            self.transform.result2base(
                make_result(TargetStatus.TARGET_ACQUIRED, (1.0, 2.0, 3.0)),
                cam_index=1,
                rbt_pq=None,
            )


class TransformResultTests(unittest.TestCase):
    def test_target_status_requires_base_point(self) -> None:
        with self.assertRaises(ValueError):
            TransformResult("target", 1, 100, TargetStatus.TARGET_ACQUIRED, None)

    def test_no_target_status_rejects_base_point(self) -> None:
        with self.assertRaises(ValueError):
            TransformResult("target", 1, 100, TargetStatus.TARGET_LOST, (1.0, 2.0, 3.0))


if __name__ == "__main__":
    unittest.main()
