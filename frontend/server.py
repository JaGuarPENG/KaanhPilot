"""现有页面的 HTTP 适配器；订单和队列仅由 Runner 管理。由根 launcher 启动。"""
from dataclasses import asdict, is_dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import unquote, urlsplit

from taskrunner.errors import (TaskRunnerError, UnknownOrderError, UnsupportedBeverageError,
                               RunnerBusyError, FatalExecutionError)

ROOT = Path(__file__).resolve().parent / 'dist'


def to_jsonable(value):
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def handler_for(runner, *, dry_run=False, simulation=None, cameras=None, runtime_id=None):
    identity = runtime_id or secrets.token_hex(16)
    cameras = dict(cameras or {})
    camera_locks = {name: threading.Lock() for name in cameras}
    mutation_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if len(args) > 1 and str(args[1]).startswith(('4', '5')):
                print('[Frontend] ' + fmt % args)

        def send_bytes(self, code, content, content_type):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def json(self, code, data):
            self.send_bytes(code, json.dumps(to_jsonable(data), ensure_ascii=False).encode(),
                            'application/json; charset=utf-8')

        def error(self, code, message, kind='HTTPError'):
            self.json(code, {'error': {'type': kind, 'message': message}})

        def body(self):
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= 65536:
                raise ValueError('请求体大小无效')
            value = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError('请求体必须是 JSON 对象')
            return value

        def dispatch(self, post=False):
            path = urlsplit(self.path).path
            if post:
                origin = self.headers.get('Origin')
                if (self.headers.get('Sec-Fetch-Site') == 'cross-site' or
                        (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
                    return self.error(403, '拒绝跨站操作')
            if post:
                with mutation_lock:
                    if path == '/api/orders':
                        return self.json(201, runner.submit_beverage(self.body().get('item_id')))
                    if path.startswith('/api/orders/') and path.endswith('/cancel'):
                        order_id = unquote(path[len('/api/orders/'):-len('/cancel')])
                        if not order_id or '/' in order_id:
                            return self.error(404, '接口不存在')
                        return self.json(200, runner.cancel_order(order_id))
                    if path == '/api/test/inventory' and simulation is not None:
                        queue = runner.get_queue()
                        if queue.current_order or queue.pending_orders:
                            return self.error(409, '请等待队列空闲后设置模拟场景')
                        body = self.body()
                        simulation.set_scenario(body.get('item_id'), body.get('scenario'))
                        return self.json(200, {'ok': True})
                return self.error(404, '接口不存在')
            if path == '/api/status':
                return self.json(200, runner.get_status())
            if path == '/api/queue':
                return self.json(200, runner.get_queue())
            if path == '/api/info':
                return self.json(200, {'dry_run': dry_run, 'runtime_id': identity,
                                       'camera_available': cameras.get('left') is not None,
                                       'cameras': {name: cameras.get(name) is not None
                                                   for name in ('head', 'left', 'right')},
                                       'inventory_test': simulation is not None})
            if path.startswith('/api/orders/'):
                order_id = unquote(path[len('/api/orders/'):])
                if not order_id or '/' in order_id:
                    return self.error(404, '接口不存在')
                return self.json(200, runner.get_order(order_id))
            if path.startswith('/api/cameras/') and path.endswith('/frame.jpg'):
                name = path[len('/api/cameras/'):-len('/frame.jpg')]
                if name not in ('head', 'left', 'right'):
                    return self.error(404, '接口不存在')
                camera = cameras.get(name)
                if camera is None:
                    return self.error(503, '当前模式没有实时相机')
                import cv2
                import numpy as np
                with camera_locks[name]:
                    frame = camera.get_latest_color_frame()
                    if frame is None:
                        return self.error(503, '相机尚无画面')
                    ok, encoded = cv2.imencode('.jpg', np.ascontiguousarray(frame.rgb[..., ::-1]))
                    if not ok:
                        return self.error(503, '相机编码失败')
                return self.send_bytes(200, encoded.tobytes(), 'image/jpeg')
            if path.startswith('/api/'):
                return self.error(404, '接口不存在')
            target = (ROOT / ('index.html' if path == '/' else unquote(path).lstrip('/'))).resolve()
            if not target.is_relative_to(ROOT.resolve()) or not target.is_file():
                return self.error(404, '页面资源不存在')
            kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix == '.js':
                kind = 'text/javascript'
            return self.send_bytes(200, target.read_bytes(), kind)

        def handle_request(self, post=False):
            try:
                self.dispatch(post)
            except UnknownOrderError as error:
                self.error(404, str(error), type(error).__name__)
            except (ValueError, UnicodeError, UnsupportedBeverageError) as error:
                self.error(400, str(error), type(error).__name__)
            except TaskRunnerError as error:
                self.error(409, str(error), type(error).__name__)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                print(f'[Frontend] 接口异常: {error!r}')
                self.error(500, '服务器内部错误', type(error).__name__)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request(True)

    return Handler


def create_runtime(config, config_dir, on_fatal):
    """dry_run 连接模拟控制器；真实模式沿用原硬件运行时。"""
    if config.get('dry_run', True):
        from taskrunner.runtime import create_recognition_test_runtime
        options = {}
        if 'simulation_robot_ip' in config:
            options['robot_ip'] = config['simulation_robot_ip']
        if 'stage_delay' in config:
            options['stage_delay_s'] = config['stage_delay']
        return create_recognition_test_runtime(config_dir=Path(config_dir),
                                               queue_capacity=config.get('queue_capacity', 10),
                                               on_fatal=on_fatal, **options)
    from taskrunner.runtime import create_hardware_runtime
    options = {name: config[name] for name in ('agv_ip', 'agv_port', 'agv_device_id') if name in config}
    return create_hardware_runtime(config_dir=Path(config_dir),
                                   queue_capacity=config.get('queue_capacity', 10),
                                   on_fatal=on_fatal, **options)


def serve(config, config_dir):
    runtime = None

    def on_fatal(error):
        print(f'[Frontend] Runner 致命故障: {error}')
        if runtime is not None:
            threading.Thread(target=runtime.close, daemon=True, name='hardware-cleanup').start()

    # 在初始化设备前占用端口，避免端口冲突时仍连接设备。
    server = ThreadingHTTPServer((config.get('host', '127.0.0.1'), config.get('port', 8088)),
                                 BaseHTTPRequestHandler)
    try:
        runtime = create_runtime(config, config_dir, on_fatal)
        dry_run = config.get('dry_run', True)
        cameras = {'left': runtime.camera} if dry_run else runtime.cameras
        server.RequestHandlerClass = handler_for(runtime.runner,
            dry_run=dry_run, cameras=cameras)
        runtime.runner.start()
        while True:
            try:
                server.serve_forever(poll_interval=.25)
                break
            except KeyboardInterrupt:
                try:
                    runtime.runner.shutdown()
                except RunnerBusyError:
                    print('队列尚未清空，请在页面取消等待/暂停订单，或等待执行完成后再退出。')
                    continue
                break
    finally:
        server.server_close()
        if runtime is not None:
            try:
                runtime.runner.shutdown()
            except RunnerBusyError:
                runtime.runner.report_fatal(FatalExecutionError('HTTP 服务异常退出'))
                runtime.runner.shutdown()
            finally:
                runtime.close()
