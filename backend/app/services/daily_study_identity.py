"""Local reading identities. Deliberately excluded from LAN/ESQ payloads."""
from __future__ import annotations

import hashlib
import os
import sqlite3


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('SAVEPOINT daily_study_identity_migration')
    try:
        _migrate(connection)
    except BaseException:
        connection.execute('ROLLBACK TO daily_study_identity_migration')
        raise
    finally:
        connection.execute('RELEASE daily_study_identity_migration')


def _migrate(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute('PRAGMA table_info(vocabulary_entries)')}
    if 'daily_study_identity' not in columns:
        connection.execute("ALTER TABLE vocabulary_entries ADD COLUMN daily_study_identity TEXT NOT NULL DEFAULT ''")
    connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('daily_study_database_id',lower(hex(randomblob(16))))")
    connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('vocabulary_revision','0')")
    connection.execute("UPDATE vocabulary_entries SET daily_study_identity=lower(hex(randomblob(16))) WHERE daily_study_identity=''")
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_study_entry_identity ON vocabulary_entries(daily_study_identity) WHERE daily_study_identity<>\'\'')
    connection.execute('CREATE TABLE IF NOT EXISTS daily_study_bank_identity(profile_id INTEGER PRIMARY KEY REFERENCES question_bank_profiles(id) ON DELETE CASCADE, identity TEXT NOT NULL UNIQUE)')
    connection.execute('INSERT OR IGNORE INTO daily_study_bank_identity SELECT id,lower(hex(randomblob(16))) FROM question_bank_profiles')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_daily_study_scheduled_review ON vocabulary_reviews(entry_id,mode,reviewed_at,id)')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS daily_study_entry_insert AFTER INSERT ON vocabulary_entries
        WHEN NEW.daily_study_identity='' BEGIN
        UPDATE vocabulary_entries SET daily_study_identity=lower(hex(randomblob(16))) WHERE id=NEW.id;
        END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS daily_study_entry_immutable BEFORE UPDATE OF daily_study_identity ON vocabulary_entries
        WHEN OLD.daily_study_identity<>'' AND NEW.daily_study_identity IS NOT OLD.daily_study_identity
        BEGIN SELECT RAISE(ABORT,'daily study identity is immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS daily_study_bank_insert AFTER INSERT ON question_bank_profiles BEGIN
        INSERT INTO daily_study_bank_identity(profile_id,identity) VALUES(NEW.id,lower(hex(randomblob(16)))); END''')
    # All writers (including import and sync) invalidate details without having
    # to remember a second application-level write. Reads never touch revision.
    for table in ('vocabulary_entries', 'vocabulary_occurrences', 'vocabulary_reviews', 'question_bank_profiles', 'papers', 'units'):
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            changed = ''
            if operation == 'UPDATE':
                changed = 'WHEN ' + ' OR '.join(f'NEW."{row[1]}" IS NOT OLD."{row[1]}"' for row in connection.execute(f'PRAGMA table_info({table})'))
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS daily_study_revision_{table}_{operation.lower()}
                AFTER {operation} ON {table} {changed} BEGIN
                UPDATE app_settings SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='vocabulary_revision'; END''')
    connection.execute("INSERT OR IGNORE INTO app_migrations(migration_key) VALUES('daily-study-identities-v1')")


def namespace(connection: sqlite3.Connection) -> str:
    """Copies in different data directories cannot share browser reading state."""
    database_id = connection.execute("SELECT value FROM app_settings WHERE key='daily_study_database_id'").fetchone()[0]
    filename = next(row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main')
    root = os.path.normcase(os.path.realpath(filename)) if filename else ':memory:'
    return hashlib.sha256((database_id + '\0' + root).encode('utf-8')).hexdigest()
