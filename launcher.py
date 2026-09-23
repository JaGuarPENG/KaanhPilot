"""统一网页与订单 API 启动入口，沿用现有本机配置。"""
import json
import ipaddress
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/launcher"

# ROOT = Path(__file__).resolve().parent


def load_config():
    config = json.loads((DEFAULT_CONFIG_PATH / 'launcher_config.json').read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('launcher_config.json 必须是 JSON 对象')
    return config


def local_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else DEFAULT_CONFIG_PATH / path


def lock_launcher():
    # 操作系统锁会在进程退出时释放，避免重复双击启动第二份服务。
    handle = open(DEFAULT_CONFIG_PATH / 'launcher.lock', 'a+b')
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


def print_access_addresses(host, port):
    """只打印当前监听范围内的候选地址；网络检测失败不影响设备服务。"""
    from frontend.ipad_network import detect_lan_addresses
    local_host = '127.0.0.1' if host in ('', '0.0.0.0') else host
    print(f'本机网页: http://{local_host}:{port}', flush=True)
    try:
        loopback = host.lower() == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == 'localhost'
    if loopback:
        print('当前仅允许本机访问；iPad 访问需在 launcher_config.json 中设置 host 为 0.0.0.0。', flush=True)
        return
    try:
        addresses = detect_lan_addresses() if host in ('', '0.0.0.0') else [host]
    except (RuntimeError, ValueError) as error:
        print(f'未能检测 iPad 访问地址：{error}。服务继续启动，可通过 ipconfig 查看本机 IPv4。', flush=True)
        return
    for address in addresses:
        print(f'iPad 访问地址: http://{address}:{port}', flush=True)
    print('请等待设备初始化完成，并让 iPad 与电脑连接同一局域网；多地址时选择对应网络的地址。', flush=True)


def open_when_ready(port, done):
    url = f'http://127.0.0.1:{port}'
    for _ in range(100):
        if done.is_set():
            return
        try:
            # 仅确认本地页面服务已启动；后端未就绪时仍可打开网页查看状态。
            with urlopen(url+'/', timeout=1) as response:
                ready = response.status == 200
            if ready:
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
    dry_run = config.get('dry_run', True)
    if not isinstance(dry_run, bool):
        raise ValueError('dry_run must be true or false')
    host, port = config.get('host', '127.0.0.1'), config.get('port', 8088)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('port must be an integer from 1 to 65535')
    lock = lock_launcher()
    done = threading.Event()
    try:
        check_port(host, port)
        from frontend.server import serve
        print_access_addresses(host, port)
        print('Mode: ' + ('simulation' if dry_run else 'real hardware'), flush=True)
        if config.get('open_browser', True):
            threading.Thread(target=open_when_ready, args=(port, done), daemon=True).start()
        serve(config, PROJECT_ROOT / 'config')
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
