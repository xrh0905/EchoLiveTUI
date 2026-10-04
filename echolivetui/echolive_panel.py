"""The conditional Echo Live settings tab."""
import asyncio
import webbrowser

from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Static

from .config import coerce
from .echolive_settings import DEFAULT_FONT, FONT_FILE, RELEASE_PAGE, latest_release, read_font, release_notice, release_page, write_font


class EchoLivePanel(Vertical):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.release_url = RELEASE_PAGE
        self.check_task = None

    @property
    def hosting(self):
        return self.app.core.server.hosting

    def compose(self):
        with Horizontal(id="echo-settings-content"):
            with Vertical(id="echo-sidebar"):
                yield Button("字体", id="echo-font-nav", variant="primary")
                yield Button("版本管理", id="echo-version-nav")
            with VerticalScroll(id="echo-settings-main"):
                with Vertical(id="echo-font-pane"):
                    yield Label("前台字体")
                    yield Input(placeholder="思源黑体, sans-serif", id="echo-font")
                    yield Static(FONT_FILE, markup=False)
                    yield Static("保存后刷新前台页面生效。", markup=False)
                with Vertical(id="echo-version-pane"):
                    yield Static(f"本地版本：{self.hosting.version or '未知'}", id="echo-local-version", markup=False)
                    yield Label("GitHub 仓库（owner/repo）")
                    yield Input(self.app.core.settings["echolive.repository"], id="echo-repository", placeholder="sheep-realms/Echo-Live")
                    yield Static("点击检查更新，获取 GitHub 最新正式版。", id="echo-release-state", markup=False)
        yield Static("", id="echo-settings-error", markup=False)
        with Horizontal(classes="actions"):
            yield Button("保存字体", id="echo-font-save", variant="primary")
            yield Button("恢复默认", id="echo-font-reset", tooltip="恢复思源黑体并写入字体文件")
            yield Button("保存仓库", id="echo-repository-save")
            yield Button("检查更新", id="echo-check", variant="primary")
            yield Button("发布页", id="echo-release")
            yield Button("返回", id="echo-back")

    def on_mount(self):
        self.select_section("font")
        try:
            self.query_one("#echo-font", Input).value = read_font(self.hosting.root)
        except (ValueError, OSError, UnicodeError) as exc:
            self.query_one("#echo-settings-error", Static).update(str(exc))

    def select_section(self, section):
        font = section == "font"
        self.query_one("#echo-font-pane").display = font
        self.query_one("#echo-version-pane").display = not font
        for selector in ("#echo-font-save", "#echo-font-reset"):
            self.query_one(selector).display = font
        for selector in ("#echo-repository-save", "#echo-check", "#echo-release"):
            self.query_one(selector).display = not font
        self.query_one("#echo-font-nav", Button).variant = "primary" if font else "default"
        self.query_one("#echo-version-nav", Button).variant = "default" if font else "primary"

    @on(Button.Pressed)
    async def button(self, event):
        event.stop()
        action = event.button.id
        status = self.query_one("#echo-settings-error", Static)
        if action in {"echo-font-nav", "echo-version-nav"}:
            self.select_section("font" if action == "echo-font-nav" else "version")
            status.update("")
        elif action in {"echo-font-save", "echo-font-reset"}:
            field = self.query_one("#echo-font", Input)
            try:
                value = DEFAULT_FONT if action == "echo-font-reset" else field.value
                write_font(self.hosting.root, value)
                field.value = read_font(self.hosting.root)
                status.update("已保存字体，请刷新前台页面。")
            except (ValueError, OSError, UnicodeError) as exc:
                status.update(str(exc))
        elif action == "echo-repository-save":
            try:
                await self.save_repository()
                self.query_one("#echo-release-state", Static).update("仓库已保存，点击检查更新。")
                status.update("")
            except ValueError as exc:
                status.update(str(exc))
        elif action == "echo-check":
            if not self.check_task:
                self.check_task = asyncio.create_task(self.check_release())
        elif action == "echo-release":
            try:
                coerce("echolive.repository", self.query_one("#echo-repository", Input).value)
                await asyncio.to_thread(webbrowser.open, self.release_url)
            except ValueError as exc:
                status.update(str(exc))
        elif action == "echo-back":
            self.app.close_page(self.app.active_page)

    @on(Input.Changed, "#echo-repository")
    def repository_changed(self, event):
        try:
            self.release_url = release_page(event.value)
        except ValueError:
            pass

    async def save_repository(self):
        field = self.query_one("#echo-repository", Input)
        repository = coerce("echolive.repository", field.value)
        await self.app.core.apply({"echolive.repository": repository})
        field.value = repository
        self.release_url = release_page(repository)
        return repository

    async def check_release(self):
        button = self.query_one("#echo-check", Button)
        state = self.query_one("#echo-release-state", Static)
        field = self.query_one("#echo-repository", Input)
        save = self.query_one("#echo-repository-save", Button)
        button.disabled = True
        field.disabled = save.disabled = True
        state.update("正在检查更新…")
        try:
            repository = await self.save_repository()
            self.query_one("#echo-settings-error", Static).update("")
            tag, self.release_url = await latest_release(repository)
            state.update(release_notice(self.hosting.version, tag))
        except ValueError as exc:
            state.update(str(exc))
        finally:
            self.check_task = None
            if self.is_mounted:
                button.disabled = False
                field.disabled = save.disabled = False

    def on_unmount(self):
        if self.check_task:
            self.check_task.cancel()
