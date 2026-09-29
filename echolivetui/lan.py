"""Interface discovery and presentation for a hosted editor; no client scanning."""
from dataclasses import dataclass
import ipaddress
import socket
import psutil
import qrcode


@dataclass(frozen=True)
class Interface:
    name: str
    address: str
    netmask: str


def valid_lan_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return ip.version == 4 and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved)
    except ValueError:
        return False


def interfaces():
    stats = psutil.net_if_stats()
    result = []
    seen = set()
    for name, addresses in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for address in addresses:
            if address.family == socket.AF_INET and valid_lan_ip(address.address) and address.address not in seen:
                result.append(Interface(name, address.address, address.netmask or ""))
                seen.add(address.address)
    return result


def choose_address(requested=""):
    available = interfaces()
    if requested:
        if requested not in {item.address for item in available}:
            raise ValueError("所选地址不属于当前启用的本机网卡，请刷新列表或更换地址")
        return requested
    if not available:
        raise ValueError("没有可用的局域网 IPv4 网卡")
    return available[0].address


def editor_url(address, port):
    return f"http://{address}:{port}/editor.html"


def qr_matrix(url):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(url)
    code.make(fit=True)
    return code.get_matrix()


def terminal_qr(url):
    matrix = qr_matrix(url)
    width = len(matrix[0])
    rows = []
    for y in range(0, len(matrix), 2):
        top = matrix[y]
        bottom = matrix[y + 1] if y + 1 < len(matrix) else [False] * width
        rows.append("".join("█" if a and b else "▀" if a else "▄" if b else " " for a, b in zip(top, bottom)))
    return "\n".join(rows)
