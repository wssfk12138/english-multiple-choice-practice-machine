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
import hashlib
import secrets
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
_token_devices: dict[str, str] = {}
_token_transports: dict[str, str] = {}
_token_categories: dict[str, list[str] | None] = {}
_pairing_invitation: tuple[str, float, str] | None = None
PAIRING_TTL_SECONDS = 180


def create_pairing_invitation(connection: sqlite3.Connection, replace_device_id: str = "") -> dict:
    global _pairing_invitation
    with _tokens_lock:
        if not sync_enabled(connection):
            raise ValueError("LAN sync disabled")
        _ensure_paired_devices_table(connection)
        if replace_device_id and not connection.execute(
                "SELECT 1 FROM sync_paired_devices WHERE device_id = ?", (replace_device_id,)).fetchone():
            raise ValueError("Replacement device no longer exists")
        code = secrets.token_urlsafe(32)
        _pairing_invitation = (_pairing_hash(code), time.monotonic() + PAIRING_TTL_SECONDS, replace_device_id)
        return {"pairing_code": code, "expires_at": int(time.time()) + PAIRING_TTL_SECONDS}


def clear_pairing_invitation() -> None:
    global _pairing_invitation
    with _tokens_lock:
        _pairing_invitation = None

def _pairing_hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()
_tokens_lock = threading.RLock()


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
    result = authenticate_device(connection, passcode, "", profile_names, device_id)
    return result[0] if result else None


def authenticate_device(
    connection: sqlite3.Connection,
    passcode: str,
    pairing_token: str,
    profile_names: Iterable[str],
    device_id: str = "",
    *, transport: str = "legacy", pairing_code: str = "", device_model: str = "",
) -> tuple[str, str | None] | None:
    # Pairing and revocation must not interleave before the session is registered.
    with _tokens_lock:
        return _authenticate_device(connection, passcode, pairing_token, profile_names, device_id,
                                    transport=transport, pairing_code=pairing_code, device_model=device_model)


