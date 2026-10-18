"""Packaged runtime verification; deliberately independent of user configuration."""
import asyncio
from pathlib import Path
import tempfile
from aiohttp import ClientSession, web
from .config import Settings
from .core import Core
from .pipeline import prepare
from .lan import terminal_qr, interfaces
from .ui import EchoApp
from .languages import language_options
from .language_controls import TargetLanguages
from .translation import TranslationService


async def run():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        settings = Settings(root / "test.yaml")
        assert prepare("打包验证 Hello", settings).data["messages"]
        assert "█" in terminal_qr("http://192.168.1.2:3000/editor.html")
        interfaces()
        core = Core(settings, root)
        try:
            await core.server.start("127.0.0.1", 0)
            port = core.server.address[1]
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/healthz") as response:
                    assert response.status == 200
                async with client.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                    assert (await ws.receive_json())["action"] == "ping"
                    await ws.send_json({"action": "hello", "from": {"uuid": "self-test-live", "type": "live"}, "data": {}})
                    async with asyncio.timeout(5):
                        while not core.hub.targets():
                            await asyncio.sleep(.01)
                    core.submit("打包测试")
                    assert (await ws.receive_json())["action"] == "message_data"
        finally:
            await core.close()
        assert [code for _, code in language_options()][:4] == ["zh-Hans", "en", "ja", "ru"]
        async def translate(request):
            texts = await request.json()
            return web.json_response([{"detectedLanguage": {"language": "en"}, "translations": [
                {"to": code, "text": code + ":" + text} for code in request.query.getall("to")]} for text in texts])
        provider = web.Application()
        provider.router.add_post("/translate", translate)
        runner = web.AppRunner(provider)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        service = TranslationService(endpoint=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/translate")
        try:
            translated = await service.translate(["test"], "", ["ja", "ru"])
            assert translated.translations == (("ja:test", "ru:test"),)
            assert translated.detected_languages == ("en",)
        finally:
            await service.close()
            await runner.cleanup()
        app = EchoApp(settings, root, start_server=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.open_screen("settings", ["network"])
            await pilot.pause()
            await pilot.press("escape")
            app.open_screen("settings", ["multitrack"])
            await pilot.pause()
            assert app.active_page.query_one(TargetLanguages).value == []
            await pilot.press("escape")
            app.open_screen("connect")
            await pilot.pause()
    print("SELF-TEST PASS: Textual, message dictionaries, QR, interfaces, HTTP, WebSocket and multitrack translation", flush=True)
