from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DATA_DIR_NAME = "EnglishPracticeMachinePublic"
APP_VARIANT = "public"
LAN_SYNC_PORT = 8776
LAN_TLS_PORT = 8777
DATA_DIR_ENV = "ENGLISH_PRACTICE_DATA_DIR"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()


def resolve_data_dir() -> Path:
    explicit = os.environ.get(DATA_DIR_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required for the public application data directory")
        return Path(local_app_data).resolve() / APP_DATA_DIR_NAME
    return SOURCE_ROOT / "backend" / "data"


DATA_DIR = resolve_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
QUESTION_BANK_DIR = DATA_DIR / "question_banks"
DATABASE_PATH = DATA_DIR / "question_bank.db"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUESTION_BANK_DIR.mkdir(parents=True, exist_ok=True)
