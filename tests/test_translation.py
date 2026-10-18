import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from aiohttp import web
import pytest

from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hub import Peer
from echolivetui.protocol import Profile
from echolivetui.translation import TranslationError, TranslationService, editor_text, paragraph_text


@pytest.fixture
async def provider():
    state = SimpleNamespace(calls=[], status=200, payload=None, gate=None)

    async def handler(request):
        texts = await request.json()
        state.calls.append((texts, list(request.query.items())))
        if state.gate:
            await state.gate.wait()
        if state.status != 200:
            return web.Response(status=state.status, text="error")
        payload = state.payload
        if payload is None:
            payload = [{"detectedLanguage": {"language": "en"},
                        "translations": [{"text": f"{code}:{text}", "to": code} for code in reversed(request.query.getall("to"))]}
                       for text in texts]
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post("/translate", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service = TranslationService(endpoint=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/translate")
    try:
        yield service, state
    finally:
        if state.gate:
            state.gate.set()
        await service.close()
        await runner.cleanup()


async def test_translation_batch_order_detect_cache_and_literal_tracks(provider):
    service, state = provider
    result = await service.translate(["hello", "world"], "", ["ja", "ru"])
    assert result.translations == (("ja:hello", "ru:hello"), ("ja:world", "ru:world"))
    assert result.detected_languages == ("en", "en")
    assert result.tracks(1) == [{"message": [{"text": "ja:world"}]}, {"message": [{"text": "ru:world"}]}]
    assert state.calls == [(["hello", "world"], [("from", ""), ("to", "ja"), ("to", "ru"), ("isEnterpriseClient", "false")])]
    assert await service.translate(["hello", "world"], "", ["ja", "ru"]) is result
    assert len(state.calls) == 1
    await service.translate(["hello", "world"], "en", ["ja", "ru"])
    await service.translate(["hello", "world"], "", ["ru", "ja"])
    assert len(state.calls) == 3


async def test_optional_dns_resolver_cannot_break_translation(provider, monkeypatch):
    service, state = provider
    incompatible = Mock(side_effect=RuntimeError("aiodns needs a SelectorEventLoop on Windows"))
    monkeypatch.setattr("aiohttp.connector.DefaultResolver", incompatible)
    result = await service.translate(["hello"], "", ["en"])
    assert result.translations == (("en:hello",),)
    incompatible.assert_not_called()
    assert len(state.calls) == 1


@pytest.mark.parametrize("status", [400, 429, 500, 503])
async def test_http_errors_not_cached_or_retried_forever(provider, status):
    service, state = provider
    state.status = status
    for _ in range(2):
        with pytest.raises(TranslationError, match=str(status)):
            await service.translate(["hello"], "", ["ja"])
    assert len(state.calls) == 2 and not service.cache


@pytest.mark.parametrize("payload", [{}, [], [{}], [{"translations": []}],
                                      [{"translations": [{"to": [], "text": "bad"}]}],
                                      [{"translations": [{"to": "ja", "text": ""}]}]])
async def test_missing_translation_is_failure(provider, payload):
    service, state = provider
    state.payload = payload
    with pytest.raises(TranslationError):
        await service.translate(["hello"], "", ["ja"])
    assert not service.cache


async def test_shared_request_survives_one_cancel_and_last_cancel_stops_it(provider):
    service, state = provider
    state.gate = asyncio.Event()
    first = asyncio.create_task(service.translate(["same"], "", ["ja"]))
    second = asyncio.create_task(service.translate(["same"], "", ["ja"]))
    while not state.calls:
        await asyncio.sleep(.001)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    state.gate.set()
    assert (await second).translations == (("ja:same",),)
    assert len(state.calls) == 1
    state.gate.clear()
    third = asyncio.create_task(service.translate(["cancel"], "", ["ja"]))
    while len(state.calls) < 2:
        await asyncio.sleep(.001)
    pending = next(iter(service.pending.values())).task
    third.cancel()
    with pytest.raises(asyncio.CancelledError):
        await third
    await asyncio.sleep(0)
    assert pending.cancelled() and not service.pending


async def test_timeout_and_bounded_cache(provider):
    service, state = provider
    service.timeout = .02
    state.gate = asyncio.Event()
    with pytest.raises(TranslationError, match="超时"):
        await service.translate(["timeout"], "", ["ja"])
    state.gate.set()
    # Use an ordinary timeout for the cache stress check.
    await service.session.close()
    service.session = None
    service.timeout = 8
    for index in range(130):
        await service.translate([str(index)], "", ["ja"])
    assert len(service.cache) == 128
    assert (("0",), "", ("ja",)) not in service.cache


def make_core(tmp_path, service):
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"multitrack.enable": True, "multitrack.target_languages": ["ja", "ru"],
                            "message.quote": False, "message.typewriting": False})
    core = Core(settings, tmp_path)
    core.hub.translator = service
    core.hub.peers["live"] = Peer(None, Profile("live", role="live"))
    return core


