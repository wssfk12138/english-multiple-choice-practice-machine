"""Category-v1 authorization. Question content remains an ESQ/S5 concern."""
from __future__ import annotations

import json

from fastapi import HTTPException

from . import sync

CATEGORY_TABLES = {
    "practice": ("practice_sessions", "practice_answers", "practice_answer_events", "practice_unit_submissions"),
    "models": ("ai_profiles",),
    "question_bank": (),
    "vocabulary": ("vocabulary_entries", "vocabulary_occurrences", "vocabulary_reviews"),
    "wrong": ("wrong_stats", "wrong_retry_rounds", "wrong_retry_round_questions", "wrong_current_questions"),
}
DEFAULT_CATEGORIES = ["practice", "models", "vocabulary", "wrong"]
DEPENDENCY_TIMESTAMP = "0001-01-01T00:00:00Z"
DEPENDENCY_FIELDS = {"sync_id", "profile_name", "mode", "paper_id_key", "unit_ids_keys",
                     "status", "started_at", "updated_at", "_dependency", "content_snapshot", "content_revisions"}


def categories(value):
    if not isinstance(value, list) or len(value) > 5 or any(
            not isinstance(item, str) or item not in CATEGORY_TABLES for item in value) or len(set(value)) != len(value):
        raise HTTPException(422, "Unknown or duplicate sync category")
    return list(value)


def preferences(connection, device_id):
    raw = sync._setting(connection, "lan_sync_categories:" + device_id)
    return categories(json.loads(raw)) if raw else list(DEFAULT_CATEGORIES)


def set_preferences(connection, device_id, value):
    value = categories(value)
    with sync._tokens_lock:
        if not connection.execute("SELECT 1 FROM sync_paired_devices WHERE device_id = ?", (device_id,)).fetchone():
            raise HTTPException(404, "Device no longer paired")
        sync._write_setting(connection, "lan_sync_categories:" + device_id, json.dumps(value))
        connection.commit()
    return {"categories": value}


def negotiate(connection, token, requested):
    with sync._tokens_lock:
        if not sync.verify_token(token):
            raise HTTPException(401, "Sync session expired")
        device = sync._token_devices[token]
        selected = None if requested is None else categories(requested)
        sync._token_categories[token] = selected
        host = preferences(connection, device)
        return {"category_protocol": 1, "host_categories": host,
                "effective_categories": [c for c in (selected or []) if c in host]}


def authorize(connection, token, category, transport):
    # Caller holds the shared authorization lock through the read/merge operation.
    profiles = sync.session_profiles(token, transport)
    if profiles is None:
        raise HTTPException(401, "Sync session expired")
    host = preferences(connection, sync._token_devices[token])
    selected = sync._token_categories.get(token)
    if category is None:
        if selected is not None or set(host) != set(DEFAULT_CATEGORIES):
            raise HTTPException(409, "Category permissions require an upgraded client")
        return profiles
    categories([category])
    if selected is None or category not in selected or category not in host:
        raise HTTPException(403, "Sync category is not approved by both devices")
    return profiles


def validate_batch(category, changes, tombstones):
    allowed = set(CATEGORY_TABLES[category]) if category else set(sync.SYNC_TABLES)
    dependencies = changes.get("practice_sessions", []) if category == "wrong" else []
    if set(changes) - allowed - ({"practice_sessions"} if category == "wrong" else set()):
        raise HTTPException(403, "Data outside the selected category")
    references = {(r.get("session_id_key"), r.get("profile_name", "")) for r in changes.get("wrong_retry_rounds", [])}
    for item in dependencies:
        if (set(item) - DEPENDENCY_FIELDS or item.get("_dependency") is not True
                or (item.get("sync_id"), item.get("profile_name", "")) not in references
                or item.get("mode") != "wrong" or item.get("status") != "completed"
                or item.get("updated_at") != DEPENDENCY_TIMESTAMP):
            raise HTTPException(403, "Unrelated or excessive session dependency")
    for table, rows in changes.items():
        for item in rows:
            if not (category == "wrong" and table == "practice_sessions") and ("_dependency" in item or item.get("updated_at") == DEPENDENCY_TIMESTAMP):
                raise HTTPException(422, "Invalid dependency marker")
            if any(key in item for key in ("api_key", "api_key_encrypted", "api_keys", "keys")):
                raise HTTPException(403, "Key transfer is not enabled")
    for item in tombstones:
        if item.get("table_name") not in allowed:
            raise HTTPException(403, "Tombstone outside the selected category")


def dependency(payload):
    return {**{key: value for key, value in payload.items() if key in DEPENDENCY_FIELDS},
            "mode": "wrong", "status": "completed", "updated_at": DEPENDENCY_TIMESTAMP, "_dependency": True}


def snapshot(connection, category, tables, profiles, cursor, tombstone_cursor):
    allowed = CATEGORY_TABLES[category] if category else tuple(sync.SYNC_TABLES)
    if set(tables) - set(allowed) or set(cursor) - set(allowed):
        raise HTTPException(403, "Table outside the selected category")
    result = sync.snapshot(connection, tables, profiles, cursor, tombstone_cursor, tombstone_tables=allowed)
    if category == "wrong":
        sessions = {}
        for item in result["changes"].get("wrong_retry_rounds", []):
            key = str(item.get("session_id_key") or "")
            row = sync._sync_row(connection, "practice_sessions", key)
            if row is None:
                raise HTTPException(422, "Missing wrong-book session dependency")
            full_row = connection.execute("SELECT * FROM practice_sessions WHERE rowid = ?", (row["_rowid"],)).fetchone()
            sessions[key] = dependency(sync._serialize_row(connection, "practice_sessions", full_row))
        result["changes"] = {"practice_sessions": list(sessions.values()), **result["changes"]}
    return result
