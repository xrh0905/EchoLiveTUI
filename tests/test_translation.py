import asyncio
from types import SimpleNamespace

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


@pytest.mark.parametrize("status", [400, 429, 500, 503])
async def test_http_errors_not_cached_or_retried_forever(provider, status):
    service, state = provider
    state.status = status
    for _ in range(2):
        with pytest.raises(TranslationError, match=str(status)):
            await service.translate(["hello"], "", ["ja"])
    assert len(state.calls) == 2 and not service.cache


@pytest.mark.parametrize("payload", [{}, [], [{}], [{"translations": []}],
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
    state.status = 503
    with pytest.raises(TranslationError, match="草稿已保留"):
        await core.send("main")
    assert core.paren_once and core.hub.peers["live"].queue.empty()
    core.settings.values["multitrack.skip_errors"] = True
    await core.send("main")
    frame, _ = core.hub.peers["live"].queue.get_nowait()
    assert "track" not in frame["data"]["messages"][0]
    assert not core.paren_once
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
