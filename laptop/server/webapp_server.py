#!/usr/bin/env python3
"""
Ping Claude -- Webapp HTTP Server

Serves the webapp static files on port 8767.
Applies the same IP allowlist as the WebSocket server.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

WEBAPP_PORT = 8767
WEBAPP_DIR = Path(__file__).parent / "webapp" / "dist"
ALLOWED_IP_PREFIXES = ("100.", "127.", "::1", "fd7a:", "10.", "192.168.")

log = logging.getLogger("pingclaude.webapp")


def _get_remote_ip(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername")
    if peername:
        return str(peername[0])
    return "unknown"


@web.middleware
async def ip_allowlist(request: web.Request, handler):
    ip = _get_remote_ip(request)
    if not any(ip.startswith(p) for p in ALLOWED_IP_PREFIXES):
        log.warning(f"REJECTED webapp request from {ip}")
        raise web.HTTPForbidden(text="Forbidden")
    return await handler(request)


async def serve_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def create_app() -> web.Application:
    app = web.Application(middlewares=[ip_allowlist])
    if WEBAPP_DIR.is_dir():
        app.router.add_get("/", serve_index)
        app.router.add_static("/", WEBAPP_DIR)
    else:
        log.error(f"Webapp directory not found: {WEBAPP_DIR}")
    return app


async def start_webapp_server() -> web.AppRunner:
    """Start the webapp HTTP server. Returns the runner for cleanup."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBAPP_PORT)
    await site.start()
    log.info(f"Webapp HTTP .......... http://0.0.0.0:{WEBAPP_PORT}")
    return runner
