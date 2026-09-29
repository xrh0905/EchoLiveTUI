"""Per-connection routing and independent, bounded playback queues."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from .protocol import Profile, Sender, target_matches, validate_envelope


@dataclass
class Peer:
    ws: object
    profile: Profile
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(256))
    control: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(256))
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task | None = None
    closed: bool = False

    def enqueue(self, envelope, delay=0, fast=False):
        if self.closed:
            return False
        try:
            (self.control if fast else self.queue).put_nowait((envelope, delay))
            self.wake.set()
            return True
        except asyncio.QueueFull:
            return False

    async def write_loop(self, report):
        ready = 0.0
        try:
            while not self.closed:
                self.wake.clear()
                now = time.monotonic()
                if not self.control.empty():
                    envelope, _ = self.control.get_nowait()
                elif not self.queue.empty() and now >= ready:
                    envelope, delay = self.queue.get_nowait()
                    ready = now + delay
                else:
                    timeout = max(0, ready - now) if not self.queue.empty() else None
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout)
                    except TimeoutError:
                        pass
                    continue
                await asyncio.wait_for(self.ws.send_json(envelope), 10)
                if envelope["action"] == "message_data":
                    report(f"已写入：{self.profile.name}")
        except (ConnectionError, RuntimeError, TimeoutError, OSError) as exc:
            report(f"发送失败 {self.profile.name}：{exc}")
            await self.ws.close()
        finally:
            self.closed = True

    async def close(self):
        self.closed = True
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
        self.history_sources: dict[str, str] = {}

    def resolve(self, selector, role=None):
        matches = [p for p in self.peers.values() if (selector == p.profile.uuid or selector == "@" + p.profile.name) and (role is None or p.profile.role == role)]
        if len(matches) != 1:
            raise ValueError(f"端点不存在或名称重复：{selector}；请使用 UUID")
        return matches[0]

    def targets(self):
        selected = self.settings.routing["targets"]
        excluded = self.settings.routing["exclude"]
        return [p for p in self.peers.values() if p.profile.role == "live" and not p.closed and (any(target_matches(t, p.profile) for t in selected) if selected else not p.profile.targeted) and not any(target_matches(t, p.profile, targeted=False) for t in excluded)]

    async def register(self, ws, envelope, ip="", metadata=None):
        uid = envelope["from"]["uuid"]
        existing = self.peers.get(uid)
        if existing and existing.ws is ws:
            peer = existing
        else:
            if existing:
                await existing.close()
            peer = Peer(ws, Profile(uid, ip=ip))
            self.peers[uid] = peer
            peer.worker = asyncio.create_task(peer.write_loop(self.report))
            self.report(f"连接：{uid}")
        peer.profile.identify(envelope, **(metadata or {}))
        override = self.settings.routing["overrides"].get("@" + peer.profile.name, self.settings.routing["overrides"].get(uid, {}))
        for key in ("role", "version", "software", "container"):
            if key in override:
                setattr(peer.profile, key, override[key])
        if "typing" in override:
            peer.profile.capabilities["typing"] = override["typing"]
            peer.profile.evidence.append("手动能力覆盖")
        return peer

    async def disconnect(self, peer):
        if self.peers.get(peer.profile.uuid) is peer:
            del self.peers[peer.profile.uuid]
            self.history_sources = {h: s for h, s in self.history_sources.items() if h != peer.profile.uuid and s != peer.profile.uuid}
            self.report(f"断开：{peer.profile.name}")
        await peer.close()

    def history_source(self, history):
        uid = history.profile.uuid
        explicit = self.settings.routing["history"].get("@" + history.profile.name, self.settings.routing["history"].get(uid))
        if explicit:
            try:
                return self.resolve(explicit, "live").profile.uuid
            except ValueError:
                return None
        if uid not in self.history_sources:
            candidate = next((p.profile.uuid for p in self.peers.values() if p.profile.role == "live" and not p.profile.hidden and not p.profile.targeted), None)
            if candidate:
                self.history_sources[uid] = candidate
        return self.history_sources.get(uid)

    async def receive(self, ws, raw, ip="", metadata=None):
        envelope = validate_envelope(raw)
        uid = envelope["from"]["uuid"]
        # A socket represents one endpoint; changing UUID is not a new identity.
        owned = next((p for p in self.peers.values() if p.ws is ws), None)
        if owned and owned.profile.uuid != uid:
            raise ValueError("同一连接不能更改 UUID")
        peer = await self.register(ws, envelope, ip, metadata)
        action = envelope["action"]
        if envelope.get("target") == "@__ws_server":
            return peer
        if action == "echo_state_update":
            peer.profile.state = envelope.get("data", {})
        elif action in ("page_hidden", "page_visible"):
            peer.profile.hidden = action == "page_hidden"
        for dest in list(self.peers.values()):
            if dest is peer or dest.closed:
                continue
            profile = dest.profile
            if not target_matches(envelope.get("target"), profile, targeted=False if action == "ping" else None):
                continue
            discovery = action in {"ping", "hello", "page_hidden", "page_visible", "echo_state_update", "error"}
            accepted = profile.accepts(action) or (discovery and (profile.role == "server" or action == "ping"))
            if action in {"echo_printing", "live_display_update"} and profile.role == "history":
                accepted = accepted and self.history_source(dest) == uid and not peer.profile.hidden
            if accepted:
                if not dest.enqueue(envelope, fast=action != "message_data"):
                    self.report(f"队列已满：{profile.name}")
        return peer

    def broadcast(self, data, delay):
        targets = self.targets()
        if any(p.queue.full() for p in targets):
            raise ValueError("接收端队列已满，草稿已保留")
        for peer in targets:
            peer.enqueue(self.sender.envelope("message_data", data, peer.profile.uuid), delay)
        return len(targets)

    def typing(self, username):
        sent = 0
        for peer in self.targets():
            if peer.profile.accepts("editor_typing"):
                sent += peer.enqueue(self.sender.envelope("editor_typing", {"username": username}, peer.profile.uuid), fast=True)
        return sent

    async def close(self):
        await asyncio.gather(*(p.close() for p in list(self.peers.values())))
        self.peers.clear()
