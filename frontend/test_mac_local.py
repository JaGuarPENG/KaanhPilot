"""Exercise both Mac launch scripts in temporary folders, without hardware."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.name == 'posix', 'Mac shell integration')
class MacLocalTests(unittest.TestCase):
    def test_frontend_to_backend_using_mac_scripts(self):
        self.exercise_scripts(ipad=False)

    def test_ipad_host_to_backend_using_mac_scripts(self):
        self.exercise_scripts(ipad=True)

    def exercise_scripts(self, ipad):
        with tempfile.TemporaryDirectory(prefix='kaanh mac test ') as folder:
            root = Path(folder)
            backend_port, frontend_port = free_port(), free_port()
            while frontend_port == backend_port:
                frontend_port = free_port()
            processes = []
            logs = []
            try:
                for directory, script, files in [
                    ('KaanhOrbit','Start-Backend.command',['launcher.py','launcher_mac_config.json','agent_api.py']),
                    ('frontend','Start-Frontend-iPad.command' if ipad else 'Start-Frontend-Local.command',
                     ['launcher.py','launcher_mac_config.json','server.py','device_adapter.py','ipad_network.py'])]:
                    dest = root/directory
                    dest.mkdir()
                    for name in [script, *files]:
                        shutil.copy2(ROOT/directory/name, dest/name)
                    cfg_path = dest/'launcher_mac_config.json'
                    cfg = json.loads(cfg_path.read_text())
                    cfg['port'] = backend_port if directory == 'KaanhOrbit' else frontend_port
                    cfg['open_browser'] = False
                    if directory == 'frontend':
                        cfg['backend_url'] = f'http://127.0.0.1:{backend_port}'
                    cfg_path.write_text(json.dumps(cfg))
                    log = (dest/'test.log').open('w+')
                    logs.append(log)
                    processes.append(subprocess.Popen(['/bin/bash',str(dest/script)],cwd='/tmp',
                                     env={**os.environ, 'KAANH_IPAD_TEST':'0', 'KAANH_IPAD_IP':'192.168.1.42'},
                                     stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True))
                base = f'http://127.0.0.1:{frontend_port}'
                def call(path, body=None):
                    headers = {} if body is None else {'Content-Type':'application/json'}
                    if ipad:
                        headers.update({'Host':f'192.168.1.42:{frontend_port}',
                                        'Origin':f'http://192.168.1.42:{frontend_port}'})
                    request = Request(base+path,data=None if body is None else json.dumps(body).encode(),
                                      headers=headers)
                    with urlopen(request,timeout=2) as response:
                        return json.load(response)
                deadline = time.monotonic()+12
                while True:
                    try:
                        if call('/api/health')['ready']:
                            break
                    except OSError:
                        pass
                    if time.monotonic()>deadline:
                        self.fail('Mac services did not become ready')
                    time.sleep(.1)
                self.assertTrue(call('/api/health')['dry_run'])
                stock = call('/api/test/inventory', {'item_id':'water','available':0})
                self.assertEqual(stock['items']['water']['available'],0)
                with self.assertRaises(HTTPError) as caught:
                    call('/api/grab',{'item_id':'water','request_id':'stock-known-empty'})
                self.assertEqual(json.load(caught.exception)['error_code'],'out_of_stock')
                call('/api/test/inventory',{'item_id':'water','available':1,'confirmation_available':0})
                task = call('/api/grab',{'item_id':'water','request_id':'stock-found-empty'})
                deadline = time.monotonic()+8
                while task['status'] not in ('completed','failed') and time.monotonic()<deadline:
                    time.sleep(.1)
                    task = call('/api/tasks/'+task['task_id'])
                self.assertEqual(task['status'],'failed',task)
                self.assertEqual(task['error_code'],'out_of_stock')
                self.assertEqual(call('/api/health')['items']['water']['available'],0)
                duplicate = call('/api/grab',{'item_id':'water','request_id':'stock-found-empty'})
                self.assertEqual(duplicate['task_id'],task['task_id'])
                call('/api/test/inventory',{'item_id':'water','available':1})
                for i in range(2):
                    task = call('/api/grab',{'item_id':'oolong_tea','request_id':f'mac-test-{i:04}'})
                    deadline = time.monotonic()+8
                    while task['status'] not in ('completed','failed') and time.monotonic()<deadline:
                        time.sleep(.1)
                        task = call('/api/tasks/'+task['task_id'])
                    self.assertEqual(task['status'],'completed',task)
                history = json.loads((root/'KaanhOrbit/data/mac_test_robot_tasks.json').read_text())
                self.assertEqual(len(history),3)
                self.assertTrue(all(t['simulation'] for t in history.values()))
                self.assertEqual(sum(t['status']=='completed' for t in history.values()),2)
                self.assertEqual(call('/api/task-history')['total'],3)
            finally:
                for proc in processes:
                    if proc.poll() is None:
                        os.killpg(proc.pid,signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL)
                        proc.wait()
                for log in logs:
                    log.seek(0)
                    print(log.read())
                    log.close()


if __name__ == '__main__':
    unittest.main()
