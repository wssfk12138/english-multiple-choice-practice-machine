from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn


URL = "http://127.0.0.1:8765"


def ensure_windowless_stdio() -> None:
    # pythonw.exe（无控制台）把 std 流设为 None，uvicorn 默认日志 formatter
    # 调用 sys.stdout.isatty() 时会崩溃，进程静默退出；补上 devnull 流兜底。
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def app_is_running() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1) as response:
            return response.status == 200 and b'"status":"ok"' in response.read(1024)
    except Exception:
        return False


def port_is_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", 8765))
            return True
        except OSError:
            return False


def open_when_ready() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=1)
            webbrowser.open(URL)
            return
        except Exception:
            time.sleep(0.25)


def main() -> int:
    ensure_windowless_stdio()
    if app_is_running():
        webbrowser.open(URL)
        return 0
    if not port_is_available():
        return 2
    threading.Thread(target=open_when_ready, daemon=True).start()
    from backend.app.lan_sync_host import (
        start_lan_sync_host_if_enabled,
        stop_lan_sync_host,
    )

    start_lan_sync_host_if_enabled()
    try:
        uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8765)
    finally:
        stop_lan_sync_host()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

