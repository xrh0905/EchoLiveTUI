"""One command grammar for interactive input, help, completion and scripts."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shlex
from .config import FIELDS, coerce

HELP = {
    "settings": "/settings [input|typewriting|formatting|typing|endpoints|network|history|log|osc] — 设置界面",
    "set": "/set key value — 修改并保存设置",
    "name": "/name [名字] — 设置或查看说话人",
    "quote": "/quote [on|off|en|cn|jp|custom] — 引号开关/样式",
    "paren": "/paren [once|on|off] — 下一条或持续使用括号",
    "endpoints": "/endpoints — 客户端识别、目标与历史来源",
    "target": "/target all|reset|set 选择器...|exclude 选择器...",
    "history": "/history clear — 清空独立历史记录",
    "status": "/status — 服务与端点摘要",
    "compose": "/compose — 多行草稿",
    "source": "/source 文件 — 预检查后执行脚本",
    "help": "/help [命令] — 帮助",
    "quit": "/quit — 退出",
    "nocc": "/nocc [on|off] — Ctrl+C 退出保护",
    "clear": "/clear — /history clear 的别名",
    "exit": "/exit — /quit 的别名",
}
INTERACTIVE = {"settings", "endpoints", "compose", "quit", "source"}


@dataclass
class Command:
    name: str
    args: list[str]


def parse(text, script=False):
    if text.startswith("//"):
        return Command("text", [text[1:]])
    if not text.startswith("/"):
        return Command("text", [text])
    try:
        lexer = shlex.shlex(text[1:], posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        lexer.escape = ""  # Preserve Windows paths; quotes still group spaces.
        parts = list(lexer)
    except ValueError as exc:
        raise ValueError(f"命令引号不完整：{exc}") from None
    if not parts:
        raise ValueError("输入 /help 查看命令")
    name, args = parts[0].lower(), parts[1:]
    if name in {"skip", "cancel", "next"}:
        raise ValueError("/skip 将在阶段二重做，当前不提供播放跳过")
    if name not in HELP:
        raise ValueError(f"未知命令 /{name}；用 // 发送以 / 开头的消息")
    if name == "clear":
        if args:
            raise ValueError(HELP[name])
        name, args = "history", ["clear"]
    if name == "exit":
        name = "quit"
    if script and name in INTERACTIVE:
        raise ValueError(f"脚本不能使用 /{name}")
    valid = True
    if name in {"quit", "compose", "endpoints", "status"}:
        valid = not args
    elif name == "settings":
        valid = len(args) <= 1 and (not args or args[0] in {"input", "listen", "message", "typing", "routing", "osc", "typewriting", "formatting", "endpoints", "network", "log", "history"})
    elif name == "set":
        valid = len(args) >= 2
        if valid:
            coerce(args[0], " ".join(args[1:]))
    elif name in {"quote", "paren", "nocc"}:
        allowed = {"quote": {"on", "off", "en", "cn", "jp", "custom"}, "paren": {"once", "on", "off"}, "nocc": {"on", "off"}}[name]
        valid = len(args) <= 1 and (not args or args[0] in allowed)
    elif name in {"help", "source"}:
        valid = len(args) == 1 if name == "source" else len(args) <= 1
    elif name == "target":
        valid = bool(args) and ((args[0] in {"all", "reset"} and len(args) == 1) or (args[0] in {"set", "exclude"} and len(args) > 1))
    elif name == "history":
        valid = args == ["clear"]
    if not valid:
        raise ValueError(HELP[name])
    return Command(name, args)


async def execute(core, command):
    name, args = command.name, command.args
    settings = core.settings
    if name == "text":
        core.submit(args[0])
    elif name in INTERACTIVE - {"source"}:
        core.cancel_typing()
        return command
    elif name == "set":
        await core.apply({args[0]: " ".join(args[1:])})
    elif name == "name":
        if args:
            await core.apply({"message.username": " ".join(args)})
        else:
            core.report(settings["message.username"])
    elif name == "quote":
        style = args[0] if args else ("off" if settings["message.quote"] else "on")
        updates = {"message.quote": style != "off"}
        if style not in {"on", "off"}:
            updates["message.quote_style"] = style
        await core.apply(updates)
    elif name == "paren":
        if args == ["once"]:
            core.paren_once = True
        elif args:
            await core.apply({"message.paren": args[0] == "on"})
            core.paren_once = False
        else:
            core.report("括号：" + ("仅下一条" if core.paren_once else str(settings["message.paren"])))
    elif name == "nocc":
        await core.apply({"input.interrupt_guard": args[0] == "on" if args else not settings["input.interrupt_guard"]})
    elif name == "target":
        if args[0] in {"all", "reset"}:
            core.save_routing({"targets": [], "exclude": []})
        else:
            for selector in args[1:]:
                core.hub.resolve(selector, "live")
            core.save_routing({"targets": args[1:], "exclude": []} if args[0] == "set" else {"exclude": args[1:]})
    elif name == "history":
        core.hub.history.clear()
    elif name == "source":
        lines = Path(args[0]).read_text(encoding="utf-8-sig").splitlines()
        commands = []
        for i, line in enumerate(lines, 1):
            if not line.strip() or line.startswith("#"):
                continue
            try:
                commands.append(parse(line, script=True))
            except ValueError as exc:
                raise ValueError(f"脚本第 {i} 行：{exc}；未执行") from None
        for item in commands:
            await execute(core, item)
    elif name == "status":
        core.report(f"服务 {core.server.address or '未启动'} · 客户端 {len(core.hub.peers)} · 目标 {len(core.hub.targets())}")
    elif name == "help":
        core.report(HELP.get(args[0].lstrip("/"), "未知命令") if args else "\n".join(HELP.values()))
    return None
