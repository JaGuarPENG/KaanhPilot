"""按相机名称加载配置和创建适配器；不加载厂商 SDK、不连接硬件。"""

import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from commands.setup import RobotSetup
from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.realsense.d435 import RealSenseD435Camera
from camera.contracts.cam_structs import AlignmentMode, CameraState
from camera.contracts.interface import Camera


ROOT = Path(__file__).resolve().parents[3]


class CameraSetupTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "head": {"type": "orbbec_g305", "device_index": 0, "settings_file": "g305.json", "extrinsic_index": 0},
            "wrist": {"type": "realsense_d435", "device_index": 1, "settings_file": "d435.json", "extrinsic_index": 2},
        }
        self.g305 = {"profile": "1280@30", "alignment": "auto", "warmup_seconds": 1.0,
                     "minimum_depth_m": 0.02, "maximum_depth_m": 2.0, "spatial_enabled": True}
        self.d435 = {"profile": "1280@6", "alignment": "software", "warmup_seconds": 2.0,
                     "minimum_depth_m": 0.15, "maximum_depth_m": 3.0, "temporal_enabled": True,
                     "hole_filling_enabled": True, "hole_filling_mode": 1}
        read_json = RobotSetup._read_json
        def read(path):
            if path.name == "cameras.json":
                return copy.deepcopy(self.registry)
            if path.name == "g305.json":
                return copy.deepcopy(self.g305)
            if path.name == "d435.json":
                return copy.deepcopy(self.d435)
            return read_json(path)
        self.reader = patch.object(RobotSetup, "_read_json", side_effect=read)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def test_each_camera_resolves_its_own_profile_and_processing(self):
        config = RobotSetup(ROOT / "config").get_robot_config()
        self.assertTrue(hasattr(config, "cameras"), "RobotConfig needs per-camera settings")
        head, wrist = config.cameras["head"], config.cameras["wrist"]
        self.assertEqual(head.profile.color_height, 800)
        self.assertEqual(wrist.profile.color_height, 720)
        self.assertEqual(wrist.profile.color_fps, 6)
        self.assertEqual(wrist.profile.depth_fps, 6)
        self.assertEqual(head.profile.color_format, "MJPG")
        self.assertEqual(wrist.profile.color_format, "RGB8")
        self.assertEqual((head.warmup_seconds, wrist.warmup_seconds), (1., 2.))
        self.assertEqual(wrist.alignment, AlignmentMode.SOFTWARE)
        self.assertTrue(head.depth_processing.spatial_enabled)
        self.assertFalse(wrist.depth_processing.spatial_enabled)
        self.assertTrue(wrist.depth_processing.temporal_enabled)
        self.assertEqual(wrist.depth_processing.minimum_depth_m, 0.15)
        self.assertEqual((wrist.device_index, wrist.extrinsic_index), (1, 2))

    def test_factory_returns_distinct_stopped_adapters(self):
        setup = RobotSetup(ROOT / "config")
        head = setup.setup_camera("head")
        wrist = setup.setup_camera("wrist")
        self.addCleanup(head.close)
        self.addCleanup(wrist.close)
        self.assertIsInstance(head, OrbbecG305Camera)
        self.assertIsInstance(wrist, RealSenseD435Camera)
        for camera in (head, wrist):
            self.assertIsInstance(camera, Camera)
            self.assertEqual(camera.state, CameraState.STOPPED)
        self.assertEqual(wrist._device_index, 1)
        self.assertIsNot(head.depth_processing, wrist.depth_processing)

    def test_unknown_camera_name_has_clear_error(self):
        setup = RobotSetup(ROOT / "config")
        with self.assertRaisesRegex(ValueError, "missing"):
            setup.setup_camera("missing")

    def test_unknown_type_is_rejected_while_loading(self):
        self.registry["wrist"]["type"] = "unknown_type"
        with self.assertRaisesRegex(ValueError, "unknown_type"):
            RobotSetup(ROOT / "config")

    def test_profile_is_validated_against_selected_type(self):
        for invalid_profile in ("648@30", "848@30"):
            with self.subTest(profile=invalid_profile):
                self.d435["profile"] = invalid_profile
                with self.assertRaisesRegex(ValueError, invalid_profile):
                    RobotSetup(ROOT / "config")

    def test_type_supplies_default_settings_filename(self):
        del self.registry["head"]["settings_file"]
        del self.registry["wrist"]["settings_file"]
        config = RobotSetup(ROOT / "config").get_robot_config()
        self.assertTrue(hasattr(config, "cameras"))
        self.assertEqual(config.cameras["wrist"].profile.depth_height, 720)

    def test_optional_depth_thresholds_can_be_disabled(self):
        self.d435.pop("minimum_depth_m")
        self.d435.pop("maximum_depth_m")
        config = RobotSetup(ROOT / "config").get_robot_config()
        self.assertTrue(hasattr(config, "cameras"))
        self.assertIsNone(config.cameras["wrist"].depth_processing.minimum_depth_m)

    def test_invalid_indices_and_warmup_fail_during_configuration(self):
        for field, value in (("device_index", -1), ("device_index", True), ("extrinsic_index", 3)):
            with self.subTest(field=field, value=value):
                original = self.registry["wrist"][field]
                self.registry["wrist"][field] = value
                try:
                    with self.assertRaises(ValueError):
                        RobotSetup(ROOT / "config")
                finally:
                    self.registry["wrist"][field] = original
        self.d435["warmup_seconds"] = -1
        with self.assertRaises(ValueError):
            RobotSetup(ROOT / "config")

    def test_d435_rejects_incompatible_filter_and_alignment_settings(self):
        self.d435["hole_filling_mode"] = 0
        with self.assertRaises(ValueError):
            RobotSetup(ROOT / "config")
        self.d435["hole_filling_mode"] = 1
        self.d435["alignment"] = "hardware"
        with self.assertRaises(ValueError):
            RobotSetup(ROOT / "config")

    def test_role_can_switch_model_without_changing_the_factory(self):
        self.registry["head"] = {"type": "realsense_d435", "device_index": 0, "extrinsic_index": 0}
        setup = RobotSetup(ROOT / "config")
        camera = setup.setup_camera("head")
        self.addCleanup(camera.close)
        self.assertIsInstance(camera, RealSenseD435Camera)
        self.assertEqual(setup.get_camera_settings("head").profile.color_height, 720)

    def test_numeric_sdk_index_is_not_a_camera_name(self):
        setup = RobotSetup(ROOT / "config")
        with self.assertRaisesRegex(ValueError, "0"):
            setup.setup_camera(0)


class ShippedCameraConfigTests(unittest.TestCase):
    def test_legacy_follower_rejects_left_before_initializing_robot(self):
        from commands.legacy.yolo_follower import YoloFollowerCommand
        with patch.object(RobotSetup, "setup_robot", side_effect=AssertionError("resources opened too early")):
            with self.assertRaisesRegex(ValueError, "extrinsic_index"):
                YoloFollowerCommand("test_target", camera_name="left")

    def test_real_config_files_create_both_stopped_adapters(self):
        setup = RobotSetup(ROOT / "config")
        self.assertEqual(setup.get_camera_settings("head").profile.depth_format, "Z16")
        self.assertEqual(setup.get_camera_settings("head").profile.depth_width, 640)
        self.assertEqual(setup.get_camera_settings("head").depth_processing.hole_filling_mode, 1)
        for name, adapter in (("head", RealSenseD435Camera), ("left", OrbbecG305Camera)):
            with self.subTest(camera=name):
                camera = setup.setup_camera(name)
                self.addCleanup(camera.close)
                self.assertIsInstance(camera, adapter)
                self.assertEqual(camera.state, CameraState.STOPPED)
if __name__ == "__main__":
    unittest.main()
