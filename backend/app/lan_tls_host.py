"""TLS-only LAN listener, separate from the legacy HTTP and desktop management apps."""
from __future__ import annotations

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

from . import database
from .config import LAN_TLS_PORT
from .lan_tls import load_or_create_identity, tls_server_config
from .lan_discovery import LanAdvertisement
from .routers.sync import public_router
from .services.sync import clear_pairing_invitation

LAN_TLS_HOST = "0.0.0.0"
_lock = threading.RLock()
_server: uvicorn.Server | None = None
_thread: threading.Thread | None = None
_descriptor: dict | None = None


def tls_status() -> dict:
    with _lock:
        ready = bool(_server and _server.started and _thread and _thread.is_alive())
        return {"tls_listening": ready, "tls_identity": dict(_descriptor) if ready and _descriptor else None}


def start_tls_host() -> bool:
    global _server, _thread, _descriptor
    with _lock:
        if _thread and _thread.is_alive():
            return bool(_server and _server.started)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((LAN_TLS_HOST, LAN_TLS_PORT))
            listener.listen()
            identity = load_or_create_identity(database.DATABASE_PATH.parent)
            # Retain the public build's raw-body limit without exposing management routes.
            from .lan_sync_host import lan_sync_app
            app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
                          middleware=list(lan_sync_app.user_middleware))
            app.state.lan_tls_host_id = identity.host_id
            app.include_router(public_router, prefix="/api")
            server = uvicorn.Server(tls_server_config(app, identity, LAN_TLS_HOST, LAN_TLS_PORT))
        except Exception:
            listener.close()
            return False

        def run() -> None:
            global _server, _thread, _descriptor
            advertisement = LanAdvertisement(identity.host_id, LAN_TLS_PORT)
            try:
                advertisement.start()
                server.run(sockets=[listener])
            finally:
                advertisement.stop()
                listener.close()
                clear_pairing_invitation()
                with _lock:
                    if _server is server:
                        _server = _thread = _descriptor = None

        _server = server
        _descriptor = identity.public_descriptor()
        thread = threading.Thread(target=run, name="lan-sync-tls", daemon=True)
        _thread = thread
        thread.start()
    deadline = time.monotonic() + 3
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if server.started and thread.is_alive():
        return True
    stop_tls_host()
    return False


def stop_tls_host(timeout: float = 5.0) -> bool:
    clear_pairing_invitation()
    with _lock:
        server, thread = _server, _thread
        if server is None or thread is None:
            return True
        server.should_exit = True
    if thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    return not thread.is_alive()
