import pytest
from textual.widgets import Input, Select, RichLog
from echolivetui.config import Settings
from echolivetui.ui import EchoApp, SettingsScreen, ComposeScreen


async def test_main_layout_settings_and_draft(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#log", RichLog).size.height >= 8
        entry = app.query_one("#entry", Input)
        entry.value = "保留草稿"
        app.open_screen("settings", ["message"])
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        app.screen.query_one("#field-message-username", Input).value = "新名字"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert entry.value == "保留草稿"
        app.open_screen("settings", ["message"])
        await pilot.pause()
        assert app.screen.query_one("#field-message-username", Input).value == "新名字"
        await pilot.click("#save")
        await pilot.pause()
        assert app.core.settings["message.username"] == "新名字"
        assert app.focused is entry


async def test_failed_submit_and_skip_retains_text(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "not sent"
        await pilot.press("enter")
        assert entry.value == "not sent"
        assert app.query_one("#error").display
        entry.value = "/skip"
        await pilot.press("enter")
        assert entry.value == "/skip"
        assert app.core.paren_once is False


async def test_compose_literal_paste_and_ctrl_guard(tmp_path):
    from textual.events import Paste
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.post_message(Paste("/name nope\n/quit"))
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.core.compose_draft == "/name nope\n/quit"
        assert app.core.settings["message.username"] == "Someone"
        await pilot.press("escape", "ctrl+c")
        assert app.is_running


async def test_endpoint_page_and_log_settings(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(80, 24)) as pilot:
        app.open_screen("endpoints")
        await pilot.pause()
        await pilot.press("escape")
        app.open_screen("settings", ["log"])
        await pilot.pause()
        app.screen.query_one("#field-log-level", Select).value = "info"
        await pilot.pause()
        await pilot.click("#save")
        await pilot.pause()
        assert app.core.settings["log.level"] == "info"


async def test_command_completion_does_not_execute(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        await pilot.press("/", "s", "e", "tab", "enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.query_one("#entry", Input).value.startswith("/settings")


async def test_history_mouse_selection_keeps_input_focus(tmp_path):
    from echolivetui.ui import HistoryLog
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(80, 24)) as pilot:
        log = app.query_one(HistoryLog)
        log.write("Selectable history 中文内容")
        await pilot.pause()
        entry = app.query_one("#entry", Input)
        assert not log.can_focus
        await pilot.mouse_down("#log", offset=(1, 0))
        await pilot.hover("#log", offset=(19, 0))
        await pilot.mouse_up("#log", offset=(19, 0))
        await pilot.pause()
        assert app.focused is entry
        assert "Selectable" in app.screen.get_selected_text()
        await pilot.press("ctrl+c")
        assert "Selectable" in app.clipboard
        await pilot.click("#top")
        assert app.focused is entry and len(app.screen_stack) == 1
        await pilot.press("tab")
        assert app.focused is entry


async def test_footer_links_and_embedded_endpoints(tmp_path):
    from echolivetui.ui import EndpointsScreen, EndpointPanel
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.click("#settings-link")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one("#category", Select).value = "endpoints"
        await pilot.pause()
        assert app.screen is screen and screen.query_one(EndpointPanel).display
        screen.query_one("#category", Select).value = "formatting"
        await pilot.pause()
        left = screen.query_one("#field-message-quote_open", Input)
        right = screen.query_one("#field-message-quote_close", Input)
        assert left.region.y == right.region.y
        assert left.region.right <= right.region.x
        await pilot.press("escape")
        await pilot.click("#endpoints-link")
        await pilot.pause()
        assert isinstance(app.screen, EndpointsScreen)


async def test_banner_is_excluded_from_history_selection(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        app.query_one("#log", RichLog).write("only history selected")
        await pilot.pause()
        await pilot.mouse_down("#log", offset=(20, 0))
        await pilot.hover("#top", offset=(0, 0))
        await pilot.mouse_up("#top", offset=(0, 0))
        await pilot.pause()
        selected = app.screen.get_selected_text()
        assert "EchoLiveTUI" not in selected
        assert "发送到" not in selected
        assert "only history" in selected


async def test_narrow_settings_keeps_switch_and_buttons_visible(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(56, 24)) as pilot:
        app.open_screen("settings", ["history"])
        await pilot.pause()
        control = app.screen.query_one("#field-history-hide_latest", Select)
        fields = app.screen.query_one("#fields")
        save = app.screen.query_one("#save")
        assert fields.region.height >= 5
        assert control.region.bottom <= fields.region.bottom
        assert save.region.bottom <= 24
