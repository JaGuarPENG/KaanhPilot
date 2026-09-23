import importlib.util
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import numpy as np
from taskrunner.runner import TaskRunner


class DirectServerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('frontend.server'), '缺少直接 HTTP 适配器')
        from frontend.server import handler_for
        from frontend.simulation import SimulatedActions
        self.actions = SimulatedActions(delay=0.05)
        self.runner = TaskRunner(self.actions)
        self.runner.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.runner, simulation=self.actions))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers=headers or {})
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def finish(self, order):
        for _ in range(200):
            current = self.request('/api/orders/' + order['order_id'])
            if current['status'] in ('succeeded', 'failed', 'cancelled', 'paused'):
                return current
            time.sleep(.01)
        self.fail('订单未结束')

    def tearDown(self):
        if not hasattr(self, 'server'):
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for _ in range(200):
            queue = self.runner.get_queue()
            if queue.current_order and queue.current_order.status.value == 'paused':
                self.runner.cancel_order(queue.current_order.order_id)
            if not queue.current_order and not queue.pending_orders:
                break
            time.sleep(.01)
        self.runner.shutdown()

    def test_direct_order_and_four_stages(self):
        self.assertTrue(self.request('/api/status')['accepting_orders'])
        first = self.request('/api/orders', {'item_id': 'water'})
        second = self.request('/api/orders', {'item_id': 'cola'})
        self.assertEqual(self.request('/api/queue')['pending_orders'][0]['order_id'], second['order_id'])
        cancelled = self.request('/api/orders/' + second['order_id'] + '/cancel', {})
        self.assertEqual(cancelled['status'], 'cancelled')
        result = self.finish(first)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([t['status'] for t in result['tasks']], ['succeeded'] * 4)

    def test_stock_and_paused_results_come_from_runner(self):
        self.request('/api/test/inventory', {'item_id': 'water', 'scenario': 'sold-out'})
        result = self.finish(self.request('/api/orders', {'item_id': 'water'}))
        self.assertEqual(result['error_code'], 'out_of_stock')
        self.assertEqual(result['tasks'][1]['status'], 'skipped')
        self.request('/api/test/inventory', {'item_id': 'cola', 'scenario': 'paused'})
        result = self.finish(self.request('/api/orders', {'item_id': 'cola'}))
        self.assertEqual(result['status'], 'paused')

    def test_static_and_rejected_cross_origin(self):
        with urlopen(self.base + '/') as response:
            self.assertIn(b'queue-model.js', response.read())
        for path in ('/../config/robot/robot_config.json', '/api/state', '/api/requests/old'):
            with self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/orders', {'item_id': 'water'}, {'Origin': 'http://elsewhere.invalid'})
        self.assertEqual(error.exception.code, 403)

    def test_no_login_required_and_real_mode_rejects_simulation(self):
        from frontend.server import handler_for
        self.server.RequestHandlerClass = handler_for(self.runner)
        self.assertTrue(self.request('/api/status')['accepting_orders'])
        self.assertFalse(self.request('/api/info')['dry_run'])
        self.assertEqual(self.finish(self.request('/api/orders', {'item_id':'water'}))['status'], 'succeeded')
        for path in ('/api/session', '/api/test/inventory'):
            with self.assertRaises(HTTPError) as error:
                self.request(path, {})
            self.assertEqual(error.exception.code, 404)

    def test_recognition_mode_reports_live_camera_without_inventory_injection(self):
        from frontend.server import handler_for
        self.server.RequestHandlerClass = handler_for(self.runner, dry_run=True, cameras={'left': object()})
        info = self.request('/api/info')
        self.assertTrue(info['dry_run'])
        self.assertTrue(info['camera_available'])
        self.assertEqual(info['cameras'], {'head': False, 'left': True, 'right': False})
        self.assertFalse(info['inventory_test'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/test/inventory', {'item_id':'water', 'scenario':'sold-out'})
        self.assertEqual(error.exception.code, 404)

    def test_each_available_camera_has_its_own_frame_route(self):
        from frontend.server import handler_for

        class Camera:
            def __init__(self, value):
                self.value = value
                self.reads = 0

            def get_latest_color_frame(self):
                self.reads += 1
                return SimpleNamespace(rgb=np.full((4, 4, 3), self.value, dtype=np.uint8))

        head, left = Camera(20), Camera(220)
        self.server.RequestHandlerClass = handler_for(self.runner, cameras={'head': head, 'left': left})
        self.assertEqual(self.request('/api/info')['cameras'],
                         {'head': True, 'left': True, 'right': False})
        with urlopen(self.base + '/api/cameras/head/frame.jpg') as response:
            head_frame = response.read()
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
        with urlopen(self.base + '/api/cameras/left/frame.jpg') as response:
            left_frame = response.read()
        self.assertTrue(head_frame.startswith(b'\xff\xd8'))
        self.assertTrue(left_frame.startswith(b'\xff\xd8'))
        self.assertNotEqual(head_frame, left_frame)
        self.assertEqual((head.reads, left.reads), (1, 1))
        for path, status in (('/api/cameras/right/frame.jpg', 503),
                             ('/api/cameras/unknown/frame.jpg', 404)):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.base + path)
            self.assertEqual(error.exception.code, status)
        right = Camera(100)
        self.server.RequestHandlerClass = handler_for(self.runner, cameras={'right': right})
        with urlopen(self.base + '/api/cameras/right/frame.jpg') as response:
            self.assertTrue(response.read().startswith(b'\xff\xd8'))
        self.assertEqual(right.reads, 1)


class RuntimeTests(unittest.TestCase):
    def test_root_launcher_passes_configuration_to_direct_server(self):
        import launcher
        config = {'dry_run':True, 'host':'127.0.0.1', 'port':8088, 'open_browser':False}
        with patch.object(launcher, 'load_config', return_value=config), \
                patch.object(launcher, 'lock_launcher') as lock, \
                patch.object(launcher, 'check_port'), patch('frontend.server.serve') as serve:
            launcher.main()
            serve.assert_called_once_with(config, launcher.PROJECT_ROOT / 'config')
            lock.return_value.close.assert_called_once()

    def test_real_mode_selects_hardware_factory_without_running_it(self):
        from frontend.server import create_runtime
        with patch('taskrunner.runtime.create_hardware_runtime') as factory:
            runtime = create_runtime({'dry_run':False, 'agv_ip':'test-agv', 'queue_capacity':3}, 'config', None)
            self.assertIs(runtime, factory.return_value)
            self.assertEqual(factory.call_args.kwargs['agv_ip'], 'test-agv')
            self.assertEqual(factory.call_args.kwargs['queue_capacity'], 3)

    def test_dry_run_selects_recognition_runtime(self):
        from frontend.server import create_runtime
        from pathlib import Path
        callback = lambda error: None
        with patch('taskrunner.runtime.create_hardware_runtime') as hardware, \
                patch('taskrunner.runtime.create_recognition_test_runtime') as recognition:
            runtime = create_runtime({'dry_run':True, 'simulation_robot_ip':'192.0.2.10',
                                      'stage_delay':2, 'queue_capacity':4}, 'config', callback)
            self.assertIs(runtime, recognition.return_value)
            recognition.assert_called_once_with(config_dir=Path('config'), robot_ip='192.0.2.10',
                                                 stage_delay_s=2, queue_capacity=4, on_fatal=callback)
            hardware.assert_not_called()

    def test_dry_run_defaults_follow_recognition_factory(self):
        from frontend.server import create_runtime
        with patch('taskrunner.runtime.create_recognition_test_runtime') as recognition:
            create_runtime({'dry_run':True}, 'config', None)
            self.assertNotIn('robot_ip', recognition.call_args.kwargs)
            self.assertNotIn('stage_delay_s', recognition.call_args.kwargs)

    def test_recognition_failure_does_not_fall_back_to_hardware(self):
        from frontend.server import create_runtime
        with patch('taskrunner.runtime.create_recognition_test_runtime', side_effect=RuntimeError('offline')), \
                patch('taskrunner.runtime.create_hardware_runtime') as hardware:
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                create_runtime({'dry_run':True}, 'config', None)
            hardware.assert_not_called()

    def test_server_uses_recognition_runner_camera_and_cleanup(self):
        from frontend.server import serve
        with patch('frontend.server.create_runtime') as factory, \
                patch('frontend.server.ThreadingHTTPServer') as server, \
                patch('frontend.server.handler_for') as handler:
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            runtime = factory.return_value
            serve({'dry_run':True, 'api_token':'obsolete-config-value'}, 'config')
            handler.assert_called_once_with(runtime.runner, dry_run=True, cameras={'left': runtime.camera})
            runtime.runner.start.assert_called_once()
            runtime.close.assert_called_once()

    def test_server_passes_hardware_camera_map(self):
        from frontend.server import serve
        with patch('frontend.server.create_runtime') as factory, \
                patch('frontend.server.ThreadingHTTPServer') as server, \
                patch('frontend.server.handler_for') as handler:
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            runtime = factory.return_value
            serve({'dry_run': False}, 'config')
            handler.assert_called_once_with(runtime.runner, dry_run=False, cameras=runtime.cameras)


if __name__ == '__main__':
    unittest.main()
