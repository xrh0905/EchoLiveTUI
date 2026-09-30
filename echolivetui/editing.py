"""Shared shortcode completion, selection formatting, and wire-data preview."""
import re
from rich.text import Text
from rich.style import Style
from .message import parse_message
from .pipeline import prepare

SHORTCODES = {
    "@@": "字面 @", "@b": "粗体", "@i": "斜体",
    "@u": "下划线", "@s": "删除线", "@+": "放大", "@-": "缩小",
    "@shout": "喊叫效果", "@rainbow": "彩虹文字",
    "@r": "清除格式", "@[#66ccff]": "颜色", "@<rainbow>": "内置样式类",
    "@<:custom>": "自定义 CSS 类", "\\@": "字面 @",
}


def completion(text, cursor):
    match = re.search(r"(?<![\\@])@[^@\s]*$", text[:cursor])
    if not match:
        return [], cursor
    prefix = match.group()
    choices = [code for code in SHORTCODES if code.startswith(prefix)]
    return choices, match.start()


def restore_codes(prefix):
    segments = parse_message(prefix + "\ufffc")
    last = segments[-1] if segments else {}
    style = last.get("style", {})
    codes = "".join(code for name, code in (("bold", "@b"), ("italic", "@i"), ("underline", "@u"), ("strikethrough", "@s")) if style.get(name))
    codes += {"extra-small": "@-@-", "small": "@-", "large": "@+", "extra-large": "@+@+"}.get(style.get("size"), "")
    if style.get("color"):
        codes += "@[" + style["color"] + "]"
    codes += "".join("@<:" + cls + ">" for cls in last.get("class", []))
    return codes


def format_selection(text, start, end, code):
    lo, hi = sorted((start, end))
    selection = text[lo:hi]
    replacement = code + selection + ("@r" + restore_codes(text[:lo]) if selection else "")
    return text[:lo] + replacement + text[hi:], lo + len(code), lo + len(code) + len(selection)


def preview(text, settings):
    result = Text()
    if not text.strip():
        return result
    for segment in prepare(text, settings).data["messages"][0]["message"]:
        style = segment.get("style", {})
        color = style.get("color")
        if color and len(color) in (5, 9):
            color = color[:-1] if len(color) == 5 else color[:-2]
        if color and len(color) == 4:
            color = "#" + "".join(c * 2 for c in color[1:])
        try:
            rich_style = Style(color=color, bold=style.get("bold"), italic=style.get("italic"), underline=style.get("underline"), strike=style.get("strikethrough"))
        except Exception:
            rich_style = Style()
        if "echo-text-rainbow" in segment.get("class", "").split():
            colors = ("#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#a855f7")
            for index, char in enumerate(segment.get("text", "")):
                result.append(char, rich_style + Style(color=colors[index % len(colors)]))
        else:
            result.append(segment.get("text", ""), rich_style)
    return result
