from pathlib import Path
from textual.widgets import Input, TextArea
from textual.widgets._input import Selection
from echolivetui.config import Settings
from echolivetui.editing import completion, format_selection, preview
from echolivetui.message import parse_message
from echolivetui.pipeline import prepare
from echolivetui.hosting import Hosting, REQUIRED
from echolivetui.ui import EchoApp, ComposeScreen


def test_shortcodes_literal_media_and_preview(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"message.quote": False, "message.typewriting": False})
    text = "@b粗体@i斜体@u下划线@s删除@r普通@[#1234]颜色@<shake>@<:custom>类"
    data = prepare(text, settings).data["messages"][0]["message"]
    assert data[3]["style"]["strikethrough"]
    assert not data[4].get("style")
    assert data[-1]["class"] == "echo-text-shake custom"
    assert preview(text, settings).plain == "粗体斜体下划线删除普通颜色类"
    raw = "前文 @@b字面 @b粗体@r @@ 后文"
    assert preview(raw, settings).plain == "前文 @b字面 粗体 @ 后文"
    assert "@{smile}" in preview("@{smile} @{sys:img:0}", settings).plain
    assert not any(item.get("data", {}).get("emoji") for item in prepare("@{smile}", settings).data["messages"][0]["message"])
    assert completion("@", 1)[0][0] == "@@"
    assert completion("前文 @b", 5) == (["@b"], 3)
    assert completion("@@literal @", 11)[0][0] == "@@"
    assert preview("@@@b粗体", settings).plain == "@粗体"


def test_selection_restores_surrounding_format():
    result, start, end = format_selection("@i前文选中文后文", 4, 7, "@b")
    assert result[start:end] == "选中文"
    tail = parse_message(result)[-1]
    assert tail["text"] == "后文"
    assert tail["style"].get("italic") and not tail["style"].get("bold")


def test_hosting_only_current_or_single_named_child(tmp_path):
    def resources(root):
        for name in REQUIRED:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test")
    settings = Settings(tmp_path / "s.yaml")
    resources(tmp_path / "other")
    assert Hosting(tmp_path, "token", settings).root is None
    resources(tmp_path / "EchoLive")
    assert Hosting(tmp_path, "token", settings).root == tmp_path / "EchoLive"
    resources(tmp_path)
    assert Hosting(tmp_path, "token", settings).root == tmp_path


async def test_shift_enter_copies_buffer_and_selection_formatting(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test(size=(80, 24)) as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "前面选中后面"
        entry.selection = Selection(2, 4)
        await pilot.pause()
        await pilot.click("FormatLink")
        await pilot.pause()
        assert entry.value == "前面@b选中@r后面"
        assert entry.selected_text == "选中"
        await pilot.press("ctrl+c")
        assert app.clipboard == "选中"
        await pilot.press("shift+enter")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one(TextArea).text == entry.value
        assert app.core.compose_draft == entry.value


async def test_shortcode_completion_keeps_prefix_and_no_extra_space(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        await pilot.press("x", "space", "@", "b")
        await pilot.pause()
        assert app.candidates == ["@b"]
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert entry.value == "x @b"


async def test_mouse_drag_selects_chinese_without_focus_loss(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "AA中文BB"
        await pilot.pause()
        x = entry.content_region.x - entry.region.x
        y = entry.content_region.y - entry.region.y
        await pilot.mouse_down("#entry", offset=(x + 2, y))
        await pilot.hover("#entry", offset=(x + 6, y))
        await pilot.mouse_up("#entry", offset=(x + 6, y))
        assert entry.selected_text == "中文"
        assert app.focused is entry
        await pilot.press("ctrl+c")
        assert app.clipboard == "中文"


def test_special_formats_emit_shout_once_across_message_splitting(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    settings.values["message.autopause"] = True
    data = prepare("@shout你好，世界！@rainbow彩虹文字@r正常", settings).data["messages"][0]["message"]
    assert sum(item.get("event") == "shout" for item in data) == 1
    assert any("echo-text-rainbow" in item.get("class", "") for item in data)
    assert "@shout" in completion("@sh", 3)[0]


async def test_format_bar_context_and_preview_settings(tmp_path):
    from echolivetui.ui import FormatBar
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        bar = app.query_one(FormatBar)
        await pilot.pause(.2)
        assert not bar.display
        entry.value = "plain"
        await pilot.pause(.2)
        assert not bar.display and app.query_one("#preview").display
        entry.selection = Selection(1, 3)
        await pilot.pause(.2)
        assert bar.display
        app.core.settings.values["input.format_bar"] = "never"
        app.core.settings.values["input.preview"] = False
        await pilot.pause(.3)
        assert not bar.display and not app.query_one("#preview").display
        app.core.settings.values["input.format_bar"] = "always"
        entry.selection = Selection(0, 0)
        await pilot.pause(.2)
        assert bar.display


async def test_compose_multiline_format_keeps_selected_text(tmp_path):
    from textual.document._document import Selection as AreaSelection
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        app.core.compose_draft = "前面\n选中\n后面"
        app.open_screen("compose")
        await pilot.pause()
        area = app.screen.query_one(TextArea)
        area.selection = AreaSelection((0, 1), (1, 2))
        await pilot.pause(.2)
        await pilot.click("FormatLink")
        await pilot.pause()
        assert area.text == "前@b面\n选中@r\n后面"
        assert area.selected_text == "面\n选中"
