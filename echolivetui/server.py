"""aiohttp lifetime independent of the terminal UI."""
from __future__ import annotations
import json
import asyncio
import ipaddress
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
        self.lan_site = None
        self.lan_address = None
        self.connections = {}

    def is_remote(self, request):
        try:
            return not ipaddress.ip_address(request.remote or "127.0.0.1").is_loopback
        except ValueError:
            return True

    @web.middleware
    async def access(self, request, handler):
        if self.is_remote(request):
            local = request.transport.get_extra_info("sockname") if request.transport else None
            if not self.lan_address or not local or local[0] != self.lan_address[0]:
                raise web.HTTPForbidden(text="LAN sending is disabled for this address")
            if request.path == "/settings.html":
                raise web.HTTPForbidden(text="Use the local settings page")
        return await handler(request)

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1024 * 1024)
        await ws.prepare(request)
        local = request.transport.get_extra_info("sockname") if request.transport else None
        self.connections[id(ws)] = (ws, self.is_remote(request), local)
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
            self.connections.pop(id(ws), None)
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
            app = web.Application(middlewares=[self.access])
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
        actual_port = site._server.sockets[0].getsockname()[1] if port == 0 else port
        self.site, self.address, self.error = site, (host, actual_port), ""
        if previous and not overlapping:
            await previous.stop()

    async def set_lan(self, host=None):
        """Stage a new listener before replacing the old one; None disables LAN."""
        if host is not None:
            root = self.hosting.root
            if not root or not all((root / name).is_file() for name in ("editor.html", "res/script/editor.js")):
                raise ValueError("当前目录缺少 Echo Live editor 页面或脚本，无法启用远程发送")
            if not self.site:
                raise ValueError("本机服务尚未监听")
        desired = (host, self.address[1]) if host else None
        if desired == self.lan_address:
            return
        previous, old_address = self.lan_site, self.lan_address
        staged = None
        if host and self.address[0] not in {host, "0.0.0.0"}:
            staged = web.TCPSite(self.runner, *desired)
            try:
                await staged.start()
            except OSError as exc:
                raise ValueError(f"局域网监听失败 {host}：{exc}") from exc
        self.lan_site, self.lan_address = staged, desired
        if previous:
            await previous.stop()
        if old_address:
            closing = [ws for ws, remote, local in list(self.connections.values()) if remote and local and local[0] == old_address[0]]
            await asyncio.gather(*(ws.close(code=1001, message=b"LAN entry changed") for ws in closing))

    async def close(self):
        await self.hub.close()
        if self.runner:
            await self.runner.cleanup()
        self.site = self.runner = None
        self.lan_site = self.lan_address = None
