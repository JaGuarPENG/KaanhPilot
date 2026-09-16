"""Read Mac LAN addresses for the iPad preview; no external network request."""
import ipaddress
import os
import re
import subprocess


def lan_address(value):
    address = ipaddress.IPv4Address(value)
    if (address.is_loopback or address.is_link_local or address.is_unspecified
            or address.is_multicast or address.is_reserved):
        raise ValueError('请填写 Mac 的局域网 IPv4 地址，不要填写 localhost 或端口')
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


def detect_lan_addresses():
    override = os.environ.get('KAANH_IPAD_IP', '').strip()
    if override:
        return [lan_address(override)]
    try:
        result = subprocess.run(['/sbin/ifconfig'], capture_output=True, text=True,
                                check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('无法读取 Mac 网络地址；可通过 KAANH_IPAD_IP 手动指定局域网 IPv4 地址') from error
    addresses = parse_lan_addresses(result.stdout)
    if not addresses:
        raise RuntimeError('未找到局域网地址，请连接与 iPad 相同的 Wi-Fi 后重试')
    return addresses
