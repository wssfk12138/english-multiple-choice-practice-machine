"""Database-local delivery sequence and portable row versions for LAN sync."""

import re
import sqlite3

MAX_SEQUENCE = 9007199254740991


def sequence(cursor):
    value = cursor.get('seq', 0)
    if type(value) is not int or not 0 <= value <= MAX_SEQUENCE:
        raise ValueError('Invalid sync sequence')
    return value


def version(payload):
    revision = payload.get('_sync_rev', 0)
    origin = payload.get('_sync_origin', '')
    if type(revision) is not int or not 0 <= revision <= MAX_SEQUENCE:
        raise ValueError('Invalid sync revision')
    if not isinstance(origin, str) or (origin and not re.fullmatch('[0-9a-f]{32}', origin)):
        raise ValueError('Invalid sync origin')
    return revision, origin


def ensure_versions(connection: sqlite3.Connection, tables):
    # AFTER triggers see the assigned rowid. UPDATE OF excludes our own metadata
    # writes, even with recursive_triggers enabled. https://sqlite.org/lang_createtrigger.html
    connection.execute('CREATE TABLE IF NOT EXISTS sync_change_clock (id INTEGER PRIMARY KEY CHECK(id=1), seq INTEGER NOT NULL)')
    connection.execute('INSERT OR IGNORE INTO sync_change_clock VALUES (1, 0)')
    for table in tables:
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
        if not columns:
            continue
        for name, definition in (('_sync_seq', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('_sync_rev', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('_sync_origin', "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        business = sorted(name for name in columns if not name.startswith('_sync_'))
        increment = f"UPDATE sync_change_clock SET seq=seq+1 WHERE id=1; UPDATE {table} SET _sync_seq=(SELECT seq FROM sync_change_clock WHERE id=1)"
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS lan_seq_{table}_insert AFTER INSERT ON {table}
            BEGIN {increment}, _sync_rev=CASE WHEN NEW._sync_rev=0 THEN 1 ELSE NEW._sync_rev END,
            _sync_origin=CASE WHEN NEW._sync_origin='' THEN lower(hex(randomblob(16))) ELSE NEW._sync_origin END
            WHERE rowid=NEW.rowid; END""")
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS lan_seq_{table}_update
            AFTER UPDATE OF {', '.join(business)} ON {table}
            BEGIN {increment},
            _sync_rev=CASE WHEN NEW._sync_rev=OLD._sync_rev AND NEW._sync_origin=OLD._sync_origin THEN OLD._sync_rev+1 ELSE NEW._sync_rev END,
            _sync_origin=CASE WHEN NEW._sync_rev=OLD._sync_rev AND NEW._sync_origin=OLD._sync_origin THEN lower(hex(randomblob(16))) ELSE NEW._sync_origin END
            WHERE rowid=NEW.rowid; END""")
        connection.execute(f'UPDATE {table} SET {business[0]}={business[0]} WHERE _sync_seq=0')
        connection.execute(f'CREATE INDEX IF NOT EXISTS lan_seq_{table} ON {table}(_sync_seq)')
