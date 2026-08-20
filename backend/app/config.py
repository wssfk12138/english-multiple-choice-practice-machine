from __future__ import annotations

import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if _is_frozen():
    # PyInstaller bundle: read-only resources live in the extraction dir,
    # while writable data (SQLite, uploads) lives next to the executable.
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    ROOT_DIR = Path(sys.executable).resolve().parent
    FRONTEND_DIST = BUNDLE_DIR / "frontend" / "dist"
    BUNDLED_BANK_DIR = BUNDLE_DIR / "examples" / "bundled-banks"
else:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
    BUNDLED_BANK_DIR = ROOT_DIR / "examples" / "bundled-banks"

DATA_DIR = ROOT_DIR / "backend" / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
QUESTION_BANK_DIR = DATA_DIR / "question_banks"
DATABASE_PATH = DATA_DIR / "question_bank.db"


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUESTION_BANK_DIR.mkdir(parents=True, exist_ok=True)