async def test_core_send_strips_formatting_and_preserves_literal_translation(provider, tmp_path):
    service, state = provider
    core = make_core(tmp_path, service)
    state.payload = [{"translations": [{"to": "ru", "text": "**literal**"}, {"to": "ja", "text": "@b literal"}]}]
    await core.send("@b**hello**@r")
    frame, _ = core.hub.peers["live"].queue.get_nowait()
    assert state.calls[0][0] == ["hello"]
    assert [p["message"][0]["text"] for p in frame["data"]["messages"][0]["track"]] == ["@b literal", "**literal**"]
    assert editor_text("@b**hello**@r") == "hello"
    assert paragraph_text([{"text": "wire", "typewrite": "ignored"}, {"data": {"emoji": "ignored"}}]) == "wire"


async def test_core_send_strict_failure_skip_and_existing_tracks(provider, tmp_path):
    service, state = provider
    core = make_core(tmp_path, service)
    core.paren_once = True
    core.hub.peers["history"] = Peer(None, Profile("history", role="history"))
    core.hub.osc.send_text = Mock(return_value=True)
    state.status = 503
    with pytest.raises(TranslationError, match="草稿已保留"):
        await core.send("main")
    assert core.paren_once and core.hub.peers["live"].queue.empty()
    assert core.hub.peers["history"].control.empty()
    core.hub.osc.send_text.assert_not_called()
    core.settings.values["multitrack.skip_errors"] = True
    await core.send("main")
    frame, _ = core.hub.peers["live"].queue.get_nowait()
    assert "track" not in frame["data"]["messages"][0]
    assert not core.paren_once
    assert core.hub.peers["history"].control.qsize() == 1
    core.hub.osc.send_text.assert_called_once_with("main")
    calls = len(state.calls)
    await core.send("main", tracks=[{"message": "authored"}])
    assert len(state.calls) == calls


async def test_settings_change_during_translation_keeps_draft(provider, tmp_path):
    service, state = provider
    core = make_core(tmp_path, service)
    state.gate = asyncio.Event()
    task = asyncio.create_task(core.send("main"))
    while not state.calls:
        await asyncio.sleep(.001)
    core.settings.values["multitrack.source_language"] = "en"
    state.gate.set()
    with pytest.raises(TranslationError, match="设置已更改"):
        await task
    assert core.hub.peers["live"].queue.empty()


def ui_app(tmp_path, service):
    from echolivetui.ui import EchoApp
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"multitrack.enable": True, "multitrack.target_languages": ["ja", "ru"],
                            "message.quote": False, "message.typewriting": False, "typing.enable": False})
    app = EchoApp(settings, tmp_path, start_server=False)
    app.core.hub.translator = service

    class Socket:
        async def close(self):
            pass

    app.core.hub.peers["live"] = Peer(Socket(), Profile("live", role="live"))
    return app


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


