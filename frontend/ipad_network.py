"""读取 Windows、macOS、Linux 的局域网 IPv4 地址"""
import ipaddress
import os
import json
import re
import subprocess
import sys


def lan_address(value):
    address = ipaddress.IPv4Address(value)
    if (address.is_loopback or address.is_link_local or address.is_unspecified
            or address.is_multicast or address.is_reserved):
        raise ValueError('请填写前端电脑的局域网 IPv4 地址，不要填写 localhost 或端口')
    return str(address)


def parse_lan_addresses(output):
    addresses = []
    for block in re.split(r'(?=^\S+: flags=)', output, flags=re.M):
        name = block.split(':', 1)[0]
        if name.startswith(('lo', 'utun', 'tun', 'tap', 'awdl', 'llw')):
            continue
        if 'status: inactive' in block:
            continue
        for value in re.findall(r'^\s+inet\s+(\S+)', block, flags=re.M):
            try:
                address = lan_address(value)
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
    return addresses


def detect_lan_addresses(override=''):
    if not isinstance(override, str):
        raise ValueError('lan_ip 必须是 IPv4 地址字符串，留空则自动检测')
    override = override.strip() or os.environ.get('KAANH_IPAD_IP', '').strip()
    if override:
        return [lan_address(override)]
    try:
        command = ['ipconfig'] if sys.platform == 'win32' else (
            ['/sbin/ifconfig'] if sys.platform == 'darwin' else ['ip', '-j', '-4', 'addr', 'show', 'up'])
        result = subprocess.run(command, capture_output=True, text=True,
                                errors='replace', check=True, timeout=5)
        if sys.platform == 'win32':
            candidates = re.findall(r'IPv4[^:\r\n]*:\s*(\d+\.\d+\.\d+\.\d+)', result.stdout)
        elif sys.platform == 'darwin':
            candidates = parse_lan_addresses(result.stdout)
        else:
            candidates = [entry['local'] for interface in json.loads(result.stdout)
                          for entry in interface.get('addr_info', []) if entry.get('family') == 'inet']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('无法读取局域网地址；请在 launcher_config.json 的 lan_ip 中填写前端电脑的 IPv4 地址') from error
    addresses = []
    for value in candidates:
        try:
            address = lan_address(value)
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise RuntimeError('未找到局域网地址，请连接与 iPad 相同的 Wi-Fi/局域网后重试，或设置 lan_ip')
    return addresses
