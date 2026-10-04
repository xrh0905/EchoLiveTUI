"""Per-connection routing and independent, bounded playback queues."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
import time
from .protocol import Profile, Sender, target_matches, validate_envelope, version_tuple
from .history import HistoryDelivery, entries_from_data
from .message import typewrite_incoming
from .playback import PrintingReceipt


@dataclass
class Peer:
    ws: object
    profile: Profile
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(256))
    control: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(256))
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task | None = None
    closed: bool = False
    closing: bool = False
    observed: Profile | None = None
    playbacks: dict[int, PrintingReceipt] = field(default_factory=dict)
    ready_at: float = 0.0
    current_playback: PrintingReceipt | None = None

    def enqueue(self, envelope, delay=0, fast=False, playback=None):
        if self.closed:
            return False
        try:
            (self.control if fast else self.queue).put_nowait((envelope, delay))
            if playback is not None:
                self.playbacks[id(envelope)] = playback
            self.wake.set()
            return True
        except asyncio.QueueFull:
            return False

    async def write_loop(self, report):
        try:
            while not self.closed:
                self.wake.clear()
                now = time.monotonic()
                if not self.control.empty():
                    envelope, _ = self.control.get_nowait()
                elif not self.queue.empty() and now >= self.ready_at:
                    envelope, delay = self.queue.get_nowait()
                    self.ready_at = now + delay
                else:
                    timeout = max(0, self.ready_at - now) if not self.queue.empty() else None
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout)
                    except TimeoutError:
                        pass
                    continue
                playback = self.playbacks.get(id(envelope))
                if envelope["action"] == "message_data":
                    self.current_playback = playback
                if playback is not None:
                    playback.sent.set()
                await asyncio.wait_for(self.ws.send_json(envelope), 10)
                if envelope["action"] in {"message_data", "echo_printing"}:
                    report(f"已写入：{self.profile.name}", "debug")
        except (ConnectionError, RuntimeError, TimeoutError, OSError) as exc:
            report(f"写入失败 {self.profile.name}：{exc}", "warn")
            await self.ws.close()
        finally:
            self.closed = True
            self.fail_playbacks()

    def observe_playback(self, action, data):
        for key, receipt in list(self.playbacks.items()):
            receipt.observe(action, data)
            if receipt.finished.is_set():
                if receipt is self.current_playback:
                    self.ready_at = 0.0
                    self.current_playback = None
                    self.wake.set()
                self.playbacks.pop(key, None)

    def fail_playbacks(self):
        for receipt in self.playbacks.values():
            receipt.fail(f"字幕端 {self.profile.name} 已断开，演出已暂停")
        self.playbacks.clear()

    async def close(self):
        if self.closing:
            return
        self.closing = True
        self.closed = True
        self.fail_playbacks()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.ws.close()


class Hub:
    def __init__(self, settings, report=lambda _: None):
        self.settings = settings
        self.report = report
        self.sender = Sender()
        self.peers: dict[str, Peer] = {}
        self.history = HistoryDelivery(self)

    def log(self, text, level="info"):
        levels = {"error": 0, "warn": 1, "info": 2, "debug": 3}
        if levels[level] <= levels[self.settings["log.level"]]:
            self.report(text)

    def resolve(self, selector, role=None):
        matches = [p for p in self.peers.values() if (selector == p.profile.uuid or selector == "@" + p.profile.name) and (role is None or p.profile.role == role)]
        if len(matches) != 1:
            raise ValueError(f"端点不存在或名称重复：{selector}；请使用 UUID")
        return matches[0]

    def targets(self):
        selected = self.settings.routing["targets"]
        excluded = self.settings.routing["exclude"]
        return [p for p in self.peers.values() if p.profile.role == "live" and not p.closed and (any(target_matches(t, p.profile) for t in selected) if selected else True) and not any(target_matches(t, p.profile, targeted=False) for t in excluded)]

    async def register(self, ws, envelope, ip="", metadata=None):
        if getattr(ws, "_eltui_retired", False):
            raise ValueError("已替换的旧连接不能重新登记")
        uid = envelope["from"]["uuid"]
        existing = self.peers.get(uid)
        replaced = None
        if existing and existing.ws is ws:
            peer = existing
        else:
            if existing:
                setattr(existing.ws, "_eltui_retired", True)
                replaced = existing
            peer = Peer(ws, Profile(uid, ip=ip))
            self.peers[uid] = peer
            peer.worker = asyncio.create_task(peer.write_loop(self.log))

        if peer.observed:
            peer.profile = copy.deepcopy(peer.observed)
        peer.profile.identify(envelope, **(metadata or {}))
        peer.observed = copy.deepcopy(peer.profile)
        self.apply_override(peer)
        if not existing or replaced:
            self.log(f"连接：{peer.profile.name} · {peer.profile.role} · {ip}")
        if replaced:
            await replaced.close()
        return peer

    def apply_override(self, peer):
        if peer.observed:
            peer.profile = copy.deepcopy(peer.observed)
        uid = peer.profile.uuid
        override = self.settings.routing["overrides"].get(uid, self.settings.routing["overrides"].get("@" + peer.profile.name, {}))
        for key in ("role", "version", "software", "container"):
            if key in override:
                setattr(peer.profile, key, override[key])
        version = version_tuple(peer.profile.version)
        peer.profile.capabilities["typing"] = version >= (1, 8, 7) if version else None
        if "typing" in override:
            peer.profile.capabilities["typing"] = override["typing"]
            peer.profile.evidence.append("手动能力覆盖")
        if override and "手动能力覆盖" not in peer.profile.evidence:
            peer.profile.evidence.append("手动能力覆盖")

    async def disconnect(self, peer):
        if self.peers.get(peer.profile.uuid) is peer:
            del self.peers[peer.profile.uuid]
            self.history.pending.pop(peer.profile.uuid, None)
            self.log(f"断开：{peer.profile.name}")
        await peer.close()

    async def receive(self, ws, raw, ip="", metadata=None, *, remote=False):
        envelope = validate_envelope(raw)
        uid = envelope["from"]["uuid"]
        # A socket represents one endpoint; changing UUID is not a new identity.
        owned = next((p for p in self.peers.values() if p.ws is ws), None)
        if owned and owned.profile.uuid != uid:
            raise ValueError("同一连接不能更改 UUID")
        peer = await self.register(ws, envelope, ip, metadata)
        action = envelope["action"]
        peer.observe_playback(action, envelope.get("data", {}))
        if action in {"websocket_heartbeat", "heartbeat"}:
            # Upstream addresses heartbeats to @__ws_server; observers need a
            # reachable target while the original sender and payload stay intact.
            for dest in list(self.peers.values()):
                if dest.profile.role == "server" and not dest.closed:
                    forwarded = {**envelope, "target": dest.profile.uuid}
                    if not dest.enqueue(forwarded, fast=True):
                        self.log(f"写入失败：{dest.profile.name} 队列已满", "warn")
            return peer
        if envelope.get("target") == "@__ws_server":
            return peer
        if action == "message_data":
            if remote and self.settings["lan.typewriting"]:
                envelope = {**envelope, "data": typewrite_incoming(envelope.get("data", {}), self.settings["message.typewriting_scheme"])}
            entries = list(entries_from_data(envelope.get("data", {})))
            self.history.publish(entries)
            if peer.profile.role == "server":
                for entry in entries:
                    self.report(f"{entry.username or peer.profile.name}：{entry.message}")
        elif action == "history_clear":
            self.history.pending.clear()
        if action == "echo_state_update":
            peer.profile.state = envelope.get("data", {})
            peer.observed.state = peer.profile.state
        elif action in ("page_hidden", "page_visible"):
            peer.profile.hidden = action == "page_hidden"
            peer.observed.hidden = peer.profile.hidden
        for dest in list(self.peers.values()):
            if dest is peer or dest.closed:
                continue
            profile = dest.profile
            if not target_matches(envelope.get("target"), profile, targeted=False if action == "ping" else None):
                continue
            discovery = action in {"ping", "hello", "close", "page_hidden", "page_visible", "echo_state_update", "echo_printing", "live_display_update", "error", "error_unknown"}
            accepted = profile.accepts(action) or (discovery and (profile.role == "server" or action == "ping"))
            if action in {"echo_printing", "live_display_update"} and profile.role == "history":
                # Live playback feedback must not create a second history record
                # or control history visibility. The accepted message is the source.
                accepted = False
            if accepted:
                if not dest.enqueue(envelope, fast=action != "message_data"):
                    self.log(f"写入失败：{profile.name} 队列已满", "warn")
        if action == "close":
            await self.disconnect(peer)
        return peer

    def broadcast(self, data, delay, *, playback=None):
        targets = self.targets()
        entries = list(entries_from_data(data))
        if any(p.queue.full() for p in targets):
            raise ValueError("接收端队列已满，草稿已保留")
        if any(p.control.qsize() + len(entries) + (p.profile.uuid in self.history.pending) > p.control.maxsize for p in self.history.receivers()):
            raise ValueError("历史端队列已满，草稿已保留")
        for peer in targets:
            receipt = PrintingReceipt(data.get("username", ""), entries[0].message, delay, endpoint=(peer.profile.uuid, peer.profile.name)) if playback is not None else None
            peer.enqueue(self.sender.envelope("message_data", data, peer.profile.uuid), delay, playback=receipt)
            if receipt is not None:
                playback.append(receipt)
        self.history.publish(entries)
        return len(targets)

    def release_playback(self, receipts):
        for peer in self.peers.values():
            for key, receipt in list(peer.playbacks.items()):
                if receipt in receipts:
                    peer.playbacks.pop(key, None)

    def typing(self, username):
        sent = 0
        for peer in self.targets():
            if peer.profile.accepts("editor_typing"):
                sent += peer.enqueue(self.sender.envelope("editor_typing", {"username": username}, peer.profile.uuid), fast=True)
        return sent

    async def close(self):
        await asyncio.gather(*(p.close() for p in list(self.peers.values())))
        self.peers.clear()
        self.history.pending.clear()
