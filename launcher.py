"""双击入口的 Python 启动程序。只组装配置与启动原服务，不改机器人动作逻辑。"""
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import socket
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
ROLE = 'backend'


def load_config():
    config = json.loads((ROOT / 'launcher_config.json').read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('launcher_config.json 必须是 JSON 对象')
    return config


def local_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def lock_launcher():
    # 操作系统锁会在进程退出时释放，避免重复双击启动第二份服务。
    handle = open(ROOT / 'launcher.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('本目录的服务已经启动，请使用已有窗口。') from None
    return handle


def check_port(host, port):
    # 启动真实机器人前先检查 HTTP 端口，减少重复启动造成的重复连接。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Unix 上允许刚退出服务留下的 TIME_WAIT，行为与 HTTPServer 一致。
        if os.name != 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            raise RuntimeError(f'端口 {port} 无法监听，可能已有服务运行；请先关闭旧窗口或修改端口。') from None


def prepare_frontend_data(config):
    inventory = local_path(config['inventory_file'])
    history = local_path(config['history_file'])
    if inventory.resolve() == history.resolve():
        raise ValueError('库存文件与任务历史必须使用不同路径')
    # 已有数据保持原样：启动程序不会补库存，也不会清空旧任务。
    if inventory.exists() != history.exists():
        raise RuntimeError('库存和历史文件只有一个存在，请恢复配套文件，避免丢失任务记录。')
    if not inventory.exists():
        inventory.parent.mkdir(parents=True, exist_ok=True)
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'data/inventory.json', inventory)
        history.write_text(json.dumps({'schema_version':1,'revision':0,'active_task_id':None,'tasks':{}},indent=2),encoding='utf-8')
        print('已创建独立演示库存和空任务历史；以后启动会沿用它们。',flush=True)
    return inventory, history


def open_when_ready(port, done):
    url = f'http://127.0.0.1:{port}'
    for _ in range(100):
        if done.is_set():
            return
        try:
            # 仅确认本地页面服务已启动；后端未就绪时仍可打开网页查看状态。
            with urlopen(url+'/api/health',timeout=1) as response:
                data = json.load(response)
            if 'items' in data:
                if not done.is_set():
                    webbrowser.open(url)
                return
        except Exception:
            pass
        done.wait(.2)
    print('网页未自动打开，请在浏览器访问 '+url,flush=True)


def main():
    config = load_config()
    configured_python = config.get('python_executable','').strip()
    if configured_python:
        executable = local_path(configured_python)
        if not executable.is_file():
            raise ValueError('python_executable 指向的 Python 不存在，请检查 launcher_config.json')
        if executable.resolve() != Path(sys.executable).resolve():
            # 直接传递参数数组，不通过 shell 拼接执行，支持路径中的空格。
            os.execv(str(executable),[str(executable),'-B','-u',str(Path(__file__).resolve())])
    if sys.version_info < (3,10):
        raise RuntimeError('需要 Python 3.10 或更高版本；可在配置文件指定 python_executable。')
    token = config.get('api_token','')
    if not isinstance(token,str) or not token or not token.isascii() or '\r' in token or '\n' in token:
        raise ValueError('api_token 请使用非空英文、数字或 ASCII 符号，且两端完全一致。')
    port = config.get('port')
    if isinstance(port,bool) or not isinstance(port,int) or not 1 <= port <= 65535:
        raise ValueError('port 必须是 1～65535 的整数')
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    lock = lock_launcher()
    done = threading.Event()
    try:
        if ROLE == 'frontend':
            from urllib.parse import urlsplit
            backend_url = config.get('backend_url','').rstrip('/')
            parsed = urlsplit(backend_url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('backend_url 请填写机器人电脑地址，例如 http://192.168.1.50:8088')
            if not isinstance(config.get('open_browser',True),bool):
                raise ValueError('open_browser 必须为 true 或 false')
            check_port('127.0.0.1',port)
            inventory,history = prepare_frontend_data(config)
            os.environ['KAANH_API_URL'] = backend_url
            os.environ['KAANH_API_TOKEN'] = token
            script = ROOT / 'server.py'
            args = ['--mode','real','--host','127.0.0.1','--port',str(port),'--data',str(inventory),'--tasks-data',str(history)]
            print(f'前端启动：连接后端 {backend_url}\n页面地址：http://127.0.0.1:{port}',flush=True)
            if config.get('open_browser',True):
                threading.Thread(target=open_when_ready,args=(port,done),daemon=True).start()
        else:
            dry_run = config.get('dry_run')
            if not isinstance(dry_run,bool):
                raise ValueError('dry_run 必须为 true 或 false')
            host = config.get('host','0.0.0.0')
            check_port(host,port)
            os.environ['ROBOT_API_TOKEN'] = token
            script = ROOT / 'agent_api.py'
            task_file = local_path(config['simulation_tasks_file' if dry_run else 'robot_tasks_file'])
            args = ['--host',host,'--port',str(port),'--tasks-data',str(task_file)]
            if dry_run:
                args.append('--dry-run')
            print('后端模式：'+('无硬件测试（不连接机器人）' if dry_run else '真实机器人（启动时登录、使能并设置速度）'),flush=True)
            print(f'HTTP 端口：{port}；模式保存在 launcher_config.json 的 dry_run 字段。',flush=True)
        print('保持此窗口运行。Ctrl+C 退出服务；退出服务不代表停止机械臂。',flush=True)
        sys.argv = [str(script),*args]
        # 在当前进程运行原服务，Ctrl+C 交给原服务的资源清理逻辑处理。
        runpy.run_path(str(script),run_name='__main__')
    finally:
        done.set()
        lock.close()


if __name__ == '__main__':
    # 关闭终端/进程时尽量让原服务执行 finally 清理连接。
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        main()
    except KeyboardInterrupt:
        print('\n服务已退出。',flush=True)
    except Exception as error:
        print('\n启动失败：'+str(error),flush=True)
        print('请检查 launcher_config.json、Python 环境和端口。配置文件可用文本编辑器打开。',flush=True)
        sys.exit(1)
