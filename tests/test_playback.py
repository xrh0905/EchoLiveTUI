import asyncio
import time

import pytest
from textual.widgets import Button, Checkbox, Input, Static, TextArea

from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.history import entries_from_data
from echolivetui.ui import EchoApp


class Socket:
    def __init__(self):
        self.sent = asyncio.Queue()

    async def send_json(self, frame):
        await self.sent.put(frame)

    async def close(self):
        pass


async def feedback(core, ws, uid, action, *, name=None, **data):
    return await core.hub.receive(ws, {"action": action, "from": {"uuid": uid, "type": "live", "name": name}, "data": data})


async def begin_print(core, ws, uid, frame):
    entry = next(entries_from_data(frame["data"]))
    await feedback(core, ws, uid, "echo_printing", username=entry.username, message=entry.message)
    await feedback(core, ws, uid, "echo_state_update", state="ready", messagesCount=0)
    await feedback(core, ws, uid, "echo_state_update", state="play", messagesCount=0)


async def message_frame(ws):
    async with asyncio.timeout(3):
        while True:
            frame = await ws.sent.get()
            if frame["action"] == "message_data":
                return frame


async def test_waits_for_matching_print_completion_on_all_targets(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    a, b = Socket(), Socket()
    await feedback(core, a, "a", "hello")
    await feedback(core, b, "b", "hello")
    try:
        receipts = []
        core.submit("sentence", playback=receipts)
        frame_a, frame_b = await asyncio.gather(message_frame(a), message_frame(b))
        wait = asyncio.create_task(core.wait_for_printing(receipts))
        await feedback(core, a, "a", "echo_printing", username="other", message="unrelated")
        await feedback(core, a, "a", "echo_state_update", state="stop")
        await asyncio.sleep(.01)
        assert not wait.done()
        await begin_print(core, a, "a", frame_a)
        await begin_print(core, b, "b", frame_b)
        await feedback(core, a, "a", "live_display_update", display=False)
        await feedback(core, b, "b", "page_hidden")
        await asyncio.sleep(.01)
        assert not wait.done()
        await feedback(core, a, "a", "echo_state_update", state="stop")
        await asyncio.sleep(.01)
        assert not wait.done()
        await feedback(core, b, "b", "echo_state_update", state="stop")
        assert await asyncio.wait_for(wait, 1) > 0
        assert all(receipt.finished.is_set() for receipt in receipts)
    finally:
        await core.close()


async def test_disconnect_pauses_instead_of_advancing(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    ws = Socket()
    peer = await feedback(core, ws, "live", "hello")
    try:
        receipts = []
        core.submit("sentence", playback=receipts)
        await message_frame(ws)
        wait = asyncio.create_task(core.wait_for_printing(receipts))
        await core.hub.disconnect(peer)
        with pytest.raises(ValueError, match="断开"):
            await wait
    finally:
        await core.close()


async def test_performance_interval_begins_after_printing_and_last_line_finishes(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    ws = Socket()
    await feedback(app.core, ws, "live", "hello")
    async with app.run_test(size=(80, 24)) as pilot:
        app.core.compose_draft = "first\nsecond"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "200"
        await pilot.click("#send")
        first = await message_frame(ws)
        await begin_print(app.core, ws, "live", first)
        # Printing deliberately lasts longer than the configured interval.
        pending = asyncio.create_task(message_frame(ws))
        await asyncio.sleep(.3)
        assert not pending.done()
        assert page.query_one(TextArea).cursor_location == (0, 0)
        finished_at = time.monotonic()
        await feedback(app.core, ws, "live", "echo_state_update", state="stop")
        await asyncio.sleep(.1)
        assert not pending.done()
        second = await asyncio.wait_for(pending, 2)
        assert time.monotonic() - finished_at >= .19
        await begin_print(app.core, ws, "live", second)
        await asyncio.sleep(.05)
        assert app.active_page is page
        await feedback(app.core, ws, "live", "echo_state_update", state="stop")
        await pilot.pause()
        assert app.active_page is None and app.core.compose_draft == ""


async def test_pause_retains_inflight_completion_and_cancel_releases_tracking(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    ws = Socket()
    peer = await feedback(app.core, ws, "live", "hello")
    async with app.run_test() as pilot:
        app.core.compose_draft = "first\nsecond"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "0"
        await pilot.click("#send")
        frame = await message_frame(ws)
        await begin_print(app.core, ws, "live", frame)
        await pilot.click("#send")
        await pilot.pause()
        assert page.paused and page.pending_playback
        await pilot.click("#send")
        pending = asyncio.create_task(message_frame(ws))
        await asyncio.sleep(.1)
        assert not pending.done()
        await feedback(app.core, ws, "live", "echo_state_update", state="stop")
        await asyncio.wait_for(pending, 2)
        await pilot.press("escape")
        await pilot.pause()
        assert not peer.playbacks and app.active_page is None


async def test_real_completion_releases_estimated_queue_delay(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    core.settings.values["message.print_speed"] = 60000
    ws = Socket()
    await feedback(core, ws, "live", "hello")
    try:
        receipts = []
        core.submit("first", playback=receipts)
        frame = await message_frame(ws)
        await begin_print(core, ws, "live", frame)
        await feedback(core, ws, "live", "echo_state_update", state="stop")
        await core.wait_for_printing(receipts)
        core.submit("second")
        assert await asyncio.wait_for(message_frame(ws), 1)
    finally:
        await core.close()


async def test_pause_on_last_line_waits_without_resending(tmp_path):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    ws = Socket()
    await feedback(app.core, ws, "live", "hello")
    async with app.run_test() as pilot:
        app.core.compose_draft = "last"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "0"
        await pilot.click("#send")
        frame = await message_frame(ws)
        await begin_print(app.core, ws, "live", frame)
        await pilot.click("#send")
        await pilot.pause()
        assert page.query_one(TextArea).cursor_location == (0, 4)
        await pilot.click("#send")
        await feedback(app.core, ws, "live", "echo_state_update", state="stop")
        await pilot.pause()
        assert app.active_page is None
        assert not any(frame["action"] == "message_data" for frame in list(ws.sent._queue))


@pytest.mark.parametrize("line_mode", [False, True])
async def test_no_output_error_clears_on_connection_without_reopening(tmp_path, line_mode):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    async with app.run_test() as pilot:
        app.core.compose_draft = "retained draft"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = line_mode
        await pilot.click("#send")
        await pilot.pause()
        assert "没有可发送" in str(page.query_one("#compose-error", Static).content)
        ws = Socket()
        await feedback(app.core, ws, "live", "hello")
        await pilot.pause(.3)
        assert app.active_page is page
        assert page.query_one("#compose-error", Static).content == ""
        assert page.query_one(TextArea).text == "retained draft"
        assert not page.send_task
        assert not any(frame["action"] == "message_data" for frame in list(ws.sent._queue))
        assert page.query_one("#send", Button).label.plain == ("继续发送" if line_mode else "发送")


@pytest.mark.parametrize("new_uid", ["live", "reconnected"])
async def test_disconnect_error_clears_and_resume_resends_failed_row(tmp_path, new_uid):
    app = EchoApp(Settings(tmp_path / "s.yaml"), tmp_path, start_server=False)
    ws = Socket()
    peer = await feedback(app.core, ws, "live", "hello", name="字幕端1")
    async with app.run_test() as pilot:
        app.core.compose_draft = "first\nsecond"
        app.open_screen("compose")
        await pilot.pause()
        page = app.active_page
        page.query_one("#line-mode", Checkbox).value = True
        page.query_one("#line-interval", Input).value = "0"
        await pilot.click("#send")
        frame = await message_frame(ws)
        await begin_print(app.core, ws, "live", frame)
        await app.core.hub.disconnect(peer)
        await pilot.pause()
        assert page.paused and "断开" in str(page.query_one("#compose-error", Static).content)
        replacement = Socket()
        await feedback(app.core, replacement, new_uid, "hello", name="字幕端1")
        await pilot.pause(.3)
        assert page.query_one("#compose-error", Static).content == ""
        assert "发送失败" not in str(page.query_one("#performance-state", Static).content)
        assert page.paused and not page.pending_playback and page.send_task is None
        assert page.query_one(TextArea).cursor_location == (0, 0)
        await pilot.click("#send")
        frame = await message_frame(replacement)
        assert "first" in next(entries_from_data(frame["data"])).message
        await begin_print(app.core, replacement, new_uid, frame)
        await feedback(app.core, replacement, new_uid, "echo_state_update", state="stop")
        frame = await message_frame(replacement)
        await begin_print(app.core, replacement, new_uid, frame)
        await feedback(app.core, replacement, new_uid, "echo_state_update", state="stop")
        await pilot.pause()
        assert app.active_page is None and app.core.compose_draft == ""
