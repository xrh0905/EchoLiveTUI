import re

import pytest
from textual.events import Paste
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea, Tab
from textual.widgets._input import Selection

from echolivetui.capabilities import Capabilities
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hosting import REQUIRED
from echolivetui.hub import Peer
from echolivetui.protocol import Profile
from echolivetui.pipeline import prepare
from echolivetui.ui import EchoApp, ComposeScreen, HistoryLog, FormatLink, PageTabs
from echolivetui.lan import editor_url, terminal_qr


@pytest.mark.parametrize("text", ["中文 paste", "/name literal", "@b样式"])
@pytest.mark.parametrize("native", [True, False])
async def test_single_paste_replaces_selection_once(tmp_path, text, native):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "before OLD after"
        entry.selection = Selection(7, 10)
        if native:
            entry.post_message(Paste(text))
        else:
            app.copy_to_clipboard(text)
            await pilot.press("ctrl+v")
        await pilot.pause()
        assert entry.value == "before " + text + " after"
        assert app.active_page is None
        assert app.core.settings["message.username"] == "Someone"


@pytest.mark.parametrize("native", [True, False])
async def test_multiline_paste_preserves_buffer_and_is_literal(tmp_path, native):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "prefix OLD suffix"
        entry.selection = Selection(7, 10)
        text = "/quit\r\n/name unchanged"
        if native:
            entry.post_message(Paste(text))
        else:
            app.copy_to_clipboard(text)
            await pilot.press("ctrl+v")
        await pilot.pause()
        assert entry.value == "prefix OLD suffix"
        assert isinstance(app.active_page, ComposeScreen)
        assert app.active_page.query_one(TextArea).text.replace("\r\n", "\n") == "prefix /quit\n/name unchanged suffix"
        assert app.core.settings["message.username"] == "Someone"
        await pilot.press("escape")
        assert app.active_page is None and app.focused is entry


async def test_pages_replace_only_history_and_footer_stays_clickable(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(100, 30)) as pilot:
        history = app.query_one("#history-region").region
        workspace = app.query_one("#workspace").region
        assert app.query_one("#entry").size.height == 1
        assert app.query_one("#top").size.height == app.query_one("#footer").size.height == 1
        for link in ("settings-link", "endpoints-link", "pair-link", "compose-link"):
            await pilot.click("#" + link)
            await pilot.pause()
            assert app.active_page.region == history
            assert app.query_one("#workspace").region == workspace
            assert not app.query_one("#log").display
        await pilot.press("escape")
        assert app.active_page is None and app.query_one("#log").display


