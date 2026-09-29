"""Input-first Textual screens; commands and widgets share the same core."""
from __future__ import annotations
import copy
from datetime import datetime
from rich.text import Text
from rich.cells import cell_len
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widgets import Button, Checkbox, Input, Label, Select, RichLog, Static, TextArea
from .commands import HELP, execute, parse
from .config import FIELDS, Settings, coerce
from .core import Core
from .pipeline import quote_symbols


class HistoryLog(RichLog, can_focus=False):
    """Wrapped, mouse-selectable records which never take keyboard focus."""
    def get_selection(self, selection):
        return selection.extract("\n".join(line.text for line in self.lines)), "\n"

    def selection_updated(self, selection):
        self.refresh()

    def render_line(self, y):
        scroll_x, scroll_y = self.scroll_offset
        row = scroll_y + y
        width = self.scrollable_content_region.width
        if row >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        text = Text(self.lines[row].text, style=self.rich_style, no_wrap=True)
        selection = self.text_selection
        if selection is not None and (span := selection.get_span(row)) is not None:
            start, end = span
            text.stylize(self.screen.get_component_rich_style("screen--selection"), start, len(text) if end == -1 else end)
        return Strip(text.render(self.app.console), cell_len(text.plain)).crop_extend(scroll_x, scroll_x + width, self.rich_style).apply_offsets(scroll_x, row)


class ComposerInput(Input):
    def on_blur(self):
        self.app.core.cancel_typing()

    class Multiline(Message):
        def __init__(self, text):
            super().__init__()
            self.text = text

    def _on_paste(self, event):
        if "\n" in event.text or "\r" in event.text:
            start, end = self.selection
            lo, hi = sorted((start, end))
            self.post_message(self.Multiline(self.value[:lo] + event.text + self.value[hi:]))
            event.stop()
        else:
            super()._on_paste(event)


class SettingsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "back", show=False)]

    def __init__(self, group="message"):
        super().__init__()
        self.group = group

    def compose(self):
        app = self.app
        if app.settings_draft is None:
            app.settings_base = dict(app.core.settings.values)
            app.settings_draft = dict(app.settings_base)
        with Vertical(id="dialog"):
            yield Label("设置 · 修改后保存应用；Esc 保留草稿返回", classes="title")
            with Horizontal(classes="filters"):
                groups = [("通用与输入", "input"), ("模拟打字", "typewriting"), ("消息修饰", "formatting"), ("输入提示", "typing"), ("网络与托管", "network"), ("端点与路由", "endpoints"), ("历史记录", "history"), ("日志", "log"), ("OSC", "osc"), ("全部设置", "all"), ("全部消息设置", "message")]
                yield Select(groups, value={"listen": "network"}.get(self.group, self.group), allow_blank=False, id="category")
                yield Input(placeholder="搜索名称或设置键", id="search")
            with VerticalScroll(id="fields"):
                for key, field in FIELDS.items():
                    value = app.settings_draft[key]
                    wid = "field-" + key.replace(".", "-")
                    with Horizontal(classes="setting-row", id="row-" + wid):
                        label = Label(field.label)
                        label.tooltip = f"{key}\n默认：{field.default}\n{field.effect(key)}"
                        yield label
                        if isinstance(field.default, bool):
                            yield Select([("关闭", False), ("开启", True)], value=value, allow_blank=False, id=wid, name=key)
                        elif field.choices:
                            yield Select([(x, x) for x in field.choices], value=value, allow_blank=False, id=wid, name=key)
                        else:
                            yield Input(str(value), id=wid, name=key, type="integer" if isinstance(field.default, int) else "text")
            yield Static("", id="setting-info", markup=False)
            yield Static("", id="settings-error", markup=False)
            with Horizontal(classes="actions"):
                yield Button("保存应用", id="save", variant="primary")
                yield Button("恢复默认", id="reset")
                yield Button("丢弃草稿", id="discard")
                yield Button("重新载入", id="reload")
                yield Button("返回", id="back")

    def on_mount(self):
        self.filter_fields()
        self.set_class(self.size.width < 70, "narrow")

    def on_resize(self, event):
        self.set_class(event.size.width < 70, "narrow")

    def filter_fields(self):
        group = self.query_one("#category", Select).value
        search = self.query_one("#search", Input).value.casefold()
        for key, field in FIELDS.items():
            belongs = self.belongs(key, group)
            self.query_one("#row-field-" + key.replace(".", "-")).display = (bool(search) or belongs) and (not search or search in (key + field.label).casefold())
        network = self.app.core.server
        host = self.app.core.settings['listen.public_host'] or self.app.core.settings['listen.host']
        hint = f"http://{host}:{self.app.core.settings['listen.port']}/live.html · 配置覆盖仅作用 HTTP 响应；浏览器刷新生效" if network.hosting.root else "当前目录未识别 Echo Live；仅提供 WS 服务。"
        self.query_one("#setting-info", Static).update(hint if group == "network" else "悬停字段查看默认值及生效方式；重置仅修改当前分类草稿。")

    @staticmethod
    def belongs(key, group):
        if group == "all":
            return True
        if group == "network":
            return key.startswith("listen.")
        if group == "input":
            return key.startswith("input.") or key == "message.username"
        simulation = {"message.typewriting", "message.typewriting_scheme", "message.print_speed", "message.autopause", "message.autopausestr", "message.autopausetime"}
        if group == "typewriting":
            return key in simulation
        if group == "formatting":
            return key.startswith("message.") and key not in simulation and key != "message.username"
        return key.startswith(str(group) + ".")

    @on(Input.Changed)
    @on(Checkbox.Changed)
    @on(Select.Changed)
    def changed(self, event):
        event.stop()
        widget = event.control
        if widget.id in {"search", "category"}:
            if widget.id == "category" and event.value == "endpoints":
                self.dismiss()
                self.app.call_after_refresh(self.app.open_screen, "endpoints")
                return
            self.filter_fields()
        elif widget.name in FIELDS:
            self.app.settings_draft[widget.name] = event.value

    @on(Button.Pressed)
    async def button(self, event):
        event.stop()
        action = event.button.id
        if action == "save":
            try:
                updates = {k: coerce(k, v) for k, v in self.app.settings_draft.items() if coerce(k, v) != self.app.settings_base[k]}
                await self.app.core.apply(updates, self.app.settings_base)
            except (ValueError, OSError) as exc:
                self.query_one("#settings-error", Static).update(str(exc))
                return
            self.app.settings_draft = None
            self.dismiss()
        elif action in {"reset", "discard", "reload"}:
            values = dict(self.app.settings_draft)
            if action == "reset":
                group = self.query_one("#category", Select).value
                values.update({k: f.default for k, f in FIELDS.items() if self.belongs(k, group)})
            elif action == "reload":
                loaded = Settings(self.app.core.settings.path)
                if loaded.load_error:
                    self.query_one("#settings-error", Static).update(loaded.load_error)
                    return
                values = dict(loaded.values)
                self.app.core.settings.load_error = ""
            else:
                values = dict(self.app.core.settings.values)
            self.app.settings_draft = values
            if action == "discard":
                self.app.settings_base = dict(values)
            for key, value in values.items():
                widget = self.query_one("#field-" + key.replace(".", "-"))
                widget.value = str(value) if isinstance(widget, Input) else value
        else:
            self.action_back()

    def action_back(self):
        self.dismiss()


class ComposeScreen(ModalScreen):
    BINDINGS = [Binding("escape", "back", show=False)]

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("多行消息 · Enter 换行 · Tab 到发送按钮", classes="title")
            yield TextArea(self.app.core.compose_draft, id="multiline", tab_behavior="focus")
            yield Static("", id="compose-error", markup=False)
            with Horizontal(classes="actions"):
                yield Button("发送", id="send", variant="primary")
                yield Button("保留并返回", id="back")

    def on_mount(self):
        self.query_one(TextArea).focus()

    @on(TextArea.Changed)
    def changed(self, event):
        self.app.core.compose_draft = event.text_area.text
        self.app.core.input_changed(event.text_area.text, literal=True)

    @on(Button.Pressed)
    def button(self, event):
        if event.button.id == "send":
            try:
                self.app.core.submit(self.query_one(TextArea).text)
            except ValueError as exc:
                self.query_one("#compose-error", Static).update(str(exc))
                return
            self.app.core.compose_draft = ""
        self.action_back()

    def action_back(self):
        self.app.core.cancel_typing()
        self.dismiss()


class EndpointsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "back", show=False)]

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("端点 · 每个连接独立识别", classes="title")
            yield Select([], id="peer", prompt="选择客户端")
            yield Static("", id="peer-detail", markup=False)
            with VerticalScroll(id="endpoint-controls"):
                with Horizontal(classes="setting-row"):
                    yield Label("定向目标（空格分隔 UUID 或 @名称）")
                    yield Input(" ".join(self.app.core.settings.routing["targets"]), id="targets")
                with Horizontal(classes="setting-row"):
                    yield Label("排除目标")
                    yield Input(" ".join(self.app.core.settings.routing["exclude"]), id="exclude")
                with Horizontal(classes="setting-row"):
                    yield Label("手动职责（当前端点）")
                    yield Select([(x, x) for x in ("auto", "live", "history", "character", "server", "unknown")], value="auto", allow_blank=False, id="role")
                with Horizontal(classes="setting-row"):
                    yield Label("输入提示能力（当前端点）")
                    yield Select([("自动/未知", "auto"), ("支持", "yes"), ("不支持", "no")], value="auto", allow_blank=False, id="typing-capability")
                for key, label in (("software", "软件覆盖"), ("version", "版本覆盖"), ("container", "容器覆盖（如 OBS）")):
                    with Horizontal(classes="setting-row"):
                        yield Label(label)
                        yield Input(placeholder="留空使用识别结果", id="profile-" + key)
                yield Checkbox("按唯一名称保存覆盖（否则仅当前 UUID）", id="persist-name")
                yield Static("历史端独立接收已接受的消息，不依赖字幕端回报。显示策略见 /settings history。", markup=False)
            yield Static("", id="endpoint-error", markup=False)
            with Horizontal(classes="actions"):
                yield Button("保存", id="save", variant="primary")
                yield Button("刷新列表", id="refresh")
                yield Button("返回", id="back")

    def on_mount(self):
        self.refresh_peers()

    def refresh_peers(self):
        peers = self.app.core.hub.peers
        self.query_one("#peer", Select).set_options([(f"{p.profile.name} · {p.profile.role} · {uid[:8]}", uid) for uid, p in peers.items()])

    @on(Select.Changed, "#peer")
    def selected(self, event):
        peer = self.app.core.hub.peers.get(event.value)
        if not peer:
            return
        p = peer.profile
        self.query_one("#peer-detail", Static).update(f"{p.uuid}\n{p.software} · {p.role} · 版本 {p.version or '未知'} · {p.container}\nIP {p.ip} · 隐藏 {p.hidden} · 定向 {p.targeted} · 队列 {peer.queue.qsize()}\n证据：{'；'.join(p.evidence)}\n状态：{p.state}")
        overrides = self.app.core.settings.routing["overrides"]
        override = overrides.get(p.uuid, overrides.get("@" + p.name, {}))
        self.query_one("#persist-name", Checkbox).value = p.uuid not in overrides and "@" + p.name in overrides
        self.query_one("#role", Select).value = override.get("role", "auto")
        self.query_one("#typing-capability", Select).value = "auto" if "typing" not in override else ("yes" if override["typing"] else "no")
        for key in ("software", "version", "container"):
            self.query_one("#profile-" + key, Input).value = override.get(key, "")

    @on(Button.Pressed)
    def button(self, event):
        action = event.button.id
        if action == "back":
            self.dismiss()
        elif action == "refresh":
            self.refresh_peers()
        elif action == "save":
            core = self.app.core
            routing = copy.deepcopy(core.settings.routing)
            try:
                for key in ("targets", "exclude"):
                    selectors = self.query_one("#" + key, Input).value.split()
                    for selector in selectors:
                        if selector != "@__live":
                            core.hub.resolve(selector, "live")
                    routing[key] = selectors
                uid = self.query_one("#peer", Select).value
                peer = core.hub.peers.get(uid)
                if peer:
                    persistent = self.query_one("#persist-name", Checkbox).value
                    selector = "@" + peer.profile.name if persistent else uid
                    if persistent:
                        core.hub.resolve(selector)
                    role = self.query_one("#role", Select).value
                    typing = self.query_one("#typing-capability", Select).value
                    override = {}
                    if role != "auto":
                        override["role"] = role
                    if typing != "auto":
                        override["typing"] = typing == "yes"
                    for key in ("software", "version", "container"):
                        value = self.query_one("#profile-" + key, Input).value.strip()
                        if value:
                            override[key] = value
                    routing["overrides"].pop(uid, None)
                    routing["overrides"].pop("@" + peer.profile.name, None)
                    routing["overrides"][selector] = override
                core.settings.save(routing=routing)
                if peer:
                    core.hub.apply_override(peer)
                self.dismiss()
            except (ValueError, OSError) as exc:
                self.query_one("#endpoint-error", Static).update(str(exc))

    def action_back(self):
        self.dismiss()


