"""Optional integration check: pip install playwright; uses installed Edge on Windows."""
import asyncio
import hashlib
from pathlib import Path
import sys
import tempfile

from playwright.async_api import async_playwright
from echolivetui.config import Settings
from echolivetui.core import Core


async def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".reference/Echo-Live").resolve()
    before = hashlib.sha256((root / "config.js").read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as temporary:
        settings = Settings(Path(temporary) / "s.yaml")
        settings.values.update({"message.typewriting": False, "message.quote": False, "typing.enable": True})
        logs = []
        core = Core(settings, root, logs.append)
        await core.server.start("127.0.0.1", 0)
        port = core.server.site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        failures, errors = [], []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel="msedge", headless=True, args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"])
                context = await browser.new_context()
                pages = []
                for filename in ("live.html", "live.html", "history.html"):
                    page = await context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: failures.append(response.url) if response.status >= 400 else None)
                    await page.goto(url + "/" + filename)
                    print("Loaded", filename, flush=True)
                    pages.append(page)
                for _ in range(100):
                    if len(core.hub.targets()) == 2 and any(p.profile.role == "history" for p in core.hub.peers.values()):
                        break
                    await asyncio.sleep(.1)
                assert len(core.hub.targets()) == 2, [(p.profile.role, p.profile.name) for p in core.hub.peers.values()]
                assert all(p.profile.version == core.server.hosting.version for p in core.hub.peers.values())
                core.submit("TUI")
                for page in pages:
                    await page.bring_to_front()
                    await page.wait_for_function("document.body.textContent.includes('TUI')", polling=100)
                # Upstream intentionally hides the newest history row by default.
                history = await pages[2].locator("#echo-live-history-message-list").text_content()
                assert history.count("TUI") == 1, history
                assert await pages[0].evaluate("config.editor.websocket.disable_broadcast") is True
                assert not failures, failures
                assert not errors, errors
                print(f"PASS {core.server.hosting.version}: two live pages, one history entry, no missing resources or page errors", flush=True)
                await browser.close()
        finally:
            await core.close()
        assert hashlib.sha256((root / "config.js").read_bytes()).hexdigest() == before


asyncio.run(main())
