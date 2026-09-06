"""LAN sync host (Windows PC side).

The phone connects to this PC's update service and pulls/pushes a small set
of personal learning tables (vocabulary, wrong answers, practice records and
AI profiles).  Because SQLite ids are assigned independently on each device,
every synced table exposes a stable key (external_key / normalized_term /
sync_id / name) and references are carried by those stable keys instead of
local integer ids.

v2 additions over the original full-snapshot design:
- Tombstones propagate hard deletes across devices.
- Profile (question-bank configuration) isolation: a device only exchanges
  data whose profile it also has locally.
- Last-write-wins by ``updated_at``.
"""

from __future__ import annotations

import json
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

PASSCODE_SETTING = "lan_sync_passcode"
ENABLED_SETTING = "lan_sync_enabled"
LAST_SYNC_SETTING = "lan_sync_last_at"
TOKEN_TTL_SECONDS = 60 * 15

_tokens: dict[str, float] = {}
_token_profiles: dict[str, list[str]] = {}
_tokens_lock = threading.Lock()


def _setting(connection: sqlite3.Connection, key: str) -> str:
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return str(row["value"]) if row and row["value"] is not None else ""


def _write_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO app_settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def set_passcode(connection: sqlite3.Connection, value: str | None = None) -> str:
    """Set or regenerate the LAN sync passcode."""
    passcode = value if value and len(value) >= 4 else secrets.token_hex(3).upper()
    _write_setting(connection, PASSCODE_SETTING, passcode)
    _write_setting(connection, ENABLED_SETTING, "1")
    connection.commit()
    return passcode


def get_passcode(connection: sqlite3.Connection) -> str:
    return _setting(connection, PASSCODE_SETTING)


def issue_token(
    connection: sqlite3.Connection,
    passcode: str,
    profile_names: Iterable[str],
    device_id: str = "",
) -> str | None:
    """Issue a short-lived token after validating the current passcode.

    ``device_id`` is an inventory identifier used for revocation, not an
    authentication secret.  Every handshake must therefore present the current
    passcode, including devices that paired before a passcode rotation.
    """
    if not sync_enabled(connection):
        return None
    current_passcode = get_passcode(connection)
    if not passcode or not current_passcode:
        return None
    if not secrets.compare_digest(str(passcode), current_passcode):
        return None
    device_id = str(device_id or "").strip()
    if device_id:
        _record_pairing(connection, device_id)
        connection.commit()
    token = secrets.token_urlsafe(24)
    with _tokens_lock:
        _tokens[token] = time.time() + TOKEN_TTL_SECONDS
        _token_profiles[token] = [str(name) for name in profile_names if str(name).strip()]
    return token


