import asyncio
import json
from pathlib import Path
import pytest
from aiohttp import ClientSession
from echolivetui.config import Settings
from echolivetui.hub import Hub, Peer
from echolivetui.protocol import Profile
from echolivetui.server import Server


def packet(uid, role="live", action="hello", **data):
    return {"action": action, "data": data, "from": {"uuid": uid, "name": uid, "type": role}}


@pytest.fixture
async def running(tmp_path):
    hub = Hub(Settings(tmp_path / "settings.yaml"))
    server = Server(hub, tmp_path)
    await server.start("127.0.0.1", 0)
    port = server.site._server.sockets[0].getsockname()[1]
    async with ClientSession() as session:
        yield hub, server, session, f"http://127.0.0.1:{port}"
    await server.close()


async def connect(session, url, uid, role="live"):
    ws = await session.ws_connect(url + "/ws")
    assert (await ws.receive_json())["action"] == "ping"
    await ws.send_json(packet(uid, role))
    await asyncio.sleep(.03)
    return ws


async def test_individual_broadcast_and_independent_history(running):
    hub, server, session, url = running
    a = await connect(session, url, "a")
    b = await connect(session, url, "b")
    h = await connect(session, url, "h", "history")
    data = {"username": "Speaker", "messages": [{"message": "same"}]}
    assert hub.broadcast(data, 0) == 2
    assert (await a.receive_json())["target"] == "a"
    assert (await b.receive_json())["target"] == "b"
    await a.send_json(packet("a", action="echo_printing", text="same"))
    await b.send_json(packet("b", action="echo_printing", text="same"))
    history = await h.receive_json()
    assert history["from"]["uuid"] == hub.sender.uuid
    assert history["data"]["message"] == "same"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(h.receive_json(), .06)
    # An intentional repetition is never content-deduplicated.
    hub.broadcast(data, 0)
    assert (await h.receive_json())["data"]["message"] == "same"


async def test_reconnect_generation_and_spoof_rejection(running):
    hub, _, session, url = running
    first = await connect(session, url, "a")
    second = await connect(session, url, "a")
    assert len(hub.peers) == 1
    await second.send_json(packet("imposter"))
    await asyncio.sleep(.03)
    assert list(hub.peers) == ["a"]
    hub.broadcast({}, 0)
    assert (await second.receive_json())["action"] == "message_data"


async def test_host_overlay_and_private_files(tmp_path):
    from echolivetui.hosting import REQUIRED
    for name in REQUIRED:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("const config = {};" if name == "config.js" else "test", encoding="utf-8")
    (tmp_path / "app.js").write_text("const APP_META = {version: '1.8.12'};")
    secret = tmp_path / "echolivetui.yaml"
    secret.write_text("private")
    before = (tmp_path / "config.js").read_bytes()
    hub = Hub(Settings(tmp_path / "other.yaml"))
    server = Server(hub, tmp_path)
    await server.start("127.0.0.1", 0)
    port = server.site._server.sockets[0].getsockname()[1]
    async with ClientSession() as session:
        root = f"http://127.0.0.1:{port}"
        async with session.get(root + "/config.js") as response:
            body = await response.text()
            assert "disable_broadcast = true" in body and "settings.html" in body
        for name in ("echolivetui.yaml", ".git/config", "res/../../echolivetui.yaml"):
            async with session.get(root + "/" + name) as response:
                assert response.status == 404
    assert (tmp_path / "config.js").read_bytes() == before
    assert server.hosting.version == "1.8.12"
    await server.close()


async def test_queue_controls_bypass_playback_delay():
    sent = []
    class Socket:
        async def send_json(self, data):
            sent.append(data["action"])
        async def close(self):
            pass
    p = Peer(Socket(), Profile("x"))
    p.worker = asyncio.create_task(p.write_loop(lambda *args: None))
    p.enqueue({"action": "message_data"}, 10)
    p.enqueue({"action": "message_data"}, 10)
    await asyncio.sleep(.01)
    p.enqueue({"action": "editor_typing"}, fast=True)
    await asyncio.sleep(.01)
    assert sent == ["message_data", "editor_typing"]
    await p.close()
