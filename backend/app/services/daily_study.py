"""Lightweight, frozen reading catalogs with local immutable identities."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import HTTPException
from ..database import get_active_profile_id
from .daily_study_identity import namespace
from .study_todos import VOCABULARY_PROFILE_CONDITION

BROWSE_ORDER = """
CASE WHEN datetime(last_seen_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END DESC,
encounter_count DESC, last_seen_at DESC, id
"""
NEW_ORDER = "julianday(NULLIF(created_at,'')) DESC, daily_study_identity DESC, id DESC"
VALID_STATUSES = {'new', 'all', 'frequent', 'review', 'learning', 'mastered', 'pending'}


def _validate(status: str, scope: str) -> None:
    if status not in VALID_STATUSES or scope not in {'all', 'current'}:
        raise HTTPException(400, '无效的每日一学筛选范围')


def _selection(connection: sqlite3.Connection, status: str, search: str, scope: str) -> tuple[list[str], list[object]]:
    _validate(status, scope)
    conditions = ['1 = 1']
    params: list[object] = []
    if scope == 'current':
        conditions.append(VOCABULARY_PROFILE_CONDITION)
        params.append(get_active_profile_id(connection))
    if status == 'frequent':
        conditions.append('(encounter_count >= 2 OR manually_frequent = 1)')
    elif status == 'new':
        # Invalid timestamps are conservative, just as on Android. A later
        # reinforcement rating or a manual mastered flag is not a formal review.
        conditions.append("""COALESCE((
            SELECT CASE WHEN julianday(NULLIF(r.reviewed_at,'')) IS NULL THEN 'unknown' ELSE r.rating END
            FROM vocabulary_reviews r WHERE r.entry_id=vocabulary_entries.id AND r.mode='scheduled'
            ORDER BY CASE WHEN julianday(NULLIF(r.reviewed_at,'')) IS NULL THEN 0 ELSE 1 END,
                julianday(r.reviewed_at) DESC,r.id DESC LIMIT 1
        ),'') IS NOT 'fluent'""")
    elif status == 'review':
        conditions.extend(["(next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP)", "translation_status = 'ready'"])
    elif status in {'learning', 'mastered'}:
        conditions.append('study_status = ?')
        params.append(status)
    elif status == 'pending':
        conditions.append("translation_status != 'ready'")
    if search:
        conditions.append('(term LIKE ? OR lemma LIKE ? OR contextual_meaning LIKE ? OR common_meaning LIKE ?)')
        params.extend([f'%{search}%'] * 4)
    return conditions, params


def daily_study_metadata(connection: sqlite3.Connection, status: str = 'new', search: str = '', scope: str = 'all') -> dict[str, Any]:
    _validate(status, scope)
    search = search.strip()
    profile = get_active_profile_id(connection) if scope == 'current' else None
    bank = connection.execute('SELECT identity FROM daily_study_bank_identity WHERE profile_id=?', (profile,)).fetchone() if profile is not None else None
    revision = connection.execute("SELECT value FROM app_settings WHERE key='vocabulary_revision'").fetchone()[0]
    scope_key = json.dumps(['daily-study', 2, namespace(connection), [profile, bank[0]] if bank else 'all', status, search, 'new-v1' if status == 'new' else 'browse-order-v1'], ensure_ascii=False, separators=(',', ':'))
    return {'scope_key': scope_key, 'revision': 'v3:' + str(revision)}


def daily_study_catalog(connection: sqlite3.Connection, status: str = 'new', search: str = '', scope: str = 'all') -> dict[str, Any]:
    # A savepoint preserves a caller's transaction and freezes metadata and
    # membership together; no detail/network work is done in this snapshot.
    connection.execute('SAVEPOINT daily_study_catalog')
    try:
        search = search.strip()
        conditions, params = _selection(connection, status, search, scope)
        metadata = daily_study_metadata(connection, status, search, scope)
        rows = connection.execute(f"SELECT id,term,daily_study_identity AS identity FROM vocabulary_entries WHERE {' AND '.join(conditions)} ORDER BY {NEW_ORDER if status == 'new' else BROWSE_ORDER}", params).fetchall()
        return {**metadata, 'items': [dict(row) for row in rows]}
    finally:
        connection.execute('RELEASE daily_study_catalog')
