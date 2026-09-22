from __future__ import annotations

import sqlite3
import unittest

from pydantic import ValidationError

from backend.app import database
from backend.app.schemas import LanSyncPushRequest
from backend.app.services import sync


class LanSyncMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(database.SCHEMA)
        database._run_migrations(self.connection)
        sync._ensure_sync_columns(self.connection)
        profile_id = self.connection.execute(
            "INSERT INTO question_bank_profiles(name, is_default) VALUES ('Test Bank', 1)"
        ).lastrowid
        paper_id = self.connection.execute(
            """
            INSERT INTO papers(profile_id, year, title, status, external_key)
            VALUES (?, 2026, 'Test Paper', 'published', 'paper-1')
            """,
            (profile_id,),
        ).lastrowid
        self.unit_id = self.connection.execute(
            """
            INSERT INTO units(paper_id, unit_type, title, external_key, sequence)
            VALUES (?, 'reading', 'Unit', 'unit-1', 1)
            """,
            (paper_id,),
        ).lastrowid
        self.question_id = self.connection.execute(
            """
            INSERT INTO questions(unit_id, number, answer, score, sequence, external_key)
            VALUES (?, 1, 'A', 1, 1, 'question-1')
            """,
            (self.unit_id,),
        ).lastrowid
        self.session_id = self.connection.execute(
            """
            INSERT INTO practice_sessions(mode, unit_ids, sync_id, updated_at)
            VALUES ('unit', ?, 'session-local', '2026-01-01T00:00:00Z')
            """,
            (f"[{self.unit_id}]",),
        ).lastrowid
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_remote_default_profile_preserves_local_default_and_secret(self) -> None:
        self.connection.execute("DELETE FROM ai_profiles")
        self.connection.execute(
            "INSERT INTO ai_profiles(name, base_url, is_default, api_key_encrypted, updated_at) "
            "VALUES ('Local device', 'https://example.invalid/v1', 1, 'local-secret-placeholder', '2026-01-01')"
        )
        self.connection.commit()
        remote = {
            'name': 'Remote device', 'is_default': 1,
            'base_url': 'https://example.invalid/v1',
            'api_key_encrypted': 'remote-secret-placeholder',
            'updated_at': '2030-01-01',
        }
        sync.merge(self.connection, {'ai_profiles': [remote]}, [])
        self.assertEqual(self.connection.execute(
            'SELECT name FROM ai_profiles WHERE is_default = 1'
        ).fetchone()[0], 'Local device')
        self.assertIsNone(self.connection.execute(
            "SELECT COALESCE(api_key_encrypted, '') FROM ai_profiles WHERE name = 'Remote device'"
        ).fetchone(), 'S4 requires the separate credential exchange')
        sync.merge(self.connection, {'ai_profiles': [{
            'name': 'Local device', 'is_default': 0,
            'base_url': 'https://changed.invalid/v1',
            'api_key_encrypted': 'replacement-placeholder', 'updated_at': '2030-01-02',
        }]}, [])
        row = self.connection.execute(
            "SELECT is_default, api_key_encrypted FROM ai_profiles WHERE name = 'Local device'"
        ).fetchone()
        self.assertEqual(tuple(row), (1, 'local-secret-placeholder'))
        self.assertEqual(self.connection.execute(
            "SELECT base_url FROM ai_profiles WHERE name = 'Local device'"
        ).fetchone()[0], 'https://example.invalid/v1')
        exported = sync.snapshot(self.connection, ['ai_profiles'], [])
        self.assertTrue(all('api_key_encrypted' not in item
                            for item in exported['changes']['ai_profiles']))
        self.assertEqual(sync.merge(self.connection, {'ai_profiles': [remote]}, [])['ai_profiles'], 0)

    def test_legacy_models_cannot_fill_empty_device_preference(self) -> None:
        self.connection.execute("DELETE FROM ai_profiles")
        self.connection.commit()
        sync.merge(self.connection, {'ai_profiles': [{
            'name': 'First profile', 'is_default': 1, 'updated_at': '2030-01-01',
            'base_url': 'https://example.invalid/v1',
        }]}, [])
        self.assertIsNone(self.connection.execute(
            "SELECT is_default FROM ai_profiles WHERE name = 'First profile'"
        ).fetchone())

    def answer_payload(
        self,
        sync_id: str,
        answer: str,
        updated_at: str,
        session_id_key: str = "session-local",
    ) -> dict:
        return {
            "sync_id": sync_id,
            "session_id_key": session_id_key,
            "question_id_key": "question-1",
            "user_answer": answer,
            "option_order": '["A", "B"]',
            "updated_at": updated_at,
        }

    def test_composite_key_keeps_canonical_id_and_alias_tombstone_deletes_it(self) -> None:
        self.connection.execute(
            """
            INSERT INTO practice_answers(
                session_id, question_id, user_answer, sync_id, updated_at
            ) VALUES (?, ?, 'A', 'answer-local', '2026-01-01T00:00:00Z')
            """,
            (self.session_id, self.question_id),
        )
        self.connection.commit()

        applied = sync.merge(
            self.connection,
            {
                "practice_answers": [
                    self.answer_payload(
                        "answer-remote", "B", "2026-02-01T00:00:00Z"
                    )
                ]
            },
            [],
        )

        self.assertEqual(applied["practice_answers"], 1)
        answer = self.connection.execute(
            "SELECT sync_id, user_answer FROM practice_answers"
        ).fetchone()
        self.assertEqual(dict(answer), {"sync_id": "answer-local", "user_answer": "B"})
        alias = self.connection.execute(
            """
            SELECT canonical_sync_id FROM sync_id_aliases
            WHERE table_name = 'practice_answers' AND alias_sync_id = 'answer-remote'
            """
        ).fetchone()
        self.assertEqual(alias["canonical_sync_id"], "answer-local")

        sync.merge(
            self.connection,
            {},
            [{
                "table_name": "practice_answers",
                "object_key": "answer-remote",
                "profile_name": "Test Bank",
                "deleted_at": "2026-03-01T00:00:00Z",
            }],
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM practice_answers").fetchone()[0],
            0,
        )
        tombstone = self.connection.execute(
            "SELECT object_key FROM sync_tombstones "
            "WHERE table_name = 'practice_answers'"
        ).fetchone()
        self.assertEqual(tombstone["object_key"], "answer-local")

    def test_alias_resolves_child_reference_in_same_batch(self) -> None:
        self.connection.execute(
            """
            INSERT INTO wrong_retry_rounds(
                unit_id, session_id, round_number, sync_id, updated_at
            ) VALUES (?, ?, 1, 'round-local', '2026-01-01T00:00:00Z')
            """,
            (self.unit_id, self.session_id),
        )
        self.connection.commit()

        sync.merge(
            self.connection,
            {
                "wrong_retry_rounds": [{
                    "sync_id": "round-remote",
                    "unit_id_key": "unit-1",
                    "session_id_key": "session-local",
                    "round_number": 1,
                    "question_count": 1,
                    "updated_at": "2026-02-01T00:00:00Z",
                }],
                "wrong_retry_round_questions": [{
                    "sync_id": "round-question-remote",
                    "round_id_key": "round-remote",
                    "question_id_key": "question-1",
                    "user_answer": "B",
                    "is_correct": 0,
                    "updated_at": "2026-02-01T00:00:01Z",
                }],
            },
            [],
        )

        child = self.connection.execute(
            """
            SELECT r.sync_id AS round_sync_id, q.user_answer
            FROM wrong_retry_round_questions q
            JOIN wrong_retry_rounds r ON r.id = q.round_id
            """
        ).fetchone()
        self.assertEqual(dict(child), {"round_sync_id": "round-local", "user_answer": "B"})

    def test_missing_required_parent_rolls_back_entire_batch(self) -> None:
        with self.assertRaises(ValueError):
            sync.merge(
                self.connection,
                {
                    "vocabulary_entries": [{
                        "term": "rollback",
                        "normalized_term": "rollback",
                        "updated_at": "2026-02-01T00:00:00Z",
                    }],
                    "practice_answers": [{
                        **self.answer_payload(
                            "answer-invalid", "B", "2026-02-01T00:00:00Z"
                        ),
                        "session_id_key": "missing-session",
                    }],
                },
                [],
            )

        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM vocabulary_entries WHERE normalized_term = 'rollback'"
            ).fetchone()
        )

    def test_stable_references_choose_active_rows_in_the_named_profile(self) -> None:
        deleted_paper = self.connection.execute(
            "INSERT INTO papers(profile_id, year, title, status, external_key, deleted_at) "
            "VALUES ((SELECT id FROM question_bank_profiles WHERE name = 'Test Bank'), "
            "2026, 'Deleted Copy', 'published', 'paper-deleted', CURRENT_TIMESTAMP)"
        ).lastrowid
        deleted_unit = self.connection.execute(
            "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) "
            "VALUES (?, 'reading', 'Deleted Unit', 'unit-1', 1)",
            (deleted_paper,),
        ).lastrowid
        deleted_question = self.connection.execute(
            "INSERT INTO questions(unit_id, number, answer, score, sequence, external_key) "
            "VALUES (?, 1, 'A', 1, 1, 'question-1')",
            (deleted_unit,),
        ).lastrowid
        self.connection.commit()

        payload = self.answer_payload(
            "active-only-answer", "B", "2026-02-01T00:00:00Z"
        )
        sync.merge(self.connection, {"practice_answers": [payload]}, [])

        resolved = self.connection.execute(
            "SELECT question_id FROM practice_answers WHERE sync_id = 'active-only-answer'"
        ).fetchone()
        self.assertEqual(resolved["question_id"], self.question_id)
        self.assertNotEqual(resolved["question_id"], deleted_question)

    def test_stable_references_do_not_cross_question_bank_profiles(self) -> None:
        other_profile = self.connection.execute(
            "INSERT INTO question_bank_profiles(name) VALUES ('Other Bank')"
        ).lastrowid
        other_paper = self.connection.execute(
            "INSERT INTO papers(profile_id, year, title, status, external_key) "
            "VALUES (?, 2026, 'Other Paper', 'published', 'paper-other')",
            (other_profile,),
        ).lastrowid
        other_unit = self.connection.execute(
            "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) "
            "VALUES (?, 'reading', 'Other Unit', 'unit-1', 1)",
            (other_paper,),
        ).lastrowid
        other_question = self.connection.execute(
            "INSERT INTO questions(unit_id, number, answer, score, sequence, external_key) "
            "VALUES (?, 1, 'A', 1, 1, 'question-1')",
            (other_unit,),
        ).lastrowid
        self.connection.commit()

        payload = self.answer_payload(
            "profile-scoped-answer", "B", "2026-02-01T00:00:00Z"
        )
        payload["profile_name"] = "Test Bank"
        sync.merge(self.connection, {"practice_answers": [payload]}, [])
        resolved = self.connection.execute(
            "SELECT question_id FROM practice_answers WHERE sync_id = 'profile-scoped-answer'"
        ).fetchone()
        self.assertEqual(resolved["question_id"], self.question_id)
        self.assertNotEqual(resolved["question_id"], other_question)

    def test_ambiguous_unscoped_stable_reference_rolls_back_batch(self) -> None:
        other_profile = self.connection.execute(
            "INSERT INTO question_bank_profiles(name) VALUES ('Other Bank')"
        ).lastrowid
        other_paper = self.connection.execute(
            "INSERT INTO papers(profile_id, year, title, status, external_key) "
            "VALUES (?, 2026, 'Other Paper', 'published', 'paper-other')",
            (other_profile,),
        ).lastrowid
        other_unit = self.connection.execute(
            "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) "
            "VALUES (?, 'reading', 'Other Unit', 'unit-1', 1)",
            (other_paper,),
        ).lastrowid
        self.connection.execute(
            "INSERT INTO questions(unit_id, number, answer, score, sequence, external_key) "
            "VALUES (?, 1, 'A', 1, 1, 'question-1')",
            (other_unit,),
        )
        self.connection.commit()
        payload = self.answer_payload(
            "ambiguous-answer", "B", "2026-02-01T00:00:00Z"
        )
        payload["profile_name"] = ""

        with self.assertRaisesRegex(ValueError, "conflicts in profile"):
            sync.merge(self.connection, {"practice_answers": [payload]}, [])
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM practice_answers WHERE sync_id = 'ambiguous-answer'"
            ).fetchone()
        )

    def test_repeated_merge_is_idempotent_and_newer_local_value_wins(self) -> None:
        payload = self.answer_payload(
            "answer-remote", "B", "2026-02-01T00:00:00Z"
        )
        first = sync.merge(
            self.connection, {"practice_answers": [payload]}, []
        )
        second = sync.merge(
            self.connection, {"practice_answers": [payload]}, []
        )
        self.assertEqual(first["practice_answers"], 1)
        self.assertEqual(second["practice_answers"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM practice_answers"
            ).fetchone()[0],
            1,
        )

        local = self.connection.execute(
            "SELECT sync_id FROM practice_answers"
        ).fetchone()
        self.connection.execute(
            """
            UPDATE practice_answers
            SET user_answer = 'C', updated_at = '2026-03-01T00:00:00Z'
            WHERE sync_id = ?
            """,
            (local["sync_id"],),
        )
        self.connection.commit()
        older = sync.merge(
            self.connection,
            {
                "practice_answers": [
                    self.answer_payload(
                        "answer-other-device", "A", "2026-02-15T00:00:00Z"
                    )
                ]
            },
            [],
        )
        self.assertEqual(older["practice_answers"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT user_answer FROM practice_answers"
            ).fetchone()["user_answer"],
            "C",
        )

    def test_two_independent_devices_round_trip_learning_history_and_alias_deletion(self) -> None:
        remote = sqlite3.connect(":memory:")
        remote.row_factory = sqlite3.Row
        remote.executescript(database.SCHEMA)
        database._run_migrations(remote)
        sync._ensure_sync_columns(remote)
        try:
            profile_id = remote.execute(
                "INSERT INTO question_bank_profiles(name, is_default) VALUES ('Test Bank', 1)"
            ).lastrowid
            paper_id = remote.execute(
                "INSERT INTO papers(profile_id, year, title, status, external_key) VALUES (?, 2026, 'Test Paper', 'published', 'paper-1')",
                (profile_id,),
            ).lastrowid
            unit_id = remote.execute(
                "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) VALUES (?, 'reading', 'Unit', 'unit-1', 1)",
                (paper_id,),
            ).lastrowid
            question_id = remote.execute(
                "INSERT INTO questions(unit_id, number, answer, score, sequence, external_key) VALUES (?, 1, 'A', 1, 1, 'question-1')",
                (unit_id,),
            ).lastrowid
            session_id = remote.execute(
                "INSERT INTO practice_sessions(mode, unit_ids, sync_id, updated_at) VALUES ('unit', ?, 'session-remote', '2026-02-01T00:00:00Z')",
                (f'[{unit_id}]',),
            ).lastrowid
            remote.execute(
                "INSERT INTO practice_answers(session_id, question_id, user_answer, sync_id, updated_at) VALUES (?, ?, 'B', 'answer-remote', '2026-02-01T00:00:01Z')",
                (session_id, question_id),
            )
            round_id = remote.execute(
                "INSERT INTO wrong_retry_rounds(unit_id, session_id, round_number, sync_id, updated_at) VALUES (?, ?, 1, 'round-remote', '2026-02-01T00:00:02Z')",
                (unit_id, session_id),
            ).lastrowid
            remote.execute(
                "INSERT INTO wrong_retry_round_questions(round_id, question_id, user_answer, is_correct, sync_id, updated_at) VALUES (?, ?, 'B', 0, 'round-question-remote', '2026-02-01T00:00:03Z')",
                (round_id, question_id),
            )
            entry_id = remote.execute(
                "INSERT INTO vocabulary_entries(term, normalized_term, updated_at) VALUES ('synchronize', 'synchronize', '2026-02-01T00:00:04Z')"
            ).lastrowid
            remote.execute(
                "INSERT INTO vocabulary_reviews(entry_id, rating, sync_id, updated_at) VALUES (?, 'good', 'review-remote', '2026-02-01T00:00:05Z')",
                (entry_id,),
            )
            remote.commit()

            incoming = sync.snapshot(remote, sync.SYNC_TABLES, ['Test Bank'])
            sync.merge(self.connection, incoming['changes'], incoming['tombstones'])
            self.assertEqual(self.connection.execute("SELECT user_answer FROM practice_answers").fetchone()[0], 'B')
            self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM wrong_retry_round_questions").fetchone()[0], 1)
            self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM vocabulary_reviews").fetchone()[0], 1)

            # Simulate the Android device independently creating the same answer
            # logical record with a different random sync id, then deleting it.
            local_answer = self.connection.execute("SELECT sync_id FROM practice_answers").fetchone()[0]
            sync.merge(
                self.connection,
                {
                    "practice_answers": [
                        self.answer_payload(
                            "answer-android",
                            "C",
                            "2026-02-02T00:00:00Z",
                            session_id_key="session-remote",
                        )
                    ]
                },
                [],
            )
            local_answers = self.connection.execute(
                "SELECT sync_id, user_answer FROM practice_answers"
            ).fetchall()
            self.assertEqual(
                [(row["sync_id"], row["user_answer"]) for row in local_answers],
                [(local_answer, "C")],
            )
            outbound = sync.snapshot(self.connection, sync.SYNC_TABLES, ['Test Bank'])
            sync.merge(remote, outbound['changes'], outbound['tombstones'])
            remote_answers = remote.execute(
                "SELECT sync_id, user_answer FROM practice_answers"
            ).fetchall()
            self.assertEqual(
                [(row["sync_id"], row["user_answer"]) for row in remote_answers],
                [("answer-remote", "C")],
            )

            sync.merge(self.connection, {}, [{
                'table_name': 'practice_answers', 'object_key': 'answer-android',
                'profile_name': 'Test Bank', 'deleted_at': '2026-02-03T00:00:00Z',
            }])
            deleted = sync.snapshot(self.connection, sync.SYNC_TABLES, ['Test Bank'])
            sync.merge(remote, deleted['changes'], deleted['tombstones'])
            self.assertEqual(remote.execute("SELECT COUNT(*) FROM practice_answers").fetchone()[0], 0)
            sync.merge(remote, deleted['changes'], deleted['tombstones'])
            self.assertEqual(remote.execute("SELECT COUNT(*) FROM practice_answers").fetchone()[0], 0)
        finally:
            remote.close()

    def test_legacy_content_rows_receive_deterministic_external_keys(self) -> None:
        profile_id = self.connection.execute(
            "INSERT INTO question_bank_profiles(name, is_default) VALUES ('Legacy Bank', 0)"
        ).lastrowid
        paper_id = self.connection.execute(
            "INSERT INTO papers(profile_id, year, title, status, external_key) VALUES (?, 2025, 'Legacy', 'published', 'legacy.paper')",
            (profile_id,),
        ).lastrowid
        unit_id = self.connection.execute(
            "INSERT INTO units(paper_id, unit_type, title, sequence) VALUES (?, 'reading', 'Legacy Unit', 3)",
            (paper_id,),
        ).lastrowid
        question_id = self.connection.execute(
            "INSERT INTO questions(unit_id, number, answer, score, sequence) VALUES (?, 7, 'B', 1, 1)",
            (unit_id,),
        ).lastrowid
        self.connection.commit()

        sync._ensure_sync_columns(self.connection)

        unit_key = self.connection.execute(
            "SELECT external_key FROM units WHERE id = ?", (unit_id,)
        ).fetchone()[0]
        question_key = self.connection.execute(
            "SELECT external_key FROM questions WHERE id = ?", (question_id,)
        ).fetchone()[0]
        self.assertEqual(unit_key, 'legacy.paper.unit3')
        self.assertEqual(question_key, 'legacy.paper.q07')

        sync._ensure_sync_columns(self.connection)
        self.assertEqual(
            self.connection.execute("SELECT external_key FROM units WHERE id = ?", (unit_id,)).fetchone()[0],
            unit_key,
        )

    def test_snapshot_cursor_uses_rowid_to_cover_equal_timestamps_once(self) -> None:
        self.connection.execute(
            "INSERT INTO practice_sessions(mode, unit_ids, sync_id, updated_at) "
            "VALUES ('unit', ?, 'session-same-time-1', '2026-04-01T00:00:00Z')",
            (f"[{self.unit_id}]",),
        )
        self.connection.execute(
            "INSERT INTO practice_sessions(mode, unit_ids, sync_id, updated_at) "
            "VALUES ('unit', ?, 'session-same-time-2', '2026-04-01T00:00:00Z')",
            (f"[{self.unit_id}]",),
        )
        self.connection.commit()

        first = sync.snapshot(
            self.connection, ["practice_sessions"], ["Test Bank"]
        )
        same_time = [
            row for row in first["changes"]["practice_sessions"]
            if row["sync_id"].startswith("session-same-time-")
        ]
        self.assertEqual(len(same_time), 2)
        self.assertEqual(
            first["cursor"]["practice_sessions"]["updated_at"],
            "2026-04-01T00:00:00Z",
        )

        second = sync.snapshot(
            self.connection,
            ["practice_sessions"],
            ["Test Bank"],
            first["cursor"],
        )
        self.assertEqual(second["changes"]["practice_sessions"], [])

    def test_invisible_profile_rows_and_tombstones_do_not_advance_watermarks(self) -> None:
        other_profile = self.connection.execute(
            "INSERT INTO question_bank_profiles(name) VALUES ('Other Bank')"
        ).lastrowid
        other_paper = self.connection.execute(
            "INSERT INTO papers(profile_id, year, title, status, external_key) "
            "VALUES (?, 2026, 'Other Paper', 'published', 'other-paper')",
            (other_profile,),
        ).lastrowid
        other_unit = self.connection.execute(
            "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) "
            "VALUES (?, 'reading', 'Other Unit', 'other-unit', 1)",
            (other_paper,),
        ).lastrowid
        self.connection.execute(
            "INSERT INTO practice_sessions(mode, unit_ids, sync_id, updated_at) "
            "VALUES ('unit', ?, 'other-session', '2026-05-01T00:00:00Z')",
            (f"[{other_unit}]",),
        )
        self.connection.execute(
            "INSERT INTO sync_tombstones(table_name, object_key, profile_name, deleted_at) "
            "VALUES ('practice_sessions', 'other-deleted', 'Other Bank', "
            "'2026-05-01T00:00:00Z')"
        )
        self.connection.commit()

        hidden = sync.snapshot(
            self.connection, ["practice_sessions"], ["Test Bank"]
        )
        self.assertNotIn(
            "other-session",
            [row["sync_id"] for row in hidden["changes"]["practice_sessions"]],
        )
        self.assertNotEqual(
            hidden["cursor"]["practice_sessions"]["updated_at"],
            "2026-05-01T00:00:00Z",
        )
        self.assertEqual(hidden["tombstone_cursor"], {})

        visible = sync.snapshot(
            self.connection, ["practice_sessions"], ["Test Bank", "Other Bank"]
        )
        self.assertIn(
            "other-session",
            [row["sync_id"] for row in visible["changes"]["practice_sessions"]],
        )
        self.assertEqual(visible["tombstones"][0]["object_key"], "other-deleted")


