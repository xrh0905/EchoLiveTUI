import pytest
from rich.cells import cell_len
from rich.console import Console
from textual.widgets import Input, TextArea

from echolivetui.config import Settings
from echolivetui.editing import completion, format_selection, preview
from echolivetui.message import parse_message
from echolivetui.pipeline import prepare
from echolivetui.ui import EchoApp, FormatLink


@pytest.fixture
def settings(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"message.quote": False, "message.typewriting": False})
    return settings


def test_script_codes_accumulate_reset_and_escape(settings):
    parts = parse_message("@bX@^2@_3@r4")
    assert parts[1]["style"] == {"bold": True, "super": True}
    assert parts[2]["style"] == {"bold": True, "super": True, "sub": True}
    assert not parts[3]["style"]
    assert preview("X@^2@_3", settings).plain.splitlines() == [" 23", "X  "]
    escaped = parse_message(r"\@^literal @@_text")
    assert "".join(p["text"] for p in escaped) == "@^literal @_text"
    assert not any(p["style"] for p in escaped)
    assert completion("@^", 2)[0] == ["@^"]
    assert completion("@_", 2)[0] == ["@_"]


def test_script_selection_restores_surrounding_style(settings):
    result, start, end = format_selection("@^前选后", 3, 4, "@_")
    assert result[start:end] == "选"
    assert parse_message(result)[-1] == {"text": "后", "style": {"super": True}}


def test_script_preview_aligns_cells_and_only_annotations_are_gray(settings):
    text = "@[#ff0000]中@^@rainbow2@rH@_₂@r👩‍💻Z"
    display = preview(text, settings)
    rows = display.plain.splitlines()
    assert len(rows) == 3
    assert rows[0].index("2") == 2
    assert rows[2].index("₂") == 4
    assert len({cell_len(row) for row in rows}) == 1
    assert rows[1].startswith("中 H ") and rows[1].endswith("👩‍💻Z")
    console = Console()
    assert display.get_style_at_offset(console, display.plain.index("2")).color.triplet == (158, 174, 185)
    assert display.get_style_at_offset(console, display.plain.index("中")).color.triplet == (255, 0, 0)
    wire = prepare(text, settings).data["messages"][0]["message"]
    assert next(p for p in wire if p.get("text") == "2")["style"]["color"] == "#ff0000"
    assert preview("x@<super>2@rH@<sub>2", settings).plain.count("\n") == 2
    hinted = preview("X@^2@rH@_2", settings, hint="zh-Hans")
    assert hinted.plain.splitlines()[1].endswith("  zh-Hans")
    assert all("zh-Hans" not in row for row in hinted.plain.splitlines()[::2])
    assert hinted.get_style_at_offset(console, hinted.plain.index("zh-Hans")).dim


@pytest.mark.parametrize("scheme", ["pinyin", "zhuyin"])
def test_script_style_survives_typing_markdown_and_pauses(settings, scheme):
    settings.values.update({"message.typewriting": True, "message.typewriting_scheme": scheme,
                            "message.autopause": True, "message.autopausestr": "！"})
    parts = prepare("基@^**中文**————！@rH@_2@rO", settings).data["messages"][0]["message"]
    chinese = next(p for p in parts if p.get("text") == "中文")
    assert chinese["style"]["super"] and chinese["style"]["bold"] and chinese["typewrite"]
    symbols = next(p for p in parts if p.get("text") == "————！")
    assert symbols["style"]["super"] and "typewrite" not in symbols
    assert next(p for p in parts if p.get("text") == "2")["style"]["sub"]


@pytest.mark.parametrize("width", [80, 40])
async def test_preview_grows_shrinks_and_preserves_controls(settings, tmp_path, width):
    app = EchoApp(settings, tmp_path, start_server=False)
    async with app.run_test(size=(width, 24)) as pilot:
        entry = app.query_one("#entry", Input)
        display = app.query_one("#preview")
        for text, height in [("normal", 1), ("X@^2", 2), ("X@^2@rH@_2", 3), ("X@_2", 2), ("normal", 1), ("", 1)]:
            entry.value = text
            await pilot.pause()
            assert display.size.height == height
            assert entry.region.bottom < app.screen.size.height
            assert app.query_one("#submission").region.bottom <= app.query_one("#footer").region.y
        settings.values["input.preview"] = False
        app.refresh_status()
        await pilot.pause()
        assert not display.display
        settings.values["input.preview"] = True
        entry.value = "X@^2@rH@_2"
        await pilot.pause()
        assert display.size.height == 3
        # Long content stays in aligned rows, without wrapping the main row away.
        entry.value = "A" * 200 + "@^2@rH@_2"
        await pilot.pause()
        assert display.size.height == 3
        assert {"@^", "@_"} <= {link.name for link in app.query(FormatLink)}
        app.core.compose_draft = "X@^2@rH@_2\nsecond line"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        assert page.query_one("#compose-preview").size.height == 3
        assert page.query_one("#send").region.bottom <= page.region.bottom
        area = page.query_one(TextArea)
        area.load_text("normal")
        await pilot.pause()
        assert page.query_one("#compose-preview").size.height == 1
