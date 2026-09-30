import asyncio
import pytest
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hub import Peer
from echolivetui.protocol import Profile
from echolivetui.commands import parse, execute


class Socket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, data):
        if self.fail:
            raise ConnectionError("test write failure")
        self.sent.append(data)

    async def close(self):
        pass


def core_with_peer(tmp_path, role="history", fail=False):
    reports = []
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path, reports.append)
    peer = Peer(Socket(fail), Profile("one", "one", role))
    core.hub.peers["one"] = peer
    return core, peer, reports


async def test_history_only_accepts_without_live_or_osc(tmp_path):
    core, peer, reports = core_with_peer(tmp_path)
    core.settings.values["message.typewriting"] = False
    core.paren_once = True
    core.submit("history-only")
    frame, _ = peer.control.get_nowait()
    assert frame["action"] == "echo_printing"
    assert "history-only" in frame["data"]["message"]
    assert not core.paren_once
    assert not any("已接受" in message for message in reports)


async def test_hide_latest_is_hub_owned_and_flushable(tmp_path):
    core, peer, _ = core_with_peer(tmp_path)
    core.settings.values.update({"history.hide_latest": True, "message.typewriting": False})
    core.submit("first")
    assert peer.control.empty()
    core.submit("second")
    frame, _ = peer.control.get_nowait()
    assert "first" in frame["data"]["message"]
    await core.apply({"history.hide_latest": False})
    frame, _ = peer.control.get_nowait()
    assert "second" in frame["data"]["message"]
    assert not core.hub.history.pending


async def test_default_info_reports_connections_and_warn_failures(tmp_path):
    core, peer, reports = core_with_peer(tmp_path, fail=True)
    core.hub.log("connected", "info")
    core.hub.log("packet", "debug")
    assert reports == ["connected"]
    reports.clear()
    peer.enqueue({"action": "echo_printing"}, fast=True)
    await peer.write_loop(core.hub.log)
    assert len(reports) == 1 and "写入失败" in reports[0]
    await core.apply({"log.level": "info"})
    core.hub.log("connected", "info")
    assert reports[-1] == "connected"


async def test_all_includes_targeted_and_set_clears_exclusions(tmp_path):
    core, peer, _ = core_with_peer(tmp_path, role="live")
    peer.profile.targeted = True
    assert core.hub.targets() == [peer]
    await execute(core, parse("/target exclude one"))
    assert not core.hub.targets()
    await execute(core, parse("/target set one"))
    assert core.hub.targets() == [peer]
    await execute(core, parse("/target all"))
    assert core.settings.routing["targets"] == []


def test_full_history_queue_preserves_once_and_message(tmp_path):
    core, peer, _ = core_with_peer(tmp_path)
    core.paren_once = True
    for _ in range(256):
        peer.enqueue({"action": "history_clear"}, fast=True)
    with pytest.raises(ValueError, match="历史端队列已满"):
        core.submit("keep")
    assert core.paren_once


async def test_server_message_records_tui_and_history_once_without_live(tmp_path):
    core, history, reports = core_with_peer(tmp_path)
    try:
        await core.hub.receive(Socket(), {"action": "message_data", "from": {"uuid": "editor", "type": "server", "name": "Editor"}, "target": "@__live", "data": {"username": "Remote", "messages": [{"message": "Hello"}]}})
        assert reports.count("Remote：Hello") == 1
        frame, _ = history.control.get_nowait()
        assert frame["action"] == "echo_printing"
        assert frame["data"] == {"username": "Remote", "message": "Hello"}
        await core.hub.receive(Socket(), {"action": "echo_printing", "from": {"uuid": "live", "type": "live"}, "data": {"username": "Remote", "message": "Hello"}})
        assert history.control.empty()
        assert reports.count("Remote：Hello") == 1
    finally:
        await core.close()


@pytest.mark.parametrize("role", ["live", "history", "character", "client", "server"])
async def test_heartbeat_retargets_all_servers_only(tmp_path, role):
    core, history, reports = core_with_peer(tmp_path)
    observers = [Peer(Socket(), Profile(uid, uid, "server", targeted=True)) for uid in ("observer1", "observer2")]
    for observer in observers:
        core.hub.peers[observer.profile.uuid] = observer
    packet = {"action": "websocket_heartbeat", "target": "@__ws_server", "from": {"uuid": "source", "type": role, "timestamp": 123}, "data": {"state": "ready"}}
    try:
        await core.hub.receive(Socket(), packet)
        for observer in observers:
            received, _ = observer.control.get_nowait()
            assert received == {**packet, "target": observer.profile.uuid}
        assert history.control.empty()
        assert len(reports) == 1 and "连接：" in reports[0]
        assert packet["target"] == "@__ws_server"
    finally:
        await core.close()
