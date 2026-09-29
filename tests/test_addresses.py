from echolivetui.addresses import hosted_notice, first_ipv4, live_url


def test_hosted_notice_has_real_port_and_named_endpoint():
    text = hosted_notice("0.0.0.0", 4321, "192.168.1.2")
    assert "http://127.0.0.1:4321/live.html?name=main" in text
    assert "http://192.168.1.2:4321/live.html?name=main" in text
    assert "editor" not in text and "未监听" not in text
    local = hosted_notice("127.0.0.1", 4321, "192.168.1.2")
    assert "192.168.1.2" not in local and "候选" not in local
    assert live_url("::1", 4321) == "http://[::1]:4321/live.html?name=main"


def test_ip_candidate_rejects_loopback_and_apipa(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 1, 6, "", (ip, 0)) for ip in ("127.0.0.1", "169.254.1.1", "0.0.0.0", "192.168.5.4", "10.0.0.2")])
    assert first_ipv4() == "192.168.5.4"


async def test_startup_notice_only_when_hosting_started(tmp_path, monkeypatch):
    from echolivetui.core import Core
    from echolivetui.config import Settings
    reports = []
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path, reports.append)
    async def started():
        core.server.site = True
        core.server.address = ("0.0.0.0", 8765)
    monkeypatch.setattr(core.server, "start", started)
    monkeypatch.setattr("echolivetui.core.first_ipv4", lambda: "192.168.2.3")
    await core.start()
    assert reports == []
    core.server.hosting.root = tmp_path
    await core.start()
    assert len(reports) == 1 and reports[0].startswith("EchoLiveTUI")
    assert "127.0.0.1:8765/live.html?name=main" in reports[0]
    assert "192.168.2.3:8765/live.html?name=main" in reports[0]
