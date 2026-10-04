"""Optional integration check: pip install playwright; uses installed Edge on Windows."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from playwright.async_api import async_playwright
from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.lan import choose_address


async def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".reference/Echo-Live").resolve()
    before = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("config.js", "res/class/EchoLiveSystem.js", "res/class/UniverseWindow.js") if (root / name).is_file()}
    with tempfile.TemporaryDirectory() as temporary:
        settings = Settings(Path(temporary) / "s.yaml")
        settings.values.update({"message.typewriting": False, "message.quote": False, "typing.enable": True})
        logs = []
        core = Core(settings, root, logs.append)
        await core.server.start("127.0.0.1", 0)
        port = core.server.site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        lan = choose_address()
        await core.server.set_lan(lan)
        failures, errors = [], []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel="msedge", headless=True, args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"])
                context = await browser.new_context()
                if core.server.hosting.version == "1.6.6":
                    # Old editor's first-run tutorial races translation loading;
                    # exercise sending with onboarding already completed.
                    await context.add_init_script("localStorage.setItem('echolive', JSON.stringify({data_version: 1, tutorial: {editor_overview: true}}));")
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
                playback = []
                core.submit("TUI", playback=playback)
                await core.wait_for_printing(playback)
                assert len(playback) == 2 and all(receipt.finished.is_set() for receipt in playback)
                assert all([await page.evaluate("echo.state === 'stop'") for page in pages[:2]])
                for page in pages:
                    await page.bring_to_front()
                    await page.wait_for_function("document.body.textContent.includes('TUI')", polling=100)
                # Upstream intentionally hides the newest history row by default.
                history = await pages[2].locator("#echo-live-history-message-list").text_content()
                assert history.count("TUI") == 1, history
                assert "TUI" in await pages[2].locator("#echo-live-history-message-list").inner_text()
                assert await pages[2].evaluate("config.history.message.latest_message_hide") is False
                assert await pages[0].evaluate("echo.printSpeedStart") == settings["message.print_speed"]
                assert await pages[0].evaluate("config.editor.websocket.disable_broadcast") is True
                await pages[0].evaluate("""() => {
                    window.eltuiShoutSeen = false;
                    new MutationObserver(() => {
                        if (document.querySelector('#echo-live').classList.contains('event-shout')) window.eltuiShoutSeen = true;
                    }).observe(document.querySelector('#echo-live'), {attributes: true});
                }""")
                core.submit("@shout@bFORMAT_BOLD@r @[#66ccff]FORMAT_BLUE@r @rainbowRAINBOW@r @@END")
                await pages[0].bring_to_front()
                await pages[0].wait_for_function("document.body.textContent.includes('@END')", polling=100)
                assert "FORMAT_BOLD" in "".join(await pages[0].locator(".echo-text-bold").all_text_contents())
                assert "RAINBOW" in "".join(await pages[0].locator(".echo-text-rainbow").all_text_contents())
                assert await pages[0].evaluate("window.eltuiShoutSeen")
                symbols = "————……！！！？？？💥👩‍💻"
                settings.values["message.typewriting"] = True
                for scheme in ("pinyin", "zhuyin"):
                    settings.values["message.typewriting_scheme"] = scheme
                    playback = []
                    prefix = f"SYMBOLS_{scheme} "
                    core.submit(prefix + "@b你好" + symbols + "@r再见", playback=playback)
                    try:
                        await core.wait_for_printing(playback)
                    except ValueError:
                        print("Print diagnostics:", json.dumps({
                            "errors": errors,
                            "receipts": [{"started": receipt.started, "message": receipt.message} for receipt in playback],
                            "pages": [await page.evaluate("({state: echo.state, message: echo.message, rendered: document.querySelector('.echo-output').textContent})") for page in pages[:2]],
                        }, ensure_ascii=True), flush=True)
                        raise
                    for page in pages[:2]:
                        rendered = await page.locator('.echo-output').text_content()
                        assert rendered.replace('\u200b', '').replace('\u2002', ' ') == prefix + "你好" + symbols + "再见", rendered
                        assert symbols in ''.join(await page.locator('.echo-text-bold').all_text_contents()).replace('\u200b', '')
                settings.values.update({"message.typewriting": False, "message.typewriting_scheme": "pinyin"})
                print('PASS literal symbol runs: pinyin, zhuyin, long punctuation, emoji, styles and print completion', flush=True)
                editor = await context.new_page()
                editor.on("pageerror", lambda error: errors.append(error.stack))
                await editor.goto(f"http://{lan}:{port}/editor.html")
                close_prompt = editor.locator('.fh-window-controller-button[data-controller-id="no"]')
                if await close_prompt.count():
                    await close_prompt.click()
                await editor.locator("#ptext-content").fill("LAN_EDITOR_TEST")
                await editor.locator("#ptext-btn-send").click()
                for page in pages:
                    await page.bring_to_front()
                    await page.wait_for_function("document.body.textContent.includes('LAN_EDITOR_TEST')", polling=100)
                assert (await pages[2].locator("#echo-live-history-message-list").text_content()).count("LAN_EDITOR_TEST") == 1
                assert any(p.profile.role == "server" for p in core.hub.peers.values())
                assert any("LAN_EDITOR_TEST" in line for line in logs)
                await core.server.set_lan(None)
                assert not failures, failures
                assert not errors, errors
                print(f"PASS {core.server.hosting.version}: LAN editor, two live pages, independent history without duplicates, no missing resources or page errors", flush=True)
                await context.close()
                await browser.close()
        finally:
            await core.close()
        assert {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in before} == before


asyncio.run(main())
