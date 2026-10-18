"""Application operations shared by commands and the terminal screens."""
from __future__ import annotations
import asyncio
import copy
from pathlib import Path
import time
from .config import coerce
from .hub import Hub
from .message import format_username
from .pipeline import prepare
from .server import Server
from .addresses import first_ipv4, hosted_notice
from .lan import choose_address, editor_url
from .capabilities import Capabilities
from .playback import DeliveryUnavailable
from .history import MessageReport
from .translation import TranslationError, editor_text

# Remote listeners expire the typing hint themselves; OSC needs an explicit stop.
TYPING_EXPIRY = 3.0


class Core:
    def __init__(self, settings, cwd: Path, report=lambda _: None):
        self.settings, self.report = settings, report
        self.hub = Hub(settings, report)
        self.server = Server(self.hub, cwd)
        self.capabilities = Capabilities.HAS_ECHO_LIVE if self.server.hosting.root else Capabilities.NONE
        self.paren_once = False
        self.typing_task = None
        self.typing_expiry = None
        self.last_typing = 0.0
        self.typing_state = "关闭"
        self.compose_draft = ""

    @property
    def osc_error(self):
        return self.hub.osc.error

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
        if self.server.site and self.settings["lan.enable"]:
            try:
                await self.server.set_lan(await asyncio.to_thread(choose_address, self.settings["lan.host"]))
                self.report_lan()
            except ValueError as exc:
                self.report(str(exc))

    def report_lan(self):
        if self.server.lan_address:
            self.report("EchoLiveTUI：远程 editor " + editor_url(*self.server.lan_address) + " · /connect 查看二维码")

    async def apply(self, updates, base=None):
        candidate = dict(self.settings.values)
        for key, value in updates.items():
            if base and base[key] != candidate[key] and value != candidate[key]:
                raise ValueError(f"设置冲突：{key} 已被其他操作修改；请丢弃草稿后重试")
            candidate[key] = coerce(key, value)
        self.settings.validate(candidate)
        network_changed = any(candidate[key] != self.settings[key] for key in
                              ("listen.host", "listen.port", "lan.enable", "lan.host"))
        # The connection page can explicitly retry an unchanged LAN setup.
        configure_network = network_changed or ("lan.enable" in updates and candidate["lan.enable"])
        if configure_network:
            lan_host = await asyncio.to_thread(choose_address, candidate["lan.host"]) if candidate["lan.enable"] else None
            if lan_host and not self.server.hosting.root:
                raise ValueError("未识别 Echo Live 目录，无法启用远程 editor")
        old_hide_latest = self.settings["history.hide_latest"]
        old_address = self.server.address
        old_lan = self.server.lan_address
        new_address = candidate["listen.host"], candidate["listen.port"]
        changed = old_address is not None and old_address != new_address
        try:
            if configure_network and changed:
                # Remove secondary bind before moving the local bind (same-port
                # changes can otherwise collide with the old LAN listener).
                await self.server.set_lan(None)
                await self.server.start(*new_address)
            if configure_network and self.server.site is None and self.server.runner is not None:
                await self.server.start(*new_address)
            if configure_network:
                await self.server.set_lan(lan_host)
            self.settings.save(candidate)
        except Exception:
            if configure_network and self.server.address != old_address and old_address:
                await self.server.set_lan(None)
                await self.server.start(*old_address)
            if configure_network and self.server.site:
                await self.server.set_lan(old_lan[0] if old_lan else None)
            raise
        if self.server.lan_address != old_lan:
            if self.server.lan_address:
                self.report_lan()
            else:
                self.report("EchoLiveTUI：远程发送已关闭，本机服务保持运行。")
        self.cancel_typing()
        if old_hide_latest and not self.settings["history.hide_latest"]:
            self.hub.history.flush()

    def save_routing(self, changes):
        routing = copy.deepcopy(self.settings.routing)
        routing.update(changes)
        self.settings.save(routing=routing)

    @property
    def has_output(self):
        return bool(self.hub.targets() or self.hub.history.receivers() or self.settings["osc.enable"])

    def submit(self, text, *, playback=None, tracks=None):
        prepared = prepare(text, self.settings, self.paren_once, tracks=tracks)
        targets = self.hub.targets()
        history_count = len(self.hub.history.receivers())
        if not targets and not history_count and not self.settings["osc.enable"]:
            raise DeliveryUnavailable("没有可发送的字幕端，草稿已保留；请连接或选择端点")
        count = self.hub.broadcast(prepared.data, prepared.delay, playback=playback)
        osc_sent = self.hub.osc.send_text(prepared.plain)
        if not count and not history_count and not osc_sent:
            raise ValueError("OSC 发送失败，草稿已保留")
        self.paren_once = False
        self.cancel_typing()
        result = f"{self.settings['message.username']}：{text}"
        self.report(MessageReport(result))
        self.hub.log(f"已接受：字幕 {count} · 历史 {history_count}" + (" · OSC 已发送" if osc_sent else ""), "debug")
        return result

    async def send(self, text, *, playback=None, tracks=None):
        """Resolve automatic tracks before any broadcast, history or OSC effect."""
        config = copy.deepcopy(self.settings.group("multitrack"))
        if not config["enable"] or tracks:
            return self.submit(text, playback=playback, tracks=tracks)
        if not self.has_output:
            raise DeliveryUnavailable("没有可发送的字幕端，草稿已保留；请连接或选择端点")
        plain = editor_text(text)
        if not plain.strip():
            return self.submit(text, playback=playback, tracks=tracks)
        error = None
        try:
            result = await self.hub.translator.translate([plain], config["source_language"], config["target_languages"])
            tracks = result.tracks(settings=self.settings)
        except TranslationError as exc:
            error = exc
            if not config["skip_errors"]:
                raise TranslationError(f"{exc}；草稿已保留") from exc
        if self.settings.group("multitrack") != config:
            raise TranslationError("多轨道设置已更改，请重新发送；草稿已保留")
        sent = self.submit(text, playback=playback, tracks=tracks)
        if error:
            self.report(f"翻译失败，已发送原消息：{error}")
        return sent

    async def wait_for_printing(self, receipts):
        if not receipts:
            return time.monotonic()
        tasks = [asyncio.create_task(receipt.wait()) for receipt in receipts]
        try:
            return max(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_typing(self):
        self._stop_typing_expiry()
        self.hub.osc.send_typing(False)
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
        osc = self.hub.osc.send_typing(True)
        unknown = any(p.profile.capabilities["typing"] is None for p in self.hub.targets())
        sent = [part for part in (f"{count} 端" if count else "", "OSC" if osc else "") if part]
        self.typing_state = (f"已发 {' + '.join(sent)}" if sent else "无支持目标") + (" · 默认兼容" if unknown else "")
        if self.hub.osc.typing:
            # Remote listeners expire the hint on their own; OSC keeps it until told otherwise.
            self._stop_typing_expiry()
            self.typing_expiry = asyncio.create_task(self._expire_typing())

    def _stop_typing_expiry(self):
        if self.typing_expiry:
            self.typing_expiry.cancel()
            self.typing_expiry = None

    async def _expire_typing(self):
        await asyncio.sleep(TYPING_EXPIRY)
        self.typing_expiry = None
        self.hub.osc.send_typing(False)

    async def close(self):
        self.cancel_typing()
        await self.server.close()
