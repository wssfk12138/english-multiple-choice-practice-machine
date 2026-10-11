"""每日一学目录与状态（从 Android 平板版移植，桌面身份键不同）。

目录语义与平板一致：一次轻量读取冻结整个范围成员，逐词浏览按
browse-order-v1 排序；身份用于续看守卫。桌面端词汇表由后端管理，
没有平板的 daily_study_identity 列，改用稳定词身 nt:<normalized_term>。
身份与续看位置不进入任何同步载荷。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..database import get_active_profile_id
from .study_todos import VOCABULARY_PROFILE_CONDITION

BROWSE_ORDER = """
CASE WHEN datetime(last_seen_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END DESC,
encounter_count DESC, last_seen_at DESC, id
"""


def _selection(connection: sqlite3.Connection, status: str, search: str, scope: str) -> tuple[list[str], list[object]]:
    conditions = ["1 = 1"]
    params: list[object] = []
    if scope == "current":
        conditions.append(VOCABULARY_PROFILE_CONDITION)
        params.append(get_active_profile_id(connection))
    if status == "frequent":
        conditions.append("(encounter_count >= 2 OR manually_frequent = 1)")
    elif status == "review":
        conditions.append("(next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP)")
        conditions.append("translation_status = 'ready'")
    elif status == "learning":
        conditions.append("study_status = 'learning'")
    elif status == "mastered":
        conditions.append("study_status = 'mastered'")
    elif status == "pending":
        conditions.append("translation_status != 'ready'")
    if search:
        conditions.append("(term LIKE ? OR lemma LIKE ? OR contextual_meaning LIKE ? OR common_meaning LIKE ?)")
        params.extend([f"%{search}%"] * 4)
    return conditions, params


def daily_study_metadata(connection: sqlite3.Connection, status: str, search: str, scope: str) -> dict[str, Any]:
    revision = connection.execute("SELECT COALESCE(MAX(id), 0) FROM vocabulary_reviews").fetchone()[0]
    profile = get_active_profile_id(connection) if scope == "current" else None
    scope_key = json.dumps(
        ["daily-study", 1, ["profile", profile] if profile is not None else "all", status, search, "browse-order-v1"],
        ensure_ascii=False,
    )
    return {"scope_key": scope_key, "revision": "v2:" + str(revision)}


def daily_study_catalog(connection: sqlite3.Connection, status: str = "all", search: str = "", scope: str = "all") -> dict[str, Any]:
    conditions, params = _selection(connection, status, search, scope)
    rows = connection.execute(
        f"""
        SELECT id, term, 'nt:' || normalized_term AS identity
        FROM vocabulary_entries
        WHERE {' AND '.join(conditions)}
        ORDER BY {BROWSE_ORDER}
        """,
        params,
    ).fetchall()
    return {**daily_study_metadata(connection, status, search, scope), "items": [dict(row) for row in rows]}
