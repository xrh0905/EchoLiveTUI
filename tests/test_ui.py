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
