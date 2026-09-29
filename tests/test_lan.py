import asyncio
from pathlib import Path
import signal
import socket
import pytest
from aiohttp import ClientSession
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.lan import Interface, interfaces, choose_address, valid_lan_ip, terminal_qr, editor_url
from echolivetui.signals import InterruptPolicy


def test_interface_selection_and_validation(monkeypatch):
    monkeypatch.setattr("echolivetui.lan.interfaces", lambda: [Interface("Ethernet", "192.168.1.3", "255.255.255.0"), Interface("VPN", "10.0.0.4", "255.0.0.0")])
    assert choose_address() == "192.168.1.3"
    assert choose_address("10.0.0.4") == "10.0.0.4"
    with pytest.raises(ValueError):
        choose_address("192.168.1.8")
    for ip in ("0.0.0.0", "127.0.0.1", "::1", "169.254.1.1", "224.1.1.1", "bad"):
        assert not valid_lan_ip(ip)


def test_qr_is_complete_rectangular_with_quiet_zone():
    lines = terminal_qr(editor_url("192.168.1.3", 3210)).splitlines()
    assert len({len(line) for line in lines}) == 1
    assert set(lines[0]) == {" "}
    assert all(line[:4] == "    " and line[-4:] == "    " for line in lines)


def test_signal_policy_is_dynamic_and_does_not_ignore_sigterm(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    exits = []
    policy = InterruptPolicy(settings, lambda: exits.append(True))
    original = signal.getsignal(signal.SIGINT)
    try:
        policy.install()
        signal.raise_signal(signal.SIGINT)
        assert exits == []
        settings.values["input.interrupt_guard"] = False
        signal.raise_signal(signal.SIGINT)
        assert exits == [True]
        settings.values["input.interrupt_guard"] = True
        policy.handle(signal.SIGTERM)
        assert len(exits) == 2
    finally:
        policy.restore()
    assert signal.getsignal(signal.SIGINT) == original


def make_host(root):
    from echolivetui.hosting import REQUIRED
    for name in (*REQUIRED, "editor.html", "res/script/editor.js"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("test", encoding="utf-8")


async def test_lan_lifecycle_closes_remote_editor_keeps_local_live(tmp_path):
    available = interfaces()
    if not available:
        pytest.skip("No active non-loopback IPv4 interface")
    host = available[0].address
    make_host(tmp_path)
    settings = Settings(tmp_path / "s.yaml")
    core = Core(settings, tmp_path)
    await core.server.start("127.0.0.1", 0)
    port = core.server.address[1]
    settings.values["listen.port"] = port
    try:
        async with ClientSession() as client:
            local = await client.ws_connect(f"http://127.0.0.1:{port}/ws")
            await local.receive_json()
            await local.send_json({"action": "hello", "from": {"uuid": "live", "type": "live"}, "data": {}})
            await core.apply({"lan.enable": True, "lan.host": host})
            assert core.server.lan_address == (host, port)
            async with client.get(editor_url(host, port)) as response:
                assert response.status == 200
            remote = await client.ws_connect(f"http://{host}:{port}/ws")
            await remote.receive_json()
            await remote.send_json({"action": "ping", "from": {"uuid": "editor", "type": "server"}, "data": {}})
            await asyncio.sleep(.03)
            await core.apply({"lan.enable": False})
            assert core.server.lan_address is None
            async with client.get(f"http://127.0.0.1:{port}/healthz") as response:
                assert response.status == 200
            assert not local.closed
            await asyncio.wait_for(remote.receive(), 3)
            assert remote.closed
            await local.close()
    finally:
        await core.close()


async def test_enabling_without_editor_does_not_save(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    with pytest.raises(ValueError):
        await core.apply({"lan.enable": True})
    assert not core.settings["lan.enable"]
    assert not core.settings.path.exists()


def test_unknown_client_assumes_typing_but_known_old_does_not():
    from echolivetui.protocol import Profile
    unknown = Profile("x", role="live")
    assert unknown.accepts("editor_typing")
    unknown.identify({"action": "hello", "from": {"type": "live"}}, "1.6.6", "live")
    assert not unknown.accepts("editor_typing")
