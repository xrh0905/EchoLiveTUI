"""Input-first Textual screens; commands and widgets share the same core."""
from __future__ import annotations
import copy
import asyncio
import time
from collections import deque
from datetime import datetime
from rich.text import Text
from rich.table import Table
from rich.cells import cell_len
from textual import events, on
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Size
from textual.message import Message
from textual.strip import Strip
from textual.widgets import Button, Checkbox, Input, Label, Select, RichLog, Static, TextArea, Tabs, Tab
from .commands import HELP, execute, parse
from .config import FIELDS, Settings, coerce
from .core import Core
from .pipeline import quote_symbols
from .lan import interfaces, editor_url, terminal_qr
from .signals import InterruptPolicy
from .editing import SHORTCODES, completion, format_selection, preview
from .symbols import QUOTES, BRACKETS, bracket_symbols
from .capabilities import Capabilities
from .playback import DeliveryUnavailable
from .echolive_panel import EchoLivePanel
from .history import MessageReport
from . import __version__


class DisplayText(Static):
    ALLOW_SELECT = False

    def get_selection(self, selection):
        # Textual may include the drag end widget even with ALLOW_SELECT=False.
        return None

    @property
    def text_selection(self):
        return None


class ActionLink(DisplayText, can_focus=False):
    def on_click(self, event):
        event.stop()
        self.app.open_screen(self.name)


