"""Dual previews without opening cameras or connecting to the robot."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import cv2
import numpy as np

from agent_api import Runtime, serve
from frontend.device_adapter import RealDevice, DeviceError


class CameraViewsTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime(dry_run=True)
        self.runtime.dry_run = False
        self.cameras = {}
        for name, color in (("head", (255, 0, 0)), ("left", (0, 255, 0))):
            observation = SimpleNamespace(frame_id=1, capture_timestamp_ms=100,
                rgb=np.full((8, 8, 3), color, dtype=np.uint8))
            self.cameras[name] = Mock(get_latest_color_frame=Mock(return_value=observation))
        self.runtime.cameras = self.cameras

    def test_caches_are_independent_and_missing_frame_is_not_stale(self):
        with patch('cv2.imencode', wraps=cv2.imencode) as encode:
            head = self.runtime.head_camera_jpeg()
            left = self.runtime.left_camera_jpeg()
            self.assertNotEqual(head, left)
            self.assertEqual(head, self.runtime.head_camera_jpeg())
            self.assertEqual(left, self.runtime.left_camera_jpeg())
            self.assertEqual(encode.call_count, 2)
        self.cameras['head'].get_latest_color_frame.return_value = None
        with self.assertRaises(RuntimeError):
            self.runtime.head_camera_jpeg()
        self.assertEqual(left, self.runtime.left_camera_jpeg())

    def test_start_and_close_both_cameras(self):
        setup = Mock()
        setup.setup_camera.side_effect = self.cameras.__getitem__
        setup.get_camera_settings.return_value = SimpleNamespace(warmup_seconds=0)
        self.runtime.cameras = {}
        self.runtime._start_cameras(setup)
        self.assertIs(self.runtime.camera, self.cameras['left'])
        self.runtime.close()
        for camera in self.cameras.values():
            camera.start.assert_called_once()
            camera.close.assert_called_once()

    def test_start_failure_closes_all_created_cameras(self):
        setup = Mock()
        setup.setup_camera.side_effect = self.cameras.__getitem__
        self.cameras['left'].start.side_effect = RuntimeError('disconnected')
        self.runtime.cameras = {}
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            self.runtime._start_cameras(setup)
        for camera in self.cameras.values():
            camera.close.assert_called_once()

    def test_authenticated_routes_select_the_right_camera(self):
        manager = SimpleNamespace(runtime=self.runtime, lock=threading.Lock(), tasks={})
        server = serve(manager, 'secret', '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            device = RealDevice()
            device.url = f'http://127.0.0.1:{server.server_port}'
            device.token = 'secret'
            for name in ('head', 'left'):
                url = f'http://127.0.0.1:{server.server_port}/api/v1/cameras/{name}/frame.jpg'
                with self.assertRaises(HTTPError) as error:
                    urlopen(url)
                self.assertEqual(error.exception.code, 401)
                with urlopen(Request(url, headers={'Authorization': 'Bearer secret'})) as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                    self.assertEqual(response.read(), self.runtime.camera_jpeg(name))
            self.assertEqual(device.head_camera_jpeg(), self.runtime.head_camera_jpeg())
            self.assertEqual(device.left_camera_jpeg(), self.runtime.left_camera_jpeg())
            self.cameras['head'].get_latest_color_frame.return_value = None
            with self.assertRaises(DeviceError) as error:
                device.head_camera_jpeg()
            self.assertEqual(error.exception.status, 503)
            self.assertEqual(device.left_camera_jpeg(), self.runtime.left_camera_jpeg())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
