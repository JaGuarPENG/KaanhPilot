"""G305 中无需连接硬件即可验证的纯计算逻辑。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

try:
    import cv2  # noqa: F401
except ModuleNotFoundError as error:
    if error.name == "cv2":
        raise unittest.SkipTest("requires opencv-python") from error
    raise

from camera.adapters.orbbec.g305 import OrbbecG305Camera
from camera.adapters.orbbec.profiles import G305_848X480_60
from camera.contracts.cam_structs import AlignmentMode, CameraIntrinsics
from camera.contracts.errors import CameraNotFoundError, CameraStreamError


class _Devices(list):
    def get_count(self) -> int:
        return len(self)


class _Device:
    def __init__(self, serial_number: str) -> None:
        self.serial_number = serial_number

    def get_device_info(self):
        return self

    def get_serial_number(self) -> str:
        return self.serial_number


def _sdk(devices: _Devices):
    return SimpleNamespace(Context=lambda: SimpleNamespace(query_devices=lambda: devices))


def _property_sdk():
    return SimpleNamespace(
        OBPropertyID=SimpleNamespace(
            OB_PROP_COLOR_AUTO_EXPOSURE_BOOL=2000,
            OB_PROP_COLOR_EXPOSURE_INT=2001,
            OB_PROP_COLOR_GAIN_INT=2002,
            OB_PROP_COLOR_SHARPNESS_INT=2006,
        ),
        OBPermissionType=SimpleNamespace(PERMISSION_WRITE="write"),
    )


def _property_device():
    device = Mock()
    device.is_property_supported.return_value = True
    limits = {2001: (1, 1990), 2002: (16, 248), 2006: (0, 100)}
    device.get_int_property_range.side_effect = lambda property_id: SimpleNamespace(
        min=limits[property_id][0], max=limits[property_id][1], step=1,
    )
    return device


class DeviceSelectionTests(unittest.TestCase):
    def test_serial_number_selects_same_camera_after_device_reordering(self) -> None:
        devices = _Devices([_Device("right"), _Device("left")])
        camera = OrbbecG305Camera(G305_848X480_60, device_index=0, serial_number="left")

        self.assertIs(camera._select_device(_sdk(devices)), devices[1])
        self.assertEqual(camera.camera_id, "left")
        devices.reverse()
        self.assertIs(camera._select_device(_sdk(devices)), devices[0])

    def test_missing_configured_serial_does_not_fall_back_to_index(self) -> None:
        devices = _Devices([_Device("right")])
        camera = OrbbecG305Camera(G305_848X480_60, device_index=0, serial_number="left")

        with self.assertRaisesRegex(CameraNotFoundError, "left"):
            camera._select_device(_sdk(devices))
        self.assertIsNone(camera.camera_id)

    def test_unconfigured_camera_still_selects_by_index(self) -> None:
        devices = _Devices([_Device("right"), _Device("left")])
        camera = OrbbecG305Camera(G305_848X480_60, device_index=1)

        self.assertIs(camera._select_device(_sdk(devices)), devices[1])
        self.assertEqual(camera.camera_id, "left")


class ColorSettingsTests(unittest.TestCase):
    def test_manual_mode_disables_ae_before_writing_exposure_gain_and_sharpness(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60, auto_exposure=False,
                                  exposure=150, gain=16, color_sharpness=65)
        device = _property_device()

        camera._apply_camera_settings(_property_sdk(), device)

        writes = [item for item in device.mock_calls if item[0] in ("set_bool_property", "set_int_property")]
        self.assertEqual(writes, [
            call.set_bool_property(2000, False),
            call.set_int_property(2001, 150),
            call.set_int_property(2002, 16),
            call.set_int_property(2006, 65),
        ])

    def test_auto_mode_ignores_manual_values_but_keeps_sharpness(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60, auto_exposure=True,
                                  exposure=2000, gain=-1, color_sharpness=65)
        device = _property_device()

        camera._apply_camera_settings(_property_sdk(), device)

        self.assertEqual(device.set_bool_property.call_args_list, [call(2000, True)])
        self.assertEqual(device.set_int_property.call_args_list, [call(2006, 65)])

    def test_unconfigured_settings_do_not_write_device(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60)
        device = _property_device()

        camera._apply_camera_settings(_property_sdk(), device)

        device.set_int_property.assert_not_called()
        device.set_bool_property.assert_not_called()

    def test_out_of_range_exposure_is_rejected_before_any_write(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60, auto_exposure=False, exposure=2000, gain=16)
        device = _property_device()

        with self.assertRaisesRegex(CameraStreamError, "曝光.*范围"):
            camera._apply_camera_settings(_property_sdk(), device)

        device.set_bool_property.assert_not_called()
        device.set_int_property.assert_not_called()

    def test_manual_exposure_requires_write_permission_and_matching_step(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60, auto_exposure=False, exposure=150, gain=16)
        device = _property_device()
        device.is_property_supported.side_effect = lambda property_id, permission: property_id != 2001
        with self.assertRaisesRegex(CameraStreamError, "曝光.*不支持写入"):
            camera._apply_camera_settings(_property_sdk(), device)
        device.set_bool_property.assert_not_called()
        device.set_int_property.assert_not_called()

        device = _property_device()
        original_range = device.get_int_property_range.side_effect
        device.get_int_property_range.side_effect = lambda property_id: (
            SimpleNamespace(min=1, max=1990, step=2)
            if property_id == 2001 else original_range(property_id)
        )
        with self.assertRaisesRegex(CameraStreamError, "曝光.*步长"):
            camera._apply_camera_settings(_property_sdk(), device)
        device.set_bool_property.assert_not_called()
        device.set_int_property.assert_not_called()

    def test_sdk_write_failure_identifies_setting(self) -> None:
        camera = OrbbecG305Camera(G305_848X480_60, color_sharpness=65)
        device = _property_device()
        device.set_int_property.side_effect = RuntimeError("rejected")

        with self.assertRaisesRegex(CameraStreamError, "彩色锐度"):
            camera._apply_camera_settings(_property_sdk(), device)


class PipelineStartupTests(unittest.TestCase):
    def test_camera_settings_are_written_after_stream_produces_a_frame(self) -> None:
        events = []
        camera = OrbbecG305Camera(G305_848X480_60, auto_exposure=False, exposure=156, gain=16)
        pipeline = Mock()
        pipeline.start.side_effect = lambda _: events.append("start")
        frame_set = Mock()
        pipeline.wait_for_frames.side_effect = lambda _: (events.append("frame"), frame_set)[1]
        camera._select_device = Mock(return_value=Mock())
        camera._create_pipeline = Mock(return_value=pipeline)
        camera._build_config = Mock(return_value=(Mock(), AlignmentMode.HARDWARE))
        camera._apply_camera_settings = Mock(side_effect=lambda *_: events.append("settings"))

        with patch("camera.adapters.orbbec.g305.OrbbecDepthFilterChain"), \
                patch("camera.adapters.orbbec.g305.LatestCapture"):
            camera._open_pipeline_session(_property_sdk())

        self.assertEqual(events, ["start", "frame", "settings", "frame", "frame"])


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