async def test_hints_selection_actions_and_blank_share_one_line(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        region = app.query_one("#workspace").region
        for text in ("/", "@", "plain"):
            entry.value = text
            await pilot.pause(.15)
            slots = [app.query_one(s) for s in ("#context", "#suggestions", "#main-format")]
            assert sum(w.display for w in slots) == 1
            assert app.query_one("#workspace").region == region
            entry.selection = Selection(0, len(text))
            await pilot.pause(.15)
            assert app.query_one("#main-format").display
            assert not app.query_one("#suggestions").display
            assert app.query_one("#workspace").region == region


async def test_footer_simulation_switches_between_open_circle_and_milliseconds(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(120, 30)) as pilot:
        settings = app.core.settings.values
        settings.update({"message.typewriting": False, "message.autopause": False})
        app.refresh_status()
        footer = app.query_one("#enhancements", Static)
        assert "模拟打字 ○ 模拟停顿 ○" in str(footer.content)
        settings.update({"message.typewriting": True, "message.autopause": True, "message.print_speed": 25, "message.autopausetime": 12})
        app.refresh_status()
        assert "模拟打字 25ms 模拟停顿 300ms" in str(footer.content)
        settings["message.autopausetime"] = 0
        app.refresh_status()
        assert "模拟停顿 0ms" in str(footer.content)
        assert "引号 「」 姓名" in str(footer.content)
        assert "引号 「」  姓名" not in str(footer.content)


async def test_connect_command_opens_connection_page_and_completes(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "/con"
        await pilot.pause()
        assert app.candidates == ["/connect"]
        await pilot.press("tab", "enter")
        assert entry.value == "/connect "
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_page.query("#lan-interface")
        assert app.query_one("#pair-link").name == "connect"


async def test_history_system_names_underlined_and_user_names_compact(tmp_path):
    class Socket:
        async def close(self):
            pass
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    app.core.settings.values["message.username"] = "短名"
    app.core.hub.peers["live"] = Peer(Socket(), Profile("live", role="live"))
    async with app.run_test(size=(80, 24)) as pilot:
        app.report("EchoLiveTUI：system notice")
        app.core.submit("user content")
        await pilot.pause()
        log = app.query_one(HistoryLog)
        system_line = next(i for i, line in enumerate(log.lines) if "system notice" in line.text)
        user_line = next(i for i, line in enumerate(log.lines) if "user content" in line.text)
        system = list(log.render_line(system_line))
        assert any("EchoLiveTUI" in segment.text and segment.style.underline for segment in system)
        assert not any(segment.style and segment.style.overline for segment in system)
        assert not any(segment.style and segment.style.underline for segment in log.render_line(user_line))
        text = log.lines[user_line].text
        assert text.index("user content") - text.index("短名") < 10
        assert log.records[-1].columns[1].width is None
        # A user can use the same name as the application without system styling.
        app.core.settings.values["message.username"] = "EchoLiveTUI"
        app.core.submit("same name user")
        await pilot.pause()
        user_line = next(i for i, line in enumerate(log.lines) if "same name user" in line.text)
        assert not any(segment.style and segment.style.underline for segment in log.render_line(user_line))


@pytest.mark.parametrize("width", [56, 80, 100])
async def test_compact_controls_and_navigation_band_stay_at_page_bottom(tmp_path, width):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(width, 30)) as pilot:
        for name in ("settings", "endpoints", "connect", "compose"):
            app.open_screen(name)
            await pilot.pause()
            page = app.active_page
            tabs = page.query_one(PageTabs)
            assert tabs.region.y == page.region.y and tabs.size.height == 2
            assert tabs.region.width == page.region.width - 2
            assert page.query_one(Tab).region.width == tabs.region.width
            assert page.query_one(Tab).label.plain.startswith("[")
            buttons = [button for button in page.query(Button) if button.display]
            assert buttons
            for button in buttons:
                assert button.size.height == 1
                assert button.region.bottom == page.region.bottom
                assert page.region.contains_region(button.region)
            assert all(not a.region.overlaps(b.region) for i, a in enumerate(buttons) for b in buttons[i + 1:])
            assert buttons[-1].region.right == page.region.right - 1
            if name == "compose":
                for selector in ("#line-mode", "#line-interval"):
                    control = page.query_one(selector)
                    assert control.size.height == 1 and control.region.y == buttons[0].region.y
                    assert page.region.contains_region(control.region)
                page.paused = True
                page.set_running(False)
                await pilot.pause()
                assert page.region.contains_region(page.query_one("#back").region)


async def test_format_labels_are_chinese_without_copy_hint(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "selected text"
        entry.selection = Selection(0, 8)
        await pilot.pause()
        bar = app.query_one("#main-format")
        assert not bar.query(".selection-copy")
        labels = [link.content for link in bar.query(FormatLink)]
        assert labels[:4] == ["粗体", "斜体", "下划线", "删除线"]


async def test_connection_qr_fits_left_half_and_controls_stay_right(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    app.core.server.lan_address = ("192.168.1.3", 3000)
    async with app.run_test(size=(100, 30)) as pilot:
        app.open_screen("connect")
        await pilot.pause()
        page = app.active_page
        left, right = page.query_one("#lan-left"), page.query_one("#lan-right")
        qr = page.query_one("#lan-qr")
        assert left.region.width == right.region.width
        assert left.region.contains_region(qr.region)
        rows = terminal_qr(editor_url("192.168.1.3", 3000)).splitlines()
        assert qr.content_size.height >= len(rows)
        assert qr.content_size.width >= max(map(len, rows))
        for selector in ("#lan-interface", "#lan-manual", "#lan-clients"):
            assert right.region.contains_region(page.query_one(selector).region)
        assert page.query_one("#lan-clients").region.bottom <= page.query_one(".actions").region.y


async def test_history_fifo_keeps_whole_wrapped_records(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    app.core.settings.values["history.max_entries"] = 2
    async with app.run_test(size=(56, 24)) as pilot:
        app.report("旧姓名：" + "旧内容" * 50)
        app.report("新姓名：保留一")
        app.report("新姓名：" + "保留二" * 40)
        await pilot.pause()
        log = app.query_one(HistoryLog)
        assert len(log.records) == 2
        rendered = "\n".join(line.text for line in log.lines)
        assert "旧内容" not in rendered and "旧姓名" not in rendered
        assert "新姓名" in rendered and "保留一" in rendered
        assert re.sub(r"\s", "", rendered).count("保留二") == 40


async def test_startup_history_is_deferred_until_first_layout(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    app.report("EchoLiveTUI：startup before layout")
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one(HistoryLog)
        assert len(log.records) == 1
        assert "startup before layout" in "\n".join(line.text for line in log.lines)
        app.core.settings.values["history.max_entries"] = 1
        app.report("EchoLiveTUI：new record")
        await pilot.pause()
        assert len(log.records) == 1
        assert "startup" not in "\n".join(line.text for line in log.lines)


async def test_symbol_presets_custom_roundtrip_and_save(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        app.open_screen("settings", ["formatting"])
        await pilot.pause()
        page = app.active_page
        for prefix, preset, symbols in (("quote", "cn", ("“", "”")), ("username_bracket", "round", ("（", "）"))):
            style = page.query_one("#field-message-" + prefix + "_style", Select)
            left = page.query_one("#field-message-" + prefix + "_open", Input)
            right = page.query_one("#field-message-" + prefix + "_close", Input)
            style.value = preset
            await pilot.pause()
            assert (left.value, right.value) == symbols
            assert left.disabled and right.disabled
            assert left.region.y == right.region.y and left.region.right <= right.region.x
            style.value = "custom"
            await pilot.pause()
            assert not left.disabled and not right.disabled
            left.value, right.value = "<", ">"
        await pilot.pause()
        await pilot.click("#save")
        await pilot.pause()
        loaded = Settings(app.core.settings.path)
        assert not loaded.load_error
        assert prepare("内容", loaded).data["username"] == "<Someone>"
        assert prepare("内容", loaded).plain == "<内容>"


async def test_compose_default_submits_whole_literal_text(tmp_path, monkeypatch):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    sent = []
    monkeypatch.setattr(app.core, "submit", lambda text, **kwargs: sent.append(text))
    async with app.run_test() as pilot:
        app.core.compose_draft = "/quit\n/name literal"
        app.open_screen("compose")
        await pilot.pause()
        assert not app.active_page.query_one("#line-mode", Checkbox).value
        await pilot.click("#send")
        await pilot.pause()
        assert sent == ["/quit\n/name literal"]
        assert app.active_page is None and not app.core.compose_draft


async def test_performance_pause_edit_resume_from_cursor_and_complete(tmp_path, monkeypatch):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    sent = []
    monkeypatch.setattr(app.core, "submit", lambda text, **kwargs: sent.append(text))
    async with app.run_test(size=(100, 30)) as pilot:
        app.core.compose_draft = "first\nsecond\nthird"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        area = page.query_one(TextArea)
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "10000"
        await pilot.pause()
        await pilot.click("#send")
        await pilot.pause()
        assert sent == ["first"] and area.read_only
        assert str(page.query_one("#send", Button).label) == "暂停"
        assert area.cursor_location == (0, 0) and area.has_class("sending")
        assert area._theme.cursor_line_style.bgcolor.get_truecolor().hex == "#31554a"
        area.focus()
        await pilot.press("down", "ctrl+space")
        assert area.cursor_location == (0, 0)
        area.post_message(Paste("must not modify"))
        await pilot.click("#send")
        await pilot.pause()
        assert not area.read_only and area.cursor_location == (1, 0)
        assert area.text == "first\nsecond\nthird"
        area.load_text("first\nsecond edited\nthird edited")
        area.move_cursor((2, 0))
        page.query_one("#line-interval", Input).value = "0"
        await pilot.click("#send")
        await pilot.pause()
        assert sent == ["first", "third edited"]
        assert app.active_page is None and app.focused is app.query_one("#entry")


async def test_performance_cancel_and_failure_preserve_draft(tmp_path, monkeypatch):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    sent = []
    monkeypatch.setattr(app.core, "submit", lambda text, **kwargs: sent.append(text))
    async with app.run_test() as pilot:
        app.core.compose_draft = "first\nsecond"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "10000"
        await pilot.click("#send")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.active_page is None and page.send_task is None
        assert sent == ["first"] and app.core.compose_draft == "first\nsecond"
        def fail(_, **kwargs):
            raise ValueError("no targets")
        monkeypatch.setattr(app.core, "submit", fail)
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        await pilot.click("#send")
        await pilot.pause()
        assert app.active_page is page and not page.query_one(TextArea).read_only
        assert page.paused and page.next_row == 0
        assert app.core.compose_draft == "first\nsecond"


def test_capabilities_detect_hosted_environment_and_support_bit_checks(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    assert Core(settings, tmp_path).capabilities == Capabilities.NONE
    for name in REQUIRED:
        path = tmp_path / "EchoLive" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    core = Core(settings, tmp_path)
    assert core.capabilities & Capabilities.HAS_ECHO_LIVE
    assert core.capabilities | Capabilities.NONE == Capabilities.HAS_ECHO_LIVE
