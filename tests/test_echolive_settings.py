import asyncio
import pytest
from aiohttp import web
from textual.widgets import Button, Input, Static, Tab
from textual.widgets._tabs import Underline

from echolivetui import echolive_settings as live
from echolivetui.capabilities import Capabilities
from echolivetui.config import Settings
from echolivetui.echolive_panel import EchoLivePanel
from echolivetui.hosting import Hosting, REQUIRED
from echolivetui.ui import EchoApp, PageTabs


@pytest.fixture
def hosted(tmp_path):
    root = tmp_path / "EchoLive"
    for relative in REQUIRED:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    (root / "app.js").write_text("const app = {version: '1.8.12'};", encoding="utf-8")
    path = root / live.FONT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('/* 保留 */\r\n:root {\r\n  --echo-default-font-family: "思源黑体";\r\n  --other: 1;\r\n}\r\n').encode("utf-8"))
    return root


def test_nested_hosting_version_and_font_preserves_other_css(hosted):
    hosting = Hosting(hosted.parent, "token", Settings(hosted.parent / "s.yaml"))
    assert hosting.root == hosted and hosting.version == "1.8.12"
    path = hosted / live.FONT_FILE
    original = path.read_bytes()
    assert live.read_font(hosted) == live.DEFAULT_FONT
    live.write_font(hosted, "Noto Sans SC, Arial, sans-serif")
    assert path.read_bytes() == original.replace(('"' + live.DEFAULT_FONT + '"').encode(), b'"Noto Sans SC", "Arial", sans-serif')
    assert live.read_font(hosted) == "Noto Sans SC, Arial, sans-serif"
    live.write_font(hosted, live.DEFAULT_FONT)
    assert path.read_bytes() == original


@pytest.mark.parametrize("source, display", [
    ('"思源黑体"', "思源黑体"),
    ("'Microsoft YaHei'", "Microsoft YaHei"),
    ('"Noto Sans SC", Arial, sans-serif', "Noto Sans SC, Arial, sans-serif"),
])
def test_read_font_removes_quotes_from_each_family(hosted, source, display):
    path = hosted / live.FONT_FILE
    path.write_text(":root { --echo-default-font-family: " + source + "; }", encoding="utf-8")
    assert live.read_font(hosted) == display


@pytest.mark.parametrize("value", ["", '"broken', "Arial; color: red", "Arial\nOther", "url(test)", "Arial /* comment */"])
def test_invalid_font_cannot_damage_css(hosted, value):
    path = hosted / live.FONT_FILE
    before = path.read_bytes()
    with pytest.raises(ValueError):
        live.write_font(hosted, value)
    assert path.read_bytes() == before


@pytest.mark.parametrize("current, latest, expected", [
    ("1.8.9", "v1.8.12", "有更新"),
    ("1.8.12", "v1.8.12", "已是最新"),
    ("1.10.0", "v1.9.9", "高于"),
    (None, "v1.8.12", "未知"),
    ("1.8.12-beta.2", "v1.8.12", "有更新"),
    ("1.8.12-beta.2", "v1.8.12-beta.10", "有更新"),
])
def test_release_comparison(current, latest, expected):
    assert expected in live.release_notice(current, latest)


@pytest.mark.parametrize("status, payload, expected", [
    (200, {"tag_name": "v1.8.12", "html_url": live.RELEASE_PAGE}, None),
    (403, {}, "受限"),
    (404, {}, "HTTP 404"),
    (200, {"tag_name": "broken"}, "版本号"),
])
async def test_release_api_success_and_failures(monkeypatch, status, payload, expected):
    async def handler(request):
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["User-Agent"] == "EchoLiveTUI"
        return web.json_response(payload, status=status)
    app = web.Application()
    app.router.add_get("/latest", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(live, "RELEASE_API", f"http://127.0.0.1:{port}/latest")
    try:
        if expected:
            with pytest.raises(ValueError, match=expected):
                await live.latest_release()
        else:
            assert await live.latest_release() == ("v1.8.12", live.RELEASE_PAGE)
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("width", [56, 80, 100])
async def test_conditional_tabs_sidebar_font_and_update_ui(hosted, width, monkeypatch):
    async def release():
        await asyncio.sleep(.01)
        return "v1.9.0", live.RELEASE_PAGE
    monkeypatch.setattr("echolivetui.echolive_panel.latest_release", release)
    app = EchoApp(Settings(hosted.parent / "s.yaml"), hosted.parent, start_server=False)
    async with app.run_test(size=(width, 30)) as pilot:
        app.open_screen("settings")
        await pilot.pause()
        page = app.active_page
        tabs = page.query_one(PageTabs)
        tui, echo = list(tabs.query(Tab))
        assert tui.region.width == echo.region.width == tabs.region.width // 2
        assert tabs.query_one(Underline).region.width == tabs.region.width
        assert tui.label.plain == "[TUI 设置]" and echo.label.plain == "Echo-Live 设置"
        assert page.query_one("#tui-settings-content").display
        assert not page.query_one(EchoLivePanel).display
        await pilot.click("#echo-settings")
        await pilot.pause()
        assert tui.label.plain == "TUI 设置" and echo.label.plain == "[Echo-Live 设置]"
        assert not page.query_one("#tui-settings-content").display
        assert page.query_one(EchoLivePanel).display
        field = page.query_one("#echo-font", Input)
        assert field.value == live.DEFAULT_FONT
        field.value = "Microsoft YaHei, sans-serif"
        await pilot.click("#echo-font-save")
        assert live.read_font(hosted) == field.value
        assert '"Microsoft YaHei", sans-serif' in (hosted / live.FONT_FILE).read_text(encoding="utf-8")
        await pilot.click("#echo-font-reset")
        assert live.read_font(hosted) == live.DEFAULT_FONT
        assert field.value == "思源黑体"
        await pilot.click("#echo-version-nav")
        await pilot.click("#echo-check")
        await pilot.pause()
        assert "有更新" in str(page.query_one("#echo-release-state", Static).content)
        assert not page.query_one("#echo-check", Button).disabled
        assert page.query_one("#echo-local-version", Static).content == "本地版本：1.8.12"
        for button in page.query(".actions Button"):
            if button.display and button.region.width:
                assert button.region.bottom == page.region.bottom
                assert page.region.contains_region(button.region)
        await pilot.click("#tui-settings")
        assert page.query_one("#tui-settings-content").display
        assert not page.query_one(EchoLivePanel).display


async def test_independent_settings_have_full_width_single_tab(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        app.open_screen("settings")
        await pilot.pause()
        assert app.core.capabilities == Capabilities.NONE
        assert not app.active_page.query(EchoLivePanel)
        tabs = app.active_page.query_one(PageTabs)
        assert tabs.tab_count == 1
        assert tabs.query_one(Tab).region.width == tabs.region.width
