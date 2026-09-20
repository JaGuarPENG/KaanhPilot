"""D435 适配契约测试；SDK/USB 是外部边界，不要求连接相机。"""

import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

from camera.adapters.realsense import d435, filters, profiles
from camera.contracts.cam_structs import (
    AlignmentMode, CameraProfile, CameraState, DepthProcessingConfig, CameraDistortion,
    CameraIntrinsics, RigidTransform, SensorCalibration,
)
from camera.contracts.errors import (
    CameraNotFoundError, CameraProfileError, CameraStateError,
    CameraStreamError, CameraTimeoutError,
)


class Frame:
    def __init__(self, data, units=0.001):
        self.data = np.asarray(data)
        self.units = units

    def get_data(self):
        return self.data

    def get_units(self):
        return self.units

    def get_width(self):
        return self.data.shape[1]

    def get_height(self):
        return self.data.shape[0]

    def get_timestamp(self):
        return 123.75

    def as_depth_frame(self):
        return self


class VideoProfile:
    def __init__(self, width=640, height=480, fps=30, fmt="rgb8"):
        self.w, self.h, self.rate, self.fmt = width, height, fps, fmt

    def as_video_stream_profile(self):
        return self

    def width(self):
        return self.w

    def height(self):
        return self.h

    def fps(self):
        return self.rate

    def format(self):
        return self.fmt

    def get_intrinsics(self):
        return NS(width=self.w, height=self.h, fx=2., fy=2., ppx=0., ppy=0.,
                  coeffs=[0., 0., 0., 0., 0.], model="none")

    def get_extrinsics_to(self, other):
        # Column-major 90-degree rotation and translation already in meters.
        return NS(rotation=[0., 1., 0., -1., 0., 0., 0., 0., 1.],
                  translation=[0.02, 0., 0.])


class PointCloud:
    def calculate(self, depth):
        z = depth.data.astype(np.float32) * depth.units
        y, x = np.indices(z.shape)
        vertices = np.stack((x * z / 2, y * z / 2, z), axis=-1).astype(np.float32)
        return NS(get_vertices=lambda: vertices)


def calibration(rotation=None, translation=(0., 0., 0.)):
    intrinsics = CameraIntrinsics(2, 2, 2., 2., 0., 0.)
    distortion = CameraDistortion(0., 0., 0., 0., 0.)
    return SensorCalibration(intrinsics, intrinsics, distortion, distortion,
                             RigidTransform(np.eye(3) if rotation is None else rotation, np.array(translation)))


class Filter:
    def __init__(self, name, log, **kwargs):
        self.name, self.log, self.options = name, log, dict(kwargs)

    def supports(self, option):
        raise TypeError("processing_block.supports expects camera_info, not option")

    def get_option_range(self, option):
        return NS(min=0.25 if option == "alpha" else 0., max=16., step=0., default=0.5)

    def set_option(self, option, value):
        self.options[option] = value

    def get_supported_options(self):
        return {"spatial": ["magnitude", "alpha"], "holes": ["holes"],
                "threshold": ["min", "max"]}.get(self.name, [])

    def get_option_description(self, option):
        return str(option)

    def process(self, frame):
        self.log.append((self.name, self.options.copy()))
        return frame


def filter_sdk(log):
    return NS(
        option=NS(filter_magnitude="magnitude", filter_smooth_alpha="alpha",
                  holes_fill="holes", min_distance="min", max_distance="max"),
        spatial_filter=lambda: Filter("spatial", log),
        temporal_filter=lambda: Filter("temporal", log),
        hole_filling_filter=lambda: Filter("holes", log),
        threshold_filter=lambda: Filter("threshold", log),
        disparity_transform=lambda forward: Filter("to_disparity" if forward else "to_depth", log),
    )


class Device:
    def __init__(self, name, serial):
        self.info = {"name": name, "serial": serial}

    def supports(self, key):
        return key in self.info

    def get_info(self, key):
        return self.info[key]


