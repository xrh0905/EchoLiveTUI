"""Create review snapshots without touching the user's configuration."""
import asyncio
from pathlib import Path
import tempfile
from echolivetui.config import Settings
from echolivetui.ui import EchoApp


async def main():
    output = Path(".artifacts").resolve()
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        app = EchoApp(Settings(Path(temp) / "settings.yaml"), Path(temp), start_server=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.report("消息记录支持鼠标框选；主输入框保持键盘焦点。")
            app.query_one("#entry").value = "@b实时粗体@r @[#66ccff]蓝色文字@r · 可以框选应用样式"
            await pilot.pause()
            app.save_screenshot("main.svg", path=str(output))
            await pilot.press("shift+enter")
            await pilot.pause()
            app.save_screenshot("compose.svg", path=str(output))
            await pilot.press("escape")
            app.open_screen("settings", ["log"])
            await pilot.pause()
            app.save_screenshot("settings.svg", path=str(output))
    print(output)


asyncio.run(main())
