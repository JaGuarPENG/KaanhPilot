"""设备边界：只在服务器内调用，不向浏览器公开设备凭据。"""
import time
import copy


class DeviceError(Exception):
    def __init__(self, status, message, error_code=None):
        super().__init__(message)
        self.status, self.error_code = status, error_code


class DemoDevice:
    mode = "demo"

    def __init__(self):
        self.started = {}
        self.stock = {item:1 for item in ('water','cola','oolong_tea','potato_chips','cookies','chocolate',
                                        'americano','latte','cappuccino')}
        self.confirmation = {}
        self.stock_revision = 0
        self.results = {}

    def inventory(self):
        return {'dry_run':True, 'revision':self.stock_revision, 'items':dict(self.stock)}

    def set_test_inventory(self, body):
        item, value = body.get('item_id'), body.get('available')
        confirmation = body.get('confirmation_available', value)
        if not isinstance(item,str) or item not in self.stock:
            raise DeviceError(400,'未知商品')
        if any(type(n) is not int or n not in (0,1) for n in (value,confirmation)):
            raise DeviceError(400,'库存只能为整数 0 或 1')
        self.stock[item] = value
        self.confirmation[item] = confirmation
        self.stock_revision += 1
        return self.inventory()

    def head_camera_jpeg(self):
        raise DeviceError(503, '模拟模式没有真实头部相机')

    def left_camera_jpeg(self):
        raise DeviceError(503, '模拟模式没有真实左手相机')

    def ready(self):
        return True

    def start(self, task):
        # task_id 是设备侧幂等键；真实适配器也必须实现去重。
        key, item = task['task_id'], task['item_id']
        if key in self.started:
            return
        if self.stock[item] == 0:
            raise DeviceError(409, '该商品已售尽', 'out_of_stock')
        self.started[key] = time.monotonic()
        available = self.confirmation.get(item, self.stock[item])
        self.stock[item] = available
        self.stock_revision += 1
        if available == 0:
            self.results[key] = {'status':'failed','phase':'checking','error_code':'out_of_stock'}

    def poll(self, task):
        if task['task_id'] in self.results:
            return copy.deepcopy(self.results[task['task_id']])
        elapsed = time.monotonic() - self.started[task["task_id"]]
        status = "completed" if elapsed >= 3.1 else "running"
        return {'status':status, 'phase':'placed' if elapsed >= 3.1 else 'picking' if elapsed >= .7 else 'checking'}

    def stop(self, task):
        # 完成与停止竞态：以设备实际结果为准。
        result = self.poll(task)
        return result if result['status'] in ('completed','failed') else 'stopped'


class RealDevice:
    """真实设备驱动"""
    mode = "real"

    def __init__(self):
        import os
        self.url = os.environ.get("KAANH_API_URL", "http://127.0.0.1:8088").rstrip("/")
        self.token = os.environ.get("KAANH_API_TOKEN", "")

    def request(self, path, body=None):
        if not self.token:
            raise RuntimeError("请设置 KAANH_API_TOKEN")
        import json
        from urllib.request import Request, urlopen
        req = Request(self.url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        from urllib.error import HTTPError
        try:
            with urlopen(req, timeout=4) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                data = json.load(error)
            except (ValueError, OSError):
                data = {}
            raise DeviceError(error.code, data.get('error','后端请求失败'), data.get('error_code')) from error

    def head_camera_jpeg(self):
        return self.camera_jpeg('head')

    def left_camera_jpeg(self):
        return self.camera_jpeg('left')

    def camera_jpeg(self, name):
        if name not in ('head', 'left'):
            raise DeviceError(404, '未知相机')
        if not self.token:
            raise DeviceError(503, '请设置 KAANH_API_TOKEN')
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        req = Request(self.url + f'/api/v1/cameras/{name}/frame.jpg',
                      headers={'Authorization': 'Bearer ' + self.token})
        try:
            with urlopen(req, timeout=4) as response:
                if response.headers.get_content_type() != 'image/jpeg':
                    raise DeviceError(502, '后端未返回 JPEG 相机画面')
                raw = response.read(5 * 1024 * 1024 + 1)
                if not raw or len(raw) > 5 * 1024 * 1024:
                    raise DeviceError(502, '相机画面大小无效')
                return raw
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            label = {'head': '头部', 'left': '左手'}[name]
            raise DeviceError(503, label + '相机暂不可用，请检查后端连接及相机状态') from error

    def ready(self):
        return self.request("/api/v1/health").get("ready") is True

    def inventory(self):
        data = self.request('/api/v1/inventory')
        if type(data.get('dry_run')) is not bool or type(data.get('revision')) is not int:
            raise RuntimeError('Invalid inventory response')
        return data

    def set_test_inventory(self, body):
        return self.request('/api/v1/test/inventory', body)

    def start(self, task):
        self.request("/api/v1/picks", {"item_id": task["item_id"], "request_id": task["task_id"]})

    def poll(self, task):
        from urllib.parse import quote
        result = self.request("/api/v1/tasks/" + quote(task["task_id"], safe=""))
        if result.get("task_id") != task["task_id"]:
            raise RuntimeError("Task identity mismatch")
        status = result.get("status")
        if status not in {"queued", "running", "completed", "failed"}:
            raise RuntimeError('Unknown device task status')
        return {key:result[key] for key in ('status','phase','error_code','recognition') if key in result}

    def stop(self, task):
        # Original movej has no confirmed cancellation interface.
        return self.poll(task)
