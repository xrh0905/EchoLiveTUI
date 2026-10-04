import asyncio
import pytest
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hub import Peer
from echolivetui.protocol import Profile


def make_core(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    core.settings.values["message.typewriting"] = False
    for uid in ("a", "b"):
        profile = Profile(uid, uid, "live")
        profile.capabilities["typing"] = True
        core.hub.peers[uid] = Peer(None, profile)
    return core


def test_osc_once_per_submission_not_per_live(tmp_path, monkeypatch):
    calls = []
    class OSC:
        def __init__(self, host, port):
            pass
        def send_message(self, address, args):
            calls.append((address, args))
    monkeypatch.setattr("echolivetui.core.SimpleUDPClient", OSC)
    core = make_core(tmp_path)
    core.settings.values["osc.enable"] = True
    core.submit("**text**")
    assert len(calls) == 1
    assert calls[0][0] == "/chatbox/input"
    assert calls[0][1] == ['「text」', True]
    assert all(peer.queue.qsize() == 1 for peer in core.hub.peers.values())


async def test_typing_first_edit_and_pending_cancellation(tmp_path):
    core = make_core(tmp_path)
    core.settings.values["typing.enable"] = True
    core.input_changed("text")
    await asyncio.sleep(.02)
    peer = core.hub.peers["a"]
    envelope, _ = peer.control.get_nowait()
    assert envelope["action"] == "editor_typing"
    core.input_changed("text2")
    core.input_changed("/settings")
    await asyncio.sleep(.02)
    assert peer.control.empty()
    assert core.typing_task is None
    core.submit("text2")
    message, _ = peer.queue.get_nowait()
    assert message["from"]["uuid"] == envelope["from"]["uuid"]
