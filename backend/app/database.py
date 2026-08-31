from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .config import DATABASE_PATH, ensure_directories


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS question_bank_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_question_bank_profiles_name
    ON question_bank_profiles(name COLLATE NOCASE)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 1,
    year INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '英语一',
    title TEXT NOT NULL,
    source_file TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES question_bank_profiles(id)
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    unit_type TEXT NOT NULL,
    subtype TEXT,
    title TEXT NOT NULL,
    external_key TEXT,
    sequence INTEGER NOT NULL,
    passage TEXT NOT NULL DEFAULT '',
    shared_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    UNIQUE (paper_id, sequence)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    stem TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL DEFAULT 'single_choice',
    answer TEXT NOT NULL,
    score REAL NOT NULL,
    sequence INTEGER NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    external_key TEXT,
    content_hash TEXT,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
    UNIQUE (unit_id, number)
);

CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    stable_key TEXT NOT NULL,
    original_label TEXT NOT NULL,
    content TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE (question_id, stable_key)
);

CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    paper_id INTEGER,
    unit_ids TEXT NOT NULL,
    shuffle_options INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TEXT,
    score REAL,
    max_score REAL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS practice_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    option_order TEXT NOT NULL DEFAULT '[]',
    is_correct INTEGER,
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE (session_id, question_id)
);

CREATE TABLE IF NOT EXISTS practice_answer_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    option_order TEXT NOT NULL DEFAULT '[]',
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS practice_unit_submissions (
    session_id INTEGER NOT NULL,
    unit_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    score REAL NOT NULL DEFAULT 0,
    max_score REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, unit_id),
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wrong_stats (
    question_id INTEGER PRIMARY KEY,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    recent_results TEXT NOT NULL DEFAULT '[]',
    consecutive_correct INTEGER NOT NULL DEFAULT 0,
    manually_frequent INTEGER NOT NULL DEFAULT 0,
    last_wrong_at TEXT,
    last_attempt_at TEXT,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vocabulary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    normalized_term TEXT NOT NULL UNIQUE,
    lemma TEXT NOT NULL DEFAULT '',
    phonetic TEXT NOT NULL DEFAULT '',
    part_of_speech TEXT NOT NULL DEFAULT '',
    contextual_meaning TEXT NOT NULL DEFAULT '',
    common_meaning TEXT NOT NULL DEFAULT '',
    synonyms TEXT NOT NULL DEFAULT '[]',
    antonyms TEXT NOT NULL DEFAULT '[]',
    similar_forms TEXT NOT NULL DEFAULT '[]',
    memory_hint TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    translation_status TEXT NOT NULL DEFAULT 'pending',
    translation_error TEXT NOT NULL DEFAULT '',
    encounter_count INTEGER NOT NULL DEFAULT 1,
    study_status TEXT NOT NULL DEFAULT 'learning',
    manually_frequent INTEGER NOT NULL DEFAULT 0,
    user_edited INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT,
    last_reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vocabulary_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    surface_form TEXT NOT NULL,
    context_sentence TEXT NOT NULL DEFAULT '',
    context_before TEXT NOT NULL DEFAULT '',
    context_after TEXT NOT NULL DEFAULT '',
    unit_id INTEGER,
    question_id INTEGER,
    year INTEGER,
    unit_title TEXT NOT NULL DEFAULT '',
    unit_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_id) REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vocabulary_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    rating TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_review_at TEXT,
    FOREIGN KEY (entry_id) REFERENCES vocabulary_entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 1,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    answer_stored_path TEXT NOT NULL DEFAULT '',
    detected_year INTEGER,
    detected_format TEXT,
    status TEXT NOT NULL DEFAULT 'analyzing',
    draft_data TEXT NOT NULL DEFAULT '{}',
    warnings TEXT NOT NULL DEFAULT '[]',
    parse_context TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES question_bank_profiles(id)
);

