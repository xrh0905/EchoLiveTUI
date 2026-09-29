"""Echo Live envelopes, ordered targets and evidence-based client identity."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
import uuid

ROLES = {"live", "history", "character", "server", "client"}
LIVE_ACTIONS = {"message_data", "editor_typing", "echo_next", "set_live_display", "set_theme", "set_theme_style_url"}
HISTORY_ACTIONS = {"echo_printing", "history_clear", "live_display_update"}
CHARACTER_ACTIONS = {"set_avatar"}


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(map(int, match.groups())) if match else None


@dataclass
class Profile:
    uuid: str
    name: str = "未命名"
    role: str = "unknown"
    software: str = "未知客户端"
    version: str | None = None
    container: str = "未知"
    ip: str = ""
    hidden: bool = False
    targeted: bool = False
    evidence: list[str] = field(default_factory=list)
    capabilities: dict[str, bool | None] = field(default_factory=lambda: {"typing": None})
    state: dict = field(default_factory=dict)

    def identify(self, envelope, hosted_version=None, page=None):
        sender = envelope.get("from", {})
        self.name = str(sender.get("name") or self.name)
        claimed = sender.get("type")
        if claimed in ROLES:
            self.role = claimed
            self.evidence = [f"from.type={claimed}"]
        data = envelope.get("data", {})
        if envelope["action"] == "hello":
            self.hidden = bool(data.get("hidden", False))
            self.targeted = bool(data.get("targeted", False))
        if page in ("live", "history", "character", "editor"):
            self.software = "Echo Live"
            self.role = "server" if page == "editor" else page
            self.version = hosted_version
            self.evidence.append(f"托管页面 {page}")
        v = version_tuple(self.version)
        self.capabilities["typing"] = v >= (1, 8, 7) if v else None

    def accepts(self, action):
        if self.role in {"live", "history", "character", "client"} and action in {"broadcast_close", "websocket_close", "shutdown"}:
            return True
        if self.role == "live":
            return action in LIVE_ACTIONS and (action != "editor_typing" or self.capabilities["typing"] is True)
        if self.role == "history":
            return action in HISTORY_ACTIONS
        if self.role == "character":
            return action in CHARACTER_ACTIONS
        return False


def target_matches(target, profile: Profile, *, targeted=None):
    """Port upstream's ordered first-match rules (negative selectors are not a set)."""
    is_targeted = profile.targeted if targeted is None else targeted
    targets = target if isinstance(target, list) else [target]
    has_not = has_other = False
    for entry in targets:
        negative = isinstance(entry, str) and entry.startswith("-")
        if negative:
            has_not = True
            entry = entry[1:]
        if entry is None:
            if len(targets) > 1:
                continue
            return not is_targeted
        if not isinstance(entry, str):
            continue
        public = entry.startswith("@__")
        if public:
            requested = entry[3:]
            matches = requested == profile.role or (requested == "client" and profile.role in {"live", "history", "character", "client"})
        elif entry.startswith("@"):
            matches = entry[1:] == profile.name
        else:
            matches = entry == profile.uuid
        if not matches:
            has_other = not negative or has_other
            continue
        # Upstream only sets inPublic on a failed role match, then continues.
        # Consequently an explicit matching @__role also reaches targeted peers.
        return not negative
    return False if is_targeted else has_not and not has_other


def validate_envelope(value):
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise ValueError("无效 action")
    if not isinstance(value.get("data", {}), dict):
        raise ValueError("data 必须为对象")
    sender = value.get("from")
    if not isinstance(sender, dict) or not isinstance(sender.get("uuid"), str) or not sender["uuid"] or len(sender["uuid"]) > 128:
        raise ValueError("from.uuid 缺失或无效")
    target = value.get("target")
    if target is not None and not isinstance(target, (str, list)):
        raise ValueError("target 必须为字符串或数组")
    if isinstance(target, list) and not all(t is None or isinstance(t, str) for t in target):
        raise ValueError("无效 target 数组")
    return value


class Sender:
    def __init__(self):
        self.uuid = str(uuid.uuid4())

    def envelope(self, action, data, target=None):
        result = {"action": action, "data": data, "from": {"name": "EchoLiveTUI", "uuid": self.uuid, "type": "server", "timestamp": int(time.time() * 1000)}}
        if target is not None:
            result["target"] = target
        return result
