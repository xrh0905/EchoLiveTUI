import asyncio
import copy
from unittest.mock import Mock

import pytest

from echolivetui.config import Settings
from echolivetui.hub import Hub, Peer
from echolivetui.protocol import Profile
from test_translation import provider, wait_until


class Socket:
    def __init__(self):
        self.sent = asyncio.Queue()
        self.closed = False

    async def send_json(self, frame):
        self.sent.put_nowait(frame)

    async def close(self):
        self.closed = True


def make_hub(tmp_path, service):
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"multitrack.enable": True, "multitrack.target_languages": ["ja", "ru"],
                            "history.hide_latest": False, "lan.typewriting": False})
    hub = Hub(settings)
    hub.translator = service
    hub.peers["live"] = Peer(Socket(), Profile("live", role="live"))
    hub.peers["history"] = Peer(Socket(), Profile("history", role="history"))
    return hub


def frame(messages, *, action="message_data"):
    return {"action": action, "from": {"uuid": "editor", "type": "server", "name": "phone"},
            "target": "live", "data": {"username": "Speaker", "messages": messages}}


async def test_lan_batches_eligible_paragraphs_keeps_wire_fields_and_accepts_once(provider, tmp_path):
    service, state = provider
    hub = make_hub(tmp_path, service)
    hub.settings.values["lan.typewriting"] = True
    published = []
    hub.osc.publish = lambda entries: published.append(list(entries))
    raw = frame([{"message": [{"text": "main", "style": {"super": True}}], "data": {"printSpeed": 42}},
                 {"message": "authored", "track": [{"message": "keep"}]},
                 {"message": [{"text": "", "data": {"image": {"url": "test.png"}}}]},
                 "other"])
    before = copy.deepcopy(raw)
    try:
        await hub.receive(Socket(), raw, remote=True)
        await wait_until(lambda: hub.peers["live"].queue.qsize() == 1)
        sent, _ = hub.peers["live"].queue.get_nowait()
        paragraphs = sent["data"]["messages"]
        assert state.calls[0][0] == ["main", "other"]
        assert paragraphs[0]["data"] == {"printSpeed": 42}
        assert paragraphs[0]["message"][0]["style"]["super"]
        assert paragraphs[0]["track"][0]["message"][0]["text"] == "ja:main"
        assert paragraphs[0]["track"][0]["message"][0]["typewrite"]
        assert paragraphs[0]["track"][0]["data"] == {"printSpeed": 42}
        assert paragraphs[1]["track"][0]["message"][0]["text"] == "keep"
        assert "track" not in paragraphs[2]
        assert paragraphs[3]["track"][1]["message"][0]["text"] == "ru:other"
        assert sent["target"] == raw["target"] and sent["from"] == raw["from"]
        assert raw == before
        assert len(published) == 1 and all("ja:" not in entry.message for entry in published[0])
        history = [hub.peers["history"].control.get_nowait()[0] for _ in range(4)]
        assert all("ja:" not in h["data"]["message"] for h in history)
        assert hub.peers["history"].control.empty()
    finally:
        await hub.close()


@pytest.mark.parametrize("skip", [False, True])
async def test_lan_failure_forwards_entire_original_and_warns_regardless_local_switch(provider, tmp_path, skip):
    service, state = provider
    state.payload = [{"translations": [{"to": "ja", "text": "partial"}]}]
    hub = make_hub(tmp_path, service)
    hub.settings.values["multitrack.skip_errors"] = skip
    hub.report = Mock()
    raw = frame([{"message": "one"}, {"message": "two"}])
    try:
        await hub.receive(Socket(), raw, remote=True)
        await wait_until(lambda: bool(hub.peers["live"].queue.qsize()))
        sent, _ = hub.peers["live"].queue.get_nowait()
        assert sent == raw
        assert any("翻译失败，已转发原消息" in call.args[0] for call in hub.report.call_args_list)
        assert hub.peers["history"].control.qsize() == 2
    finally:
        await hub.close()


async def test_lan_fifo_does_not_block_heartbeats_and_disconnect_cancels(provider, tmp_path):
    service, state = provider
    state.gate = asyncio.Event()
    hub = make_hub(tmp_path, service)
    ws = Socket()
    try:
        peer = await hub.receive(ws, frame([{"message": "one"}]), remote=True)
        await wait_until(lambda: bool(state.calls))
        await hub.receive(ws, frame([{"message": "two"}]), remote=True)
        heartbeat = frame([], action="heartbeat")
        heartbeat["target"] = "@__ws_server"
        await hub.receive(ws, heartbeat, remote=True)
        async with asyncio.timeout(1):
            observed = await ws.sent.get()
        assert observed["action"] == "heartbeat"
        assert hub.peers["live"].queue.empty()
        state.gate.set()
        await wait_until(lambda: hub.peers["live"].queue.qsize() == 2)
        frames = [hub.peers["live"].queue.get_nowait()[0] for _ in range(2)]
        assert [f["data"]["messages"][0]["message"] for f in frames] == ["one", "two"]
        assert [c[0] for c in state.calls] == [["one"], ["two"]]
        state.gate.clear()
        await hub.receive(ws, frame([{"message": "cancelled"}]), remote=True)
        await wait_until(lambda: len(state.calls) == 3)
        await hub.disconnect(peer)
        assert ws.closed and peer.incoming_worker.cancelled() and peer.incoming.empty()
        assert hub.peers["live"].queue.empty() and not service.pending
    finally:
        state.gate.set()
        await hub.close()


async def test_lan_existing_tracks_do_not_request_translation(provider, tmp_path):
    service, state = provider
    hub = make_hub(tmp_path, service)
    raw = frame([{"message": "main", "track": [{"message": "authored"}]}])
    try:
        await hub.receive(Socket(), raw, remote=True)
        await wait_until(lambda: bool(hub.peers["live"].queue.qsize()))
        assert hub.peers["live"].queue.get_nowait()[0] == raw
        assert not state.calls
    finally:
        await hub.close()


async def test_lan_translation_queue_is_bounded_without_partial_acceptance(provider, tmp_path):
    service, state = provider
    state.gate = asyncio.Event()
    hub = make_hub(tmp_path, service)
    ws = Socket()
    try:
        peer = await hub.receive(ws, frame([{"message": "first"}]), remote=True)
        await wait_until(lambda: bool(state.calls))
        for index in range(256):
            await hub.receive(ws, frame([{"message": str(index)}]), remote=True)
        with pytest.raises(ValueError, match="队列已满"):
            await hub.receive(ws, frame([{"message": "overflow"}]), remote=True)
        assert peer.incoming.qsize() == 256
        assert hub.peers["live"].queue.empty() and hub.peers["history"].control.empty()
    finally:
        state.gate.set()
        await hub.close()
