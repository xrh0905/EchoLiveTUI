"""Application operations shared by commands and the terminal screens."""
from __future__ import annotations
import asyncio
import copy
from pathlib import Path
import time
from pythonosc.udp_client import SimpleUDPClient
from .config import coerce
from .hub import Hub
from .message import format_username
from .pipeline import prepare
from .server import Server
from .addresses import first_ipv4, hosted_notice


class Core:
    def __init__(self, settings, cwd: Path, report=lambda _: None):
        self.settings, self.report = settings, report
        self.hub = Hub(settings, report)
        self.server = Server(self.hub, cwd)
        self.paren_once = False
        self.typing_task = None
        self.last_typing = 0.0
        self.typing_state = "关闭"
        self.osc_error = ""
        self.compose_draft = ""

    async def start(self):
        try:
            await self.server.start()
        except ValueError as exc:
            self.report(str(exc))
        if self.settings.load_error:
            self.report(f"配置无效，使用默认值且禁止覆盖：{self.settings.load_error}")
        if self.server.site and self.server.hosting.root:
            candidate = await asyncio.to_thread(first_ipv4)
            host, port = self.server.address
            self.report(hosted_notice(host, port, candidate))

    async def apply(self, updates, base=None):
        candidate = dict(self.settings.values)
        for key, value in updates.items():
            if base and base[key] != candidate[key] and value != candidate[key]:
                raise ValueError(f"设置冲突：{key} 已被其他操作修改；请丢弃草稿后重试")
            candidate[key] = coerce(key, value)
        self.settings.validate(candidate)
        old_hide_latest = self.settings["history.hide_latest"]
        old_address = self.server.address
        new_address = candidate["listen.host"], candidate["listen.port"]
        changed = old_address is not None and old_address != new_address
        if changed:
            await self.server.start(*new_address)
        try:
            self.settings.save(candidate)
        except Exception:
            if changed:
                await self.server.start(*old_address)
            raise
        if self.server.site is None and self.server.runner is not None:
            await self.server.start(*new_address)
        self.cancel_typing()
        if old_hide_latest and not self.settings["history.hide_latest"]:
            self.hub.history.flush()

    def save_routing(self, changes):
        routing = copy.deepcopy(self.settings.routing)
        routing.update(changes)
        self.settings.save(routing=routing)

    def submit(self, text):
        prepared = prepare(text, self.settings, self.paren_once)
        targets = self.hub.targets()
        history_count = len(self.hub.history.receivers())
        if not targets and not history_count and not self.settings["osc.enable"]:
            raise ValueError("没有可发送的字幕端，草稿已保留；请连接或选择端点")
        count = self.hub.broadcast(prepared.data, prepared.delay)
        osc_sent = False
        if self.settings["osc.enable"]:
            try:
                SimpleUDPClient(self.settings["osc.host"], self.settings["osc.port"]).send_message("/chatbox/input", [prepared.plain, True])
                osc_sent, self.osc_error = True, ""
            except Exception as exc:
                self.osc_error = str(exc)
                self.report(f"OSC 发送失败：{exc}")
        if not count and not history_count and not osc_sent:
            raise ValueError("OSC 发送失败，草稿已保留")
        self.paren_once = False
        self.cancel_typing()
        result = f"{self.settings['message.username']}：{text}"
        self.report(result)
        self.hub.log(f"已接受：字幕 {count} · 历史 {history_count}" + (" · OSC 已发送" if osc_sent else ""))
        return result

    def cancel_typing(self):
        if self.typing_task:
            self.typing_task.cancel()
            self.typing_task = None
        self.typing_state = "待输入" if self.settings["typing.enable"] else "关闭"

    def input_changed(self, text, *, literal=False):
        if not self.settings["typing.enable"] or not text or (not literal and text.startswith("/") and not text.startswith("//")):
            self.cancel_typing()
            return
        if self.typing_task and not self.typing_task.done():
            return
        self.typing_task = asyncio.create_task(self._send_typing())

    async def _send_typing(self):
        await asyncio.sleep(max(0, 1.5 - (time.monotonic() - self.last_typing)))
        self.last_typing = time.monotonic()
        count = self.hub.typing(format_username(self.settings.group("message")))
        unknown = any(p.profile.capabilities["typing"] is None for p in self.hub.targets())
        self.typing_state = (f"已发 {count} 端" if count else "无支持目标") + (" · 部分未知" if unknown else "")

    async def close(self):
        self.cancel_typing()
        await self.server.close()
