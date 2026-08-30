from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.database import _migrate_question_label_auto_lock
from backend.app.services.question_labeling import (
    _ensure_run_context,
    _load_run_context,
    _next_unit,
    _save_labels,
    labeling_status,
    _normalized_label_list,
    pause_label_run,
    _request_batch_with_fallback,
)


class QuestionLabelingTests(unittest.TestCase):
    @staticmethod
    def _run_context_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE question_bank_profiles (id INTEGER PRIMARY KEY, deleted_at TEXT);
            CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE papers (id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL, year INTEGER NOT NULL, deleted_at TEXT);
            CREATE TABLE units (id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL, unit_type TEXT NOT NULL, title TEXT NOT NULL, passage TEXT NOT NULL DEFAULT '', sequence INTEGER NOT NULL);
            CREATE TABLE questions (id INTEGER PRIMARY KEY, unit_id INTEGER NOT NULL);
            CREATE TABLE question_ai_labels (question_id INTEGER PRIMARY KEY, locked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE question_label_run_items (run_id TEXT NOT NULL, question_id INTEGER NOT NULL, PRIMARY KEY(run_id, question_id));
            CREATE TABLE question_label_runs (
                run_id TEXT PRIMARY KEY, question_bank_profile_id INTEGER NOT NULL, scope_kind TEXT NOT NULL,
                year INTEGER, paper_ids TEXT NOT NULL, overwrite_unlocked INTEGER NOT NULL, status TEXT NOT NULL,
                started_at TEXT, updated_at TEXT, finished_at TEXT, last_error TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO question_bank_profiles(id, deleted_at) VALUES (1, NULL), (2, NULL);
            INSERT INTO app_settings(key, value) VALUES ('active_question_bank_profile_id', '1');
            INSERT INTO papers(id, profile_id, year) VALUES (10, 1, 2020), (20, 2, 2020);
            INSERT INTO units(id, paper_id, unit_type, title, sequence) VALUES (100, 10, 'reading', 'old', 1), (200, 20, 'reading', 'new', 1);
            INSERT INTO questions(id, unit_id) VALUES (1000, 100), (2000, 200);
        """)
        return connection

    def test_run_context_remains_bound_after_active_profile_switch(self) -> None:
        connection = self._run_context_connection()
        run_id, context = _ensure_run_context(
            connection, run_id="run-fixed-profile", year=None, paper_ids=[],
            overwrite_unlocked=False, question_bank_profile_id=1,
        )
        connection.execute("UPDATE app_settings SET value = '2' WHERE key = 'active_question_bank_profile_id'")
        unit = _next_unit(
            connection, year=context["year"], paper_ids=context["paper_ids"],
            overwrite_unlocked=context["overwrite_unlocked"], run_id=run_id,
            question_bank_profile_id=context["question_bank_profile_id"],
        )
        self.assertEqual(unit["id"], 100)
        self.assertEqual(context["question_bank_profile_id"], 1)

    def test_pause_is_persisted_and_same_run_can_resume(self) -> None:
        connection = self._run_context_connection()
        run_id, _ = _ensure_run_context(
            connection, run_id="run-pause-resume", year=2020, paper_ids=[],
            overwrite_unlocked=False, question_bank_profile_id=1,
        )
        self.assertEqual(pause_label_run(connection, run_id)["status"], "paused")
        self.assertEqual(_load_run_context(connection, run_id)["status"], "paused")

        resumed_run_id, _ = _ensure_run_context(
            connection, run_id=run_id, year=None, paper_ids=[],
            overwrite_unlocked=True, question_bank_profile_id=2,
        )
        self.assertEqual(resumed_run_id, run_id)
        resumed = _load_run_context(connection, run_id)
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["question_bank_profile_id"], 1)
        self.assertEqual(resumed["year"], 2020)
        self.assertFalse(resumed["overwrite_unlocked"])

    def test_legacy_label_auto_lock_migration_filters_empty_rows_and_is_idempotent(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE questions (id INTEGER PRIMARY KEY);
            CREATE TABLE question_ai_labels (
                question_id INTEGER PRIMARY KEY, primary_skill TEXT NOT NULL DEFAULT '',
                locked INTEGER NOT NULL DEFAULT 0, user_edited INTEGER NOT NULL DEFAULT 0,
                model_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE app_migrations (
                migration_key TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO questions (id) VALUES (1), (2), (3), (4);
            INSERT INTO question_ai_labels
                (question_id, primary_skill, locked, user_edited, model_name)
            VALUES
                (1, '阅读主旨', 0, 0, ''),
                (2, '', 0, 0, 'test-model'),
                (3, '', 0, 1, ''),
                (4, '   ', 0, 0, '   '),
                (99, '孤立标签', 0, 0, '');
        """)

        _migrate_question_label_auto_lock(connection)
        first_result = connection.execute(
            "SELECT question_id, locked FROM question_ai_labels ORDER BY question_id"
        ).fetchall()
        self.assertEqual([(row["question_id"], row["locked"]) for row in first_result], [
            (1, 1), (2, 1), (3, 1), (4, 0), (99, 0),
        ])
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM app_migrations WHERE migration_key = 'question-label-auto-lock-v1'"
            ).fetchone()[0],
            1,
        )

        connection.execute("UPDATE question_ai_labels SET primary_skill = '稍后写入' WHERE question_id = 4")
        _migrate_question_label_auto_lock(connection)
        self.assertEqual(
            connection.execute(
                "SELECT locked FROM question_ai_labels WHERE question_id = 4"
            ).fetchone()["locked"],
            0,
        )

    def test_model_labels_lock_new_and_explicitly_unlocked_rows(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE question_ai_labels (
                question_id INTEGER PRIMARY KEY, primary_skill TEXT NOT NULL DEFAULT '',
                secondary_skills TEXT NOT NULL DEFAULT '[]', trap_types TEXT NOT NULL DEFAULT '[]',
                attention_points TEXT NOT NULL DEFAULT '[]', vocabulary_demand TEXT NOT NULL DEFAULT 'medium',
                context_dependency TEXT NOT NULL DEFAULT 'medium', grammar_dependency TEXT NOT NULL DEFAULT 'medium',
                confidence REAL NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 1,
                user_edited INTEGER NOT NULL DEFAULT 0, model_name TEXT NOT NULL DEFAULT '',
                label_version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE question_label_run_items (
                run_id TEXT NOT NULL, question_id INTEGER NOT NULL,
                PRIMARY KEY (run_id, question_id)
            );
        """)
        label = {"question_id": 1, "primary_skill": "上下文逻辑"}

        self.assertEqual(_save_labels(connection, [label], model_name="test-model", run_id="run-1"), 1)
        row = connection.execute(
            "SELECT primary_skill, locked, label_version FROM question_ai_labels WHERE question_id = 1"
        ).fetchone()
        self.assertEqual((row["primary_skill"], row["locked"], row["label_version"]), ("上下文逻辑", 1, 1))

        connection.execute("UPDATE question_ai_labels SET locked = 0 WHERE question_id = 1")
        replacement = {"question_id": 1, "primary_skill": "同义替换"}
        self.assertEqual(_save_labels(connection, [replacement], model_name="test-model", run_id="run-2"), 1)
        row = connection.execute(
            "SELECT primary_skill, locked, label_version FROM question_ai_labels WHERE question_id = 1"
        ).fetchone()
        self.assertEqual((row["primary_skill"], row["locked"], row["label_version"]), ("同义替换", 1, 2))

        protected = {"question_id": 1, "primary_skill": "不应覆盖"}
        self.assertEqual(_save_labels(connection, [protected], model_name="test-model", run_id="run-3"), 0)
        row = connection.execute(
            "SELECT primary_skill, locked, label_version FROM question_ai_labels WHERE question_id = 1"
        ).fetchone()
        self.assertEqual((row["primary_skill"], row["locked"], row["label_version"]), ("同义替换", 1, 2))

    def test_labeling_status_accepts_explicit_paper_scope(self) -> None:
        class FakeConnection:
            count_sql = ""
            count_params = []

            def execute(self, sql, params=()):
                if "COUNT(q.id)" in sql:
                    self.count_sql = sql
                    self.count_params = list(params)
                    return type("Result", (), {"fetchone": lambda _: {
                        "total": 3,
                        "labeled": 1,
                        "locked": 1,
                        "review_pending": 0,
                    }})()
                return type("Result", (), {"fetchall": lambda _: [{"year": 2002}]})()

        connection = FakeConnection()
        result = labeling_status(connection, paper_ids=[8, 7, 8])
        self.assertEqual(result["paper_ids"], [7, 8])
        self.assertIn("p.id IN (?,?)", connection.count_sql)
        self.assertIn("u.unit_type <> 'listening'", connection.count_sql)
        self.assertEqual(connection.count_params, [7, 8])
        self.assertEqual(result["remaining"], 2)

    def test_invalid_large_batch_is_split_and_retried(self) -> None:
        questions = [{"id": value} for value in range(1, 6)]
        calls: list[int] = []

        def fake_request(connection, *, unit, questions, **kwargs):
            calls.append(len(questions))
            if len(questions) > 2:
                raise json.JSONDecodeError("truncated", "{", 1)
            return [
                {
                    "question_id": question["id"],
                    "primary_skill": "上下文逻辑",
                }
                for question in questions
            ]

        with patch(
            "backend.app.services.question_labeling._request_labels",
            side_effect=fake_request,
        ):
            labels = _request_batch_with_fallback(
                object(),
                unit=object(),
                questions=questions,
            )
        self.assertEqual({label["question_id"] for label in labels}, {1, 2, 3, 4, 5})
        self.assertEqual(calls, [5, 2, 3, 1, 2])

    def test_single_string_label_is_kept_as_one_complete_item(self) -> None:
        self.assertEqual(
            _normalized_label_list("先定位题干关键词，再核对同义替换"),
            ["先定位题干关键词，再核对同义替换"],
        )

    def test_label_list_is_trimmed_deduplicated_and_limited(self) -> None:
        self.assertEqual(
            _normalized_label_list([" 词义辨析 ", "", "词义辨析", "上下文逻辑"], limit=2),
            ["词义辨析", "上下文逻辑"],
        )


if __name__ == "__main__":
    unittest.main()
