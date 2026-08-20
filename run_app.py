from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from backend.app.main import app

# PyInstaller windowed builds (console=False) leave these as None; uvicorn's
# log formatters call .isatty() on them and crash, so point them at os.devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


URL = "http://127.0.0.1:8765"


def open_when_ready() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=1)
            # A unique query string forces the browser to navigate to a fresh
            # page instead of focusing a stale tab from a previous run.
            webbrowser.open(f"{URL}?v={int(time.time())}")
            return
        except Exception:
            time.sleep(0.25)


if __name__ == "__main__":
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)

