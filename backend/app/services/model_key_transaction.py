"""Recoverable SQLite/DPAPI update. The journal never contains plaintext credentials."""
from __future__ import annotations

import base64
import json
import uuid

from . import model_keys as keys


def journal_path(connection):
    path = keys._path(connection)
    return path.with_name(path.name + ".recovery")


def _recover_locked(connection):
    path = journal_path(connection)
    if not path.exists():
        return
    try:
        journal = json.loads(keys.unprotect_text(path.read_text(encoding="utf-8")))
        if journal.get("version") != 1 or not isinstance(journal.get("transaction"), str):
            raise ValueError()
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_key_commit'").fetchone()
        marker = connection.execute("SELECT transaction_id FROM model_key_commit WHERE id=1").fetchone() if exists else None
        if not marker or marker[0] != journal["transaction"]:
            old = journal["previous"]
            keys.restore_snapshot((keys._path(connection), None if old is None else base64.b64decode(old, validate=True)))
        path.unlink()
    except Exception:
        raise ValueError("Credential recovery pending; no automatic reset") from None


def recover(connection):
    with keys.LOCK:
        if not journal_path(connection).exists():
            return
        if connection.in_transaction:
            raise ValueError("Credential recovery requires an idle connection")
        # Acquire the SQLite writer lock BEFORE reading the journal/commit marker,
        # so a second process cannot undo an in-flight update.
        connection.execute("BEGIN IMMEDIATE")
        try:
            _recover_locked(connection)
        finally:
            connection.rollback()


def atomic_update(connection, update):
    with keys.LOCK:
        if connection.in_transaction:
            raise ValueError("Credential update requires an idle connection")
        # Explicit transaction: https://docs.python.org/3.12/library/sqlite3.html#transaction-control
        connection.execute("BEGIN IMMEDIATE")
        try:
            _recover_locked(connection)
            state = keys.load(connection)
            old = keys.sidecar_snapshot(connection)[1]
            result = update(state)
            txid = uuid.uuid4().hex
            connection.execute("CREATE TABLE IF NOT EXISTS model_key_commit (id INTEGER PRIMARY KEY CHECK(id=1), transaction_id TEXT NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO model_key_commit VALUES (1, ?)", (txid,))
            journal = {"version": 1, "transaction": txid,
                       "previous": None if old is None else base64.b64encode(old).decode("ascii")}
            keys._write_bytes(journal_path(connection), keys.protect_text(json.dumps(journal)).encode("utf-8"))
            keys.save(connection, state)
            connection.commit()
        except BaseException:
            connection.rollback()
            recover(connection)
            raise
        # A cleanup failure must not turn a committed operation into a rollback.
        # The next access checks the committed marker and finishes cleanup.
        try:
            journal_path(connection).unlink(missing_ok=True)
        except OSError:
            pass
        return result