def _authenticate_device(
    connection: sqlite3.Connection,
    passcode: str,
    pairing_token: str,
    profile_names: Iterable[str],
    device_id: str = "",
    *, transport: str = "legacy", pairing_code: str = "", device_model: str = "",
) -> tuple[str, str | None] | None:
    """Authenticate a device and issue a short-lived sync session.

    A passcode is required only for first pairing (or after revocation).  A
    previously issued per-device pairing token survives passcode rotation and
    is stored only as a hash on the host.
    """
    global _pairing_invitation
    if not sync_enabled(connection) or transport not in ("legacy", "tls-v2"):
        return None
    _ensure_paired_devices_table(connection)
    current_passcode = get_passcode(connection)
    device_id = str(device_id or "").strip()
    pairing_token = str(pairing_token or "").strip()
    row = connection.execute(
        "SELECT pairing_token_hash, transport FROM sync_paired_devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if transport == "legacy" and row and row["transport"] == "tls-v2":
        return None
    if transport == "tls-v2" and not device_id:
        return None
    authenticated_by_pairing = False
    if pairing_token and device_id:
        authenticated_by_pairing = bool(row and row["transport"] == transport and row["pairing_token_hash"] and secrets.compare_digest(
            str(row["pairing_token_hash"]), _pairing_hash(pairing_token)
        ))
    if not authenticated_by_pairing:
        if transport == "tls-v2":
            if (not pairing_code or not _pairing_invitation
                    or time.monotonic() >= _pairing_invitation[1]
                    or not secrets.compare_digest(_pairing_hash(pairing_code), _pairing_invitation[0])):
                return None
        elif not passcode or not current_passcode or not secrets.compare_digest(str(passcode), current_passcode):
            return None
    replacement = (_pairing_invitation[2] if transport == "tls-v2"
                   and not authenticated_by_pairing and _pairing_invitation else "")
    if replacement and (replacement == device_id or row or not connection.execute(
            "SELECT 1 FROM sync_paired_devices WHERE device_id = ?", (replacement,)).fetchone()):
        return None
    issued_pairing_token: str | None = None
    if device_id:
        try:
            if authenticated_by_pairing:
                _record_pairing(connection, device_id, None, device_model)
            else:
                issued_pairing_token = secrets.token_urlsafe(32)
                _record_pairing(connection, device_id, issued_pairing_token, device_model)
                connection.execute("UPDATE sync_paired_devices SET transport = ? WHERE device_id = ?",
                                   (transport, device_id))
            if replacement:
                connection.execute("DELETE FROM sync_paired_devices WHERE device_id = ?", (replacement,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        if replacement:
            _revoke_device_sessions(replacement)
        if transport == "tls-v2" and not authenticated_by_pairing:
            _pairing_invitation = None
        if issued_pairing_token:
            _revoke_device_sessions(device_id)
    token = secrets.token_urlsafe(24)
    with _tokens_lock:
        _tokens[token] = time.time() + TOKEN_TTL_SECONDS
        _token_profiles[token] = [str(name) for name in profile_names if str(name).strip()]
        _token_devices[token] = device_id
        _token_transports[token] = transport
        _token_categories[token] = None
    return token, issued_pairing_token


def _ensure_paired_devices_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_paired_devices (
            device_id TEXT PRIMARY KEY,
            paired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            pairing_token_hash TEXT
        )
        """
    )
    from ..database import _ensure_column
    _ensure_column(connection, "sync_paired_devices", "pairing_token_hash", "TEXT")
    _ensure_column(connection, "sync_paired_devices", "transport", "TEXT NOT NULL DEFAULT 'legacy'")
    _ensure_column(connection, "sync_paired_devices", "display_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "sync_paired_devices", "device_model", "TEXT NOT NULL DEFAULT ''")
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


def _record_pairing(connection: sqlite3.Connection, device_id: str, pairing_token: str | None, device_model: str = "") -> None:
    _ensure_paired_devices_table(connection)
    token_hash = _pairing_hash(pairing_token) if pairing_token else None
    connection.execute(
        """
        INSERT INTO sync_paired_devices(device_id, pairing_token_hash, device_model) VALUES (?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP, pairing_token_hash = COALESCE(excluded.pairing_token_hash, sync_paired_devices.pairing_token_hash)
        """,
        (device_id, token_hash, device_model),
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
            _token_devices.pop(token, None)
            _token_transports.pop(token, None)
            _token_categories.pop(token, None)
            return False
    return True


def token_profiles(token: str) -> list[str]:
    with _tokens_lock:
        return list(_token_profiles.get(token, []))


def session_profiles(token: str, transport: str | None = None) -> list[str] | None:
    """Read authorization atomically; a revoked session is never an empty scope."""
    with _tokens_lock:
        if not verify_token(token) or (transport is not None and _token_transports.get(token) != transport):
            return None
        return list(_token_profiles[token])


def revoke_token(token: str) -> None:
    with _tokens_lock:
        _tokens.pop(token, None)
        _token_profiles.pop(token, None)
        _token_devices.pop(token, None)
        _token_transports.pop(token, None)
        _token_categories.pop(token, None)


def _revoke_device_sessions(device_id: str) -> None:
    for token, owner in list(_token_devices.items()):
        if owner == device_id:
            revoke_token(token)


def paired_devices(connection: sqlite3.Connection, limit: int, offset: int) -> dict[str, Any]:
    _ensure_paired_devices_table(connection)
    total = connection.execute("SELECT COUNT(*) FROM sync_paired_devices").fetchone()[0]
    items = connection.execute(
        "SELECT device_id, paired_at, last_seen_at, display_name, device_model FROM sync_paired_devices "
        "ORDER BY paired_at DESC, device_id LIMIT ? OFFSET ?", (limit, offset),
    ).fetchall()
    return {"items": [dict(item) for item in items], "total": total}


def revoke_device(connection: sqlite3.Connection, device_id: str) -> int:
    with _tokens_lock:
        clear_pairing_invitation()
        _ensure_paired_devices_table(connection)
        cursor = connection.execute("DELETE FROM sync_paired_devices WHERE device_id = ?", (device_id,))
        connection.commit()
        _revoke_device_sessions(device_id)
        return int(cursor.rowcount)


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
    from .sync_versions import ensure_versions
    _ensure_column(connection, 'wrong_stats', 'attempt_ledger', 'TEXT')
    ensure_versions(connection, [*SYNC_TABLES, 'sync_tombstones'])
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
        row = _vocabulary_sync_row(connection, stable_key)
        return int(row["id"]) if row else None
    return None



def _entry_surface(value: str) -> str:
    return ' '.join(str(value or '').strip().replace('’', "'").split()).lower()


def _vocabulary_sync_row(connection, key: str, term: str | None = None):
    alias = connection.execute(
        "SELECT canonical_sync_id FROM sync_id_aliases WHERE table_name='vocabulary_entries' AND alias_sync_id=?", (key,)
    ).fetchone()
    canonical = str(alias[0]) if alias else key
    found = connection.execute('SELECT rowid AS _rowid, * FROM vocabulary_entries WHERE normalized_term=?', (canonical,)).fetchone()
    surface = _entry_surface(term) if term is not None else (key[11:] if key.startswith('surface:v1:') else '')
    if term is not None and (not surface or (key.startswith('surface:v1:') and key[11:] != surface)):
        raise ValueError('单词同步身份与词形不一致，请升级全部设备后重试')
    if found is not None and surface and _entry_surface(found['term']) != surface:
        raise ValueError('单词同步身份对应不同词形，学习记录未被合并')
    if found is None and surface:
        matches = connection.execute(
            "SELECT rowid AS _rowid, * FROM vocabulary_entries WHERE lower(replace(trim(term), '’', char(39)))=? LIMIT 2", (surface,)
        ).fetchall()
        if len(matches) > 1:
            raise ValueError('存在多个相同词形的词条，请先解决单词身份冲突')
        found = matches[0] if matches else None
    return found


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
        if column == "daily_study_identity":
            continue
        if column == spec.get("secret") or column == "_sync_seq":
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
    if table == 'wrong_stats' and not payload.get('attempt_ledger'):
        from .attempt_stats import aggregate
        payload.update(aggregate(payload))
    return payload


def snapshot(
    connection: sqlite3.Connection,
    tables: Iterable[str],
    profile_names: Iterable[str],
    cursor: dict[str, dict[str, Any]] | None = None,
    tombstone_cursor: dict[str, Any] | None = None,
    *, tombstone_tables: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Serialize changes after a database-local mutation sequence."""
    from .sync_versions import sequence
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
                "WHERE _sync_seq > ? ORDER BY _sync_seq",
                (sequence(watermark),),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        serialized = []
        for row in rows:
            if table == "practice_sessions" and row["updated_at"] == "0001-01-01T00:00:00Z":
                continue
            payload = _serialize_row(connection, table, row)
            if visible(payload.get("profile_name") or ""):
                serialized.append(payload)
                next_cursor[table] = {
                    "updated_at": str(row["updated_at"] or ""),
                    "rowid": int(row["_sync_rowid"]),
                    "seq": int(row["_sync_seq"]),
                }
        result[table] = serialized
        if table not in next_cursor:
            next_cursor[table] = {
                "updated_at": updated_at,
                "rowid": rowid,
                "seq": sequence(watermark),
            }

    tombstones = []
    try:
        tombstone_cursor = tombstone_cursor or {}
        deleted_at = str(tombstone_cursor.get("deleted_at") or "")
        tombstone_rowid = max(0, int(tombstone_cursor.get("rowid") or 0))
        tomb_rows = connection.execute(
            "SELECT rowid AS _sync_rowid, * "
            "FROM sync_tombstones WHERE _sync_seq > ? ORDER BY _sync_seq",
            (sequence(tombstone_cursor),),
        ).fetchall()
    except sqlite3.OperationalError:
        tomb_rows = []
    for row in tomb_rows:
        if row["table_name"] == "ai_profiles":
            continue
        if (tombstone_tables is None or row["table_name"] in tombstone_tables) and visible(str(row["profile_name"] or "")):
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
                "seq": int(row["_sync_seq"]),
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
    if table == "ai_profiles":
        return False
    from .sync_versions import version
    remote_version = version(payload)
    spec = SYNC_TABLES[table]
    columns = _table_columns(connection, table)
    update: dict[str, Any] = {}
    profile_name = str(payload.get("profile_name") or "")
    for column in columns:
        if column == "daily_study_identity":
            continue
        if column == "id" or column == spec.get("secret") or column.startswith("_sync_"):
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
        existing = _vocabulary_sync_row(connection, str(key_value), str(payload.get('term') or ''))
        canonical = str(existing['normalized_term']) if existing is not None else _canonical_object_key(connection, table, str(key_value))
        _record_sync_alias(connection, table, str(key_value), canonical)
        update["normalized_term"] = canonical
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

    if table == 'practice_sessions' and not (existing is not None and payload.get('_dependency') is True):
        from .practice_snapshots import validate_incoming_session_content
        validate_incoming_session_content(connection, update)
    object_key = str(update.get("sync_id") or update.get("normalized_term") or payload.get(key) or "")
    tombstone = connection.execute(
        "SELECT deleted_at FROM sync_tombstones WHERE table_name=? AND object_key=? "
        "AND (profile_name=? OR ? IN ('vocabulary_entries','vocabulary_reviews')) "
        "ORDER BY deleted_at DESC LIMIT 1", (table, object_key, profile_name, table)
    ).fetchone()
    if tombstone and remote_ts and remote_ts <= str(tombstone["deleted_at"]):
        return False

    if table == 'wrong_stats':
        from .attempt_stats import aggregate
        if existing is not None and not update.get('attempt_ledger'):
            current_ledger = connection.execute('SELECT attempt_ledger FROM wrong_stats WHERE rowid=?', (existing['_rowid'],)).fetchone()[0]
            if current_ledger:
                raise ValueError('Legacy peer cannot merge graded attempt statistics; upgrade all devices')
        update.update(aggregate(update))
    if existing is None:
        placeholders = ", ".join("?" for _ in update)
        if not update:
            return False
        inserted = connection.execute(
            f"INSERT INTO {table} ({', '.join(update)}) VALUES ({placeholders})",
            list(update.values()),
        )
        connection.execute(f"UPDATE {table} SET _sync_rev=?, _sync_origin=? WHERE rowid=?",
                           (*remote_version, inserted.lastrowid))
        return True

    if table == "practice_sessions" and payload.get("_dependency") is True:
        return False
    local_ts = str(existing["updated_at"] or "")
    local_version = connection.execute(
        f"SELECT _sync_rev, _sync_origin FROM {table} WHERE rowid=?", (existing["_rowid"],)
    ).fetchone()
    if table == 'wrong_stats':
        current = dict(connection.execute('SELECT * FROM wrong_stats WHERE rowid=?', (existing['_rowid'],)).fetchone())
        merged = aggregate(current, update)
        if (remote_ts, *remote_version) <= (local_ts, *local_version):
            update = {**merged, 'updated_at': local_ts}
            remote_version = tuple(local_version)
        else:
            update.update(merged)
        if all(current.get(key) == value for key, value in update.items()):
            return False
    elif remote_ts and local_ts and (remote_ts, *remote_version) <= (local_ts, *local_version):
        return False
    if table == 'practice_sessions':
        from .practice_snapshots import read_snapshot, snapshots_equivalent
        stored = connection.execute('SELECT content_snapshot FROM practice_sessions WHERE rowid=?', (existing['_rowid'],)).fetchone()[0]
        local_snapshot = read_snapshot(stored)
        remote_snapshot = read_snapshot(update.get('content_snapshot', '{}'))
        if local_snapshot and not snapshots_equivalent(local_snapshot, remote_snapshot):
            raise ValueError('旧练习内容快照不一致，请升级全部设备后同步；学习记录未被覆盖')
    set_clause = ", ".join(f"{column} = ?" for column in update)
    if not set_clause:
        return False
    connection.execute(
        f"UPDATE {table} SET {set_clause} WHERE rowid = ?",
        [*update.values(), existing["_rowid"]],
    )
    connection.execute(f"UPDATE {table} SET _sync_rev=?, _sync_origin=? WHERE rowid=?",
                       (*remote_version, existing["_rowid"]))
    return True


def _record_tombstone(
    connection: sqlite3.Connection,
    table: str,
    object_key: str,
    profile_name: str = "",
    deleted_at: str | None = None,
) -> None:
    if not object_key:
        return
    connection.execute(
        """
        INSERT INTO sync_tombstones(table_name, object_key, profile_name, deleted_at)
        VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        ON CONFLICT(table_name, object_key, profile_name)
        DO UPDATE SET deleted_at = excluded.deleted_at
        WHERE ? IS NULL OR excluded.deleted_at > sync_tombstones.deleted_at
        """,
        (table, object_key, profile_name or "", deleted_at, deleted_at),
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
    if table == "vocabulary_entries":
        found = _vocabulary_sync_row(connection, object_key)
        if found is not None:
            return str(found["normalized_term"])
    elif SYNC_TABLES[table]["key"] != "sync_id":
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
    deleted_at: str | None = None,
) -> None:
    """Record tombstones for a vocabulary entry and its occurrences/reviews
    before the entry is hard-deleted (so the cascade delete also propagates)."""
    entry = connection.execute(
        "SELECT id FROM vocabulary_entries WHERE normalized_term = ? LIMIT 1",
        (normalized_term,),
    ).fetchone()
    _record_tombstone(connection, "vocabulary_entries", normalized_term, profile_name, deleted_at)
    aliases = connection.execute("SELECT alias_sync_id FROM sync_id_aliases WHERE table_name='vocabulary_entries' AND canonical_sync_id=?", (normalized_term,)).fetchall()
    for alias in aliases:
        _record_tombstone(connection, 'vocabulary_entries', str(alias[0]), profile_name, deleted_at)
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
        _record_tombstone(connection, "vocabulary_occurrences", str(occ["sync_id"]), occ_profile, deleted_at)
    for review in connection.execute(
        "SELECT sync_id FROM vocabulary_reviews WHERE entry_id = ?", (entry_id,)
    ).fetchall():
        _record_tombstone(connection, "vocabulary_reviews", str(review["sync_id"]), "", deleted_at)


def _apply_tombstone(
    connection: sqlite3.Connection,
    tombstone: dict[str, Any],
) -> bool:
    table = str(tombstone.get("table_name") or "")
    if table == "ai_profiles":
        return False
    object_key = str(tombstone.get("object_key") or "")
    profile_name = str(tombstone.get("profile_name") or "")
    deleted_at = str(tombstone.get("deleted_at") or "")
    if table not in SYNC_TABLES or not object_key:
        return False
    if not isinstance(tombstone.get("deleted_at"), str) or not deleted_at.strip():
        raise ValueError("Remote tombstone requires deleted_at")
    canonical_key = _canonical_object_key(connection, table, object_key)
    local_ts = _local_updated_at(connection, table, canonical_key)
    if not deleted_at or not local_ts or deleted_at >= local_ts:
        if table == "vocabulary_entries":
            _vocabulary_delete_tombstones(connection, canonical_key, profile_name, deleted_at or None)
        _delete_local_row(connection, table, canonical_key)
    _record_tombstone(connection, table, canonical_key, profile_name, deleted_at or None)
    return True


def merge(
    connection: sqlite3.Connection,
    changes: dict[str, list[dict[str, Any]]],
    tombstones: list[dict[str, Any]],
    allowed_profiles: Iterable[str] | None = None,
    *, category: str | None = None,
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
            if category == "practice" and table == "practice_sessions":
                session = _sync_row(connection, table, str(tombstone.get("object_key") or ""))
                if session:
                    deleted_at = str(tombstone.get("deleted_at") or "")
                    local_ts = str(session["updated_at"] or "")
                    if ((not deleted_at or not local_ts or deleted_at >= local_ts)
                            and connection.execute("SELECT 1 FROM wrong_retry_rounds WHERE session_id = ?", (session["_rowid"],)).fetchone()):
                        raise ValueError("Session deletion would cascade into the wrong-book category")
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
    """Regenerate the passcode while keeping explicitly paired devices bound."""
    with _tokens_lock:
        clear_pairing_invitation()
        _token_transports.clear()
        _tokens.clear()
        _token_profiles.clear()
        _token_devices.clear()
        _token_categories.clear()
        return set_passcode(connection)


def revoke_all_devices(connection: sqlite3.Connection) -> int:
    """Unpair every device so each one must present the current passcode again."""
    with _tokens_lock:
        clear_pairing_invitation()
        _token_transports.clear()
        _ensure_paired_devices_table(connection)
        cursor = connection.execute("DELETE FROM sync_paired_devices")
        connection.commit()
        _tokens.clear()
        _token_profiles.clear()
        _token_devices.clear()
        _token_categories.clear()
    return int(cursor.rowcount)


def sync_enabled(connection: sqlite3.Connection) -> bool:
    return _setting(connection, ENABLED_SETTING) == "1" and bool(get_passcode(connection))


def disable_sync(connection: sqlite3.Connection) -> None:
    with _tokens_lock:
        clear_pairing_invitation()
        _token_transports.clear()
        _write_setting(connection, ENABLED_SETTING, "0")
        connection.commit()
        _tokens.clear()
        _token_profiles.clear()
        _token_devices.clear()
        _token_categories.clear()


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
    from ..lan_addresses import local_ipv4_addresses
    from ..config import LAN_SYNC_PORT
    return [f"http://{address}:{LAN_SYNC_PORT}" for address in local_ipv4_addresses()]