async def test_ui_debounce_stale_results_settings_and_no_command_requests(provider, tmp_path, monkeypatch):
    from textual.widgets import Input, Static
    from echolivetui.translation_preview import TranslationPreview
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", .05)
    service, state = provider
    app = ui_app(tmp_path, service)
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        preview = app.query_one("#translations", TranslationPreview)
        entry.value = "old"
        app.refresh_status()
        entry.value = "latest"
        app.refresh_status()
        await wait_until(lambda: preview.state == "ready")
        assert [call[0] for call in state.calls] == [["latest"]]
        assert "ja:latest" in preview.query_one(Static).content.plain
        await pilot.pause()
        assert "en" in app.query_one("#preview", Static).content.plain
        assert "主轨：" not in preview.query_one(Static).content.plain
        old_gate = state.gate = asyncio.Event()
        entry.value = "slow"
        app.refresh_status()
        await wait_until(lambda: len(state.calls) == 2)
        assert "主轨：" not in preview.query_one(Static).content.plain
        assert not preview.query_one(Static).content.plain.startswith("\n")
        state.gate = None
        try:
            entry.value = "newest"
            app.refresh_status()
            await wait_until(lambda: preview.state == "ready")
            old_gate.set()
            await pilot.pause()
            assert "ja:newest" in preview.query_one(Static).content.plain
            assert "ja:slow" not in preview.query_one(Static).content.plain
        finally:
            old_gate.set()
        app.core.settings.values["multitrack.source_language"] = "en"
        app.refresh_status()
        await wait_until(lambda: preview.state == "ready")
        assert ("from", "en") in state.calls[-1][1]
        count = len(state.calls)
        entry.value = "/help"
        app.refresh_status()
        await pilot.pause(.1)
        assert not preview.display and len(state.calls) == count
        app.core.settings.values["multitrack.enable"] = False
        entry.value = "disabled"
        app.refresh_status()
        await pilot.pause(.1)
        assert not preview.display and len(state.calls) == count


async def test_ui_enter_flushes_debounce_is_readonly_and_cancellable(provider, tmp_path, monkeypatch):
    from textual.widgets import Input
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", 10)
    service, state = provider
    app = ui_app(tmp_path, service)
    state.gate = asyncio.Event()
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "waiting"
        await pilot.pause()
        assert not state.calls
        await pilot.press("enter")
        await wait_until(lambda: bool(state.calls))
        assert app.submission_task and entry.has_class("sending")
        await pilot.press("x", "enter")
        assert entry.value == "waiting" and len(state.calls) == 1
        await pilot.press("escape")
        await wait_until(lambda: app.submission_task is None)
        assert entry.value == "waiting" and app.core.hub.peers["live"].queue.empty()
        state.gate.set()
        await pilot.press("enter")
        await wait_until(lambda: app.submission_task is None)
        assert entry.value == "" and app.core.hub.peers["live"].queue.qsize() == 1


async def test_ui_session_initialization_failure_shows_error(provider, tmp_path, monkeypatch):
    from textual.widgets import Input, Static
    from echolivetui.translation_preview import TranslationPreview
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", .01)
    monkeypatch.setattr("aiohttp.ClientSession", Mock(side_effect=RuntimeError("session initialization failed")))
    service, state = provider
    app = ui_app(tmp_path, service)
    async with app.run_test() as pilot:
        app.query_one("#entry", Input).value = "hello"
        preview = app.query_one("#translations", TranslationPreview)
        await wait_until(lambda: preview.state == "error")
        assert "session initialization failed" in preview.query_one(Static).content.plain
        assert not state.calls


async def test_ui_cached_preview_does_not_wait_for_debounce(provider, tmp_path, monkeypatch):
    from textual.widgets import Input, Static
    from echolivetui.translation_preview import TranslationPreview
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", 30)
    service, state = provider
    await service.translate(["hello"], "", ["ja", "ru"])
    app = ui_app(tmp_path, service)
    async with app.run_test() as pilot:
        app.query_one("#entry", Input).value = "hello"
        preview = app.query_one("#translations", TranslationPreview)
        await wait_until(lambda: preview.state == "ready")
        assert "ja:hello" in preview.query_one(Static).content.plain
        assert len(state.calls) == 1 and preview.request_task is None


