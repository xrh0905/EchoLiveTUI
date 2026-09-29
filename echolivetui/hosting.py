"""Read-only Echo Live hosting with a response-only configuration overlay."""
from __future__ import annotations

import json
from pathlib import Path
import re
from aiohttp import web

REQUIRED = ("live.html", "config.js", "res/class/EchoLive.js", "res/class/EchoLiveBroadcast.js")
ROOT_FILES = {"live.html", "history.html", "character.html", "editor.html", "settings.html", "template.html", "index.html", "config.js", "app.js", "start.js", "extensions.js", "favicon.ico", "favicon.png", "manifest.json"}
DIRECTORIES = {"res", "lib", "lang", "extensions"}
ASSET_EXTENSIONS = {".html", ".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".ogg", ".wav", ".mp4", ".webm"}


class Hosting:
    def __init__(self, cwd: Path, token: str, settings):
        root = cwd.resolve()
        self.root = root if all((root / p).is_file() for p in REQUIRED) else None
        self.token, self.settings = token, settings
        self.version = None
        if self.root and (root / "app.js").is_file():
            match = re.search(r"version\s*:\s*['\"](\d+\.\d+\.\d+)['\"]", (root / "app.js").read_text(encoding="utf-8"))
            self.version = match.group(1) if match else None

    def metadata(self, request):
        if self.root and request.query.get("tui") == self.token:
            page = request.query.get("page")
            if page in {"live", "history", "character", "editor"}:
                return {"hosted_version": self.version, "page": page}
        return None

    def overlay(self):
        return """
;(() => {
  if (location.pathname.endsWith('/settings.html')) return;
  const page = location.pathname.split('/').pop().replace(/\\.html$/, '');
  const url = new URL('/ws', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('tui', TOKEN);
  url.searchParams.set('page', page);
  const b = config.echolive.broadcast;
  b.enable = true;
  b.websocket_enable = true;
  b.websocket_url = url.href;
  b.channel = 'echolivetui:' + TOKEN;
  if (config.echolive.messages_polling) config.echolive.messages_polling.enable = false;
  const e = config.editor.websocket;
  e.enable = true;
  e.url = url.href;
  e.auto_url = false;
  e.disable_broadcast = true;
  if (config.echolive.typing) config.echolive.typing.enable = TYPING;
  if (config.history && config.history.message) {
    config.history.message.latest_message_hide = false;
    config.history.message.live_display_hidden_latest_message_show = false;
  }
})();
""".replace("TOKEN", json.dumps(self.token)).replace("TYPING", json.dumps(self.settings["typing.enable"]))

    async def serve(self, request):
        if not self.root:
            raise web.HTTPNotFound()
        relative = request.match_info.get("path", "")
        if not relative:
            raise web.HTTPFound("/live.html")
        parts = Path(relative).parts
        if not parts or any(p.startswith(".") or p in {"..", "/", "\\"} for p in parts) or "\\" in relative or ":" in relative:
            raise web.HTTPNotFound()
        if relative not in ROOT_FILES and parts[0] not in DIRECTORIES:
            raise web.HTTPNotFound()
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.suffix.lower() not in ASSET_EXTENSIONS:
            raise web.HTTPNotFound()
        if relative == "config.js":
            return web.Response(text=path.read_text(encoding="utf-8-sig") + self.overlay(), content_type="application/javascript", headers={"Cache-Control": "no-store"})
        if relative == "res/class/EchoLiveSystem.js" and self.version == "1.8.12":
            # 1.8.12 inserts dependencies first but dynamic scripts default to
            # async, so editor-help can execute before editor. Preserve explicit
            # async resources, and execute the remainder in insertion order.
            source = path.read_text(encoding="utf-8-sig")
            source = source.replace("if (async) script.async = true;", "script.async = Boolean(async);")
            return web.Response(text=source, content_type="application/javascript", headers={"Cache-Control": "no-store"})
        if relative == "res/class/UniverseWindow.js" and self.version == "1.8.12":
            source = path.read_text(encoding="utf-8-sig")
            source = source.replace("if (data.closable) $(`.fh-window", "if (this.windowList[index]?.data?.closable !== false) $(`.fh-window")
            return web.Response(text=source, content_type="application/javascript", headers={"Cache-Control": "no-store"})
        return web.FileResponse(path)
