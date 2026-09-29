"""Independent history delivery, owned by ELTUI rather than live playback ACKs."""
from dataclasses import dataclass


def plain_message(message):
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(plain_message(item) for item in message)
    if isinstance(message, dict):
        return str(message.get("text", ""))
    return ""


@dataclass(frozen=True)
class HistoryEntry:
    username: str
    message: str


def entries_from_data(data):
    username = str(data.get("username", ""))
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        return
    for message in messages:
        yield HistoryEntry(username, plain_message(message.get("message", "") if isinstance(message, dict) else message))


class HistoryDelivery:
    def __init__(self, hub):
        self.hub = hub
        self.pending = {}

    def receivers(self):
        return [p for p in self.hub.peers.values() if p.profile.role == "history" and not p.closed]

    def publish(self, entries):
        entries = list(entries)
        for peer in self.receivers():
            uid = peer.profile.uuid
            for entry in entries:
                previous = self.pending.pop(uid, None)
                if previous:
                    self.send(peer, previous)
                if self.hub.settings["history.hide_latest"]:
                    self.pending[uid] = entry
                else:
                    self.send(peer, entry)

    def send(self, peer, entry):
        envelope = self.hub.sender.envelope("echo_printing", {"username": entry.username, "message": entry.message}, peer.profile.uuid)
        if not peer.enqueue(envelope, fast=True):
            self.hub.log(f"历史写入失败：{peer.profile.name} 队列已满", "error")

    def flush(self):
        for uid, entry in list(self.pending.items()):
            peer = self.hub.peers.get(uid)
            if peer and not peer.closed:
                self.send(peer, entry)
            self.pending.pop(uid, None)

    def clear(self):
        self.pending.clear()
        for peer in self.receivers():
            peer.enqueue(self.hub.sender.envelope("history_clear", {}, peer.profile.uuid), fast=True)
