"""D435 与 G305 一致的恢复策略；用虚拟时钟验证 5/10 秒边界。"""

import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from camera.adapters.realsense import d435
from camera.adapters.realsense.profiles import D435_640X480_30
from camera.contracts.cam_structs import CameraState
from camera.contracts.errors import CameraNotFoundError, CameraProfileError, CameraStreamError, CameraTimeoutError
from test_realsense_d435 import StreamingSDK, calibration


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.camera = d435.RealSenseD435Camera(D435_640X480_30, frame_timeout_ms=10)
        self.old = NS(frame_id=1)
        self.camera._latest_observation = self.old
        self.camera._state = CameraState.STREAMING
        self.now = 0.

    def method(self, name):
        result = getattr(self.camera, name, None)
        self.assertIsNotNone(result, f"Missing recovery operation {name}")
        return result

    def test_short_gap_keeps_last_observation(self):
        consume = self.method("_consume_pipeline_session")
        def wait(pipeline):
            self.now += 1
            self.assertEqual(self.camera.state, CameraState.STREAMING)
            self.assertIs(self.camera.get_latest_observation(), self.old)
            if self.now == 4:
                self.camera._stop_event.set()
            return None
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait):
            consume(object(), calibration())

    def test_five_second_gap_clears_both_observations_and_ten_seconds_rebuilds(self):
        consume = self.method("_consume_pipeline_session")
        self.camera._latest_unfiltered_observation = self.old
        def wait(pipeline):
            if self.now == 5:
                self.assertEqual(self.camera.state, CameraState.RECOVERING)
                self.assertIsNone(self.camera.get_latest_observation())
                self.assertIsNone(self.camera._latest_unfiltered_observation)
            self.now += 1
            return None
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait):
            with self.assertRaises(CameraTimeoutError):
                consume(object(), calibration())
        self.assertEqual(self.now, 10)

    def test_same_pipeline_requires_three_consecutive_good_frames(self):
        consume = self.method("_consume_pipeline_session")
        sequence = iter([(5., None), (5.1, NS(frame_id=2)), (5.2, None),
                         (5.3, NS(frame_id=3)), (5.4, NS(frame_id=4)), (5.5, NS(frame_id=5))])
        def wait(pipeline):
            if self.now == 5.5:
                self.assertEqual(self.camera.state, CameraState.STREAMING)
                self.assertEqual(self.camera.get_latest_observation().frame_id, 5)
                self.camera._stop_event.set()
                return None
            if self.now >= 5:
                self.assertEqual(self.camera.state, CameraState.RECOVERING)
                self.assertIsNone(self.camera.get_latest_observation())
            self.now, frame = next(sequence)
            return frame
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait), \
                patch.object(self.camera, "_make_observations", side_effect=lambda f, c: (None, f)):
            consume(object(), calibration())
        self.assertIsNone(self.camera._last_error)

    def test_reconnect_uses_original_serial_after_usb_reordering(self):
        sdk = StreamingSDK()
        self.camera._select_device(sdk)
        sdk.devices.reverse()
        device = self.camera._select_device(sdk)
        self.assertEqual(device.get_info("serial"), "d435-a")
        sdk.devices = [device for device in sdk.devices if device.get_info("serial") != "d435-a"]
        with self.assertRaises(CameraNotFoundError):
            self.camera._select_device(sdk)

    def test_recovery_exhaustion_reports_final_error(self):
        rebuild = self.method("_rebuild_pipeline")
        self.method("_enter_recovering")(CameraStreamError("old USB error"))
        attempts = []
        def open_session(rs):
            attempts.append(1)
            raise CameraNotFoundError("original camera missing")
        with patch.object(self.camera, "_open_pipeline_session", side_effect=open_session), \
                patch.object(d435, "_REBUILD_BACKOFF_S", (0., 0., 0.)):
            with self.assertRaises(CameraStreamError) as caught:
                rebuild(object(), CameraStreamError("old USB error"))
        self.assertEqual(len(attempts), 3)
        self.camera._fail(caught.exception)
        with self.assertRaises(CameraStreamError) as read_error:
            self.camera.get_latest_observation()
        self.assertIs(read_error.exception, caught.exception)
        self.assertIsInstance(caught.exception.__cause__, CameraNotFoundError)

    def test_profile_error_is_terminal_without_retry(self):
        rebuild = self.method("_rebuild_pipeline")
        attempts = []
        def open_session(rs):
            attempts.append(1)
            raise CameraProfileError("bad profile")
        with patch.object(self.camera, "_open_pipeline_session", side_effect=open_session):
            with self.assertRaises(CameraProfileError):
                rebuild(object(), CameraStreamError("disconnect"))
        self.assertEqual(len(attempts), 1)

    def test_stop_interrupts_retry_backoff(self):
        rebuild = self.method("_rebuild_pipeline")
        attempted = threading.Event()
        attempts = []
        def open_session(rs):
            attempts.append(1)
            attempted.set()
            raise CameraNotFoundError("missing")
        result = []
        with patch.object(self.camera, "_open_pipeline_session", side_effect=open_session), \
                patch.object(d435, "_REBUILD_BACKOFF_S", (0., 30., 30.)):
            thread = threading.Thread(target=lambda: result.append(rebuild(object(), RuntimeError("USB"))))
            thread.start()
            try:
                self.assertTrue(attempted.wait(1))
                self.camera._stop_event.set()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result, [None])
                self.assertEqual(len(attempts), 1)
            finally:
                self.camera._stop_event.set()
                thread.join(1)

    def test_sdk_resolve_failure_during_recovery_is_retryable(self):
        sdk = StreamingSDK()
        device = self.camera._select_device(sdk)
        self.camera._state = CameraState.RECOVERING
        sdk.resolve_error = True
        with self.assertRaises(CameraStreamError):
            self.camera._build_config(sdk, sdk.pipeline(None), device)

    def test_rebuild_validates_three_frames_before_publishing(self):
        rebuild = self.method("_rebuild_pipeline")
        self.method("_enter_recovering")(CameraStreamError("USB"))
        frames = iter([NS(frame_id=2), None, NS(frame_id=3), NS(frame_id=4), NS(frame_id=5)])
        session = object()
        def wait(pipeline):
            self.now += 0.1
            self.assertEqual(self.camera.state, CameraState.RECOVERING)
            self.assertIsNone(self.camera.get_latest_observation())
            return next(frames)
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(self.camera, "_open_pipeline_session", return_value=(session, calibration())), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait), \
                patch.object(self.camera, "_make_observations", side_effect=lambda f, c: (None, f)):
            pipeline, _ = rebuild(object(), RuntimeError("USB"))
        self.assertIs(pipeline, session)
        self.assertEqual(self.camera.state, CameraState.STREAMING)
        self.assertEqual(self.camera.get_latest_observation().frame_id, 5)

    def test_full_disconnect_recreates_resources_and_resumes_same_camera(self):
        camera = d435.RealSenseD435Camera(D435_640X480_30, frame_timeout_ms=10)
        sdk = StreamingSDK()
        original_pipeline = sdk.pipeline
        sessions = []
        def pipeline(context):
            if sessions:
                sdk.fail.clear()  # 新连接正常；原 Pipeline 已由适配器停止。
                sdk.devices.reverse()  # 模拟 USB 枚举顺序改变。
            result = original_pipeline(context)
            sessions.append(result)
            return result
        sdk.pipeline = pipeline
        restored = threading.Event()
        publishes = []
        original_publish = camera._publish_streaming_observation
        def publish(raw, observation, calib):
            original_publish(raw, observation, calib)
            publishes.append(observation.frame_id)
            if len(publishes) == 2:
                restored.set()
        with patch.object(camera, "_load_sdk", return_value=sdk), \
                patch.object(camera, "_publish_streaming_observation", side_effect=publish):
            try:
                camera.start()
                original_calibration = camera.calibration
                original_cloud = camera._point_cloud
                original_filters = camera._depth_filter_chain
                sdk.fail.set()
                self.assertTrue(restored.wait(2))
                self.assertEqual(camera.state, CameraState.STREAMING)
                self.assertEqual(camera.camera_id, "d435-a")
                self.assertEqual(len(sessions), 2)
                self.assertEqual(sdk.stop_count, 1)
                self.assertGreaterEqual(publishes[1] - publishes[0], 3)
                self.assertIsNot(camera.calibration, original_calibration)
                self.assertIsNot(camera._point_cloud, original_cloud)
                self.assertIsNot(camera._depth_filter_chain, original_filters)
                self.assertIsNone(camera._last_error)
            finally:
                camera.close()
        self.assertEqual(camera.state, CameraState.CLOSED)
        self.assertEqual(sdk.stop_count, 2)

    def test_stop_while_rebuild_waits_for_frames_releases_new_pipeline(self):
        camera = d435.RealSenseD435Camera(D435_640X480_30, frame_timeout_ms=10)
        sdk = StreamingSDK()
        original_pipeline = sdk.pipeline
        awaiting_frames = threading.Event()
        sessions = []
        def pipeline(context):
            result = original_pipeline(context)
            if sessions:
                def wait(timeout):
                    awaiting_frames.set()
                    camera._stop_event.wait(timeout / 1000.)
                    return False, None
                result.try_wait_for_frames = wait
            sessions.append(result)
            return result
        sdk.pipeline = pipeline
        with patch.object(camera, "_load_sdk", return_value=sdk):
            try:
                camera.start()
                sdk.fail.set()
                self.assertTrue(awaiting_frames.wait(2))
                self.assertEqual(camera.state, CameraState.RECOVERING)
                self.assertIsNone(camera.get_latest_observation())
                camera.stop()
                self.assertEqual(camera.state, CameraState.STOPPED)
                self.assertFalse(camera._stream_thread.is_alive())
                self.assertEqual(sdk.stop_count, 2)
            finally:
                camera.close()

    def test_startup_tolerates_missing_frames_until_startup_deadline(self):
        wait_stable = self.method("_wait_for_stable_observations")
        self.camera._state = CameraState.STARTING
        frame = NS(frame_id=2)
        frames = iter([None, None, frame])
        def wait(pipeline):
            self.now += 1
            return next(frames)
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait), \
                patch.object(self.camera, "_make_observations", side_effect=lambda f, c: (None, f)):
            self.assertEqual(wait_stable(object(), calibration(), 1, 8.), (None, frame))

    def test_rebuild_session_that_never_stabilizes_is_closed_before_retry(self):
        rebuild = self.method("_rebuild_pipeline")
        self.method("_enter_recovering")(RuntimeError("USB"))
        opened, closed = [], []
        def open_session(rs):
            session = object()
            opened.append(session)
            return session, calibration()
        def wait(pipeline):
            self.now += 1
            return None
        with patch.object(d435, "time", NS(monotonic=lambda: self.now)), \
                patch.object(d435, "_REBUILD_BACKOFF_S", (0., 0., 0.)), \
                patch.object(self.camera, "_open_pipeline_session", side_effect=open_session), \
                patch.object(self.camera, "_close_pipeline_session", side_effect=closed.append), \
                patch.object(self.camera, "_wait_complete_frames", side_effect=wait):
            with self.assertRaises(CameraStreamError):
                rebuild(object(), RuntimeError("USB"))
        self.assertEqual(len(opened), 3)
        self.assertEqual(closed, opened)
        self.assertEqual(self.now, 30.)


if __name__ == "__main__":
    unittest.main()
