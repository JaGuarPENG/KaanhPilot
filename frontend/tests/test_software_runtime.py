import contextlib
import io
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from frontend.server import create_runtime, handler_for, serve


class SoftwareRuntimeTests(unittest.TestCase):
    def test_software_mode_serves_orders_without_hardware(self):
        # Hardware modules must not even be imported in software mode.
        with patch.dict('sys.modules', {'taskrunner.runtime': None, 'commands.setup': None}):
            runtime = create_runtime({'software_only': True, 'dry_run': True,
                                      'stage_delay': 0.01}, 'missing-config-directory', None)
        self.assertIsNone(runtime.camera)
        runtime.runner.start()
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(
            runtime.runner, dry_run=True, cameras={'left': runtime.camera}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/info', timeout=3) as response:
                info = json.load(response)
            self.assertTrue(info['dry_run'])
            self.assertEqual(info['cameras'], {'head': False, 'left': False, 'right': False})
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/api/cameras/left/frame.jpg', timeout=3)
            self.assertEqual(error.exception.code, 503)
            error.exception.close()
            request = Request(base + '/api/orders', data=b'{"item_id":"cola"}',
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 201)
                order = json.load(response)
            for _ in range(100):
                with urlopen(base + '/api/orders/' + order['order_id'], timeout=3) as response:
                    result = json.load(response)
                if result['status'] == 'succeeded':
                    break
                time.sleep(0.01)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual([task['status'] for task in result['tasks']], ['succeeded'] * 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            runtime.runner.shutdown()
            runtime.close()

    def test_invalid_or_conflicting_mode_never_imports_hardware(self):
        for config in ({'software_only': 'true'},
                       {'software_only': True, 'dry_run': False}):
            with self.subTest(config=config), \
                    patch.dict('sys.modules', {'taskrunner.runtime': None}):
                with self.assertRaises(ValueError):
                    create_runtime(config, 'config', None)

    def test_server_starts_and_shuts_down_software_runtime(self):
        with patch('frontend.server.ThreadingHTTPServer') as server, \
                patch.dict('sys.modules', {'taskrunner.runtime': None}):
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            serve({'software_only': True, 'dry_run': True}, 'missing-config-directory')
            server.return_value.serve_forever.assert_called_once()
            server.return_value.server_close.assert_called_once()

    def test_launcher_labels_software_mode_and_rejects_conflict(self):
        import launcher
        config = {'software_only': True, 'dry_run': True, 'open_browser': False}
        output = io.StringIO()
        with patch.object(launcher, 'load_config', return_value=config), \
                patch.object(launcher, 'lock_launcher'), patch.object(launcher, 'check_port'), \
                patch('frontend.server.serve') as start, contextlib.redirect_stdout(output):
            launcher.main()
            start.assert_called_once_with(config, launcher.PROJECT_ROOT / 'config')
        self.assertIn('software only', output.getvalue())
        config['dry_run'] = False
        with patch.object(launcher, 'load_config', return_value=config), \
                patch('frontend.server.serve') as start:
            with self.assertRaises(ValueError):
                launcher.main()
            start.assert_not_called()