class StreamingSDK:
    """只替换外部 USB/SDK；执行真实配置选择、采集线程和数据发布代码。"""

    def __init__(self):
        self.stream = NS(color="color", depth="depth")
        self.format = NS(rgb8="rgb8", z16="z16")
        self.camera_info = NS(name="name", serial_number="serial")
        self.distortion = NS(none="none", brown_conrady="brown", modified_brown_conrady="modified")
        self.devices = [Device("Intel RealSense D455", "wrong"), Device("Intel RealSense D435", "d435-a"),
                        Device("Intel RealSense D435", "d435-b")]
        self.configs = []
        self.stop_count = 0
        self.fail = threading.Event()
        self.stopped = threading.Event()
        self.resolve_error = False
        self.start_error = False
        self.calibration_error = False
        self.wrong_profile = False

    def context(self):
        return NS(query_devices=lambda: self.devices)

    def config(self):
        streams = {}
        values = NS(serial=None, streams=streams)
        def enable_device(serial):
            values.serial = serial
        def enable_stream(stream, w, h, fmt, fps):
            streams[stream] = VideoProfile(w, h, fps, fmt)
        def resolve(wrapper):
            if self.resolve_error:
                raise RuntimeError("unsupported USB profile")
            if self.wrong_profile:
                streams["color"].rate = 15
            if self.calibration_error:
                streams["color"].get_intrinsics = lambda: (_ for _ in ()).throw(RuntimeError("bad calibration"))
            return NS(get_stream=lambda key: streams[key],
                      get_device=lambda: next(d for d in self.devices if d.info["serial"] == values.serial))
        values.enable_device, values.enable_stream, values.resolve = enable_device, enable_stream, resolve
        self.configs.append(values)
        return values

    def pipeline_wrapper(self, pipeline):
        return pipeline

    def pipeline(self, context):
        def start(config):
            if self.start_error:
                raise RuntimeError("device busy")
            self.stopped.clear()
            return config.resolve(None)
        def stop():
            self.stop_count += 1
            self.stopped.set()
        def wait(timeout):
            if self.fail.wait(0.005):
                raise RuntimeError("USB disconnected")
            color = Frame(np.full((480, 640, 3), 128, dtype=np.uint8))
            depth = Frame(np.full((480, 640), 1000, dtype=np.uint16))
            frames = NS(get_color_frame=lambda: color, get_depth_frame=lambda: depth)
            frames.as_frameset = lambda: frames
            return True, frames
        return NS(start=start, stop=stop, try_wait_for_frames=wait)

    def align(self, stream):
        if stream != "color":
            raise AssertionError("alignment must target color")
        return NS(process=lambda frames: frames)

    def pointcloud(self):
        return PointCloud()