@pytest.mark.parametrize("eager", [False, True])
async def test_ui_send_cached_translation_then_exit(provider, tmp_path, monkeypatch, eager):
    from textual.widgets import Input
    from echolivetui.translation_preview import TranslationPreview
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", .01)
    service, state = provider
    app = ui_app(tmp_path, service)
    app.core.settings.values["message.typewriting"] = True
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        if eager:
            loop.set_task_factory(asyncio.eager_task_factory)
        try:
            entry = app.query_one("#entry", Input)
            entry.value = "你好，世界！"
            translated = app.query_one(TranslationPreview)
            await wait_until(lambda: translated.state == "ready")
            await pilot.press("enter")
            await wait_until(lambda: app.submission_task is None)
            assert not entry.has_class("sending") and entry.value == ""
            assert app.core.hub.peers["live"].queue.qsize() == 1
            assert len(state.calls) == 1
            entry.value = "你好，世界！"
            await pilot.press("enter")
            await wait_until(lambda: app.submission_task is None)
            assert entry.value == "" and app.core.hub.peers["live"].queue.qsize() == 2
            assert len(state.calls) == 1
            entry.value = "/status"
            await pilot.press("enter")
            await wait_until(lambda: app.submission_task is None)
            assert entry.value == ""
            entry.value = "/exit"
            await pilot.press("enter")
            await wait_until(lambda: not app.is_running)
        finally:
            loop.set_task_factory(previous_factory)


@pytest.mark.parametrize("skip", [False, True])
async def test_ui_translation_failure_keeps_or_sends_draft(provider, tmp_path, monkeypatch, skip):
    from textual.widgets import Input
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", 10)
    service, state = provider
    state.status = 503
    app = ui_app(tmp_path, service)
    app.core.settings.values["multitrack.skip_errors"] = skip
    async with app.run_test() as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "draft"
        await pilot.press("enter")
        await wait_until(lambda: app.submission_task is None)
        assert entry.value == ("" if skip else "draft")
        assert app.core.hub.peers["live"].queue.qsize() == int(skip)
        assert len(state.calls) == 1


@pytest.mark.parametrize("width", [80, 40])
async def test_ui_preview_scrolls_below_input_and_compose_tracks_cursor(provider, tmp_path, monkeypatch, width):
    from textual.widgets import Checkbox, Input, TextArea
    from echolivetui.translation_preview import TranslationPreview
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", .02)
    service, state = provider
    app = ui_app(tmp_path, service)
    app.core.settings.values.update({"multitrack.target_languages": ["ja", "en", "ru", "zh-Hans"], "input.preview": False})
    async with app.run_test(size=(width, 24)) as pilot:
        entry = app.query_one("#entry", Input)
        entry.value = "long text " * 50
        preview = app.query_one("#translations", TranslationPreview)
        await wait_until(lambda: preview.state == "ready")
        await pilot.pause()
        assert preview.region.y >= entry.region.bottom
        assert preview.size.height <= 8 and preview.max_scroll_y > 0
        assert app.query_one("#submission").region.bottom <= app.query_one("#footer").region.y
        app.core.compose_draft = "first\nsecond"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        translated = page.query_one(TranslationPreview)
        await wait_until(lambda: translated.state == "ready")
        assert translated.key[0] == "first\nsecond"
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one(TextArea).move_cursor((1, 0))
        await wait_until(lambda: translated.key and translated.key[0] == "second" and translated.state == "ready")
        assert page.query_one("#send").region.bottom <= page.region.bottom


async def test_ui_performance_translates_and_waits_for_each_printed_line(provider, tmp_path, monkeypatch):
    from textual.widgets import Button, Checkbox, Input, TextArea
    from echolivetui.history import plain_message
    monkeypatch.setattr("echolivetui.translation_preview.DEBOUNCE", 10)
    service, state = provider
    app = ui_app(tmp_path, service)
    printed = []

    async def acknowledge():
        peer = app.core.hub.peers["live"]
        for _ in range(2):
            envelope, _ = await peer.queue.get()
            printed.append(envelope)
            receipt = peer.playbacks[id(envelope)]
            receipt.sent.set()
            peer.observe_playback("echo_printing", {"username": envelope["data"]["username"],
                                                    "message": plain_message(envelope["data"]["messages"][0]["message"])})
            peer.observe_playback("echo_state_update", {"state": "stop"})

    async with app.run_test() as pilot:
        app.core.compose_draft = "one\n\ntwo"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one(TextArea).move_cursor((0, 0))
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "0"
        await pilot.pause()
        task = asyncio.create_task(acknowledge())
        page.query_one("#send", Button).press()
        async with asyncio.timeout(3):
            await task
        await wait_until(lambda: app.active_page is None)
        assert [c[0] for c in state.calls] == [["one"], ["two"]]
        assert [p["data"]["messages"][0]["track"][0]["message"][0]["text"] for p in printed] == ["ja:one", "ja:two"]
        assert app.core.compose_draft == ""
