"""Camera JPEGs use the existing authenticated device boundary."""
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from server import Store, handler_for, ThreadingHTTPServer
from device_adapter import DemoDevice

class CameraDevice(DemoDevice):
    def head_camera_jpeg(self):
        return b'\xff\xd8camera\xff\xd9'

class CameraProxyTests(unittest.TestCase):
    def test_jpeg_proxy_and_unavailable_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = CameraDevice()
            store = Store(Path(tmp)/'tasks.json', device)
            http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store, {'127.0.0.1:0'}))
            # guard needs actual bound port
            http.RequestHandlerClass = handler_for(store, {f'127.0.0.1:{http.server_port}'})
            thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
            try:
                url = f'http://127.0.0.1:{http.server_port}/api/cameras/head/frame.jpg'
                with urlopen(url) as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                    self.assertEqual(response.read(), b'\xff\xd8camera\xff\xd9')
                store.device = DemoDevice()
                with self.assertRaises(HTTPError) as caught:
                    urlopen(url)
                self.assertEqual(caught.exception.code, 503)
            finally:
                http.shutdown(); http.server_close(); thread.join()

if __name__ == '__main__': unittest.main()