class D435Tests(unittest.TestCase):
    def camera(self, **kwargs):
        cls = getattr(d435, "RealSenseD435Camera", None)
        self.assertIsNotNone(cls, "D435 adapter is not implemented")
        return cls(profiles.D435_640X480_30, **kwargs)

    def test_hardware_alignment_is_rejected(self):
        self.assertIsNotNone(getattr(d435, "RealSenseD435Camera", None))
        with self.assertRaises(CameraProfileError):
            self.camera(alignment_mode=AlignmentMode.HARDWARE)

    def test_unsupported_profile_is_not_silently_replaced(self):
        camera = self.camera()
        with self.assertRaises(CameraProfileError):
            type(camera)(CameraProfile(1280, 720, 90, "RGB8", 1280, 720, 90, "Z16"))

    def test_depth_scale_is_meters_not_millimeters(self):
        camera = self.camera()
        depth = camera._depth_frame_to_m(Frame(np.array([[0, 1000], [500, 2000]], dtype=np.uint16), 0.002))
        np.testing.assert_allclose(depth, [[0., 2.], [1., 4.]])
        self.assertEqual(depth.dtype, np.float32)

    def test_observations_are_owned_aligned_and_comparison_is_same_frame(self):
        camera = self.camera(observation_mode="RAW_AND_FILTERED")
        rgb_data = np.full((2, 2, 3), [10, 20, 30], dtype=np.uint8)
        depth = Frame(np.array([[1000, 0], [2000, 3000]], dtype=np.uint16))
        frames = NS(get_color_frame=lambda: Frame(rgb_data), get_depth_frame=lambda: depth)
        camera._point_cloud = PointCloud()
        raw, final = camera._make_observations(frames, calibration())
        np.testing.assert_allclose(final.point_cloud_m[1, 0], [0., 1., 2.])
        self.assertTrue(np.isnan(final.point_cloud_m[0, 1]).all())
        self.assertEqual((raw.frame_id, final.frame_id), (1, 1))
        self.assertEqual(final.capture_timestamp_ms, 123)
        self.assertEqual(final.alignment_mode, AlignmentMode.SOFTWARE)
        rgb_data[:] = 0
        depth.data[:] = 0
        np.testing.assert_array_equal(final.rgb[0, 0], [10, 20, 30])
        self.assertEqual(final.depth_m[0, 0], 1.)
        for array in (final.rgb, final.depth_m, final.point_cloud_m):
            self.assertFalse(array.flags.writeable)

    def test_final_only_does_not_filter_twice(self):
        camera = self.camera()
        camera._point_cloud = PointCloud()
        calls = []
        def process(frame):
            calls.append(frame)
            return frame
        camera._depth_filter_chain = NS(process=process)
        frames = NS(get_color_frame=lambda: Frame(np.zeros((2, 2, 3), dtype=np.uint8)),
                    get_depth_frame=lambda: Frame(np.ones((2, 2), dtype=np.uint16)))
        raw, final = camera._make_observations(frames, calibration())
        self.assertIsNone(raw)
        self.assertEqual(len(calls), 1)
        self.assertEqual(final.frame_id, 1)

    def test_calibration_uses_column_major_rotation_and_meter_translation(self):
        camera = self.camera()
        rs = NS(stream=NS(color="color", depth="depth"),
                distortion=NS(none="none", brown_conrady="brown", modified_brown_conrady="modified"))
        active = NS(get_stream=lambda stream: VideoProfile(fmt="z16" if stream == "depth" else "rgb8"))
        calibration = camera._read_calibration(active, rs)
        np.testing.assert_allclose(calibration.depth_to_rgb.rotation,
                                   [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        np.testing.assert_allclose(calibration.depth_to_rgb.translation_m, [0.02, 0., 0.])

    def test_failed_start_is_terminal_and_propagates_cause(self):
        camera = self.camera()
        with patch.object(camera, "_load_sdk", side_effect=CameraNotFoundError("missing")):
            with self.assertRaises(CameraNotFoundError):
                camera.start()
        self.assertEqual(camera.state, CameraState.FAILED)
        with self.assertRaises(CameraNotFoundError):
            camera.get_latest_observation()
        with self.assertRaises(CameraStateError):
            camera.start()
        camera.close()
        self.assertEqual(camera.state, CameraState.CLOSED)

    def test_startup_timeout_is_terminal(self):
        camera = self.camera(startup_timeout_s=0.01, frame_timeout_ms=1)
        with patch.object(camera, "_stream_main", side_effect=lambda: camera._stop_event.wait(2)):
            with self.assertRaises(CameraTimeoutError):
                camera.start()
        self.assertEqual(camera.state, CameraState.FAILED)
        camera.close()

    def test_missing_frames_are_recoverable(self):
        camera = self.camera()
        pipeline = NS(try_wait_for_frames=lambda timeout: (False, None))
        self.assertIsNone(camera._wait_complete_frames(pipeline))

    def test_transient_incomplete_frames_are_skipped_within_timeout(self):
        camera = self.camera()
        depth = Frame(np.ones((2, 2), dtype=np.uint16))
        color = Frame(np.zeros((2, 2, 3), dtype=np.uint8))
        partial = NS(get_depth_frame=lambda: depth, get_color_frame=lambda: None)
        complete = NS(get_depth_frame=lambda: depth, get_color_frame=lambda: color)
        complete.as_frameset = lambda: complete
        results = iter(((True, partial), (True, complete)))
        pipeline = NS(try_wait_for_frames=lambda timeout: next(results))
        camera._align_filter = NS(process=lambda frames: frames)
        self.assertIs(camera._wait_complete_frames(pipeline), complete)

    def test_close_prevents_restart(self):
        camera = self.camera()
        camera.close()
        camera.close()
        with self.assertRaises(CameraStateError):
            camera.start()

    def test_full_stream_start_stop_restart_and_serial_selection(self):
        camera = self.camera(device_index=1, alignment_mode=AlignmentMode.AUTO,
                             observation_mode="RAW_AND_FILTERED")
        sdk = StreamingSDK()
        with patch.object(camera, "_load_sdk", return_value=sdk):
            try:
                camera.start()
                self.assertEqual(camera.state, CameraState.STREAMING)
                self.assertEqual(camera.camera_id, "d435-b")
                raw, final = camera.get_latest_filter_comparison_observations()
                self.assertEqual(raw.frame_id, final.frame_id)
                self.assertEqual(final.rgb.shape, (480, 640, 3))
                self.assertEqual(camera.actual_alignment_mode, AlignmentMode.SOFTWARE)
                self.assertIsNotNone(camera.calibration)
                camera.stop()
                self.assertEqual(camera.state, CameraState.STOPPED)
                self.assertEqual(sdk.stop_count, 1)
                previous = camera.get_latest_observation().frame_id
                camera.start()
                self.assertGreater(camera.get_latest_observation().frame_id, previous)
            finally:
                camera.close()
        self.assertEqual(sdk.stop_count, 2)
        self.assertEqual(camera.state, CameraState.CLOSED)

    def test_serial_number_takes_precedence_over_index(self):
        camera = self.camera(device_index=100, serial_number="d435-a")
        camera._select_device(StreamingSDK())
        self.assertEqual(camera.camera_id, "d435-a")

    def test_missing_serial_does_not_fall_back(self):
        camera = self.camera(serial_number="missing")
        with self.assertRaises(CameraNotFoundError):
            camera._select_device(StreamingSDK())

    def test_profile_resolve_failure_is_profile_error(self):
        camera = self.camera()
        sdk = StreamingSDK()
        sdk.resolve_error = True
        with patch.object(camera, "_load_sdk", return_value=sdk):
            with self.assertRaises(CameraProfileError):
                camera.start()
            camera.close()
        self.assertEqual(sdk.stop_count, 0)

    def test_wrong_negotiated_fps_is_rejected(self):
        camera = self.camera()
        sdk = StreamingSDK()
        sdk.wrong_profile = True
        with patch.object(camera, "_load_sdk", return_value=sdk):
            with self.assertRaises(CameraProfileError):
                camera.start()
            camera.close()

    def test_pipeline_is_released_if_calibration_fails_after_start(self):
        camera = self.camera()
        sdk = StreamingSDK()
        sdk.calibration_error = True
        with patch.object(camera, "_load_sdk", return_value=sdk):
            with self.assertRaises(CameraStreamError):
                camera.start()
            camera.close()
        self.assertEqual(sdk.stop_count, 1)

    def test_exhausted_recovery_is_visible_to_reader_and_releases_pipelines(self):
        camera = self.camera()
        sdk = StreamingSDK()
        with patch.object(camera, "_load_sdk", return_value=sdk), \
                patch.object(d435, "_REBUILD_BACKOFF_S", (0., 0., 0.), create=True):
            try:
                camera.start()
                sdk.fail.set()
                camera._stream_thread.join(2)
                with self.assertRaises(CameraStreamError):
                    camera.get_latest_observation()
                self.assertEqual(camera.state, CameraState.FAILED)
            finally:
                camera.close()
        self.assertEqual(sdk.stop_count, 4)

    def test_every_declared_profile_requests_its_exact_pair(self):
        camera = self.camera()
        for profile in profiles.D435_SUPPORTED_PROFILES:
            sdk = StreamingSDK()
            candidate = type(camera)(profile)
            device = candidate._select_device(sdk)
            config = candidate._build_config(sdk, sdk.pipeline(None), device)
            self.assertEqual(config.serial, "d435-a")
            self.assertEqual(config.streams["color"].fmt, "rgb8")
            self.assertEqual(config.streams["depth"].fmt, "z16")
        hd = type(camera)(profiles.D435_1920X1080_30)
        sdk = StreamingSDK()
        config = hd._build_config(sdk, sdk.pipeline(None), hd._select_device(sdk))
        self.assertEqual((config.streams["color"].w, config.streams["color"].h), (1920, 1080))
        self.assertEqual((config.streams["depth"].w, config.streams["depth"].h), (1280, 720))

    def test_invalid_depth_units_are_rejected(self):
        camera = self.camera()
        for units in (0., -1., float("nan"), float("inf")):
            with self.assertRaises(CameraStreamError):
                camera._depth_frame_to_m(Frame(np.ones((2, 2), dtype=np.uint16), units))

    def test_aligned_depth_z_is_converted_to_color_optical_z(self):
        camera = self.camera(observation_mode="RAW_AND_FILTERED")
        camera._point_cloud = PointCloud()
        # 60 degree rotation about Y and +0.2 m color Z translation.
        rotation = np.array([[0.5, 0., np.sqrt(3) / 2], [0., 1., 0.], [-np.sqrt(3) / 2, 0., 0.5]])
        calib = calibration(rotation, (0., 0., 0.2))
        frames = NS(get_color_frame=lambda: Frame(np.zeros((2, 2, 3), dtype=np.uint8)),
                    get_depth_frame=lambda: Frame(np.array([[1000, 0], [1000, 1000]], dtype=np.uint16)))
        raw, final = camera._make_observations(frames, calib)
        # Center color ray is [0,0,1]. Its depth-camera Z is 0.5*(Zc-0.2)=1.
        np.testing.assert_allclose(final.point_cloud_m[0, 0], [0., 0., 2.2], atol=1e-6)
        self.assertAlmostEqual(float(final.depth_m[0, 0]), 2.2, places=6)
        np.testing.assert_allclose(raw.point_cloud_m, final.point_cloud_m)
        self.assertEqual(final.depth_m[0, 1], 0.)
        self.assertTrue(np.isnan(final.point_cloud_m[0, 1]).all())

    def test_modified_brown_is_rejected_before_sdk_deprojection(self):
        camera = self.camera()
        sdk = StreamingSDK()
        color = VideoProfile()
        intr = color.get_intrinsics()
        intr.model = "modified"
        # SDK deprojection asserts on this model even if coefficients are zero.
        color.get_intrinsics = lambda: intr
        active = NS(get_stream=lambda stream: color if stream == "color" else VideoProfile(fmt="z16"))
        with self.assertRaises(CameraProfileError):
            camera._read_calibration(active, sdk)


class FilterTests(unittest.TestCase):
    def test_unsupported_option_is_rejected_before_setting(self):
        with self.assertRaisesRegex(CameraProfileError, "unsupported"):
            filters.RealSenseDepthFilterChain._set_option(Filter("spatial", []), "unsupported", 1)

    def test_installed_sdk_builds_shipped_filter_config_without_hardware(self):
        try:
            import pyrealsense2 as rs
        except ImportError:
            self.skipTest("pyrealsense2 is not installed")
        from pathlib import Path
        from commands.setup import RobotSetup
        setup = RobotSetup(Path(__file__).resolve().parents[3] / "config")
        config = setup.get_camera_settings("head").depth_processing
        chain = filters.RealSenseDepthFilterChain(rs, config)
        self.assertIn("SpatialFilter", chain.active_filter_names)
        blocks = dict(chain._filters)
        self.assertAlmostEqual(blocks["SpatialFilter"].get_option(rs.option.filter_smooth_alpha), config.spatial_alpha)
        self.assertEqual(blocks["HoleFillingFilter"].get_option(rs.option.holes_fill), 2)
        self.assertAlmostEqual(blocks["ThresholdFilter"].get_option(rs.option.min_distance), config.minimum_depth_m)
        self.assertTrue(chain.parameter_descriptors())

    def chain(self, config, log):
        cls = getattr(filters, "RealSenseDepthFilterChain", None)
        self.assertIsNotNone(cls, "RealSense filter chain is not implemented")
        return cls(filter_sdk(log), config)

    def test_disabled_filters_preserve_input(self):
        log = []
        chain = self.chain(DepthProcessingConfig(), log)
        frame = Frame([[1]])
        self.assertIs(chain.process(frame), frame)
        self.assertEqual(log, [])

    def test_stereo_filter_order_mapping_and_meter_thresholds(self):
        log = []
        chain = self.chain(DepthProcessingConfig(
            spatial_enabled=True, temporal_enabled=True, hole_filling_enabled=True,
            hole_filling_mode=1, minimum_depth_m=0.15, maximum_depth_m=2.), log)
        chain.process(Frame([[1]]))
        self.assertEqual([name for name, _ in log],
                         ["to_disparity", "spatial", "temporal", "to_depth", "holes", "threshold"])
        self.assertEqual(log[4][1]["holes"], 2)
        self.assertEqual(log[5][1], {"min": 0.15, "max": 2.})

    def test_farthest_hole_mode_is_mapped(self):
        log = []
        self.chain(DepthProcessingConfig(hole_filling_enabled=True, hole_filling_mode=2), log).process(Frame([[1]]))
        self.assertEqual(log[0][1]["holes"], 1)

    def test_top_hole_mode_has_no_silent_left_substitution(self):
        self.assertIsNotNone(getattr(filters, "RealSenseDepthFilterChain", None))
        with self.assertRaises(CameraProfileError):
            self.chain(DepthProcessingConfig(hole_filling_enabled=True, hole_filling_mode=0), [])

    def test_sdk_range_is_checked_instead_of_clamping_alpha(self):
        self.assertIsNotNone(getattr(filters, "RealSenseDepthFilterChain", None))
        with self.assertRaises(CameraProfileError):
            self.chain(DepthProcessingConfig(spatial_enabled=True, spatial_alpha=0.1), [])


if __name__ == "__main__":
    unittest.main()
