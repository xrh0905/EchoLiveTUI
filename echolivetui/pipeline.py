"""Pure message preparation; one-shot state is consumed only after acceptance."""
import json
import copy
from dataclasses import dataclass
from .message import parse_message, apply_autopause, render, get_delay, typewrite_incoming

from .symbols import QUOTES


def quote_symbols(settings):
    return QUOTES.get(settings["message.quote_style"], (settings["message.quote_open"], settings["message.quote_close"]))


@dataclass
class Prepared:
    original: str
    plain: str
    data: dict
    delay: float


def prepare(text, settings, paren_once=False, *, tracks=None):
    """Prepare editor text and optional, non-nested upstream wire paragraphs."""
    if not text.strip():
        raise ValueError("消息不能为空")
    modified = text
    if settings["message.suffix"]:
        modified += settings["message.suffix_value"]
    if settings["message.quote"]:
        left, right = quote_symbols(settings)
        modified = left + modified + right
    cfg = settings.group("message")
    if paren_once:
        cfg["username_brackets"] = True
    parsed = apply_autopause(cfg, parse_message(modified))
    data = json.loads(render(cfg, parsed))["data"]
    delay = get_delay(cfg, parsed)
    if tracks is not None:
        delay = max(delay, get_delay(cfg, data["messages"][0]["message"]))
        if not isinstance(tracks, list) or any(not isinstance(track, dict) or
                not isinstance(track.get("message"), (str, dict, list)) for track in tracks):
            raise ValueError("子轨道必须为包含 message 的段落对象列表")
        if any("track" in track for track in tracks):
            raise ValueError("不允许嵌套子轨道")
        children = copy.deepcopy(tracks)
        for track in children:
            if not isinstance(track.get("data", {}), dict):
                raise ValueError("子轨道 data 必须为对象")
            track.setdefault("data", {}).setdefault("printSpeed", cfg["print_speed"])
        if cfg["typewriting"]:
            children = typewrite_incoming({"messages": children}, cfg["typewriting_scheme"])["messages"]
        data["messages"][0]["track"] = children
        for track in children:
            message = track["message"]
            parts = message if isinstance(message, list) else [message]
            parts = [{"text": part} if isinstance(part, str) else part for part in parts]
            track_config = {**cfg, "print_speed": track["data"]["printSpeed"]}
            delay = max(delay, get_delay(track_config, parts))
    return Prepared(text, "".join(x.get("text", "") for x in parsed), data, delay / 1000)
