from __future__ import annotations

import json
import pytest

from echolivetui.config import FIELDS
DEFAULT_CONFIG = {k.split(".", 1)[1]: f.default for k, f in FIELDS.items() if k.startswith("message.")}
from echolivetui.message import (
    apply_autopause,
    format_username,
    get_delay,
    get_typewriting_string,
    parse_message,
    render,
    typewrite_incoming,
)


def test_parse_message_supports_markdown_and_fast_formatting() -> None:
    parsed = parse_message("@b粗体 @r**强** *斜* `码` @[#66ccff]蓝 @{smile} @<warn>类")

    assert any(entry.get("style", {}).get("bold") for entry in parsed if entry.get("text") == "粗体 ")
    assert any(entry.get("style", {}).get("bold") for entry in parsed if entry.get("text") == "强")
    assert any(entry.get("style", {}).get("italic") for entry in parsed if entry.get("text") == "斜")
    assert any(entry.get("style", {}).get("code") for entry in parsed if entry.get("text") == "码")
    assert "@{smile}" in "".join(entry.get("text", "") for entry in parsed)
    assert any("echo-text-warn" in entry.get("class", []) for entry in parsed)


def test_parse_message_supports_escaped_at_and_editor_strike_code() -> None:
    parsed = parse_message(r"\@字面 @sh喊")

    assert parsed[0]["text"].startswith("@字面")
    assert any(entry.get("text") == "h喊" and entry.get("style", {}).get("strikethrough") for entry in parsed)


def test_wire_format_adapts_legacy_emoji_classes_and_speed():
    config = dict(DEFAULT_CONFIG, typewriting=False, print_speed=7)
    payload = json.loads(render(config, parse_message("@{smile} @<a>@<b>`code`")))
    message = payload["data"]["messages"][0]
    assert message["data"]["printSpeed"] == 7
    assert not any(p.get("data", {}).get("emoji") for p in message["message"])
    assert all(isinstance(p["class"], str) for p in message["message"] if "class" in p)


def test_autopause_delay_and_render_payload() -> None:
    config = DEFAULT_CONFIG.copy()
    config.update(
        {
            "username": "测试",
            "username_brackets": True,
            "autopause": True,
            "autopausestr": "，",
            "autopausetime": 2,
            "typewriting": True,
            "print_speed": 5,
        }
    )

    messages = apply_autopause(config, parse_message("你好，世界"))
    payload = json.loads(render(config, messages))

    assert format_username(config) == "【测试】"
    assert any(entry.get("pause") == 2 for entry in messages)
    assert get_delay(config, messages) > 0
    assert payload["action"] == "message_data"
    rendered_entries = payload["data"]["messages"][0]["message"]
    assert all(entry.get("speed") == 5 for entry in rendered_entries if entry.get("text"))
    assert any("typewrite" in entry for entry in rendered_entries if entry.get("text"))


def test_typewriting_schemes() -> None:
    assert get_typewriting_string("凉宫", "pinyin") == "liang'gong"
    assert get_typewriting_string("凉宫", "zhuyin")


def test_incoming_typewriting_preserves_editor_fields():
    import copy
    data = {"username": "手机", "messages": [
        {"message": "你好世界", "data": {"printSpeed": 42}},
        {"message": [{"text": "凉宫", "style": {"bold": True}, "speed": 7, "event": "shout"},
                     {"text": "现有", "typewrite": "custom"}, {"pause": 4},
                     {"data": {"emoji": "smile"}}, "文字"]},
    ]}
    before = copy.deepcopy(data)
    result = typewrite_incoming(data, "zhuyin")
    assert data == before
    assert result["username"] == "手机"
    assert result["messages"][0]["data"] == {"printSpeed": 42}
    plain = result["messages"][0]["message"]
    assert "".join(part["text"] for part in plain) == "你好世界"
    assert all(part["typewrite"] for part in plain)
    parts = result["messages"][1]["message"]
    assert parts[0] == {**data["messages"][1]["message"][0], "typewrite": get_typewriting_string("凉宫", "zhuyin")}
    assert parts[1:4] == data["messages"][1]["message"][1:4]
    assert parts[4]["text"] == "文字"


@pytest.mark.parametrize("scheme", ["pinyin", "zhuyin"])
@pytest.mark.parametrize("symbols", [
    "——————", "……", "！！！？？？", "，，。。。", "...---___!!!???",
    "━━━━━━━━───", "≈≠≤≥±∞", "💥💥👩‍💻👨‍👩‍👧‍👦", "❤️❤️👍🏽", "🇨🇳🇯🇵",
    "—" * 4096,
])
def test_symbol_runs_are_single_literal_segments(scheme, symbols):
    config = dict(DEFAULT_CONFIG, typewriting=True, typewriting_scheme=scheme, print_speed=7)
    source = [{"text": "你好" + symbols + "再见", "style": {"bold": True}, "class": ["echo-text-warn"], "event": "shout"}]
    parts = json.loads(render(config, source))["data"]["messages"][0]["message"]
    assert [part["text"] for part in parts] == ["你好", symbols, "再见"]
    assert "typewrite" not in parts[1]
    assert parts[0]["typewrite"] and parts[2]["typewrite"]
    assert all(part["style"]["bold"] and part["speed"] == 7 and part["class"] == "echo-text-warn" for part in parts)
    assert [part.get("event") for part in parts] == ["shout", None, None]
    assert get_typewriting_string(symbols, scheme) == ""
    incoming = {"username": "手机", "messages": [{"message": "你好" + symbols + "再见", "data": {"printSpeed": 42}}]}
    converted = typewrite_incoming(incoming, scheme)
    assert [part["text"] for part in converted["messages"][0]["message"]] == ["你好", symbols, "再见"]
    assert converted["messages"][0]["message"][1] == {"text": symbols}
    assert converted["messages"][0]["data"] == {"printSpeed": 42}
    assert incoming["messages"][0]["message"] == "你好" + symbols + "再见"


def test_symbol_grouping_preserves_formatting_and_pause_boundaries():
    config = dict(DEFAULT_CONFIG, typewriting=True, autopause=True, autopausestr="！", autopausetime=2)
    parsed = apply_autopause(config, parse_message("@b你好————！！！@r？？？再见"))
    parts = json.loads(render(config, parsed))["data"]["messages"][0]["message"]
    assert "".join(part.get("text", "") for part in parts) == "你好————！！！？？？再见"
    assert next(part for part in parts if part.get("text") == "————！！！")["style"]["bold"]
    assert not next(part for part in parts if part.get("text") == "？？？").get("style", {}).get("bold")
    assert [part["pause"] for part in parts if "pause" in part] == [2, 2]


def test_symbol_grouping_keeps_whitespace_and_editor_phonetics():
    source = {"messages": [{"message": ["————  ！！！\n世界", {"text": "……", "typewrite": "editor"}]}]}
    parts = typewrite_incoming(source, "pinyin")["messages"][0]["message"]
    assert "".join(part["text"] for part in parts) == "————  ！！！\n世界……"
    assert parts[0] == {"text": "————"}
    assert next(part for part in parts if part["text"] == "！！！") == {"text": "！！！"}
    assert parts[-1] == {"text": "……", "typewrite": "editor"}