def _ensure_paired_devices_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_paired_devices (
            device_id TEXT PRIMARY KEY,
            paired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_sync_aliases_table(connection)


def _ensure_sync_aliases_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_id_aliases (
            table_name TEXT NOT NULL,
            alias_sync_id TEXT NOT NULL,
            canonical_sync_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (table_name, alias_sync_id)
        )
        """
    )


def _record_pairing(connection: sqlite3.Connection, device_id: str) -> None:
    _ensure_paired_devices_table(connection)
    connection.execute(
        """
        INSERT INTO sync_paired_devices(device_id) VALUES (?)
        ON CONFLICT(device_id) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
        """,
        (device_id,),
    )


def verify_token(token: str) -> bool:
    if not token:
        return False
    with _tokens_lock:
        expires = _tokens.get(token)
        if expires is None:
            return False
        if time.time() > expires:
            _tokens.pop(token, None)
            _token_profiles.pop(token, None)
            return False
    return True


def token_profiles(token: str) -> list[str]:
    with _tokens_lock:
        return list(_token_profiles.get(token, []))


def revoke_token(token: str) -> None:
    with _tokens_lock:
        _tokens.pop(token, None)
        _token_profiles.pop(token, None)


def _ensure_sync_columns(connection: sqlite3.Connection) -> None:
    """Add sync_id / updated_at columns and the tombstone table used by LAN
    sync, then backfill stable keys for pre-existing rows."""
    from ..database import _ensure_column

    _ensure_learning_history_tables(connection)

    sync_id_tables = (
        "practice_sessions",
        "practice_answers",
        "practice_answer_events",
        "practice_unit_submissions",
        "wrong_retry_rounds",
        "wrong_retry_round_questions",
        "wrong_current_questions",
        "vocabulary_occurrences",
        "vocabulary_reviews",
    )
    updated_at_tables = sync_id_tables + (
        "wrong_stats",
    )
    for table in sync_id_tables:
        _ensure_column(connection, table, "sync_id", "TEXT")
    for table in updated_at_tables:
        _ensure_column(connection, table, "updated_at", "TEXT")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            table_name TEXT NOT NULL,
            object_key TEXT NOT NULL,
            profile_name TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (table_name, object_key, profile_name)
        )
        """
    )
    _ensure_sync_aliases_table(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_paired_devices (
            device_id TEXT PRIMARY KEY,
            paired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for table in sync_id_tables:
        connection.execute(
            f"UPDATE {table} SET sync_id = lower(hex(randomblob(16))) "
            "WHERE sync_id IS NULL OR sync_id = ''"
        )
    for table in updated_at_tables:
        connection.execute(
            f"UPDATE {table} SET updated_at = CURRENT_TIMESTAMP "
            "WHERE updated_at IS NULL OR updated_at = ''"
        )
    _backfill_content_keys(connection)
    connection.commit()


def _ensure_learning_history_tables(connection: sqlite3.Connection) -> None:
    """Install the learning-history tables absent from early public builds."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS wrong_retry_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            question_count INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT,
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
            UNIQUE (unit_id, round_number)
        );

        CREATE TABLE IF NOT EXISTS wrong_retry_round_questions (
            round_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            user_answer TEXT NOT NULL DEFAULT '',
            is_correct INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (round_id, question_id),
            FOREIGN KEY (round_id) REFERENCES wrong_retry_rounds(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS wrong_current_questions (
            unit_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            since_round_id INTEGER,
            deleted_at TEXT,
            PRIMARY KEY (unit_id, question_id),
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (since_round_id) REFERENCES wrong_retry_rounds(id) ON DELETE SET NULL
        );
        """
    )


def _backfill_content_keys(connection: sqlite3.Connection) -> None:
    """Give legacy question-bank rows deterministic stable keys.

    Early databases predate the ESQ identity columns and contain many NULL
    unit/question keys.  Learning-history sync references these rows by key,
    so serializing an empty key makes a valid Android payload impossible to
    merge.  Keep existing keys untouched and mirror the ESQ export fallbacks
    wherever possible; only use the local row id as a final collision-safe
    suffix.
    """
    papers = connection.execute(
        "SELECT id, year, external_key FROM papers WHERE external_key IS NOT NULL AND external_key <> ''"
    ).fetchall()
    paper_keys = {int(row["id"]): str(row["external_key"]) for row in papers}
    if not paper_keys:
        return

    units = connection.execute(
        "SELECT id, paper_id, sequence, external_key FROM units ORDER BY id"
    ).fetchall()
    for row in units:
        if row["external_key"]:
            continue
        paper_key = paper_keys.get(int(row["paper_id"]))
        if not paper_key:
            continue
        sequence = row["sequence"] if row["sequence"] is not None else int(row["id"])
        candidate = f"{paper_key}.unit{sequence}"
        conflict = connection.execute(
            "SELECT 1 FROM units WHERE external_key = ? AND id <> ? LIMIT 1",
            (candidate, row["id"]),
        ).fetchone()
        if conflict:
            candidate = f"{paper_key}.unit{sequence}.local{row['id']}"
        connection.execute(
            "UPDATE units SET external_key = ? WHERE id = ? AND (external_key IS NULL OR external_key = '')",
            (candidate, row["id"]),
        )

    questions = connection.execute(
        "SELECT q.id, q.unit_id, q.number, q.sequence, q.external_key, u.paper_id, u.sequence AS unit_sequence "
        "FROM questions q JOIN units u ON u.id = q.unit_id ORDER BY q.id"
    ).fetchall()
    for row in questions:
        if row["external_key"]:
            continue
        paper_key = paper_keys.get(int(row["paper_id"]))
        if not paper_key:
            continue
        number = row["number"] if isinstance(row["number"], int) else row["sequence"]
        if number is None:
            number = int(row["id"])
        try:
            candidate = f"{paper_key}.q{int(number):02d}"
        except (TypeError, ValueError):
            candidate = f"{paper_key}.q{row['id']}"
        conflict = connection.execute(
            "SELECT 1 FROM questions WHERE external_key = ? AND id <> ? LIMIT 1",
            (candidate, row["id"]),
        ).fetchone()
        if conflict:
            candidate = f"{candidate}.u{row['unit_sequence']}"
            suffix = 2
            while connection.execute(
                "SELECT 1 FROM questions WHERE external_key = ? AND id <> ? LIMIT 1",
                (candidate, row["id"]),
            ).fetchone():
                candidate = f"{paper_key}.q{int(number):02d}.u{row['unit_sequence']}.x{suffix}"
                suffix += 1
        connection.execute(
            "UPDATE questions SET external_key = ? WHERE id = ? AND (external_key IS NULL OR external_key = '')",
            (candidate, row["id"]),
        )


# Per-table sync schema: (key columns, reference remapping).
SYNC_TABLES: dict[str, dict[str, Any]] = {
    "practice_sessions": {
        "key": "sync_id",
        "refs": {"paper_id": "paper", "unit_ids": "units"},
    },
    "practice_answers": {
        "key": "sync_id",
        "refs": {"session_id": "session", "question_id": "question"},
    },
    "practice_answer_events": {
        "key": "sync_id",
        "refs": {"session_id": "session", "question_id": "question"},
    },
    "practice_unit_submissions": {
        "key": "sync_id",
        "refs": {"session_id": "session", "unit_id": "unit"},
    },
    "wrong_stats": {
        "key": "question_external_key",
        "refs": {"question_id": "question"},
    },
    "wrong_retry_rounds": {
        "key": "sync_id",
        "refs": {"unit_id": "unit", "session_id": "session"},
    },
    "wrong_retry_round_questions": {
        "key": "sync_id",
        "refs": {"round_id": "round", "question_id": "question"},
    },
    "wrong_current_questions": {
        "key": "sync_id",
        "refs": {"unit_id": "unit", "question_id": "question", "since_round_id": "round"},
    },
    "vocabulary_entries": {
        "key": "normalized_term",
        "refs": {},
    },
    "vocabulary_occurrences": {
        "key": "sync_id",
        "refs": {"entry_id": "entry", "unit_id": "unit", "question_id": "question"},
    },
    "vocabulary_reviews": {
        "key": "sync_id",
        "refs": {"entry_id": "entry"},
    },
    "ai_profiles": {
        "key": "name",
        "refs": {},
        "secret": "api_key_encrypted",
    },
}

LOGICAL_KEYS: dict[str, tuple[str, ...]] = {
    "practice_answers": ("session_id", "question_id"),
    "practice_unit_submissions": ("session_id", "unit_id"),
    "wrong_retry_rounds": ("unit_id", "round_number"),
    "wrong_retry_round_questions": ("round_id", "question_id"),
    "wrong_current_questions": ("unit_id", "question_id"),
}

REQUIRED_REFS: dict[str, tuple[str, ...]] = {
    "practice_answers": ("session_id", "question_id"),
    "practice_answer_events": ("session_id", "question_id"),
    "practice_unit_submissions": ("session_id", "unit_id"),
    "wrong_stats": ("question_id",),
    "wrong_retry_rounds": ("unit_id", "session_id"),
    "wrong_retry_round_questions": ("round_id", "question_id"),
    "wrong_current_questions": ("unit_id", "question_id"),
    "vocabulary_occurrences": ("entry_id",),
    "vocabulary_reviews": ("entry_id",),
}

GLOBAL_SYNC_TABLES = {"vocabulary_entries", "vocabulary_reviews", "ai_profiles"}


def _payload_in_profile_scope(
    table: str,
    payload: dict[str, Any],
    allowed_profiles: set[str] | None,
) -> bool:
    if allowed_profiles is None or table in GLOBAL_SYNC_TABLES:
        return True
    profile_name = str(payload.get("profile_name") or "").strip()
    return bool(profile_name) and profile_name in allowed_profiles


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _sync_row(
    connection: sqlite3.Connection, table: str, sync_id: str
) -> sqlite3.Row | None:
    if not sync_id:
        return None
    return connection.execute(
        f"""
        SELECT rowid AS _rowid, updated_at, sync_id
        FROM {table}
        WHERE sync_id = (
            SELECT canonical_sync_id FROM sync_id_aliases
            WHERE table_name = ? AND alias_sync_id = ?
            UNION ALL SELECT ?
            LIMIT 1
        )
        LIMIT 1
        """,
        (table, sync_id, sync_id),
    ).fetchone()


def _logical_row(
    connection: sqlite3.Connection, table: str, update: dict[str, Any]
) -> sqlite3.Row | None:
    columns = LOGICAL_KEYS.get(table)
    if not columns or any(update.get(column) is None for column in columns):
        return None
    where = " AND ".join(f"{column} = ?" for column in columns)
    return connection.execute(
        f"SELECT rowid AS _rowid, updated_at, sync_id FROM {table} WHERE {where} LIMIT 1",
        tuple(update[column] for column in columns),
    ).fetchone()


def _record_sync_alias(
    connection: sqlite3.Connection, table: str, alias: str, canonical: str
) -> None:
    if not alias or not canonical or alias == canonical:
        return
    connection.execute(
        """
        INSERT INTO sync_id_aliases(table_name, alias_sync_id, canonical_sync_id)
        VALUES (?, ?, ?)
        ON CONFLICT(table_name, alias_sync_id)
        DO UPDATE SET canonical_sync_id = excluded.canonical_sync_id
        """,
        (table, alias, canonical),
    )


def _resolve_id(
    connection: sqlite3.Connection,
    kind: str,
    stable_key: str,
    profile_name: str = "",
) -> int | None:
    """Resolve a stable key to a local integer id."""
    if not stable_key:
        return None
    if kind in ("paper", "unit", "question"):
        if kind == "paper":
            joins = "papers pa JOIN question_bank_profiles p ON p.id = pa.profile_id"
            key_column, id_column = "pa.external_key", "pa.id"
        elif kind == "unit":
            joins = (
                "units u JOIN papers pa ON pa.id = u.paper_id "
                "JOIN question_bank_profiles p ON p.id = pa.profile_id"
            )
            key_column, id_column = "u.external_key", "u.id"
        else:
            joins = (
                "questions q JOIN units u ON u.id = q.unit_id "
                "JOIN papers pa ON pa.id = u.paper_id "
                "JOIN question_bank_profiles p ON p.id = pa.profile_id"
            )
            key_column, id_column = "q.external_key", "q.id"
        candidates = connection.execute(
            f"SELECT {id_column} AS id FROM {joins} "
            f"WHERE {key_column} = ? AND pa.deleted_at IS NULL "
            "AND p.deleted_at IS NULL AND (? = '' OR p.name = ?) "
            f"ORDER BY {id_column} LIMIT 2",
            (stable_key, profile_name, profile_name),
        ).fetchall()
        if len(candidates) > 1:
            raise ValueError(
                f"{kind} stable key {stable_key!r} conflicts in profile "
                f"{profile_name or '<unspecified>'!r}"
            )
        return int(candidates[0]["id"]) if candidates else None
    if kind == "session":
        row = connection.execute(
            """
            SELECT id FROM practice_sessions WHERE sync_id = ?
            UNION ALL
            SELECT p.id FROM sync_id_aliases a
            JOIN practice_sessions p ON p.sync_id = a.canonical_sync_id
            WHERE a.table_name = 'practice_sessions' AND a.alias_sync_id = ?
            LIMIT 1
            """,
            (stable_key, stable_key),
        ).fetchone()
        return int(row["id"]) if row else None
    if kind == "round":
        row = connection.execute(
            """
            SELECT id FROM wrong_retry_rounds WHERE sync_id = ?
            UNION ALL
            SELECT r.id FROM sync_id_aliases a
            JOIN wrong_retry_rounds r ON r.sync_id = a.canonical_sync_id
            WHERE a.table_name = 'wrong_retry_rounds' AND a.alias_sync_id = ?
            LIMIT 1
            """,
            (stable_key, stable_key),
        ).fetchone()
        return int(row["id"]) if row else None
    if kind == "entry":
        row = connection.execute(
            "SELECT id FROM vocabulary_entries WHERE normalized_term = ? LIMIT 1",
            (stable_key,),
        ).fetchone()
        return int(row["id"]) if row else None
    return None


def _stable_key(
    connection: sqlite3.Connection,
    kind: str,
    local_id: int | None,
) -> str:
    if local_id is None:
        return ""
    if kind == "paper":
        row = connection.execute(
            "SELECT external_key FROM papers WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["external_key"]) if row and row["external_key"] else ""
    if kind == "unit":
        row = connection.execute(
            "SELECT external_key FROM units WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["external_key"]) if row and row["external_key"] else ""
    if kind == "question":
        row = connection.execute(
            "SELECT external_key FROM questions WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["external_key"]) if row and row["external_key"] else ""
    if kind == "session":
        row = connection.execute(
            "SELECT sync_id FROM practice_sessions WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["sync_id"]) if row and row["sync_id"] else ""
    if kind == "round":
        row = connection.execute(
            "SELECT sync_id FROM wrong_retry_rounds WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["sync_id"]) if row and row["sync_id"] else ""
    if kind == "entry":
        row = connection.execute(
            "SELECT normalized_term FROM vocabulary_entries WHERE id = ?", (local_id,)
        ).fetchone()
        return str(row["normalized_term"]) if row else ""
    return ""


def _profile_for(connection: sqlite3.Connection, kind: str, local_id: int | None) -> str:
    """Resolve the question-bank profile name for a local reference id."""
    if local_id is None:
        return ""
    if kind == "paper":
        row = connection.execute(
            "SELECT p.name FROM question_bank_profiles p "
            "JOIN papers pa ON pa.profile_id = p.id WHERE pa.id = ?",
            (local_id,),
        ).fetchone()
    elif kind == "unit":
        row = connection.execute(
            "SELECT p.name FROM question_bank_profiles p "
            "JOIN papers pa ON pa.profile_id = p.id "
            "JOIN units u ON u.paper_id = pa.id WHERE u.id = ?",
            (local_id,),
        ).fetchone()
    elif kind == "question":
        row = connection.execute(
            "SELECT p.name FROM question_bank_profiles p "
            "JOIN papers pa ON pa.profile_id = p.id "
            "JOIN units u ON u.paper_id = pa.id "
            "JOIN questions q ON q.unit_id = u.id WHERE q.id = ?",
            (local_id,),
        ).fetchone()
    else:
        return ""
    return str(row["name"]) if row and row["name"] else ""


def _parse_unit_ids(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return [int(item) for item in parsed if str(item).isdigit()]
    if isinstance(value, list):
        return [int(item) for item in value if str(item).isdigit()]
    return []


def _row_profile(
    connection: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
) -> str:
    """Return the question-bank profile name a synced row belongs to ('' for
    globally shared tables)."""
    if table in ("vocabulary_entries", "vocabulary_reviews", "ai_profiles"):
        return ""
    if table == "practice_sessions":
        profile = _profile_for(connection, "paper", row["paper_id"])
        if profile:
            return profile
        unit_ids = _parse_unit_ids(row["unit_ids"])
        return _profile_for(connection, "unit", unit_ids[0]) if unit_ids else ""
    if table in ("practice_answers", "practice_answer_events"):
        return _profile_for(connection, "question", row["question_id"])
    if table in ("practice_unit_submissions", "wrong_retry_rounds", "wrong_current_questions"):
        return _profile_for(connection, "unit", row["unit_id"])
    if table in ("wrong_stats", "wrong_retry_round_questions"):
        return _profile_for(connection, "question", row["question_id"])
    if table == "vocabulary_occurrences":
        profile = _profile_for(connection, "unit", row["unit_id"])
        if profile:
            return profile
        return _profile_for(connection, "question", row["question_id"])
    return ""


def _serialize_row(
    connection: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
) -> dict[str, Any]:
    spec = SYNC_TABLES[table]
    payload: dict[str, Any] = {}
    columns = _table_columns(connection, table)
    for column in columns:
        if column == spec.get("secret"):
            continue
        value = row[column]
        if column in spec["refs"]:
            kind = spec["refs"][column]
            if column == "unit_ids":
                ids = _parse_unit_ids(value)
                payload["unit_ids_keys"] = [
                    _stable_key(connection, "unit", int(item)) for item in ids
                ]
                continue
            payload[f"{column}_key"] = _stable_key(connection, kind, value)
        else:
            payload[column] = value
    key = spec["key"]
    if key == "sync_id":
        if not payload.get("sync_id"):
            generated = str(uuid.uuid4())
            payload["sync_id"] = generated
            connection.execute(
                f"UPDATE {table} SET sync_id = ? WHERE id = ?",
                (generated, row["id"]),
            )
    elif key == "question_external_key":
        payload["question_external_key"] = _stable_key(
            connection, "question", row["question_id"]
        )
    elif key == "normalized_term":
        payload["normalized_term"] = row["normalized_term"]
    payload["profile_name"] = _row_profile(connection, table, row)
    return payload


def snapshot(
    connection: sqlite3.Connection,
    tables: Iterable[str],
    profile_names: Iterable[str],
    cursor: dict[str, dict[str, Any]] | None = None,
    tombstone_cursor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize changes after a stable (updated_at, rowid) watermark."""
    _ensure_sync_columns(connection)
    profiles = set(str(name) for name in profile_names if str(name).strip())
    cursor = cursor or {}

    def visible(profile: str) -> bool:
        return not profile or profile in profiles

    result: dict[str, list[dict[str, Any]]] = {}
    next_cursor: dict[str, dict[str, Any]] = {}
    for table in tables:
        if table not in SYNC_TABLES:
            continue
        try:
            watermark = cursor.get(table) or {}
            updated_at = str(watermark.get("updated_at") or "")
            rowid = max(0, int(watermark.get("rowid") or 0))
            rows = connection.execute(
                f"SELECT rowid AS _sync_rowid, * FROM {table} "
                "WHERE updated_at > ? OR (updated_at = ? AND rowid > ?) "
                "ORDER BY updated_at, rowid",
                (updated_at, updated_at, rowid),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        serialized = []
        for row in rows:
            payload = _serialize_row(connection, table, row)
            if visible(payload.get("profile_name") or ""):
                serialized.append(payload)
                next_cursor[table] = {
                    "updated_at": str(row["updated_at"] or ""),
                    "rowid": int(row["_sync_rowid"]),
                }
        result[table] = serialized
        if table not in next_cursor:
            next_cursor[table] = {
                "updated_at": updated_at,
                "rowid": rowid,
            }

    tombstones = []
    try:
        tombstone_cursor = tombstone_cursor or {}
        deleted_at = str(tombstone_cursor.get("deleted_at") or "")
        tombstone_rowid = max(0, int(tombstone_cursor.get("rowid") or 0))
        tomb_rows = connection.execute(
            "SELECT rowid AS _sync_rowid, table_name, object_key, profile_name, deleted_at "
            "FROM sync_tombstones WHERE deleted_at > ? OR (deleted_at = ? AND rowid > ?) "
            "ORDER BY deleted_at, rowid",
            (deleted_at, deleted_at, tombstone_rowid),
        ).fetchall()
    except sqlite3.OperationalError:
        tomb_rows = []
    for row in tomb_rows:
        if visible(str(row["profile_name"] or "")):
            tombstones.append(
                {
                    "table_name": row["table_name"],
                    "object_key": row["object_key"],
                    "profile_name": row["profile_name"] or "",
                    "deleted_at": row["deleted_at"],
                }
            )
            tombstone_cursor = {
                "deleted_at": str(row["deleted_at"] or ""),
                "rowid": int(row["_sync_rowid"]),
            }
    return {
        "changes": result,
        "tombstones": tombstones,
        "cursor": next_cursor,
        "tombstone_cursor": tombstone_cursor or {},
    }


def _apply_row(
    connection: sqlite3.Connection,
    table: str,
    payload: dict[str, Any],
) -> bool:
    spec = SYNC_TABLES[table]
    columns = _table_columns(connection, table)
    update: dict[str, Any] = {}
    profile_name = str(payload.get("profile_name") or "")
    for column in columns:
        if column == "id" or column == spec.get("secret"):
            continue
        if column in spec["refs"]:
            kind = spec["refs"][column]
            if column == "unit_ids":
                keys = payload.get("unit_ids_keys") or []
                ids = [
                    _resolve_id(connection, "unit", key, profile_name) for key in keys
                ]
                if any(item is None for item in ids):
                    raise ValueError(
                        "practice_sessions.unit_ids contains an unresolved stable reference"
                    )
                update[column] = json.dumps(
                    [int(item) for item in ids], ensure_ascii=False
                )
                continue
            update[column] = _resolve_id(
                connection,
                kind,
                payload.get(f"{column}_key") or "",
                profile_name,
            )
            continue
        if column in payload:
            update[column] = payload[column]
    for required in REQUIRED_REFS.get(table, ()):
        if update.get(required) is None:
            raise ValueError(
                f"{table}.{required} cannot be resolved from its stable reference"
            )

    key = spec["key"]
    remote_ts = str(payload.get("updated_at") or "")
    existing: sqlite3.Row | None
    remote_sync_id = ""
    if key == "question_external_key":
        key_value = payload.get("question_external_key") or ""
        question_id = _resolve_id(connection, "question", key_value, profile_name)
        if question_id is None:
            raise ValueError(f"{table}.question_id cannot be resolved from {key_value!r}")
        update["question_id"] = question_id
        existing = connection.execute(
            f"SELECT rowid AS _rowid, updated_at FROM {table} WHERE question_id = ? LIMIT 1",
            (question_id,),
        ).fetchone()
    elif key == "normalized_term":
        key_value = payload.get("normalized_term") or ""
        update["normalized_term"] = key_value
        existing = connection.execute(
            "SELECT rowid AS _rowid, updated_at FROM vocabulary_entries WHERE normalized_term = ? LIMIT 1",
            (key_value,),
        ).fetchone()
    elif key == "name":
        key_value = payload.get("name") or ""
        update["name"] = key_value
        existing = connection.execute(
            "SELECT rowid AS _rowid, updated_at, is_default FROM ai_profiles WHERE name = ? LIMIT 1",
            (key_value,),
        ).fetchone()
        # The default model and credentials belong to this device.
        if existing is not None:
            update["is_default"] = existing["is_default"]
            update.pop("base_url", None)
        elif connection.execute(
            "SELECT 1 FROM ai_profiles WHERE is_default = 1 LIMIT 1"
        ).fetchone():
            update["is_default"] = 0
    else:
        remote_sync_id = str(payload.get("sync_id") or uuid.uuid4())
        existing = _sync_row(connection, table, remote_sync_id)
        if existing is None:
            existing = _logical_row(connection, table, update)
        if existing is not None:
            canonical_sync_id = str(existing["sync_id"] or remote_sync_id)
            _record_sync_alias(connection, table, remote_sync_id, canonical_sync_id)
            update["sync_id"] = canonical_sync_id
        else:
            update["sync_id"] = remote_sync_id

    if existing is None:
        placeholders = ", ".join("?" for _ in update)
        if not update:
            return False
        connection.execute(
            f"INSERT INTO {table} ({', '.join(update)}) VALUES ({placeholders})",
            list(update.values()),
        )
        return True

    local_ts = str(existing["updated_at"] or "")
    if remote_ts and local_ts and remote_ts <= local_ts:
        return False
    set_clause = ", ".join(f"{column} = ?" for column in update)
    if not set_clause:
        return False
    connection.execute(
        f"UPDATE {table} SET {set_clause} WHERE rowid = ?",
        [*update.values(), existing["_rowid"]],
    )
    return True


def _record_tombstone(
    connection: sqlite3.Connection,
    table: str,
    object_key: str,
    profile_name: str = "",
) -> None:
    if not object_key:
        return
    connection.execute(
        """
        INSERT INTO sync_tombstones(table_name, object_key, profile_name, deleted_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(table_name, object_key, profile_name)
        DO UPDATE SET deleted_at = CURRENT_TIMESTAMP
        """,
        (table, object_key, profile_name or ""),
    )


def _local_updated_at(
    connection: sqlite3.Connection,
    table: str,
    object_key: str,
) -> str:
    key = SYNC_TABLES[table]["key"]
    if key == "sync_id":
        row = _sync_row(connection, table, object_key)
    elif key == "normalized_term":
        row = connection.execute(
            "SELECT updated_at FROM vocabulary_entries WHERE normalized_term = ? LIMIT 1",
            (object_key,),
        ).fetchone()
    elif key == "name":
        row = connection.execute(
            "SELECT updated_at FROM ai_profiles WHERE name = ? LIMIT 1", (object_key,)
        ).fetchone()
    elif key == "question_external_key":
        row = connection.execute(
            "SELECT updated_at FROM wrong_stats WHERE question_id = "
            "(SELECT id FROM questions WHERE external_key = ?) LIMIT 1",
            (object_key,),
        ).fetchone()
    else:
        return ""
    return str(row["updated_at"]) if row and row["updated_at"] else ""


def _canonical_object_key(
    connection: sqlite3.Connection,
    table: str,
    object_key: str,
) -> str:
    if SYNC_TABLES[table]["key"] != "sync_id":
        return object_key
    alias = connection.execute(
        "SELECT canonical_sync_id FROM sync_id_aliases "
        "WHERE table_name = ? AND alias_sync_id = ? LIMIT 1",
        (table, object_key),
    ).fetchone()
    return str(alias["canonical_sync_id"]) if alias else object_key


def _delete_local_row(
    connection: sqlite3.Connection,
    table: str,
    object_key: str,
) -> None:
    key = SYNC_TABLES[table]["key"]
    if key == "sync_id":
        row = _sync_row(connection, table, object_key)
        if row is not None:
            connection.execute(f"DELETE FROM {table} WHERE rowid = ?", (row["_rowid"],))
    elif key == "normalized_term":
        connection.execute(
            "DELETE FROM vocabulary_entries WHERE normalized_term = ?", (object_key,)
        )
    elif key == "name":
        connection.execute("DELETE FROM ai_profiles WHERE name = ?", (object_key,))
    elif key == "question_external_key":
        connection.execute(
            "DELETE FROM wrong_stats WHERE question_id = "
            "(SELECT id FROM questions WHERE external_key = ?)",
            (object_key,),
        )


def _vocabulary_delete_tombstones(
    connection: sqlite3.Connection,
    normalized_term: str,
    profile_name: str = "",
) -> None:
    """Record tombstones for a vocabulary entry and its occurrences/reviews
    before the entry is hard-deleted (so the cascade delete also propagates)."""
    entry = connection.execute(
        "SELECT id FROM vocabulary_entries WHERE normalized_term = ? LIMIT 1",
        (normalized_term,),
    ).fetchone()
    _record_tombstone(connection, "vocabulary_entries", normalized_term, profile_name)
    if entry is None:
        return
    entry_id = int(entry["id"])
    for occ in connection.execute(
        "SELECT sync_id, unit_id, question_id FROM vocabulary_occurrences WHERE entry_id = ?",
        (entry_id,),
    ).fetchall():
        occ_profile = profile_name or _profile_for(connection, "unit", occ["unit_id"])
        if not occ_profile:
            occ_profile = _profile_for(connection, "question", occ["question_id"])
        _record_tombstone(connection, "vocabulary_occurrences", str(occ["sync_id"]), occ_profile)
    for review in connection.execute(
        "SELECT sync_id FROM vocabulary_reviews WHERE entry_id = ?", (entry_id,)
    ).fetchall():
        _record_tombstone(connection, "vocabulary_reviews", str(review["sync_id"]), "")


def _apply_tombstone(
    connection: sqlite3.Connection,
    tombstone: dict[str, Any],
) -> bool:
    table = str(tombstone.get("table_name") or "")
    object_key = str(tombstone.get("object_key") or "")
    profile_name = str(tombstone.get("profile_name") or "")
    deleted_at = str(tombstone.get("deleted_at") or "")
    if table not in SYNC_TABLES or not object_key:
        return False
    canonical_key = _canonical_object_key(connection, table, object_key)
    local_ts = _local_updated_at(connection, table, canonical_key)
    if not deleted_at or not local_ts or deleted_at >= local_ts:
        if table == "vocabulary_entries":
            _vocabulary_delete_tombstones(connection, canonical_key, profile_name)
        _delete_local_row(connection, table, canonical_key)
    _record_tombstone(connection, table, canonical_key, profile_name)
    return True


def merge(
    connection: sqlite3.Connection,
    changes: dict[str, list[dict[str, Any]]],
    tombstones: list[dict[str, Any]],
    allowed_profiles: Iterable[str] | None = None,
) -> dict[str, int]:
    """Merge client changes (and tombstones) with last-write-wins semantics."""
    _ensure_sync_columns(connection)
    profile_scope = (
        None
        if allowed_profiles is None
        else {str(name) for name in allowed_profiles if str(name).strip()}
    )
    applied: dict[str, int] = {}
    connection.execute("SAVEPOINT lan_sync_merge")
    try:
        for table, rows in changes.items():
            if table not in SYNC_TABLES:
                continue
            count = 0
            for payload in rows:
                if not _payload_in_profile_scope(table, payload, profile_scope):
                    continue
                if _apply_row(connection, table, payload):
                    count += 1
            applied[table] = count
        tombstone_count = 0
        for tombstone in tombstones:
            table = str(tombstone.get("table_name") or "")
            if not _payload_in_profile_scope(table, tombstone, profile_scope):
                continue
            if _apply_tombstone(connection, tombstone):
                tombstone_count += 1
        applied["_tombstones"] = tombstone_count
        connection.execute("RELEASE SAVEPOINT lan_sync_merge")
        connection.commit()
        return applied
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT lan_sync_merge")
        connection.execute("RELEASE SAVEPOINT lan_sync_merge")
        connection.rollback()
        raise


def reset_passcode(connection: sqlite3.Connection) -> str:
    """Regenerate the passcode and drop all active tokens.

    The paired-device inventory is retained, but every device must use the new
    passcode on its next handshake.
    """
    with _tokens_lock:
        _tokens.clear()
        _token_profiles.clear()
    return set_passcode(connection)


def revoke_all_devices(connection: sqlite3.Connection) -> int:
    """Unpair every device so each one must present the current passcode again."""
    _ensure_paired_devices_table(connection)
    cursor = connection.execute("DELETE FROM sync_paired_devices")
    with _tokens_lock:
        _tokens.clear()
        _token_profiles.clear()
    connection.commit()
    return int(cursor.rowcount)


def sync_enabled(connection: sqlite3.Connection) -> bool:
    return _setting(connection, ENABLED_SETTING) == "1" and bool(get_passcode(connection))


def disable_sync(connection: sqlite3.Connection) -> None:
    _write_setting(connection, ENABLED_SETTING, "0")
    with _tokens_lock:
        _tokens.clear()
        _token_profiles.clear()
    connection.commit()


def touch_last_sync(connection: sqlite3.Connection) -> None:
    _write_setting(
        connection,
        LAST_SYNC_SETTING,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    connection.commit()


def sync_status(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        paired = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM sync_paired_devices"
            ).fetchone()["n"]
        )
    except sqlite3.OperationalError:
        paired = 0
    return {
        "enabled": sync_enabled(connection),
        "passcode_required": True,
        "tables": sorted(SYNC_TABLES),
        "last_sync_at": _setting(connection, LAST_SYNC_SETTING),
        "paired_devices": paired,
        "host_urls": _local_host_urls() if sync_enabled(connection) else [],
    }


def _local_host_urls() -> list[str]:
    """Return private IPv4 candidates clients on the same LAN can use."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(info[4][0])
            if address.startswith(("10.", "192.168.")):
                addresses.add(address)
            elif address.startswith("172."):
                second = int(address.split(".")[1])
                if 16 <= second <= 31:
                    addresses.add(address)
    except (OSError, ValueError, IndexError):
        pass
    return [f"http://{address}:8766" for address in sorted(addresses)]