class LanSyncRequestBoundsTests(unittest.TestCase):
    def test_push_accepts_bounded_change_batch(self) -> None:
        payload = LanSyncPushRequest(
            token="token",
            changes={"practice_sessions": [{"sync_id": "one"}]},
        )
        self.assertEqual(len(payload.changes["practice_sessions"]), 1)

    def test_push_rejects_more_than_ten_thousand_change_rows(self) -> None:
        with self.assertRaisesRegex(ValidationError, "10000"):
            LanSyncPushRequest(
                token="token",
                changes={
                    "practice_sessions": [
                        {"sync_id": f"session-{index}"}
                        for index in range(10_001)
                    ]
                },
            )

    def test_push_rejects_payload_larger_than_sixteen_mib(self) -> None:
        with self.assertRaisesRegex(ValidationError, "16 MiB"):
            LanSyncPushRequest(
                token="token",
                changes={"ai_profiles": [{"system_prompt": "x" * (17 * 1024 * 1024)}]},
            )


class LanSyncPairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(database.SCHEMA)
        database._run_migrations(self.connection)
        sync._ensure_sync_columns(self.connection)
        sync.set_passcode(self.connection, "AAAA")

    def tearDown(self) -> None:
        sync.disable_sync(self.connection)
        self.connection.close()

    def test_sync_is_disabled_until_a_passcode_is_generated(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(database.SCHEMA)
        try:
            self.assertFalse(sync.sync_enabled(connection))
            self.assertIsNone(sync.issue_token(connection, "AAAA", [], "new-device"))
        finally:
            connection.close()

    def test_passcode_rotation_requires_current_passcode_for_paired_devices(self) -> None:
        old_token = sync.issue_token(
            self.connection, "AAAA", ["Test Bank"], "device-1"
        )
        self.assertIsNotNone(old_token)

        new_passcode = sync.reset_passcode(self.connection)
        self.assertNotEqual(new_passcode, "AAAA")
        self.assertFalse(sync.verify_token(old_token or ""))

        self.assertIsNone(
            sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-1")
        )
        self.assertIsNone(
            sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-new")
        )
        self.assertIsNotNone(
            sync.issue_token(self.connection, new_passcode, ["Test Bank"], "device-1")
        )
        paired = self.connection.execute(
            "SELECT COUNT(*) FROM sync_paired_devices"
        ).fetchone()[0]
        self.assertEqual(paired, 1)

    def test_paired_device_id_is_not_an_authentication_secret(self) -> None:
        self.assertIsNotNone(
            sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-1")
        )
        self.assertIsNone(
            sync.issue_token(self.connection, "WRONG", ["Test Bank"], "device-1")
        )

    def test_revoke_all_devices_requires_passcode_again(self) -> None:
        old_token = sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-1")
        sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-2")
        revoked = sync.revoke_all_devices(self.connection)
        self.assertEqual(revoked, 2)
        paired = self.connection.execute(
            "SELECT COUNT(*) FROM sync_paired_devices"
        ).fetchone()[0]
        self.assertEqual(paired, 0)
        self.assertFalse(sync.verify_token(old_token or ""))
        # The current passcode remains the gate: the device must handshake
        # (re-pair) with it again.
        self.assertIsNotNone(
            sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-1")
        )

    def test_disable_invalidates_tokens_and_requires_explicit_reenable(self) -> None:
        token = sync.issue_token(self.connection, "AAAA", ["Test Bank"], "device-1")
        self.assertTrue(sync.verify_token(token or ""))
        sync.disable_sync(self.connection)
        self.assertFalse(sync.sync_enabled(self.connection))
        self.assertFalse(sync.verify_token(token or ""))
        self.assertIsNone(sync.issue_token(self.connection, "AAAA", [], "device-1"))


class LanSyncProfileScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(database.SCHEMA)
        database._run_migrations(self.connection)
        sync._ensure_sync_columns(self.connection)

        for name in ("Test Bank", "Other Bank"):
            profile_id = self.connection.execute(
                "INSERT INTO question_bank_profiles(name) VALUES (?)", (name,)
            ).lastrowid
            paper_id = self.connection.execute(
                "INSERT INTO papers(profile_id, year, title, status, external_key) "
                "VALUES (?, 2026, ?, 'published', ?)",
                (profile_id, name, f"{name}.paper"),
            ).lastrowid
            self.connection.execute(
                "INSERT INTO units(paper_id, unit_type, title, external_key, sequence) "
                "VALUES (?, 'reading', ?, ?, 1)",
                (paper_id, name, f"{name}.unit"),
            )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_merge_only_accepts_profile_scoped_rows_and_tombstones_in_token_scope(self) -> None:
        applied = sync.merge(
            self.connection,
            {
                "practice_sessions": [
                    {
                        "sync_id": "allowed-session",
                        "mode": "unit",
                        "unit_ids_keys": ["Test Bank.unit"],
                        "profile_name": "Test Bank",
                        "updated_at": "2026-08-31T01:00:00Z",
                    },
                    {
                        "sync_id": "hidden-session",
                        "mode": "unit",
                        "unit_ids_keys": ["Other Bank.unit"],
                        "profile_name": "Other Bank",
                        "updated_at": "2026-08-31T01:00:00Z",
                    },
                    {
                        "sync_id": "unscoped-session",
                        "mode": "unit",
                        "unit_ids_keys": ["Other Bank.unit"],
                        "updated_at": "2026-08-31T01:00:00Z",
                    },
                ],
                "vocabulary_entries": [
                    {
                        "term": "global",
                        "normalized_term": "global",
                        "updated_at": "2026-08-31T01:00:00Z",
                    }
                ],
            },
            [
                {
                    "table_name": "practice_sessions",
                    "object_key": "hidden-delete",
                    "profile_name": "Other Bank",
                    "deleted_at": "2026-08-31T01:01:00Z",
                }
            ],
            ["Test Bank"],
        )

        sessions = self.connection.execute(
            "SELECT sync_id FROM practice_sessions ORDER BY sync_id"
        ).fetchall()
        self.assertEqual([row[0] for row in sessions], ["allowed-session"])
        self.assertEqual(applied["practice_sessions"], 1)
        self.assertEqual(applied["vocabulary_entries"], 1)
        self.assertEqual(applied["_tombstones"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sync_tombstones WHERE profile_name = 'Other Bank'"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
