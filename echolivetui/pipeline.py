"""Pure message preparation; one-shot state is consumed only after acceptance."""
import json
from dataclasses import dataclass
from .message import parse_message, apply_autopause, render, get_delay

from .symbols import QUOTES


def quote_symbols(settings):
    return QUOTES.get(settings["message.quote_style"], (settings["message.quote_open"], settings["message.quote_close"]))


@dataclass
class Prepared:
    original: str
    plain: str
    data: dict
    delay: float


def prepare(text, settings, paren_once=False):
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
    return Prepared(text, "".join(x.get("text", "") for x in parsed), json.loads(render(cfg, parsed))["data"], get_delay(cfg, parsed) / 1000)
