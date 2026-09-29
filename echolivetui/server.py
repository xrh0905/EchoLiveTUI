"""aiohttp lifetime independent of the terminal UI."""
from __future__ import annotations
import json
from aiohttp import web, WSMsgType
from .hosting import Hosting


class Server:
    def __init__(self, hub, cwd):
        self.hub = hub
        self.hosting = Hosting(cwd, hub.sender.uuid, hub.settings)
        self.runner = None
        self.site = None
        self.address = None
        self.error = ""

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1024 * 1024)
        await ws.prepare(request)
        # Discover clients even when their first hello was emitted before WS open.
        await ws.send_json(self.hub.sender.envelope("ping", {}))
        peer = None
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        peer = await self.hub.receive(ws, json.loads(msg.data), request.remote or "", self.hosting.metadata(request))
                    except (ValueError, TypeError, KeyError) as exc:
                        self.hub.log(f"无效客户端消息：{exc}", "debug")
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if peer:
                await self.hub.disconnect(peer)
        return ws

    async def root(self, request):
        if web.WebSocketResponse().can_prepare(request).ok:
            return await self.websocket(request)
        return await self.hosting.serve(request)

    async def health(self, request):
        return web.json_response({"status": "ok", "clients": len(self.hub.peers), "hosting": self.hosting.root is not None})

    async def start(self, host=None, port=None):
        host = self.hub.settings["listen.host"] if host is None else host
        port = self.hub.settings["listen.port"] if port is None else port
        if self.runner is None:
            app = web.Application()
            app.router.add_get("/ws", self.websocket)
            app.router.add_get("/healthz", self.health)
            app.router.add_get("/", self.root)
            app.router.add_get("/{path:.*}", self.hosting.serve)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        previous = self.site
        old_address = self.address
        overlapping = previous is not None and old_address[1] == port and old_address != (host, port)
        if overlapping:
            await previous.stop()
        try:
            await site.start()
        except OSError as exc:
            self.error = str(exc)
            if overlapping:
                rollback = web.TCPSite(self.runner, *old_address)
                try:
                    await rollback.start()
                    self.site = rollback
                except OSError as rollback_error:
                    self.site = None
                    self.error += f"；恢复原监听也失败：{rollback_error}"
            raise ValueError(f"监听失败 {host}:{port}：{exc}") from exc
        self.site, self.address, self.error = site, (host, port), ""
        if previous and not overlapping:
            await previous.stop()

    async def close(self):
        await self.hub.close()
        if self.runner:
            await self.runner.cleanup()
        self.site = self.runner = None
