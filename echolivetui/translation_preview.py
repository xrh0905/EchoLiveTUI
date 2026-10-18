"""Debounced translation preview; only the current editor revision may render."""
import asyncio

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from .languages import language_label
from .translation import TranslationError, editor_text

DEBOUNCE = .5


class TranslationPreview(VerticalScroll):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.key = None
        self.request_task = None
        self.state = "empty"

    def compose(self):
        yield Static("", markup=False)

    def on_mount(self):
        self.display = False
        self.limit_height()

    def limit_height(self):
        self.styles.max_height = max(1, min(8, self.app.size.height // 3))

    def on_resize(self):
        self.limit_height()

    def clear(self):
        self.key = None
        self.state = "empty"
        self.display = False
        if self.request_task:
            self.request_task.cancel()
            self.request_task = None

    def show_text(self, text, *, literal=False):
        core = self.app.core
        config = core.settings.group("multitrack")
        if not config["enable"] or not text.strip() or (not literal and text.startswith("/") and not text.startswith("//")):
            self.clear()
            return
        if not literal and text.startswith("//"):
            text = text[1:]
        plain = editor_text(text)
        if not plain.strip():
            self.clear()
            return
        key = plain, config["source_language"], tuple(config["target_languages"])
        if key == self.key:
            return
        self.clear()
        self.key, self.state, self.display = key, "waiting", True
        cached = core.hub.translator.cached([plain], key[1], key[2])
        if cached is not None:
            self.state = "ready"
            self.render_status("", result=cached)
            return
        self.render_status("等待翻译")
        self.request_task = asyncio.create_task(self.load(key, debounce=True))

    def render_status(self, status, *, result=None):
        if self.key is None:
            return
        _, source, targets = self.key
        detected = result.detected_languages[0] if result else None
        language = source or detected
        text = Text("主轨：" + language_label(language) if language else "")
        for index, code in enumerate(targets):
            text.append(("\n" if text else "") + language_label(code) + "：", style="#9eaeb9")
            text.append(result.translations[0][index] if result else status)
        self.query_one(Static).update(text)

    async def load(self, key, *, debounce):
        try:
            if debounce:
                await asyncio.sleep(DEBOUNCE)
            if self.key != key:
                return
            self.state = "loading"
            self.render_status("翻译中…")
            plain, source, targets = key
            result = await self.app.core.hub.translator.translate([plain], source, targets)
            if self.key == key and self.is_mounted:
                self.state = "ready"
                self.render_status("", result=result)
        except TranslationError as exc:
            if self.key == key and self.is_mounted:
                self.state = "error"
                self.render_status(str(exc))

    def flush(self):
        """Start immediately so Core.send can share the same request."""
        if self.key is not None and self.state in {"waiting", "error"}:
            if self.request_task:
                self.request_task.cancel()
            self.request_task = asyncio.create_task(self.load(self.key, debounce=False))

    def on_unmount(self):
        self.clear()
