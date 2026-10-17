import asyncio
import pytest
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hub import Peer
from echolivetui.protocol import Profile


class FakeOSC:
    calls = []
    fail = False

    def __init__(self, host, port):
        self.host, self.port = host, port

    def send_message(self, address, value):
        if FakeOSC.fail:
            raise OSError("unreachable")
        FakeOSC.calls.append((self.host, self.port, address, value))


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, envelope):
        self.sent.append(envelope)

    async def close(self):
        pass


@pytest.fixture
def osc(monkeypatch):
    FakeOSC.calls, FakeOSC.fail = [], False
    monkeypatch.setattr("echolivetui.osc.SimpleUDPClient", FakeOSC)
    return FakeOSC


def make_core(tmp_path, peers=("live1",)):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    core.settings.values["osc.enable"] = True
    core.settings.values["message.typewriting"] = False
    for uid in peers:
        profile = Profile(uid, uid, "live")
        profile.capabilities["typing"] = True
        core.hub.peers[uid] = Peer(None, profile)
    return core


async def editor_message(core, data, *, remote=True, ip="192.168.1.20", uid="editor"):
    envelope = {"action": "message_data", "data": data, "from": {"uuid": uid, "type": "server", "name": "editor"}}
    return await core.hub.receive(FakeWS(), envelope, ip, {"hosted_version": "1.8.12", "page": "editor"}, remote=remote)


def chatbox(calls):
    return [(call[2], call[3]) for call in calls]


async def test_lan_editor_message_reaches_osc_once(tmp_path, osc):
    core = make_core(tmp_path)
    data = {"username": "手机", "messages": [{"message": [{"text": "来自局域网"}]}, {"message": [{"text": "第二行"}]}]}
    peer = await editor_message(core, data)
    assert chatbox(osc.calls) == [("/chatbox/input", ["来自局域网\n第二行", True])]
    assert (osc.calls[0][0], osc.calls[0][1]) == ("127.0.0.1", 9000)
    assert core.hub.peers["live1"].queue.qsize() == 1
    await core.hub.disconnect(peer)


async def test_local_editor_message_keeps_lan_typewriting_out_of_osc(tmp_path, osc):
    core = make_core(tmp_path)
    core.settings.values["lan.typewriting"] = True
    data = {"username": "本机", "messages": [{"message": [{"text": "本机编辑器"}]}]}
    peer = await editor_message(core, data, remote=False, ip="127.0.0.1")
    assert chatbox(osc.calls) == [("/chatbox/input", ["本机编辑器", True])]
    await core.hub.disconnect(peer)


async def test_lan_tracks_keep_styles_and_send_only_main_to_osc_and_history(tmp_path, osc):
    import copy
    core = make_core(tmp_path, peers=("live1", "live2"))
    core.settings.values["history.hide_latest"] = False
    core.hub.peers["history"] = Peer(None, Profile("history", role="history"))
    data = {"username": "手机", "messages": [{"message": "主轨", "track": [
        {"message": [{"text": "子轨", "style": {"super": True}, "speed": 42}],
         "data": {"printSpeed": 60}}]}]}
    before = copy.deepcopy(data)
    peer = await editor_message(core, data)
    assert data == before
    assert chatbox(osc.calls) == [("/chatbox/input", ["主轨", True])]
    for uid in ("live1", "live2"):
        frame, _ = core.hub.peers[uid].queue.get_nowait()
        child = frame["data"]["messages"][0]["track"][0]
        assert child["message"][0]["typewrite"]
        assert child["message"][0]["style"]["super"] and child["message"][0]["speed"] == 42
        assert child["data"] == {"printSpeed": 60}
    history, _ = core.hub.peers["history"].control.get_nowait()
    assert history["data"]["message"] == "主轨"
    assert core.hub.peers["history"].control.empty()
    await core.hub.disconnect(peer)


async def test_osc_disabled_stays_silent(tmp_path, osc):
    core = make_core(tmp_path)
    core.settings.values["osc.enable"] = False
    peer = await editor_message(core, {"username": "手机", "messages": [{"message": "文字"}]})
    core.submit("文字")
    assert osc.calls == []
    assert core.osc_error == ""
    await core.hub.disconnect(peer)


async def test_osc_failure_keeps_delivery_and_reports(tmp_path, osc):
    core = make_core(tmp_path)
    osc.fail = True
    peer = await editor_message(core, {"username": "手机", "messages": [{"message": "文字"}]})
    assert core.hub.peers["live1"].queue.qsize() == 1
    assert core.osc_error == "unreachable"
    await core.hub.disconnect(peer)
    core.hub.peers.clear()
    with pytest.raises(ValueError, match="OSC 发送失败，草稿已保留"):
        core.submit("只有 OSC")


async def test_tui_submit_sends_typing_then_message(tmp_path, osc):
    core = make_core(tmp_path)
    core.input_changed("你好")
    await asyncio.sleep(.02)
    core.input_changed("你好啊")
    await asyncio.sleep(.02)
    assert core.typing_state == "已发 1 端 + OSC"
    core.submit("**text**")
    assert chatbox(osc.calls) == [
        ("/chatbox/typing", True),
        ("/chatbox/input", ["「text」", True]),
        ("/chatbox/typing", False),
    ]
    assert core.typing_state == "待输入"


async def test_typing_hint_expires_without_submit(tmp_path, osc, monkeypatch):
    core = make_core(tmp_path, peers=())
    monkeypatch.setattr("echolivetui.core.TYPING_EXPIRY", .02)
    core.input_changed("你好")
    await asyncio.sleep(.05)
    assert chatbox(osc.calls) == [("/chatbox/typing", True), ("/chatbox/typing", False)]


async def test_typing_hint_without_endpoints_and_when_disabled(tmp_path, osc):
    core = make_core(tmp_path, peers=())
    core.input_changed("你好")
    await asyncio.sleep(.02)
    assert core.typing_state == "已发 OSC"
    core.input_changed("")
    assert chatbox(osc.calls) == [("/chatbox/typing", True), ("/chatbox/typing", False)]
    core.settings.values["typing.enable"] = False
    core.input_changed("你好")
    await asyncio.sleep(.02)
    assert chatbox(osc.calls) == [("/chatbox/typing", True), ("/chatbox/typing", False)]
