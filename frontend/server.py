"""Python 3.9+，无第三方依赖；本地联调服务，不是生产级设备网关。"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from device_adapter import DemoDevice, RealDevice, DeviceError

ROOT = Path(__file__).resolve().parent
TERMINAL = {"completed", "failed", "stopped"}
STATES = TERMINAL | {"queued", "running", "stopping", "needs_attention"}
CATALOG = {"water": "drinks", "cola": "drinks", "oolong_tea": "drinks",
           "potato_chips": "snacks", "cookies": "snacks", "chocolate": "snacks"}

ITEM_NAMES = {"water": "矿泉水", "cola": "可乐", "oolong_tea": "乌龙茶",
              "potato_chips": "薯片", "cookies": "饼干", "chocolate": "巧克力"}


class APIError(Exception):
    def __init__(self, code, message, error_code=None):
        self.code, self.message, self.error_code = code, message, error_code


class Store:
    """设备任务独立落盘；单进程锁保证每次状态转换按顺序执行。"""

    def __init__(self, task_history_path, device):
        self.task_history_path = Path(task_history_path)
        self.task_history_path.parent.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.lock = threading.RLock()
        self.fault = False
        self._load_and_migrate()
        active = self.active()
        if active and active["mode"] == device.mode == "demo":
            # 演示没有实际动作；实机任务保持活动状态，由 tick 查询，绝不重新下发。
            self.finish("stopped")

    @staticmethod
    def _read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def _write_atomic(path, value, prefix):
        path = Path(path)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=prefix, suffix=".tmp", delete=False) as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
                temp_path = file.name
            os.replace(temp_path, path)
        except OSError:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise

    def _load_and_migrate(self):
        if self.task_history_path.exists():
            self.history = self._read_json(self.task_history_path)
        else:
            self.history = {"schema_version": 1, "revision": 0, "active_task_id": None, "tasks": {}}
            self._write_atomic(self.task_history_path, self.history, "task-history-")
        if not isinstance(self.history, dict) or not isinstance(self.history.get("tasks"), dict):
            raise ValueError("任务历史格式无效")
        changed = False
        for task in self.history["tasks"].values():
            if not isinstance(task, dict):
                raise ValueError("任务记录无效")
            old_status = task.get("status")
            if old_status == "inventory_pending" or (
                    old_status in ("needs_attention", "abandoned")
                    and "inventory_revision_before" in task):
                # 旧版仅在收到设备 completed 后记录扣库事务，此处无需再扣库。
                task.update(status="completed", completed_at=task.get("completed_at") or now_iso(),
                            updated_at=now_iso())
                changed = True
            elif old_status == "abandoned":
                task.update(status="needs_attention", previous_status=old_status, updated_at=now_iso())
                changed = True
            for field in ("inventory_revision_before", "quantity_before", "inventory_applied"):
                if field in task:
                    del task[field]
                    changed = True
        active = self.history.get("active_task_id")
        if active and self.history["tasks"].get(active, {}).get("status") in TERMINAL:
            self.history["active_task_id"] = None
            changed = True
        self.validate_history(self.history)
        if changed:
            self.history["revision"] += 1
            self._write_atomic(self.task_history_path, self.history, "task-history-")

    @staticmethod
    def validate_history(data):
        if data.get("schema_version") != 1 or type(data.get("revision")) is not int or data["revision"] < 0:
            raise ValueError("任务历史 schema_version 或 revision 不正确")
        tasks = data.get("tasks")
        if not isinstance(tasks, dict):
            raise ValueError("tasks 必须是对象")
        requests = set()
        for key, task in tasks.items():
            if (task.get("task_id") != key or task.get("item_id") not in CATALOG
                    or task.get("status") not in STATES or task.get("mode") not in ("demo", "real")
                    or not isinstance(task.get("request_id"), str) or task["request_id"] in requests):
                raise ValueError("任务记录无效")
            requests.add(task["request_id"])
        pending = [key for key, task in tasks.items() if task["status"] not in TERMINAL]
        active = data.get("active_task_id")
        if pending != ([] if active is None else [active]):
            raise ValueError("活动任务与任务历史不一致")

    def _commit_history(self, value):
        value["revision"] += 1
        self.validate_history(value)
        try:
            self._write_atomic(self.task_history_path, value, "task-history-")
            self.history = value
        except OSError:
            self.fault = True
            raise

    def active(self):
        return self.history["tasks"].get(self.history["active_task_id"])

    def snapshot(self):
        with self.lock:
            active = self.active()
            items = {key: {"name": ITEM_NAMES[key], "category": category, "available":None}
                     for key, category in CATALOG.items()}
            stock = {'dry_run':False, 'revision':0}
            try:
                device_ready = bool(self.device.ready())
                stock = self.device.inventory()
                if type(stock.get('dry_run')) is not bool or type(stock.get('revision')) is not int:
                    raise ValueError('Invalid inventory response')
                if not all(type(stock['items'].get(key)) is int and stock['items'][key] in (0,1) for key in CATALOG):
                    raise ValueError('Invalid inventory response')
                for key in items:
                    items[key]['available'] = stock['items'][key]
            except Exception:
                device_ready = False
                stock = {'dry_run':False, 'revision':0}
            return {"revision": self.history["revision"],
                    "task_revision": self.history["revision"], "mode": self.device.mode,
                    "dry_run": stock["dry_run"], "inventory_revision":stock["revision"],
                    "ready": device_ready and not self.fault and active is None,
                    "storage_ok": not self.fault, "items": items,
                    "active_task": copy.deepcopy(active)}

    def finish(self, result):
        detail = result if isinstance(result, dict) else {}
        status = detail.get('status') if detail else result
        if status not in STATES:
            status = "needs_attention"
        metadata = {key:detail[key] for key in ('phase','error_code','recognition') if key in detail}
        if not self.active() or (self.active()['status'] == status and
                all(self.active().get(key) == value for key,value in metadata.items())):
            return
        value = copy.deepcopy(self.history)
        task = value["tasks"][value["active_task_id"]]
        task["status"] = status
        task.update(metadata)
        task["updated_at"] = now_iso()
        if status in TERMINAL:
            task["completed_at"] = task["updated_at"]
        if status in TERMINAL:
            value["active_task_id"] = None
        self._commit_history(value)

    def grab(self, body):
        with self.lock:
            item_id = body.get("item_id") or body.get("drink_id") or body.get("snack_id")
            request_id = body.get("request_id")
            if not isinstance(item_id, str) or item_id not in CATALOG:
                raise APIError(400, "未知商品")
            expected = "grab_drink" if CATALOG[item_id] == "drinks" else "grab_snack"
            if body.get("action", expected) != expected:
                raise APIError(400, "动作与商品不匹配")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
                raise APIError(400, "缺少有效 request_id")
            for task in self.history["tasks"].values():
                if task["request_id"] == request_id:
                    if task["item_id"] != item_id:
                        raise APIError(409, "同一 request_id 不能用于其他商品")
                    return copy.deepcopy(task)
            if self.active():
                raise APIError(409, "设备忙碌或任务待人工核对")
            snapshot = self.snapshot()
            if snapshot['items'][item_id]['available'] == 0:
                raise APIError(409, '该商品已售尽，请选择其他商品', 'out_of_stock')
            if not snapshot["ready"]:
                raise APIError(503, "控制服务或设备未就绪")
            created_at = now_iso()
            task = {"task_id": str(uuid.uuid4()), "request_id": request_id,
                    "item_id": item_id, "status": "queued", "mode": self.device.mode,
                    "created_at": created_at, "updated_at": created_at}
            value = copy.deepcopy(self.history)
            value["tasks"][task["task_id"]] = task
            value["active_task_id"] = task["task_id"]
            self._commit_history(value)  # 先保存任务，成功后才允许设备动作。
            try:
                self.device.start(copy.deepcopy(task))
            except DeviceError as error:
                self.finish({'status':'failed', 'phase':'checking', 'error_code':error.error_code})
            except Exception:
                self.finish("needs_attention")
            return copy.deepcopy(self.history["tasks"][task["task_id"]])

    def tick(self):
        with self.lock:
            task = self.active()
            if not task or self.fault or task["mode"] != self.device.mode:
                return
            try:
                result = self.device.poll(copy.deepcopy(task))
            except Exception:
                result = "needs_attention"
            self.finish(result)

    def stop(self, body):
        with self.lock:
            task = self.history["tasks"].get(body.get("task_id"))
            if task is None and isinstance(body.get("request_id"), str):
                task = next((t for t in self.history["tasks"].values() if t["request_id"] == body["request_id"]), None)
            if task is None:
                raise APIError(404, "未找到指定任务")
            task_id = task["task_id"]
            if task["status"] in TERMINAL:
                return copy.deepcopy(task)
            if task["mode"] != self.device.mode:
                raise APIError(409, "任务模式与当前设备不匹配，请恢复原模式查询任务")
            try:
                result = self.device.stop(copy.deepcopy(task))
            except Exception:
                result = "needs_attention"
            self.finish(result)
            return copy.deepcopy(self.history["tasks"][task_id])

    def set_test_inventory(self, body):
        with self.lock:
            try:
                stock = self.device.inventory()
                if stock.get('dry_run') is not True:
                    raise APIError(403, '仅模拟模式允许修改测试库存')
                if self.active() or self.fault:
                    raise APIError(409, '任务进行中，请等待结束后修改库存')
                self.device.set_test_inventory(body)
                return self.snapshot()
            except DeviceError as error:
                raise APIError(error.status, str(error), error.error_code) from error
            except (OSError, RuntimeError) as error:
                raise APIError(503, '无法连接库存服务') from error

    def task_history(self, limit=100):
        with self.lock:
            tasks = list(self.history["tasks"].values())[-limit:]
            tasks.reverse()
            return {"schema_version": self.history["schema_version"],
                    "revision": self.history["revision"], "total": len(self.history["tasks"]),
                    "active_task_id": self.history["active_task_id"],
                    "tasks": copy.deepcopy(tasks)}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def handler_for(store, allowed_hosts):
    class Handler(SimpleHTTPRequestHandler):
        # 页面会自动轮询健康度和当前任务。关闭这些普通访问日志，
        # 终端只保留创建/停止任务的 POST 请求和 HTTP 错误。
        def log_message(self, format, *args):
            status_code = 0
            if len(args) > 1:
                try:
                    status_code = int(args[1])
                except (TypeError, ValueError):
                    pass

            path = urlsplit(self.path).path
            if path in ("/api/grab", "/api/stop") or status_code >= 400:
                super().log_message(format, *args)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

        def guard(self, write=False):
            host = self.headers.get("Host", "").lower()
            if host not in allowed_hosts:
                raise APIError(403, "Host 不在允许列表")
            if write:
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + host:
                    raise APIError(403, "仅允许同源请求")
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise APIError(403, "禁止跨站请求")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise APIError(415, "需要 application/json")

        def send_json(self, code, value):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                self.guard()
                parsed = urlsplit(self.path)
                path = parsed.path
                if path == "/api/cameras/head/frame.jpg":
                    try:
                        raw = store.device.head_camera_jpeg()
                    except DeviceError as error:
                        raise APIError(error.status, str(error)) from error
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if path == "/api/health":
                    return self.send_json(200, store.snapshot())
                if path in ("/api/tasks", "/api/task-history"):
                    values = parse_qs(parsed.query).get("limit", ["100"])
                    try:
                        limit = int(values[0])
                    except (TypeError, ValueError):
                        raise APIError(400, "limit 必须是 1–500 的整数")
                    if not 1 <= limit <= 500:
                        raise APIError(400, "limit 必须是 1–500 的整数")
                    return self.send_json(200, store.task_history(limit))
                if path.startswith("/api/requests/"):
                    with store.lock:
                        request_id = path.removeprefix("/api/requests/")
                        task = next((t for t in store.history["tasks"].values() if t["request_id"] == request_id), None)
                        if not task:
                            raise APIError(404, "尚未找到该请求；不表示请求未在途中")
                        return self.send_json(200, task)
                if path.startswith("/api/tasks/"):
                    with store.lock:
                        task = store.history["tasks"].get(path.removeprefix("/api/tasks/"))
                        if not task:
                            raise APIError(404, "任务不存在")
                        return self.send_json(200, task)
                if path.startswith("/api/"):
                    raise APIError(404, "接口不存在")
                return super().do_GET()
            except APIError as error:
                self.send_json(error.code, {"error": error.message, "error_code":error.error_code})

        def do_POST(self):
            try:
                self.guard(write=True)
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 4096:
                    raise APIError(400, "请求体大小无效")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise APIError(400, "请求体必须为 JSON 对象")
                path = urlsplit(self.path).path
                if path == "/api/grab":
                    return self.send_json(202, store.grab(body))
                if path == "/api/test/inventory":
                    return self.send_json(200, store.set_test_inventory(body))
                if path == "/api/stop":
                    return self.send_json(200, store.stop(body))
                raise APIError(404, "接口不存在")
            except APIError as error:
                self.send_json(error.code, {"error": error.message, "error_code":error.error_code})
            except (ValueError, TypeError):
                self.send_json(400, {"error": "请求数据无效"})
            except OSError:
                self.send_json(503, {"error": "任务历史保存失败；已禁止新抓取，请核对设备状态"})

        def list_directory(self, path):
            self.send_error(403)
            return None
    return Handler


def acquire_process_lock(path):
    # OS 自动在进程退出时释放锁，禁止两个服务同时写同一份 JSON。
    handle = open(str(path) + ".lock", "a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--allow-host", action="append", default=[], help="LAN IP:端口，可重复")
    parser.add_argument("--mode", choices=("demo", "real"), default="demo")
    parser.add_argument("--tasks-data", type=Path, default=ROOT / "data/task_history.json",
                        help="任务历史 JSON 文件")
    args = parser.parse_args()
    tasks_data = args.tasks_data.resolve()
    tasks_data.parent.mkdir(parents=True, exist_ok=True)
    process_lock = acquire_process_lock(tasks_data)
    store = Store(tasks_data, DemoDevice() if args.mode == "demo" else RealDevice())
    hosts = {f"localhost:{args.port}", f"127.0.0.1:{args.port}", *args.allow_host}
    server = ThreadingHTTPServer((args.host, args.port), handler_for(store, hosts))
    done = threading.Event()

    def worker():
        while not done.wait(.2):
            try:
                store.tick()
            except Exception:
                store.fault = True  # 不可静默继续发出新动作。

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(f"KAANH: http://localhost:{args.port} · {args.mode}，Ctrl+C 停止服务", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        thread.join(timeout=5)
        server.server_close()
        process_lock.close()


if __name__ == "__main__":
    main()
