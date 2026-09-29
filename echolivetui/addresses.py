"""Display candidate URLs without implying that discovery changes the bind address."""
import ipaddress
import socket


def first_ipv4():
    try:
        results = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return None
    for result in results:
        value = result[4][0]
        ip = ipaddress.ip_address(value)
        if not (ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved):
            return value
    return None


def live_url(host, port):
    host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{host}:{port}/live.html?name=main"


def hosted_notice(bind_host, port, candidate):
    loopback_bound = bind_host in {"127.0.0.1", "localhost", "0.0.0.0"}
    lines = ["EchoLiveTUI：已识别 Echo Live，托管已启动。"]
    if loopback_bound:
        lines.append("本机：" + live_url("127.0.0.1", port))
    if candidate and bind_host in {candidate, "0.0.0.0"}:
        lines.append("IPv4：" + live_url(candidate, port))
    if len(lines) == 1:
        lines.append("监听地址：" + live_url("::1" if bind_host == "::" else bind_host, port))
    return "\n".join(lines)