class HistoryLog(RichLog, can_focus=False):
    """Wrapped, mouse-selectable records which never take keyboard focus."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = deque()
        self.record_lines = deque()

    def write(self, content, *args, **kwargs):
        if not self._size_known:
            return super().write(content, *args, **kwargs)
        before = len(self.lines)
        super().write(content, *args, **kwargs)
        self.records.append(content)
        self.record_lines.append(len(self.lines) - before)
        self.trim_records()
        return self

    def trim_records(self):
        limit = self.app.core.settings["history.max_entries"]
        if len(self.records) <= limit:
            return
        removed = 0
        while len(self.records) > limit:
            self.records.popleft()
            removed += self.record_lines.popleft()
        self.lines = self.lines[removed:]
        self._start_line += removed
        self._line_cache.clear()
        self.virtual_size = Size(self.virtual_size.width, len(self.lines))
        self.refresh()

    def clear(self):
        self.records.clear()
        self.record_lines.clear()
        return super().clear()

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
        text = Text.assemble(*[(segment.text, segment.style) for segment in self.lines[row]], style=self.rich_style, no_wrap=True)
        selection = self.text_selection
        if selection is not None and (span := selection.get_span(row)) is not None:
            start, end = span
            text.stylize(self.screen.get_component_rich_style("screen--selection"), start, len(text) if end == -1 else end)
        return Strip(text.render(self.app.console), cell_len(text.plain)).crop_extend(scroll_x, scroll_x + width, self.rich_style).apply_offsets(scroll_x, row)


class ComposerInput(Input):
    class SelectionChanged(Message):
        pass

    def _watch_selection(self, selection):
        super()._watch_selection(selection)
        if self.is_mounted:
            self.post_message(self.SelectionChanged())

    async def _on_key(self, event):
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Multiline(self.value))
            return
        await super()._on_key(event)

    def on_blur(self):
        self.app.core.cancel_typing()

    class Multiline(Message):
        def __init__(self, text):
            super().__init__()
            self.text = text

    def _on_paste(self, event):
        # Textual dispatches handlers along the MRO itself. Stop the default
        # dispatch after explicitly delegating, otherwise Input inserts twice.
        event.prevent_default()
        if "\n" in event.text or "\r" in event.text:
            start, end = self.selection
            lo, hi = sorted((start, end))
            self.post_message(self.Multiline(self.value[:lo] + event.text + self.value[hi:]))
            event.stop()
        else:
            super()._on_paste(event)

    def action_paste(self):
        self._on_paste(events.Paste(self.app.clipboard))


class FormatLink(DisplayText, can_focus=False):
    class Apply(Message):
        def __init__(self, code, link):
            super().__init__()
            self.code = code
            self.link = link

    def on_click(self, event):
        event.stop()
        self.post_message(self.Apply(self.name, self))


class FormatBar(Horizontal):
    def compose(self):
        for label, code in (("粗体", "@b"), ("斜体", "@i"), ("下划线", "@u"), ("删除线", "@s"), ("重置", "@r"), ("放大", "@+"), ("缩小", "@-"), ("颜色", "@[#66ccff]"), ("喊叫", "@shout"), ("彩虹", "@rainbow")):
            link = FormatLink(label, name=code)
            link.tooltip = SHORTCODES[code] + " · " + code + " · 应用于选中文字，未选中时插入短码"
            yield link

    def on_mount(self):
        self.refresh_visibility()
        self.set_interval(.1, self.refresh_visibility)

    def refresh_visibility(self):
        if not self.app.is_running or not self.app.query("#context"):
            return
        mode = self.app.core.settings["input.format_bar"]
        if isinstance(self.app.active_page, ComposeScreen) and self.app.active_page in self.ancestors:
            editor = self.app.active_page.query_one(TextArea)
            row, column = editor.cursor_location
            cursor = sum(len(line) + 1 for line in editor.text.split("\n")[:row]) + column
            text = editor.text
        else:
            editor = self.app.query_one("#entry", Input)
            text, cursor = editor.value, editor.cursor_position
        _, start = completion(text, cursor)
        self.display = mode == "always" or (mode == "context" and bool(editor.selected_text or (self.id != "main-format" and start < cursor)))
        if self.id == "main-format":
            self.display = self.display and not self.app.candidates
            self.app.query_one("#context").display = not self.display and not self.app.candidates


class ComposeArea(TextArea):
    BINDINGS = [Binding("ctrl+space", "complete_shortcode", show=False, priority=True)]

    def _on_paste(self, event):
        if self.read_only:
            event.prevent_default()
            event.stop()

    def _on_key(self, event):
        if self.has_class("sending") and event.key not in {"escape", "tab", "shift+tab", "ctrl+c"}:
            event.prevent_default()
            event.stop()

    def _on_mouse_down(self, event):
        if self.has_class("sending"):
            event.prevent_default()
            event.stop()

    def action_complete_shortcode(self):
        if self.read_only:
            return
        row, column = self.cursor_location
        offset = sum(len(line) + 1 for line in self.text.split("\n")[:row]) + column
        choices, start = completion(self.text, offset)
        if choices and self.app.core.settings["input.suggestions"]:
            self.replace(choices[0], (row, column - (offset - start)), (row, column), maintain_selection_offset=False)


class PageTabs(Tabs):
    """Equal-width labels above a full-width navigation band."""
    def __init__(self, *tabs, **kwargs):
        self.labels = {tab.id: tab.label.plain for tab in tabs}
        super().__init__(*tabs, **kwargs)

    @on(Tabs.TabActivated)
    def mark_active(self, event):
        for tab in self.query(Tab):
            label = self.labels.setdefault(tab.id, tab.label.plain)
            tab.label = Text(f"[{label}]" if tab is event.tab else label)


class HistoryPage(Vertical):
    """A page mounted in the history region, preserving the main chrome."""
    BINDINGS = [Binding("escape", "back", show=False)]

    def dismiss(self):
        self.app.close_page(self)

    def action_back(self):
        self.dismiss()


class SettingsScreen(HistoryPage):
    BINDINGS = [Binding("escape", "back", show=False)]

    def __init__(self, group="all"):
        super().__init__()
        self.group = "all" if group == "message" else group

    def compose(self):
        app = self.app
        if app.settings_draft is None:
            app.settings_base = dict(app.core.settings.values)
            app.settings_draft = dict(app.settings_base)
        with Vertical(id="dialog"):
            tabs = [Tab("TUI 设置", id="tui-settings")]
            if app.core.capabilities & Capabilities.HAS_ECHO_LIVE:
                tabs.append(Tab("Echo-Live 设置", id="echo-settings"))
            yield PageTabs(*tabs, classes="page-tabs")
            with Vertical(id="tui-settings-content"):
                with Horizontal(classes="filters"):
                    groups = [("所有设置", "all"), ("通用与输入", "input"), ("模拟打字", "typewriting"), ("消息修饰", "formatting"), ("输入提示", "typing"), ("网络与托管", "network"), ("端点与路由", "endpoints"), ("历史记录", "history"), ("日志", "log"), ("OSC", "osc")]
                    yield Select(groups, value={"listen": "network"}.get(self.group, self.group), allow_blank=False, id="category")
                    yield Input(placeholder="搜索名称或设置键", id="search")
                with VerticalScroll(id="fields"):
                    for key, field in FIELDS.items():
                        if key in {"message.quote_close", "message.username_bracket_close"}:
                            continue
                        value = app.settings_draft[key]
                        wid = "field-" + key.replace(".", "-")
                        with Horizontal(classes="setting-row", id="row-" + wid):
                            label = Label("引号（左 / 右）" if key == "message.quote_open" else "姓名括号（左 / 右）" if key == "message.username_bracket_open" else field.label)
                            label.tooltip = f"{key}\n默认：{field.default}\n{field.effect(key)}"
                            yield label
                            if key in {"message.quote_open", "message.username_bracket_open"}:
                                close_key = key.replace("_open", "_close")
                                with Horizontal(classes="quote-pair"):
                                    yield Input(str(value), id=wid, name=key, placeholder="左引号")
                                    yield Input(str(app.settings_draft[close_key]), id="field-" + close_key.replace(".", "-"), name=close_key, placeholder="右符号")
                            elif isinstance(field.default, bool):
                                yield Select([("关闭", False), ("开启", True)], value=value, allow_blank=False, id=wid, name=key)
                            elif field.choices:
                                labels = {"context": "选区 / 短码时显示", "always": "始终显示", "never": "始终隐藏"} if key == "input.format_bar" else {"en": '\" \"', "cn": "“ ”", "jp": "「 」", "square": "【 】", "round": "（ ）", "corner": "「 」", "custom": "自定义"}
                                yield Select([(labels.get(x, x), x) for x in field.choices], value=value, allow_blank=False, id=wid, name=key)
                            else:
                                yield Input(str(value), id=wid, name=key, type="integer" if isinstance(field.default, int) else "text")
                yield EndpointPanel(id="endpoint-panel")
                yield Static("", id="setting-info", markup=False)
                yield Static("", id="settings-error", markup=False)
                with Horizontal(classes="actions"):
                    yield Button("保存", id="save", variant="primary", tooltip="保存并应用草稿")
                    yield Button("重置", id="reset", tooltip="恢复当前分类的默认值")
                    yield Button("丢弃", id="discard", tooltip="丢弃设置草稿")
                    yield Button("重载", id="reload", tooltip="从配置文件重新载入")
                    yield Button("连接", id="lan-entry", tooltip="局域网 editor 入口 / 二维码")
                    yield Button("刷新列表", id="endpoint-refresh")
                    yield Button("返回", id="back")

            if app.core.capabilities & Capabilities.HAS_ECHO_LIVE:
                yield EchoLivePanel(id="echo-settings-panel")

    def on_mount(self):
        self.sync_pairs()
        self.filter_fields()
        self.set_class(self.size.width < 70, "narrow")
        self.query_one("#search", Input).focus()

    def on_resize(self, event):
        self.set_class(event.size.width < 70, "narrow")

    @on(Tabs.TabActivated)
    def switch_tab(self, event):
        if not self.query("#tui-settings-content"):
            return
        tui = event.tab.id == "tui-settings"
        self.query_one("#tui-settings-content").display = tui
        if self.query(EchoLivePanel):
            self.query_one(EchoLivePanel).display = not tui

    def filter_fields(self):
        group = self.query_one("#category", Select).value
        search = self.query_one("#search", Input).value.casefold()
        for key, field in FIELDS.items():
            if key in {"message.quote_close", "message.username_bracket_close"}:
                continue
            belongs = self.belongs(key, group)
            searchable = key + field.label + (key.replace("_open", "_close") if key.endswith("_open") else "")
            self.query_one("#row-field-" + key.replace(".", "-")).display = (bool(search) or belongs) and (not search or search in searchable.casefold())
        self.query_one("#fields").display = group != "endpoints"
        self.query_one("#endpoint-panel").display = group == "endpoints"
        self.query_one("#lan-entry").display = group == "network"
        self.query_one("#endpoint-refresh").display = group == "endpoints"
        network = self.app.core.server
        host = self.app.core.settings['listen.public_host'] or self.app.core.settings['listen.host']
        hint = f"http://{host}:{self.app.core.settings['listen.port']}/live.html · 配置覆盖仅作用 HTTP 响应；浏览器刷新生效" if network.hosting.root else "当前目录未识别 Echo Live；仅提供 WS 服务。"
        self.query_one("#setting-info", Static).update(hint if group == "network" else "悬停字段查看默认值及生效方式；重置仅修改当前分类草稿。")

    @staticmethod
    def belongs(key, group):
        if group == "all":
            return True
        if group == "network":
            return key.startswith(("listen.", "lan."))
        if group == "input":
            return key.startswith("input.") or key == "message.username"
        simulation = {"message.typewriting", "message.typewriting_scheme", "message.print_speed", "message.autopause", "message.autopausestr", "message.autopausetime"}
        if group == "typewriting":
            return key in simulation or key == "lan.typewriting"
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
            self.filter_fields()
        elif widget.name in FIELDS:
            self.app.settings_draft[widget.name] = event.value
            if widget.name in {"message.quote_style", "message.username_bracket_style"}:
                self.sync_pairs()

    def sync_pairs(self):
        for prefix, presets in (("message.quote", QUOTES), ("message.username_bracket", BRACKETS)):
            style = self.app.settings_draft[prefix + "_style"]
            for suffix, symbol in zip(("_open", "_close"), presets.get(style, (None, None))):
                key = prefix + suffix
                control = self.query_one("#field-" + key.replace(".", "-"), Input)
                control.disabled = style != "custom"
                if symbol is not None:
                    self.app.settings_draft[key] = symbol
                    # Avoid stale Changed events when resetting an entire draft.
                    with control.prevent(Input.Changed):
                        control.value = symbol

    @on(Button.Pressed)
    async def button(self, event):
        event.stop()
        action = event.button.id
        if action == "lan-entry":
            self.app.open_screen("connect")
            return
        if action == "endpoint-refresh":
            self.query_one(EndpointPanel).refresh_peers()
            return
        if action == "save":
            if self.query_one("#category", Select).value == "endpoints":
                if self.query_one(EndpointPanel).save():
                    self.query_one("#settings-error", Static).update("端点设置已保存")
                return
            try:
                updates = {k: coerce(k, v) for k, v in self.app.settings_draft.items() if coerce(k, v) != self.app.settings_base[k]}
                await self.app.core.apply(updates, self.app.settings_base)
            except (ValueError, OSError) as exc:
                self.query_one("#settings-error", Static).update(str(exc))
                return
            self.app.settings_draft = None
            self.dismiss()
        elif action in {"reset", "discard", "reload"}:
            if self.query_one("#category", Select).value == "endpoints":
                panel = self.query_one(EndpointPanel)
                panel.query_one("#targets", Input).value = "" if action == "reset" else " ".join(self.app.core.settings.routing["targets"])
                panel.query_one("#exclude", Input).value = "" if action == "reset" else " ".join(self.app.core.settings.routing["exclude"])
                return
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
                with widget.prevent(Input.Changed, Select.Changed):
                    widget.value = str(value) if isinstance(widget, Input) else value
            self.sync_pairs()
        else:
            self.action_back()

    def action_back(self):
        self.dismiss()


class ComposeScreen(HistoryPage):
    BINDINGS = [Binding("escape", "back", show=False)]

    def __init__(self):
        super().__init__()
        self.send_task = None
        self.next_row = 0
        self.paused = False
        self.pending_playback = []
        self.current_row = 0
        self.delivery_error = None

    def compose(self):
        with Vertical(id="dialog"):
            yield PageTabs(Tab("演出", id="performance-tab"), classes="page-tabs")
            yield DisplayText("", id="performance-state", markup=False)
            yield ComposeArea(self.app.core.compose_draft, id="multiline", tab_behavior="focus", show_line_numbers=True)
            yield Static("", id="compose-suggestions", markup=False)
            yield FormatBar(id="compose-format")
            yield Static("", id="compose-preview", markup=False)
            yield Static("", id="compose-error", markup=False)
            with Horizontal(id="performance-controls"):
                yield Checkbox("逐行发送", id="line-mode", compact=True)
                yield Label("间隔 ms")
                yield Input("1000", id="line-interval", type="integer", disabled=True, compact=True)
                yield Static("", classes="action-spacer")
                yield Button("发送", id="send", variant="primary")
                yield Button("取消/返回", id="back")

    def on_mount(self):
        self.query_one(TextArea).focus()
        self.query_one("#compose-preview", Static).update(preview(self.app.core.compose_draft, self.app.core.settings))
        self.query_one("#compose-preview").display = self.app.core.settings["input.preview"]

    @on(TextArea.Changed)
    def changed(self, event):
        self.app.core.compose_draft = event.text_area.text
        self.app.core.input_changed(event.text_area.text, literal=True)
        self.query_one("#compose-preview", Static).update(preview(event.text_area.text, self.app.core.settings))
        row, column = event.text_area.cursor_location
        offset = sum(len(line) + 1 for line in event.text_area.text.split("\n")[:row]) + column
        choices, _ = completion(event.text_area.text, offset)
        hint = self.query_one("#compose-suggestions", Static)
        hint.display = bool(choices) and self.app.core.settings["input.suggestions"]
        hint.update("Ctrl+Space 补全 · " + "  ".join(code + " " + SHORTCODES[code] for code in choices))

    @on(Checkbox.Changed, "#line-mode")
    def line_mode_changed(self, event):
        self.query_one("#line-interval", Input).disabled = not event.value

    def set_running(self, running):
        area = self.query_one(TextArea)
        area.read_only = running
        self.query_one("#line-mode", Checkbox).disabled = running
        self.query_one("#line-interval", Input).disabled = running or not self.query_one("#line-mode", Checkbox).value
        button = self.query_one("#send", Button)
        # Pause must remain responsive even immediately after starting.
        button.remove_class("-active")
        button.label = "暂停" if running else "继续发送" if self.paused else "发送"
        area.set_class(running, "sending")

    def show_error(self, error):
        self.delivery_error = error if isinstance(error, DeliveryUnavailable) else None
        self.query_one("#compose-error", Static).update(str(error))

    def refresh_delivery_state(self):
        error = self.delivery_error
        if error is None or self.send_task:
            return
        targets = self.app.core.hub.targets()
        if error.endpoints:
            endpoints = {receipt.endpoint for receipt in self.pending_playback if receipt.error} or set(error.endpoints)
            recovered = all(any(peer.profile.uuid == uid for peer in targets) or (bool(name) and sum(peer.profile.name == name for peer in targets) == 1) for uid, name in endpoints)
        else:
            recovered = self.app.core.has_output
        if recovered:
            self.delivery_error = None
            self.query_one("#compose-error", Static).update("")
            self.app.core.hub.release_playback(self.pending_playback)
            self.pending_playback = []
            self.query_one("#performance-state", Static).update("已暂停 · 从游标所在行继续" if self.paused else "")
            self.set_running(False)

    async def send_lines(self, interval):
        area = self.query_one(TextArea)
        lines = area.text.split("\n")
        try:
            start_row = area.cursor_location[0]
            if self.pending_playback:
                if self.next_row >= len(lines) and area.cursor_location == (len(lines) - 1, len(lines[-1])):
                    start_row = self.next_row
                completed_at = await self.app.core.wait_for_printing(self.pending_playback)
                await asyncio.sleep(max(0, completed_at + interval - time.monotonic()))
                self.pending_playback = []
            for row in range(start_row, len(lines)):
                self.next_row = row
                if not lines[row].strip():
                    continue
                area.move_cursor((row, 0))
                self.current_row = row
                self.app.core.submit(lines[row], playback=self.pending_playback)
                self.next_row = row + 1
                self.query_one("#performance-state", Static).update(f"正在发送 {row + 1}/{len(lines)}")
                completed_at = await self.app.core.wait_for_printing(self.pending_playback)
                if any(line.strip() for line in lines[row + 1:]):
                    self.query_one("#performance-state", Static).update(f"行间等待 · {row + 1}/{len(lines)}")
                    await asyncio.sleep(max(0, completed_at + interval - time.monotonic()))
                self.pending_playback = []
            self.app.core.compose_draft = ""
            self.dismiss()
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError) as exc:
            self.paused = True
            area.move_cursor((min(self.current_row, len(lines) - 1), 0))
            self.show_error(exc)
            self.query_one("#performance-state", Static).update("发送失败，已暂停")
        finally:
            self.send_task = None
            if self.is_mounted and self.app.active_page is self and self.query(TextArea):
                self.set_running(False)

    @on(Button.Pressed)
    async def button(self, event):
        event.stop()
        if event.button.id == "send":
            if self.send_task:
                task = self.send_task
                self.paused = True
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                area = self.query_one(TextArea)
                row = min(self.next_row, area.document.line_count - 1)
                area.move_cursor((row, len(area.document.get_line(row)) if self.next_row >= area.document.line_count else 0))
                area.focus()
                self.query_one("#performance-state", Static).update("已暂停 · 从游标所在行继续")
                return
            self.delivery_error = None
            self.query_one("#compose-error", Static).update("")
            if self.query_one("#line-mode", Checkbox).value:
                try:
                    interval = int(self.query_one("#line-interval", Input).value)
                    if not 0 <= interval <= 3600000:
                        raise ValueError
                    if not self.query_one(TextArea).text.strip():
                        raise ValueError("消息不能为空")
                except ValueError as exc:
                    self.show_error(ValueError(str(exc) or "行间隔需为 0–3600000 ms"))
                    return
                self.query_one("#compose-error", Static).update("")
                if any(receipt.error for receipt in self.pending_playback):
                    self.app.core.hub.release_playback(self.pending_playback)
                    self.pending_playback = []
                self.set_running(True)
                self.send_task = asyncio.create_task(self.send_lines(interval / 1000))
                return
            try:
                self.app.core.submit(self.query_one(TextArea).text)
            except (ValueError, OSError) as exc:
                self.show_error(exc)
                return
            self.app.core.compose_draft = ""
        self.action_back()

    def action_back(self):
        self.app.core.cancel_typing()
        self.dismiss()

    def on_unmount(self):
        if self.send_task:
            self.send_task.cancel()
        self.app.core.cancel_typing()
        self.app.core.hub.release_playback(self.pending_playback)


class EndpointPanel(Vertical):
    def compose(self):
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
                yield Select([("自动（未知按支持）", "auto"), ("支持", "yes"), ("不支持", "no")], value="auto", allow_blank=False, id="typing-capability")
            for key, label in (("software", "软件覆盖"), ("version", "版本覆盖"), ("container", "容器覆盖（如 OBS）")):
                with Horizontal(classes="setting-row"):
                    yield Label(label)
                    yield Input(placeholder="留空使用识别结果", id="profile-" + key)
            yield Checkbox("按唯一名称保存覆盖（否则仅当前 UUID）", id="persist-name", compact=True)
            yield Static("历史端独立接收已接受的消息，不依赖字幕端回报。显示策略见 /settings history。", markup=False)
        yield Static("", id="endpoint-error", markup=False)

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

    def save(self):
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
            return True
        except (ValueError, OSError) as exc:
            self.query_one("#endpoint-error", Static).update(str(exc))

        return False

class EndpointsScreen(HistoryPage):
    BINDINGS = [Binding("escape", "back", show=False)]

    def compose(self):
        with Vertical(id="dialog"):
            yield PageTabs(Tab("端点与路由", id="endpoint-tab"), classes="page-tabs")
            yield EndpointPanel(id="endpoint-panel")
            with Horizontal(classes="actions"):
                yield Button("保存", id="save", variant="primary")
                yield Button("刷新列表", id="endpoint-refresh")
                yield Button("返回", id="back")

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        if event.button.id == "back":
            self.dismiss()
        elif event.button.id == "save" and self.query_one(EndpointPanel).save():
            self.dismiss()
        elif event.button.id == "endpoint-refresh":
            self.query_one(EndpointPanel).refresh_peers()

    def action_back(self):
        self.dismiss()

    def on_mount(self):
        self.query_one("#peer", Select).focus()


class LanScreen(HistoryPage):
    BINDINGS = [Binding("escape", "back", show=False)]

    def compose(self):
        with Vertical(id="dialog"):
            yield PageTabs(Tab("局域网发送", id="lan-tab"), classes="page-tabs")
            with Horizontal(id="lan-content"):
                with VerticalScroll(id="lan-left"):
                    yield DisplayText("启用连接后显示二维码", id="lan-qr", markup=False)
                with VerticalScroll(id="lan-right"):
                    yield Select([], id="lan-interface", prompt="选择当前启用的网卡")
                    yield Input(self.app.core.settings["lan.host"], id="lan-manual", placeholder="本机 IPv4；留空自动选择")
                    yield Static("", id="lan-state", markup=False)
                    yield Static("", id="lan-url", markup=False)
                    yield Static("", id="lan-clients", markup=False)
                    yield Static("", id="lan-error", markup=False)
            with Horizontal(classes="actions"):
                yield Button("启用/应用", id="lan-enable", variant="primary")
                yield Button("关闭", id="lan-disable")
                yield Button("复制 URL", id="lan-copy")
                yield Button("刷新网卡", id="lan-refresh")
                yield Button("返回", id="lan-back")

    async def on_mount(self):
        await self.refresh_interfaces()
        self.refresh_state()
        self.query_one("#lan-interface").focus()
        self.set_interval(1, self.refresh_state)

    async def refresh_interfaces(self):
        available = await asyncio.to_thread(interfaces)
        self.query_one("#lan-interface", Select).set_options([(f"{item.name} · {item.address}", item.address) for item in available])
        configured = self.app.core.settings["lan.host"]
        if available:
            self.query_one("#lan-interface", Select).value = configured if configured in {i.address for i in available} else available[0].address

    @on(Select.Changed, "#lan-interface")
    def interface_selected(self, event):
        if event.value is not Select.NULL:
            self.query_one("#lan-manual", Input).value = str(event.value)

    def refresh_state(self):
        if not self.query("#lan-state"):
            return
        core = self.app.core
        address = core.server.lan_address
        url = editor_url(*address) if address else ""
        self.query_one("#lan-state", Static).update("远程监听已启用；访问链接即可访问编辑器。" if address else "远程发送未启用。需在完整 Echo Live 目录中启动。")
        self.query_one("#lan-url", Static).update(url)
        qr = self.query_one("#lan-qr", Static)
        if getattr(self, "qr_url", None) != url:
            qr.update(terminal_qr(url) if url else "启用连接后显示二维码")
            self.qr_url = url
        clients = [p.profile for p in core.hub.peers.values() if p.profile.role == "server"]
        self.query_one("#lan-clients", Static).update("已连接发送端：" + ("；".join(f"{p.name} · {p.ip}" for p in clients) or "无"))

    @on(Button.Pressed)
    async def button(self, event):
        event.stop()
        action = event.button.id
        try:
            if action == "lan-enable":
                await self.app.core.apply({"lan.enable": True, "lan.host": self.query_one("#lan-manual", Input).value.strip()})
            elif action == "lan-disable":
                await self.app.core.apply({"lan.enable": False})
            elif action == "lan-copy" and self.app.core.server.lan_address:
                self.app.copy_to_clipboard(editor_url(*self.app.core.server.lan_address))
            elif action == "lan-refresh":
                await self.refresh_interfaces()
            elif action == "lan-back":
                self.dismiss()
                return
            self.query_one("#lan-error", Static).update("")
        except (ValueError, OSError) as exc:
            self.query_one("#lan-error", Static).update(str(exc))
        self.refresh_state()

    def action_back(self):
        self.dismiss()


class EchoApp(App, inherit_bindings=False):
    TITLE = "EchoLiveTUI"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "interrupt", show=False, priority=True)]
    CSS = """
    Screen { background: #101820; color: #d9e4ed; }
    Button { height: 1; min-height: 1; min-width: 0; width: auto; border: none !important; padding: 0; margin-left: 1; }
    Checkbox { height: 1; border: none !important; padding: 0; }
    #top { height: 1; background: #213746; color: #95e4d0; }
    #brand { width: auto; height: 1; padding-left: 1; text-style: bold; }
    #route { width: 1fr; height: 1; content-align: center middle; }
    #environment { width: auto; height: 1; padding-right: 1; content-align: right middle; }
    #history-region { height: 1fr; min-height: 5; }
    #log { height: 1fr; padding: 0 1; scrollbar-size: 1 1; overflow-x: hidden; overflow-y: scroll; }
    #workspace { height: auto; }
    #context, #suggestions, #preview { height: 1; padding: 0 1; overflow: hidden; }
    #context { color: #9eaeb9; }
    #suggestions { color: #95e4d0; }
    #preview { color: #d9e4ed; }
    #entry { height: 1; min-height: 1; margin: 0; padding: 0 1; border: none; background: #192b37; }
    #submission { height: 1; }
    #hints { height: 1; width: 1fr; padding-left: 1; color: #91a7b9; }
    #typing-state { height: 1; width: auto; padding-right: 1; color: #95e4d0; }
    #footer { height: 1; background: #192b37; }
    #enhancements { height: 1; width: 1fr; padding-left: 1; color: #e6c18e; }
    ActionLink { width: auto; height: 1; margin-right: 1; color: #95e4d0; text-style: underline; }
    ActionLink:hover { background: #3f827b; }
    #error { height: auto; max-height: 2; color: #ffaaa0; padding: 0 1; }
    FormatBar { height: 1; layout: horizontal; background: #192b37; overflow: hidden; }
    FormatLink { width: auto; margin-right: 1; height: 1; color: #95e4d0; }
    FormatLink:hover { background: #3f827b; text-style: bold; }
    HistoryPage { width: 100%; height: 100%; background: #152530; }
    #dialog { width: 100%; height: 100%; padding: 0 1; }
    .page-tabs { height: 2; width: 100%; }
    .page-tabs #tabs-list, .page-tabs #tabs-list-bar { width: 100%; }
    .page-tabs Tab { width: 1fr; padding: 0; content-align: center middle; }
    .page-tabs Underline { height: 1; }
    .page-tabs .underline--bar { color: #67c8b1; background: #334c5c; }
    #performance-state { width: 100%; height: 1; content-align: right middle; }
    #tui-settings-content, EchoLivePanel { height: 1fr; }
    #echo-settings-panel { display: none; }
    #echo-settings-content { height: 1fr; }
    #echo-sidebar { width: 15; padding-top: 1; border-right: solid #334c5c; }
    #echo-sidebar Button { margin: 0 0 1 0; width: 100%; }
    #echo-settings-main { width: 1fr; height: 1fr; padding: 1 2; scrollbar-size: 1 1; }
    #echo-font-pane, #echo-version-pane { height: auto; }
    #echo-font-pane Static, #echo-version-pane Static { height: auto; margin-bottom: 1; }
    #echo-font-pane Label { height: 1; }
    #echo-settings-error { height: auto; max-height: 2; color: #e6c18e; }
    .filters { height: 3; }
    .filters Select { width: 22; }
    .filters Input { width: 1fr; }
    #fields, #endpoint-controls { height: 1fr; scrollbar-size: 1 1; }
    #endpoint-panel { height: 1fr; }
    #endpoint-refresh, #lan-entry { height: 1; }
    #lan-content { height: 1fr; }
    #lan-left, #lan-right { width: 1fr; height: 100%; scrollbar-size: 1 1; }
    #lan-left { align: center middle; }
    #lan-right { padding-left: 1; }
    #lan-state, #lan-url, #lan-clients, #lan-error { height: auto; margin-bottom: 1; }
    #lan-error { color: #ffaaa0; }
    #lan-qr { width: auto; height: auto; background: white; color: black; }
    .quote-pair { width: 1fr; height: 3; }
    .quote-pair Input { width: 1fr; }
    .quote-pair Input:first-child { margin-right: 1; }
    .setting-row { height: 4; padding-bottom: 1; }
    .setting-row Label { width: 38%; height: 3; content-align: left middle; padding-right: 1; }
    .setting-row Input, .setting-row Select { width: 1fr; }
    .setting-row Checkbox { width: 1fr; border: none; }
    SettingsScreen.narrow .filters Select { width: 16; }
    SettingsScreen.narrow #setting-info { display: none; }
    SettingsScreen.narrow .setting-row { layout: vertical; height: 5; }
    SettingsScreen.narrow .setting-row Label { width: 100%; height: 1; }
    SettingsScreen.narrow .setting-row Input, SettingsScreen.narrow .setting-row Select { width: 100%; }
    SettingsScreen.narrow .quote-pair { width: 100%; }
    SettingsScreen.narrow .quote-pair Input { width: 1fr; }
    .actions { height: 1; dock: bottom; align-horizontal: right; }
    .actions Button { min-width: 0; width: auto; }
    #settings-error, #compose-error, #endpoint-error { height: auto; color: #ffaaa0; max-height: 2; }
    #setting-info { height: 1; color: #91a7b9; }
    #peer-detail { height: auto; max-height: 4; }
    #multiline { height: 1fr; min-height: 3; scrollbar-size: 1 1; }
    #compose-preview, #compose-suggestions { height: 1; overflow: hidden; }
    #performance-controls { height: 1; dock: bottom; }
    .action-spacer { width: 1fr; height: 1; }
    #performance-controls Checkbox { width: auto; margin-right: 1; }
    #performance-controls Label { width: auto; height: 1; margin-right: 1; }
    #line-interval { width: 9; height: 1; min-height: 1; border: none !important; padding: 0 1; margin-right: 1; }
    ComposeArea.sending .text-area--cursor-line { background: #31554a; }
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
        self.active_page = None

    def compose(self):
        with Horizontal(id="top"):
            yield DisplayText(f"Echo-Live TUI v{__version__}", id="brand", markup=False)
            yield DisplayText("", id="route", markup=False)
            yield DisplayText("", id="environment", markup=False)
        with Vertical(id="history-region"):
            yield HistoryLog(id="log", wrap=True, markup=False, min_width=1)
        with Vertical(id="workspace"):
            yield Static("", id="error", markup=False)
            yield DisplayText("", id="context", markup=False)
            yield Static("", id="suggestions", markup=False)
            yield FormatBar(id="main-format")
            yield Static("", id="preview", markup=False)
            yield ComposerInput(placeholder="输入消息，或 /help", id="entry")
            with Horizontal(id="submission"):
                yield DisplayText("", id="hints", markup=False)
                yield DisplayText("", id="typing-state", markup=False)
        with Horizontal(id="footer"):
            yield DisplayText("", id="enhancements", markup=False)
            yield ActionLink("连接", name="connect", id="pair-link")
            yield ActionLink("端点", name="endpoints", id="endpoints-link")
            yield ActionLink("多行", name="compose", id="compose-link")
            yield ActionLink("设置", name="settings", id="settings-link")
            yield ActionLink("退出", name="quit", id="exit-link")

    async def on_mount(self):
        self.query_one("#error").display = False
        loop = asyncio.get_running_loop()
        self.interrupt_policy = InterruptPolicy(self.core.settings, lambda: loop.call_soon_threadsafe(self.exit)).install()
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
            name, separator, content = text.partition("：")
            if not separator or len(name) > 32:
                name, content = "EchoLiveTUI", text
            record = Table.grid(padding=(0, 1), expand=True)
            record.add_column(width=8, no_wrap=True)
            record.add_column(min_width=4, max_width=max(4, min(12, (self.size.width - 2) // 6)), no_wrap=True, overflow="ellipsis")
            record.add_column(ratio=1, overflow="fold")
            record.add_row(f"{datetime.now():%H:%M:%S}", Text(name, style="underline" if not isinstance(text, MessageReport) else ""), Text(content))
            self.query_one("#log", RichLog).write(record)
        except Exception:
            self.pending_logs.append(text)

    def refresh_status(self):
        if not self.is_mounted or not self.is_running or not self.query("#top"):
            return
        core, s = self.core, self.core.settings
        targets = core.hub.targets()
        routing = s.routing
        names = "全部" if not routing["targets"] and not routing["exclude"] else " + ".join(p.profile.name for p in targets) or "无目标"
        self.query_one("#route", Static).update(f"OSC {'错误' if core.osc_error else '启用' if s['osc.enable'] else '禁用'} 发送到 {names}")
        roles = [p.profile.role for p in core.hub.peers.values()]
        self.query_one("#environment", Static).update(f"L{roles.count('live')} H{roles.count('history')} E{roles.count('server')} - {'承载模式' if core.capabilities & Capabilities.HAS_ECHO_LIVE else '独立模式'}")
        self.query_one(HistoryLog).trim_records()
        entry = self.query_one("#entry", Input)
        preview_key = (entry.value, tuple(s.values.items()))
        if getattr(self, "preview_key", None) != preview_key:
            self.preview_key = preview_key
            display = self.query_one("#preview", Static)
            display.display = s["input.preview"]
            if display.display:
                display.update(preview(entry.value[1:] if entry.value.startswith("//") else entry.value, s) if not entry.value.startswith("/") or entry.value.startswith("//") else "")
        quote = "".join(quote_symbols(s)) if s["message.quote"] else "关"
        paren = "".join(bracket_symbols(s.group("message"))) if s["message.username_brackets"] or core.paren_once else "无"
        basic = f"引号 {quote} 姓名 {paren} 后缀 {s['message.suffix_value'] if s['message.suffix'] else '无'}"
        more = f" 模拟打字 {str(s['message.print_speed']) + 'ms' if s['message.typewriting'] else '○'} 模拟停顿 {str(s['message.autopausetime'] * s['message.print_speed']) + 'ms' if s['message.autopause'] else '○'}"
        self.query_one("#enhancements", Static).update(basic + more)
        self.query_one("#hints", Static).update("Tab/↑↓ 选择 · Enter 补全 · Esc 关闭" if self.completing else "Enter 发送")
        typing = bool(s["typing.enable"] and core.typing_task and not core.typing_task.done()) or core.typing_state.startswith("已发")
        self.query_one("#typing-state", Static).update("正在输入 " + ("●" if typing else "○"))
        if isinstance(self.active_page, ComposeScreen) and self.active_page.query("#compose-error"):
            self.active_page.refresh_delivery_state()

    @on(Input.Changed, "#entry")
    def input_changed(self, event):
        self.core.input_changed(event.value)
        self.refresh_candidates(event.input)
        self.refresh_status()

    def refresh_candidates(self, entry):
        self.completion_state = (entry.value, entry.cursor_position, entry.selected_text)
        prefix = entry.value[1:entry.cursor_position]
        self.candidates = ["/" + name for name in HELP if name.startswith(prefix)] if entry.value.startswith("/") and " " not in entry.value and not entry.value.startswith("//") else []
        self.completion_start = 0
        if not self.candidates:
            self.candidates, self.completion_start = completion(entry.value, entry.cursor_position)
        if not self.core.settings["input.suggestions"] or entry.selected_text:
            self.candidates = []
        self.candidate_index = 0
        self.completing = False
        self.show_candidates()

    def show_candidates(self):
        widget = self.query_one("#suggestions", Static)
        widget.display = bool(self.candidates)
        start = max(0, self.candidate_index - 3) if self.completing else 0
        widget.update("  ".join(("›" if self.completing and i == self.candidate_index else "") + name + (" " + SHORTCODES[name] if name in SHORTCODES else "") for i, name in enumerate(self.candidates) if start <= i < start + 7))
        self.query_one("#context").display = not self.candidates and not self.query_one("#main-format").display

    def accept_completion(self, entry):
        code = self.candidates[self.candidate_index]
        start = self.completion_start
        suffix = " " if code.startswith("/") else ""
        end = entry.cursor_position
        entry.replace(code + suffix, start, end)
        entry.cursor_position = start + len(code + suffix)
        self.candidates, self.completing = [], False

    async def on_key(self, event):
        if event.key == "escape" and self.active_page is not None and isinstance(self.focused, ComposerInput):
            event.prevent_default()
            event.stop()
            self.active_page.action_back()
            return
        if self.screen is not self.screen_stack[0] or self.focused is not self.query_one("#entry"):
            return
        entry = self.query_one("#entry", Input)
        if getattr(self, "completion_state", None) != (entry.value, entry.cursor_position, entry.selected_text):
            self.refresh_candidates(entry)
        if self.candidates and (event.key in {"tab", "escape"} or (self.completing and event.key in {"up", "down", "enter"})):
            event.prevent_default()
            event.stop()
            if event.key == "escape":
                self.candidates, self.completing = [], False
            elif event.key == "enter":
                self.accept_completion(entry)
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
            self.accept_completion(event.input)
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
            return
        elif name == "settings":
            page = LanScreen() if args and args[0] == "lan" else SettingsScreen("endpoints" if args and args[0] == "routing" else args[0] if args else "all")
        elif name == "endpoints":
            page = EndpointsScreen()
        elif name == "compose":
            page = ComposeScreen()
        elif name == "connect":
            page = LanScreen()
        else:
            return
        if self.active_page is not None:
            if isinstance(self.active_page, ComposeScreen) and self.active_page.send_task:
                self.active_page.send_task.cancel()
            self.active_page.remove()
        self.active_page = page
        self.query_one("#log").display = False
        self.query_one("#history-region").mount(page)

    def close_page(self, page):
        if page is not self.active_page:
            return
        if isinstance(page, ComposeScreen) and page.send_task and page.send_task is not asyncio.current_task():
            page.send_task.cancel()
        page.remove()
        self.active_page = None
        self.query_one("#log").display = True
        self.return_focus()

    def return_focus(self, _=None):
        self.query_one("#entry").focus()
        self.refresh_status()

    @on(ComposerInput.Multiline)
    def pasted(self, event):
        self.core.compose_draft = event.text
        self.open_screen("compose")

    def on_click(self, event):
        if self.active_page is None:
            self.query_one("#entry").focus(scroll_visible=False)

    def on_mouse_down(self, event):
        if self.active_page is None:
            self.query_one("#entry").focus(scroll_visible=False)

    def action_interrupt(self):
        if isinstance(self.focused, (Input, TextArea)) and self.focused.selected_text:
            self.copy_to_clipboard(self.focused.selected_text)
            return
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        if not self.core.settings["input.interrupt_guard"]:
            self.exit()

    @on(FormatLink.Apply)
    def apply_format(self, event):
        event.stop()
        if isinstance(self.active_page, ComposeScreen) and self.active_page in event.link.ancestors:
            area = self.active_page.query_one(TextArea)
            if area.read_only:
                return
            start, end = area.selection.start, area.selection.end
            lines = area.text.split("\n")
            offset = lambda position: sum(len(line) + 1 for line in lines[:position[0]]) + position[1]
            lo, hi = sorted((offset(start), offset(end)))
            try:
                updated, selected_start, selected_end = format_selection(area.text, lo, hi, event.code)
            except ValueError as exc:
                self.active_page.query_one("#compose-error", Static).update(str(exc))
                return
            length = len(updated) - len(area.text) + hi - lo
            area.replace(updated[lo:lo + length], *sorted((start, end)))
            from textual.document._document import Selection as AreaSelection
            def location(index):
                prefix = updated[:index]
                return prefix.count("\n"), len(prefix.rsplit("\n", 1)[-1])
            area.selection = AreaSelection(location(selected_start), location(selected_end))
            area.focus()
        else:
            entry = self.query_one("#entry", Input)
            try:
                updated, start, end = format_selection(entry.value, *entry.selection, event.code)
            except ValueError as exc:
                self.query_one("#error", Static).update(str(exc))
                self.query_one("#error").display = True
                return
            from textual.widgets._input import Selection
            entry.value = updated
            entry.selection = Selection(start, end)
            entry.focus()

    @on(ComposerInput.SelectionChanged)
    @on(TextArea.SelectionChanged)
    def editor_selection_changed(self, event):
        if isinstance(event, ComposerInput.SelectionChanged):
            entry = self.query_one("#entry", Input)
            if getattr(self, "completion_state", None) != (entry.value, entry.cursor_position, entry.selected_text):
                self.refresh_candidates(entry)
        for bar in self.query(FormatBar):
            bar.refresh_visibility()

    async def on_unmount(self):
        if hasattr(self, "interrupt_policy"):
            self.interrupt_policy.restore()
        await self.core.close()