CREATE TABLE IF NOT EXISTS trash_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deletion_batch_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    resource_name TEXT NOT NULL DEFAULT '',
    profile_id INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    purge_after TEXT NOT NULL,
    restored_at TEXT,
    FOREIGN KEY (profile_id) REFERENCES question_bank_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS revision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_job_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_ref TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL,
    model_name TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (import_job_id) REFERENCES import_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '本地模型',
    base_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:11434/v1',
    api_key_encrypted TEXT,
    model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 1200,
    system_prompt TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO ai_settings (id) VALUES (1);

CREATE TABLE IF NOT EXISTS ai_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    adapter TEXT NOT NULL DEFAULT 'openai-chat',
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    default_model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 1200,
    reasoning_effort TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_profile_models (
    profile_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    owned_by TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    is_visible INTEGER NOT NULL DEFAULT 1,
    is_available INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, model_id),
    FOREIGN KEY (profile_id) REFERENCES ai_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    attachments TEXT,
    profile_id INTEGER,
    model_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES ai_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS question_ai_labels (
    question_id INTEGER PRIMARY KEY,
    primary_skill TEXT NOT NULL DEFAULT '',
    secondary_skills TEXT NOT NULL DEFAULT '[]',
    trap_types TEXT NOT NULL DEFAULT '[]',
    attention_points TEXT NOT NULL DEFAULT '[]',
    vocabulary_demand TEXT NOT NULL DEFAULT 'medium',
    context_dependency TEXT NOT NULL DEFAULT 'medium',
    grammar_dependency TEXT NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 1,
    user_edited INTEGER NOT NULL DEFAULT 0,
    model_name TEXT NOT NULL DEFAULT '',
    label_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_label_run_items (
    run_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, question_id),
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_label_runs (
    run_id TEXT PRIMARY KEY,
    question_bank_profile_id INTEGER NOT NULL,
    scope_kind TEXT NOT NULL DEFAULT 'all',
    year INTEGER,
    paper_ids TEXT NOT NULL DEFAULT '[]',
    overwrite_unlocked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (question_bank_profile_id) REFERENCES question_bank_profiles(id)
);

CREATE TABLE IF NOT EXISTS app_migrations (
    migration_key TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wrong_analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL DEFAULT '',
    unit_ids TEXT NOT NULL DEFAULT '[]',
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    scope_title TEXT NOT NULL DEFAULT '',
    question_count INTEGER NOT NULL DEFAULT 0,
    aggregate_data TEXT NOT NULL DEFAULT '{}',
    report TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wrong_analysis_states (
    unit_id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL,
    analyzed_session_id INTEGER NOT NULL DEFAULT 0,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
    FOREIGN KEY (report_id) REFERENCES wrong_analysis_reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_bank_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    manifest_data TEXT NOT NULL DEFAULT '{}',
    source_file TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (package_id, content_version)
);

CREATE TABLE IF NOT EXISTS question_bank_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (package_id, content_version, asset_id)
);

CREATE TABLE IF NOT EXISTS question_bank_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    paper_external_key TEXT NOT NULL,
    action TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO ai_profiles
    (name, base_url, api_key_encrypted, enabled, is_default, default_model,
     temperature, max_tokens, system_prompt)
SELECT
    name, base_url, api_key_encrypted, 1, 1, model,
    temperature, max_tokens, system_prompt
FROM ai_settings
WHERE NOT EXISTS (SELECT 1 FROM ai_profiles);

INSERT OR IGNORE INTO ai_profile_models
    (profile_id, model_id, display_name, is_visible, is_available)
SELECT id, default_model, default_model, 1, 1
FROM ai_profiles
WHERE default_model <> '';

CREATE INDEX IF NOT EXISTS idx_units_paper ON units(paper_id);
CREATE INDEX IF NOT EXISTS idx_questions_unit ON questions(unit_id);
CREATE INDEX IF NOT EXISTS idx_answers_session ON practice_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_answer_events_question
    ON practice_answer_events(question_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_unit_submissions_session
    ON practice_unit_submissions(session_id);
CREATE INDEX IF NOT EXISTS idx_wrong_count ON wrong_stats(wrong_count DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_priority
    ON vocabulary_entries(encounter_count DESC, next_review_at, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_occurrences_entry
    ON vocabulary_occurrences(entry_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_translation_queue
    ON vocabulary_entries(translation_status, user_edited, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_profiles_single_default
    ON ai_profiles(is_default) WHERE is_default = 1;
CREATE INDEX IF NOT EXISTS idx_ai_profile_models_selector
    ON ai_profile_models(profile_id, is_visible, is_available);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_updated
    ON ai_conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
    ON ai_messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_question_ai_labels_locked
    ON question_ai_labels(locked, updated_at);
CREATE INDEX IF NOT EXISTS idx_question_label_run_items_question
    ON question_label_run_items(question_id, run_id);
CREATE INDEX IF NOT EXISTS idx_question_label_runs_profile_status
    ON question_label_runs(question_bank_profile_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_wrong_analysis_created
    ON wrong_analysis_reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_question_bank_assets_lookup
    ON question_bank_assets(package_id, content_version, asset_id);
CREATE INDEX IF NOT EXISTS idx_question_bank_revisions_paper
    ON question_bank_revisions(paper_external_key, created_at DESC);
"""


def connect() -> sqlite3.Connection:
    ensure_directories()
    # FastAPI may enter and resume a synchronous dependency on different
    # worker threads. SQLite's default thread check would then intermittently
    # reject an otherwise valid request with a 500 response.
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
        )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _rebuild_child_tables(
    connection: sqlite3.Connection,
    *,
    create_statements: dict[str, str],
    order: list[str],
) -> None:
    """Rebuild tables that reference the rebuilt parent table.

    Runs inside the papers-table rebuild where foreign keys are already
    disabled. Child rows are preserved by copying every column that exists
    in both the old child table and the new table definition. Tables that do
    not exist are skipped.
    """
    for table in order:
        snapshot_table = f"papers_rebuild_snapshot_{table}"
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (snapshot_table,),
        ).fetchone():
            continue
        old_columns = _table_columns(connection, snapshot_table)
        connection.execute(f"DROP TABLE {table}")
        connection.execute(create_statements[table])
        new_columns = _table_columns(connection, table)
        shared = [column for column in new_columns if column in old_columns]
        if shared:
            columns = ", ".join(f'"{column}"' for column in shared)
            connection.execute(
                f"""
                INSERT INTO {table} ({columns})
                SELECT {columns} FROM {snapshot_table}
                """
            )
        connection.execute(f"DROP TABLE {snapshot_table}")


def _paper_child_create_statements() -> dict[str, str]:
    statements: dict[str, str] = {}
    import re

    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\);",
        SCHEMA,
        re.DOTALL,
    ):
        name = match.group(1)
        body = match.group(2)
        if name in {
            "units",
            "questions",
            "options",
            "practice_sessions",
            "practice_answers",
            "practice_answer_events",
            "practice_unit_submissions",
            "wrong_stats",
            "vocabulary_occurrences",
            "wrong_analysis_states",
        }:
            statements[name] = f"CREATE TABLE {name} ({body})"
    return statements


def _migrate_question_label_auto_lock(connection: sqlite3.Connection) -> None:
    label_lock_migration = connection.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key = 'question-label-auto-lock-v1'"
    ).fetchone()
    if label_lock_migration is not None:
        return
    connection.execute(
        """
        UPDATE question_ai_labels
        SET locked = 1, updated_at = CURRENT_TIMESTAMP
        WHERE locked = 0
          AND EXISTS (
              SELECT 1 FROM questions
              WHERE questions.id = question_ai_labels.question_id
          )
          AND (
              TRIM(COALESCE(primary_skill, '')) <> ''
              OR TRIM(COALESCE(model_name, '')) <> ''
              OR user_edited = 1
          )
        """
    )
    connection.execute(
        "INSERT INTO app_migrations (migration_key) VALUES ('question-label-auto-lock-v1')"
    )


def _run_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO question_bank_profiles (name, description, is_default)
        SELECT '考研英语一', '现有题库自动迁移配置', 1
        WHERE NOT EXISTS (
            SELECT 1 FROM question_bank_profiles
            WHERE name = '考研英语一' COLLATE NOCASE
        )
        """
    )
    default_profile_id = connection.execute(
        "SELECT id FROM question_bank_profiles WHERE name = '考研英语一' COLLATE NOCASE ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    _ensure_column(connection, "papers", "external_key", "TEXT")
    _ensure_column(connection, "papers", "package_id", "TEXT")
    _ensure_column(connection, "papers", "content_version", "TEXT")
    _ensure_column(connection, "papers", "source_metadata", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "papers", "profile_id", f"INTEGER NOT NULL DEFAULT {int(default_profile_id)}")
    _ensure_column(connection, "papers", "deleted_at", "TEXT")
    _ensure_column(connection, "units", "external_key", "TEXT")
    _ensure_column(connection, "questions", "external_key", "TEXT")
    _ensure_column(connection, "questions", "content_hash", "TEXT")
    _ensure_column(connection, "options", "metadata", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "wrong_analysis_reports", "scope_key", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "ai_messages", "attachments", "TEXT")
    _ensure_column(connection, "ai_profiles", "adapter", "TEXT NOT NULL DEFAULT 'openai-chat'")
    _ensure_column(connection, "ai_profiles", "reasoning_effort", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "wrong_analysis_reports", "unit_ids", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(connection, "wrong_analysis_reports", "input_snapshot", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "wrong_analysis_states", "analyzed_session_id", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "import_jobs", "parse_context", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "import_jobs", "profile_id", f"INTEGER NOT NULL DEFAULT {int(default_profile_id)}")
    _ensure_column(connection, "import_jobs", "answer_stored_path", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "import_jobs", "deleted_at", "TEXT")
    _ensure_column(connection, "vocabulary_entries", "synonyms", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(connection, "vocabulary_entries", "antonyms", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(connection, "vocabulary_entries", "similar_forms", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(connection, "papers", "exam_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "papers", "exam_month", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "papers", "set_number", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "papers", "session_group_key", "TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET translation_status = 'queued'
        WHERE translation_status = 'pending'
        """
    )
    connection.execute(
        "UPDATE papers SET profile_id = ? WHERE profile_id IS NULL OR profile_id = 0",
        (default_profile_id,),
    )
    connection.execute(
        "UPDATE import_jobs SET profile_id = ? WHERE profile_id IS NULL OR profile_id = 0",
        (default_profile_id,),
    )
    _migrate_question_label_auto_lock(connection)
    # Older databases declared papers.year globally UNIQUE. Rebuild only that
    # table so the new invariant can be scoped by profile/external_key while
    # preserving all existing IDs and foreign-key references.
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'papers'"
    ).fetchone()["sql"] or ""
    if "YEAR INTEGER NOT NULL UNIQUE" in table_sql.upper():
        child_create = _paper_child_create_statements()
        connection.execute("PRAGMA foreign_keys = OFF")
        snapshot_columns: dict[str, list[str]] = {}
        for child in child_create:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (child,),
            ).fetchone():
                columns = _table_columns(connection, child)
                snapshot_columns[child] = columns
                column_list = ", ".join(f'"{column}"' for column in columns)
                connection.execute(
                    f"""
                    CREATE TABLE papers_rebuild_snapshot_{child} AS
                    SELECT {column_list} FROM {child}
                    """
                )
        connection.execute(
            "ALTER TABLE papers RENAME TO papers_rebuild_tmp_papers"
        )
        connection.execute(
            """
            CREATE TABLE papers_rebuild (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                subject TEXT NOT NULL DEFAULT '英语一',
                title TEXT NOT NULL,
                exam_type TEXT NOT NULL DEFAULT '',
                exam_month INTEGER NOT NULL DEFAULT 0,
                set_number INTEGER NOT NULL DEFAULT 1,
                session_group_key TEXT NOT NULL DEFAULT '',
                source_file TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                external_key TEXT,
                package_id TEXT,
                content_version TEXT,
                source_metadata TEXT NOT NULL DEFAULT '{}',
                deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (profile_id) REFERENCES question_bank_profiles(id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO papers_rebuild
                (id, profile_id, year, subject, title, source_file, status,
                 exam_type, exam_month, set_number, session_group_key,
                 external_key, package_id, content_version, source_metadata,
                 deleted_at, created_at, updated_at)
            SELECT id, profile_id, year, subject, title, source_file, status, '', 0, 1, '',
                   external_key, package_id, content_version, source_metadata,
                   deleted_at, created_at, updated_at
            FROM papers_rebuild_tmp_papers
            """
        )
        connection.execute("ALTER TABLE papers_rebuild RENAME TO papers")
        connection.execute("DROP TABLE papers_rebuild_tmp_papers")
        _rebuild_child_tables(
            connection,
            create_statements=child_create,
            order=(
                "units",
                "questions",
                "options",
                "practice_sessions",
                "practice_answers",
                "practice_answer_events",
                "practice_unit_submissions",
                "wrong_stats",
                "vocabulary_occurrences",
                "wrong_analysis_states",
            ),
        )
        connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"数据库迁移后外键校验失败：{violations}"
            )
    connection.execute(
        """
        INSERT OR IGNORE INTO app_settings(key, value)
        VALUES ('active_question_bank_profile_id', ?)
        """,
        (str(default_profile_id),),
    )
    connection.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_units_external_key
            ON units(paper_id, external_key)
            WHERE external_key IS NOT NULL AND external_key <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_external_key
            ON questions(unit_id, external_key)
            WHERE external_key IS NOT NULL AND external_key <> '';
        CREATE INDEX IF NOT EXISTS idx_papers_external_key
            ON papers(external_key);
        CREATE INDEX IF NOT EXISTS idx_papers_profile
            ON papers(profile_id, deleted_at, year DESC);
        CREATE INDEX IF NOT EXISTS idx_import_jobs_profile
            ON import_jobs(profile_id, deleted_at, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trash_purge
            ON trash_entries(purge_after, restored_at);
        """
    )


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)
        _run_migrations(connection)


def get_default_profile_id(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM question_bank_profiles WHERE is_default = 1 AND deleted_at IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        return int(row["id"])
    row = connection.execute(
        "SELECT id FROM question_bank_profiles WHERE deleted_at IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        cursor = connection.execute(
            "INSERT INTO question_bank_profiles(name, is_default) VALUES ('考研英语一', 1)"
        )
        connection.commit()
        return int(cursor.lastrowid)
    return int(row["id"])


def get_active_profile_id(connection: sqlite3.Connection) -> int:
    default_id = get_default_profile_id(connection)
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'active_question_bank_profile_id'"
    ).fetchone()
    try:
        profile_id = int(row["value"]) if row else default_id
    except (TypeError, ValueError):
        profile_id = default_id
    exists = connection.execute(
        "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
        (profile_id,),
    ).fetchone()
    if not exists:
        profile_id = default_id
        connection.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES ('active_question_bank_profile_id', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(profile_id),),
        )
        connection.commit()
    return profile_id


def set_active_profile_id(connection: sqlite3.Connection, profile_id: int) -> None:
    exists = connection.execute(
        "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
        (profile_id,),
    ).fetchone()
    if not exists:
        raise ValueError("题库配置不存在或已在回收站")
    connection.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES ('active_question_bank_profile_id', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(profile_id),),
    )
    connection.commit()


def new_trash_batch() -> tuple[str, str]:
    deleted_at = datetime.now(timezone.utc).replace(microsecond=0)
    purge_after = deleted_at + timedelta(days=7)
    return uuid4().hex, purge_after.strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> Generator[sqlite3.Connection, None, None]:
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()
