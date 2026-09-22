from __future__ import annotations

import json
import socket
import threading
from contextlib import closing
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastapi import FastAPI

from .database import connect, initialize_database
from .routers.sync import public_router
from .schemas import LAN_SYNC_MAX_REQUEST_BYTES
from .services.sync import sync_enabled

from .config import LAN_SYNC_PORT

LAN_SYNC_HOST = "0.0.0.0"


class RequestBodyLimitMiddleware:
    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                payload = json.dumps(
                    {"detail": "局域网同步单次请求不能超过 16 MiB"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 422,
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(payload)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": payload})
                return
            more_body = bool(message.get("more_body", False))

        delivered = False

        async def replay_body() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                # StreamingResponse waits here for a real disconnect after consuming the body.
                # https://asgi.readthedocs.io/en/latest/specs/www.html#disconnect-receive-event
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_body, send)

lan_sync_app = FastAPI(title="英语刷题机局域网同步", docs_url=None, redoc_url=None, openapi_url=None)
lan_sync_app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=LAN_SYNC_MAX_REQUEST_BYTES,
)
lan_sync_app.include_router(public_router, prefix="/api")

_lock = threading.Lock()
_server: uvicorn.Server | None = None
_thread: threading.Thread | None = None
_listener: socket.socket | None = None


def lan_sync_host_running() -> bool:
    with _lock:
        return (
            _thread is not None
            and _thread.is_alive()
            and _listener is not None
        )


def start_lan_sync_host() -> bool:
    global _server, _thread, _listener
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((LAN_SYNC_HOST, LAN_SYNC_PORT))
            listener.listen()
        except OSError:
            listener.close()
            return False
        config = uvicorn.Config(lan_sync_app, host=LAN_SYNC_HOST, port=LAN_SYNC_PORT,
                                log_level="warning", proxy_headers=False, access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=_run_server,
            args=(server, listener),
            name="lan-sync-host",
            daemon=True,
        )
        _server = server
        _listener = listener
        _thread = thread
        _thread.start()
        return True


def _run_server(server: uvicorn.Server, listener: socket.socket) -> None:
    global _server, _thread, _listener
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        with _lock:
            if _server is server:
                _server = None
                _thread = None
                _listener = None


def start_lan_sync_host_if_enabled() -> bool:
    initialize_database()
    with closing(connect()) as connection:
        if not sync_enabled(connection):
            return False
        from .lan_tls_host import start_tls_host
        started = start_lan_sync_host()
        start_tls_host()
        return started


def stop_lan_sync_host(timeout: float = 5.0) -> bool:
    from .lan_tls_host import stop_tls_host
    stop_tls_host(timeout)
    with _lock:
        server = _server
        thread = _thread
        if server is None or thread is None:
            return False
        server.should_exit = True
    if thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    return not thread.is_alive()