class EchoApp(App, inherit_bindings=False):
    TITLE = "EchoLiveTUI"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "interrupt", show=False, priority=True)]
    CSS = """
    Screen { background: #101820; color: #d9e4ed; }
    #top, #route, #context, #enhancements, #hints { height: 1; padding: 0 1; }
    #top { background: #213746; text-style: bold; color: #95e4d0; }
    #route { background: #192b37; }
    #log { height: 1fr; min-height: 8; padding: 0 1; }
    #entry { height: 3; margin: 0; border: solid #3f827b; }
    #context { color: #9eaeb9; }
    #enhancements { color: #e6c18e; }
    #hints { color: #91a7b9; background: #192b37; }
    #error { height: auto; max-height: 2; color: #ffaaa0; padding: 0 1; }
    #suggestions { height: auto; max-height: 3; padding: 0 1; color: #95e4d0; }
    ModalScreen { align: center middle; background: #000000 65%; }
    #dialog { width: 94%; max-width: 110; height: 92%; padding: 1 2; border: solid #3f827b; background: #152530; }
    .title { height: 2; text-style: bold; }
    .filters { height: 4; padding-bottom: 1; }
    .filters Select { width: 20; }
    .filters Input { width: 1fr; }
    #fields, #endpoint-controls { height: 1fr; }
    .setting-row { height: 4; padding-bottom: 1; }
    .setting-row Label { width: 38%; height: 3; content-align: left middle; padding-right: 1; }
    .setting-row Input, .setting-row Select { width: 1fr; }
    .setting-row Checkbox { width: 1fr; border: none; }
    SettingsScreen.narrow .filters { layout: vertical; height: 7; }
    SettingsScreen.narrow .filters Select, SettingsScreen.narrow .filters Input { width: 100%; }
    SettingsScreen.narrow .setting-row { layout: vertical; height: 6; }
    SettingsScreen.narrow .setting-row Label { width: 100%; height: 2; }
    SettingsScreen.narrow .setting-row Input, SettingsScreen.narrow .setting-row Select { width: 100%; }
    SettingsScreen.narrow .actions { height: 6; layout: grid; grid-size: 3; grid-gutter: 0; }
    SettingsScreen.narrow .actions Button { width: 100%; min-width: 6; }
    .actions { height: 3; }
    .actions Button { min-width: 10; width: 1fr; }
    #settings-error, #compose-error, #endpoint-error { height: auto; color: #ffaaa0; max-height: 3; }
    #setting-info { height: auto; max-height: 2; color: #91a7b9; }
    #peer-detail { height: auto; max-height: 6; }
    #multiline { height: 1fr; }
    """

    def __init__(self, settings, cwd, *, start_server=True):
        super().__init__()
        self.core = Core(settings, cwd, self.report)
        self.start_server = start_server
        self.settings_draft = self.settings_base = None
        self.input_history = []
        self.history_index = 0
        self.saved_input = ""
        self.candidates = []
        self.candidate_index = 0
        self.completing = False
        self.pending_logs = []

    def compose(self):
        yield Static("EchoLiveTUI", id="top", markup=False)
        yield Static("", id="route", markup=False)
        yield HistoryLog(id="log", wrap=True, markup=False, max_lines=2000)
        yield Static("", id="error", markup=False)
        yield Static("", id="context", markup=False)
        yield Static("", id="suggestions", markup=False)
        yield ComposerInput(placeholder="输入消息，或 /help", id="entry")
        yield Static("", id="enhancements", markup=False)
        yield Static("", id="hints", markup=False)

    async def on_mount(self):
        self.query_one("#error").display = False
        self.query_one("#suggestions").display = False
        self.query_one("#entry").focus()
        if self.start_server:
            await self.core.start()
        for line in self.pending_logs:
            self.report(line)
        self.pending_logs.clear()
        self.set_interval(.25, self.refresh_status)
        self.refresh_status()

    def report(self, text):
        try:
            self.query_one("#log", RichLog).write(f"{datetime.now():%H:%M}  {text}")
        except Exception:
            self.pending_logs.append(text)

    def refresh_status(self):
        if not self.is_mounted or not self.query("#top"):
            return
        core, s = self.core, self.core.settings
        targets = core.hub.targets()
        names = " + ".join(p.profile.name for p in targets) or "无目标"
        service = "运行" if core.server.site else "未监听"
        self.query_one("#top", Static).update(f"EchoLiveTUI   服务{service} · 字幕端 {sum(p.profile.role == 'live' for p in core.hub.peers.values())}")
        self.query_one("#route", Static).update(f"发送到 {names[:max(10, self.size.width - 34)]} · {'本地托管' if core.server.hosting.root else '独立 WS'} · OSC {'错误' if core.osc_error else '开' if s['osc.enable'] else '关'}")
        entry = self.query_one("#entry", Input)
        self.query_one("#context", Static).update(f"{s['message.username']} → {names[:30]} · 原文 {len(entry.value)} 字")
        quote = "".join(quote_symbols(s)) if s["message.quote"] else "关"
        paren = "仅下一条" if core.paren_once else "开" if s["message.paren"] else "关"
        basic = f"引号{quote[:8]} · 括号{paren}"
        more = f" · 后缀{'开' if s['message.suffix'] else '关'} · 模拟打字 {s['message.typewriting_scheme'] if s['message.typewriting'] else '关'} · {s['message.print_speed']}ms · 停顿{'开' if s['message.autopause'] else '关'}"
        self.query_one("#enhancements", Static).update(basic + (more if self.size.width >= 80 else " · 增强 +4"))
        self.query_one("#hints", Static).update(("Tab/方向键选择 · Enter 补全 · Esc 关闭" if self.completing else "Enter 发送 · / 命令 · /settings 设置") + f"  输入提示：{core.typing_state}")

    @on(Input.Changed, "#entry")
    def input_changed(self, event):
        self.core.input_changed(event.value)
        prefix = event.value[1:]
        self.candidates = ["/" + name for name in HELP if name.startswith(prefix)] if event.value.startswith("/") and " " not in event.value and not event.value.startswith("//") else []
        self.candidate_index = 0
        self.completing = False
        self.show_candidates()
        self.refresh_status()

    def show_candidates(self):
        widget = self.query_one("#suggestions", Static)
        widget.display = bool(self.candidates)
        widget.update("  ".join(("›" if self.completing and i == self.candidate_index else "") + name for i, name in enumerate(self.candidates[:8])))

    async def on_key(self, event):
        if self.screen is not self.screen_stack[0] or self.focused is not self.query_one("#entry"):
            return
        entry = self.query_one("#entry", Input)
        if self.candidates and event.key in {"tab", "up", "down", "escape", "enter"}:
            if event.key == "enter" and not self.completing:
                return
            event.prevent_default()
            event.stop()
            if event.key == "escape":
                self.candidates, self.completing = [], False
            elif event.key == "enter":
                entry.value = self.candidates[self.candidate_index] + " "
                entry.cursor_position = len(entry.value)
                self.candidates, self.completing = [], False
            else:
                self.candidate_index = (self.candidate_index + (-1 if event.key == "up" else 1)) % len(self.candidates) if self.completing else 0
                self.completing = True
            self.show_candidates()
        elif event.key in {"up", "down"} and self.input_history:
            event.prevent_default()
            event.stop()
            if self.history_index == len(self.input_history):
                self.saved_input = entry.value
            self.history_index = max(0, min(len(self.input_history), self.history_index + (-1 if event.key == "up" else 1)))
            entry.value = self.saved_input if self.history_index == len(self.input_history) else self.input_history[self.history_index]
            entry.cursor_position = len(entry.value)

    @on(Input.Submitted, "#entry")
    async def submitted(self, event):
        if self.completing:
            event.input.value = self.candidates[self.candidate_index] + " "
            self.completing = False
            return
        text = event.value
        try:
            command = parse(text)
            result = await execute(self.core, command)
            self.input_history.append(text)
            self.history_index = len(self.input_history)
            event.input.value = ""
            self.query_one("#error").display = False
            if result:
                self.open_screen(result.name, result.args)
        except (ValueError, OSError) as exc:
            self.query_one("#error", Static).update(str(exc))
            self.query_one("#error").display = True

    def open_screen(self, name, args=()):
        self.core.cancel_typing()
        if name == "quit":
            self.exit()
        elif name == "settings":
            self.push_screen(EndpointsScreen() if args and args[0] in {"routing", "endpoints"} else SettingsScreen(args[0] if args else "input"), self.return_focus)
        elif name == "endpoints":
            self.push_screen(EndpointsScreen(), self.return_focus)
        elif name == "compose":
            self.push_screen(ComposeScreen(), self.return_focus)

    def return_focus(self, _=None):
        self.query_one("#entry").focus()
        self.refresh_status()

    @on(ComposerInput.Multiline)
    def pasted(self, event):
        self.core.compose_draft = event.text
        self.open_screen("compose")

    def on_click(self, event):
        if self.screen is self.screen_stack[0]:
            self.query_one("#entry").focus(scroll_visible=False)

    def on_mouse_down(self, event):
        if self.screen is self.screen_stack[0]:
            self.query_one("#entry").focus(scroll_visible=False)

    def action_interrupt(self):
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        if not self.core.settings["input.interrupt_guard"]:
            self.exit()

    async def on_unmount(self):
        await self.core.close()
