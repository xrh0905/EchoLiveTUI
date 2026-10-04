"""Wait for a subtitle's print completion, independently of window visibility."""
import asyncio
from dataclasses import dataclass, field
import time

from .history import plain_message


class DeliveryUnavailable(ValueError):
    """A recoverable output failure, with the disconnected target identities."""
    def __init__(self, message, endpoints=()):
        super().__init__(message)
        self.endpoints = endpoints


@dataclass(eq=False)
class PrintingReceipt:
    username: str
    message: str
    delay: float
    endpoint: tuple[str, str] = ("", "")
    sent: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False
    completed_at: float = 0.0
    error: str = ""

    def observe(self, action, data):
        if not self.sent.is_set() or self.finished.is_set():
            return
        if action == "echo_printing":
            # Echo Live's mood-symbol filter inserts invisible word boundaries.
            username = data.get("username", "")
            self.started = (
                isinstance(username, str)
                and username.replace("\u200b", "") == self.username.replace("\u200b", "")
                and plain_message(data.get("message", "")).replace("\u200b", "") == self.message.replace("\u200b", "")
            )
        elif action == "echo_state_update" and data.get("state") == "stop" and self.started:
            self.completed_at = time.monotonic()
            self.finished.set()

    def fail(self, message):
        if not self.finished.is_set():
            self.error = message
            self.sent.set()
            self.finished.set()

    async def wait(self):
        await self.sent.wait()
        try:
            async with asyncio.timeout(max(15, self.delay * 4 + 5)):
                await self.finished.wait()
        except TimeoutError:
            raise ValueError("字幕端未反馈打印完成，演出已暂停；请检查连接后继续或取消") from None
        if self.error:
            raise DeliveryUnavailable(self.error, (self.endpoint,))
        return self.completed_at
