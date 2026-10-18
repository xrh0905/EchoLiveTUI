"""Shared shortcode completion, selection formatting, and wire-data preview."""
import re
from rich.text import Text
from rich.style import Style
from rich.cells import cell_len
from .message import parse_message
from .pipeline import prepare

SHORTCODES = {
    "@@": "字面 @", "@b": "粗体", "@i": "斜体",
    "@u": "下划线", "@s": "删除线", "@+": "放大", "@-": "缩小",
    "@^": "上标", "@_": "下标",
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
    codes = "".join(code for name, code in (("bold", "@b"), ("italic", "@i"), ("underline", "@u"), ("strikethrough", "@s"), ("super", "@^"), ("sub", "@_")) if style.get(name))
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


def preview(text, settings, *, hint=None):
    """Preview the first logical line, with aligned rows for script text."""
    result = Text(no_wrap=True, overflow="crop")
    if not text.strip():
        return result
    rows = {name: Text() for name in ("super", "main", "sub")}
    for segment in prepare(text, settings).data["messages"][0]["message"]:
        style = segment.get("style", {})
        classes = segment.get("class", "").split()
        layer = "super" if style.get("super") or "echo-text-super" in classes else "sub" if style.get("sub") or "echo-text-sub" in classes else "main"
        value, newline, _ = segment.get("text", "").partition("\n")
        color = style.get("color")
        if color and len(color) in (5, 9):
            color = color[:-1] if len(color) == 5 else color[:-2]
        if color and len(color) == 4:
            color = "#" + "".join(c * 2 for c in color[1:])
        try:
            rich_style = Style(color=color, bold=style.get("bold"), italic=style.get("italic"), underline=style.get("underline"), strike=style.get("strikethrough"))
        except Exception:
            rich_style = Style()
        if layer != "main":
            rich_style += Style(color="#9eaeb9")
        if layer == "main" and "echo-text-rainbow" in classes:
            colors = ("#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#a855f7")
            for index, char in enumerate(value):
                rows[layer].append(char, rich_style + Style(color=colors[index % len(colors)]))
        else:
            rows[layer].append(value, rich_style)
        for name, row in rows.items():
            if name != layer:
                row.append(" " * cell_len(value))
        if newline:
            break
    if hint:
        rows["main"].append("  " + hint, style="dim #9eaeb9")
    used = [rows["main"]]
    if rows["super"].plain.strip():
        used.insert(0, rows["super"])
    if rows["sub"].plain.strip():
        used.append(rows["sub"])
    result.append_text(Text("\n").join(used))
    return result
