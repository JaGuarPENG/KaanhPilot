"""Task persistence and HTTP regressions; no physical robot is connected."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from server import APIError, Store, ThreadingHTTPServer, handler_for
from device_adapter import DemoDevice
from launcher import prepare_frontend_data


class ControlledDevice(DemoDevice):
    def __init__(self, mode="demo"):
        super().__init__()
        self.mode = mode
        self.result = "running"
        self.starts = []

    def start(self, task):
        self.starts.append(task["task_id"])

    def poll(self, task):
        return self.result

    def stop(self, task):
        return "completed" if self.result == "completed" else "stopped"


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history_path = Path(self.temp.name) / "task_history.json"
        self.device = ControlledDevice()
        self.store = Store(self.history_path, self.device)

    def grab(self, item="water", request="request-0001"):
        return self.store.grab({"item_id": item, "request_id": request})

    def test_repeated_completed_grabs_do_not_decrement_binary_stock(self):
        for i in range(12):
            task = self.grab(request=f"request-{i:04}")
            self.device.result = "completed"
            self.store.tick()
            self.assertEqual(self.store.history["tasks"][task["task_id"]]["status"], "completed")
            self.assertTrue(self.store.snapshot()["ready"])
        self.assertEqual(len(self.device.starts), 12)
        self.assertEqual(Store(self.history_path, self.device).task_history()["total"], 12)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.history_path])
        self.assertEqual(self.store.snapshot()["items"]["water"]["available"], 1)

    def test_duplicate_survives_completion_and_restart(self):
        task = self.grab()
        self.assertEqual(self.grab()["task_id"], task["task_id"])
        self.device.result = "completed"
        self.store.tick()
        self.store = Store(self.history_path, self.device)
        self.assertEqual(self.grab()["status"], "completed")
        self.assertEqual(len(self.device.starts), 1)
        with self.assertRaises(APIError):
            self.grab("cola")

    def test_concurrent_clients_dispatch_only_one(self):
        def attempt(i):
            try:
                return self.grab(request=f"request-{i:04}")
            except APIError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.device.starts), 1)

    def test_stop_and_completion_race(self):
        task = self.grab()
        self.assertEqual(self.store.stop({"task_id": task["task_id"]})["status"], "stopped")
        task = self.grab(request="request-0002")
        self.device.result = "completed"
        self.assertEqual(self.store.stop({"task_id": task["task_id"]})["status"], "completed")
        self.assertIsNone(self.store.active())

    def test_invalid_inputs_and_not_ready(self):
        for body in ({"item_id": "unknown", "request_id": "request-0001"},
                     {"item_id": "water", "request_id": "x"},
                     {"item_id": "water", "action": "grab_snack", "request_id": "request-0001"}):
            with self.assertRaises(APIError):
                self.store.grab(body)
        with patch.object(self.device, "ready", return_value=False):
            with self.assertRaises(APIError):
                self.grab()
        self.assertEqual(self.device.starts, [])

    def test_demo_restart_closes_task_without_dispatch(self):
        self.grab()
        restarted = Store(self.history_path, ControlledDevice())
        self.assertIsNone(restarted.active())
        self.assertEqual(restarted.task_history()["tasks"][0]["status"], "stopped")

    def test_real_restart_polls_existing_task_without_dispatch(self):
        self.device.mode = "real"
        task = self.grab()
        device = ControlledDevice("real")
        restarted = Store(self.history_path, device)
        self.assertEqual(restarted.active()["task_id"], task["task_id"])
        self.assertFalse(restarted.snapshot()["ready"])
        device.result = "completed"
        restarted.tick()
        self.assertIsNone(restarted.active())
        self.assertEqual(device.starts, [])

    def test_mode_switch_does_not_simulate_real_completion(self):
        self.device.mode = "real"
        self.grab()
        restarted = Store(self.history_path, ControlledDevice("demo"))
        restarted.tick()
        self.assertIsNotNone(restarted.active())
        self.assertFalse(restarted.snapshot()["ready"])

    def test_mode_switch_cannot_stop_real_task_through_demo_device(self):
        self.device.mode = "real"
        task = self.grab()
        restarted = Store(self.history_path, ControlledDevice("demo"))
        with self.assertRaises(APIError):
            restarted.stop({"task_id": task["task_id"]})
        self.assertIsNotNone(restarted.active())

    def test_unknown_or_poll_failure_keeps_task_until_confirmed(self):
        task = self.grab()
        for result in ("unrecognized", None):
            self.device.result = result
            self.store.tick()
            self.assertEqual(self.store.active()["status"], "needs_attention")
        with patch.object(self.device, "poll", side_effect=TimeoutError):
            self.store.tick()
        with self.assertRaises(APIError):
            self.grab(request="request-0002")
        self.device.result = "completed"
        self.store.tick()
        self.assertEqual(self.store.history["tasks"][task["task_id"]]["status"], "completed")

    def test_old_pending_inventory_migrates_once_without_dispatch(self):
        task = self.grab()
        old = copy.deepcopy(self.store.history)
        old["tasks"][task["task_id"]].update(status="inventory_pending", inventory_revision_before=5,
                                            quantity_before=1, inventory_applied=False)
        self.history_path.write_text(json.dumps(old))
        device = ControlledDevice()
        migrated = Store(self.history_path, device)
        self.assertEqual(migrated.task_history()["tasks"][0]["status"], "completed")
        self.assertNotIn("quantity_before", migrated.task_history()["tasks"][0])
        self.assertIsNone(migrated.active())
        saved = self.history_path.read_bytes()
        Store(self.history_path, device)
        self.assertEqual(self.history_path.read_bytes(), saved)
        self.assertEqual(device.starts, [])

    def test_storage_failure_prevents_dispatch(self):
        with patch("server.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.grab()
        self.assertEqual(self.device.starts, [])
        self.assertFalse(self.store.snapshot()["ready"])

    def test_launcher_preserves_history_without_inventory(self):
        before = self.history_path.read_bytes()
        self.assertEqual(prepare_frontend_data({"history_file": str(self.history_path)}), self.history_path)
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_http_status_grab_history_and_removed_routes(self):
        allowed = set()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store, allowed))
        allowed.add(f"127.0.0.1:{server.server_port}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(close)
        base = f"http://127.0.0.1:{server.server_port}"
        def call(path, body=None, headers=None):
            req = Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers or ({"Content-Type":"application/json"} if body is not None else {}))
            with urlopen(req, timeout=2) as response:
                return json.load(response)
        self.assertTrue(call("/api/health")["ready"])
        task = call("/api/grab", {"item_id":"water", "request_id":"http-0001"})
        self.assertEqual(call("/api/requests/http-0001")["task_id"], task["task_id"])
        self.assertEqual(call("/api/tasks/"+task["task_id"])["status"], "queued")
        self.assertEqual(call("/api/task-history?limit=1")["total"], 1)
        self.assertFalse(call("/api/health")["ready"])
        self.assertEqual(call("/api/stop", {"request_id":"http-0001"})["status"], "stopped")
        for path, body in [("/api/inventory",None), ("/api/inventory/restock",{}),
                           ("/data/task_history.json",None), ("/server.py",None)]:
            with self.assertRaises(HTTPError) as error:
                call(path,body)
            self.assertEqual(error.exception.code,404)
        with self.assertRaises(HTTPError) as error:
            call("/api/grab", {}, {"Content-Type":"application/json", "Origin":"https://untrusted.example"})
        self.assertEqual(error.exception.code,403)


if __name__ == "__main__":
    unittest.main()
