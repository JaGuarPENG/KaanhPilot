"""Preview publication must not wait for RGB-D processing."""
import queue
import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from camera.contracts.cam_structs import RGBFrame


class RGBFrameTests(unittest.TestCase):
    def test_owns_readonly_memory(self):
        source = np.ones((2, 3, 3), dtype=np.uint8)
        frame = RGBFrame(1, 10, source)
        source[:] = 9
        self.assertTrue(np.all(frame.rgb == 1))
        self.assertFalse(frame.rgb.flags.writeable)
        self.assertTrue(source.flags.writeable)

    def test_invalid_shape_and_identity_are_rejected(self):
        for frame_id, timestamp, shape in ((0, 0, (2, 2, 3)), (1, -1, (2, 2, 3)), (1, 0, (2, 2))):
            with self.subTest(shape=shape, frame_id=frame_id):
                with self.assertRaises(ValueError):
                    RGBFrame(frame_id, timestamp, np.zeros(shape, dtype=np.uint8))


class LatestCaptureTests(unittest.TestCase):
    def test_capture_continues_and_pending_slot_keeps_only_latest(self):
        from camera.adapters.latest_capture import LatestCapture
        incoming = queue.Queue()
        published = queue.Queue()
        def read():
            try:
                value = incoming.get(timeout=.01)
                if isinstance(value, Exception):
                    raise value
                return value
            except queue.Empty:
                return None
        pump = LatestCapture(read, lambda n: RGBFrame(n, n, np.zeros((1, 1, 3), np.uint8)), published.put)
        pump.start()
        try:
            for number in (1, 2, 3):
                incoming.put(number)
                self.assertEqual(published.get(timeout=1).frame_id, number)
            packet = pump.get(.1)
            self.assertEqual(packet.frames, 3)
            self.assertEqual(packet.color.frame_id, 3)
            self.assertIsNone(pump.get(.02))
            incoming.put(RuntimeError('USB disconnected'))
            self.assertIsNone(published.get(timeout=1))
            with self.assertRaisesRegex(RuntimeError, 'USB disconnected'):
                pump.get(.1)
        finally:
            pump.close(1)
        self.assertFalse(pump.thread.is_alive())


class AdapterPreviewTests(unittest.TestCase):
    def test_reader_shutdown_timeout_prevents_restart_and_can_be_cleaned_up(self):
        from camera.adapters.realsense.d435 import RealSenseD435Camera
        from camera.adapters.realsense.profiles import D435_640X480_30
        from camera.adapters.orbbec.g305 import OrbbecG305Camera
        from camera.adapters.orbbec.profiles import G305_848X480_30
        from camera.contracts.cam_structs import CameraState
        from camera.contracts.errors import CameraTimeoutError, CameraStateError
        for camera in (RealSenseD435Camera(D435_640X480_30), OrbbecG305Camera(G305_848X480_30)):
            with self.subTest(camera=type(camera).__name__):
                pump = Mock()
                pump.close.side_effect = CameraTimeoutError('reader stuck')
                pipeline = Mock()
                camera._capture = pump
                with self.assertRaises(CameraTimeoutError):
                    camera._close_pipeline_session(pipeline)
                self.assertEqual(camera.state, CameraState.FAILED)
                self.assertIs(camera._capture, pump)
                self.assertIs(camera._unclosed_pipeline, pipeline)
                pipeline.stop.assert_not_called()
                with self.assertRaises(CameraStateError):
                    camera.start()
                pump.close.side_effect = None
                camera.close()
                pipeline.stop.assert_called_once()
                self.assertIsNone(camera._capture)
                self.assertIsNone(camera._unclosed_pipeline)

    def test_both_adapters_publish_preview_while_depth_processing_is_blocked(self):
        from camera.adapters.realsense.d435 import RealSenseD435Camera
        from camera.adapters.realsense.profiles import D435_640X480_30
        from camera.adapters.orbbec.g305 import OrbbecG305Camera
        from camera.adapters.orbbec.profiles import G305_848X480_30
        from camera.contracts.cam_structs import AlignmentMode
        from test_realsense_d435 import StreamingSDK, Frame, calibration

        for kind in ('d435', 'g305'):
            with self.subTest(camera=kind), ExitStack() as stack:
                if kind == 'd435':
                    camera = RealSenseD435Camera(D435_640X480_30)
                    sdk = StreamingSDK()
                    stack.enter_context(patch.object(camera, '_load_sdk', return_value=sdk))
                else:
                    camera = OrbbecG305Camera(G305_848X480_30)
                    def read(timeout):
                        time.sleep(.005)
                        return NS(get_color_frame=lambda: Frame(np.ones((2, 2, 3), np.uint8)),
                                  get_depth_frame=lambda: Frame(np.full((2, 2), 1000, np.uint16)))
                    pipeline = NS(start=lambda config: None, stop=lambda: None, wait_for_frames=read)
                    for name, value in (('_load_sdk', NS()), ('_select_device', NS()),
                                        ('_create_pipeline', pipeline),
                                        ('_build_config', (NS(), AlignmentMode.HARDWARE)),
                                        ('_read_calibration', calibration())):
                        stack.enter_context(patch.object(camera, name, return_value=value))
                    stack.enter_context(patch.object(camera, '_frame_to_rgb', side_effect=lambda frame, sdk: frame.data))
                    stack.enter_context(patch.object(camera, '_depth_frame_to_m', side_effect=lambda frame: frame.data.astype(np.float32) * .001))

                entered, release = threading.Event(), threading.Event()
                original = camera._make_observations
                calls = []
                def slow_depth(packet, *args):
                    calls.append(packet)
                    if len(calls) == 2:
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError('test did not release processing')
                    result = original(packet, *args)
                    self.assertEqual(result[1].frame_id, packet.color.frame_id)
                    self.assertEqual(result[1].capture_timestamp_ms, packet.color.capture_timestamp_ms)
                    np.testing.assert_array_equal(result[1].rgb, packet.color.rgb)
                    return result
                stack.enter_context(patch.object(camera, '_make_observations', side_effect=slow_depth))
                stack.callback(camera.close)
                stack.callback(release.set)
                camera.start()
                self.assertTrue(entered.wait(2))
                observation = camera.get_latest_observation()
                first = camera.get_latest_color_frame()
                deadline = time.monotonic() + 2
                while camera.get_latest_color_frame().frame_id <= first.frame_id and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertGreater(camera.get_latest_color_frame().frame_id, first.frame_id)
                self.assertIs(camera.get_latest_observation(), observation)
                pump = camera._capture
                release.set()
                camera.stop()
                self.assertFalse(pump.thread.is_alive())
                self.assertIsNone(camera.get_latest_color_frame())
                camera.start()
                self.assertGreater(camera.get_latest_observation().frame_id, first.frame_id)


if __name__ == '__main__':
    unittest.main()
