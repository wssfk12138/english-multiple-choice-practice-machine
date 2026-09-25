from __future__ import annotations

import sqlite3
import unittest

from backend.app.routers.ai import _latest_completed_wrong_retry_session


class WrongAnalysisLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE practice_sessions (
                id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE practice_unit_submissions (
                session_id INTEGER NOT NULL,
                unit_id INTEGER NOT NULL
            );
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_only_submitted_wrong_retry_for_the_same_unit_unlocks_analysis(self) -> None:
        self.connection.executemany(
            "INSERT INTO practice_sessions (id, mode, status) VALUES (?, ?, ?)",
            [
                (2, "paper", "submitted"),
                (3, "wrong", "active"),
                (4, "wrong", "active"),
            ],
        )
        self.connection.executemany(
            "INSERT INTO practice_unit_submissions (session_id, unit_id) VALUES (?, ?)",
            [
                (2, 10),
                (3, 11),
                (4, 10),
            ],
        )

        self.assertEqual(
            _latest_completed_wrong_retry_session(self.connection, 10, 1),
            4,
        )
        self.assertIsNone(
            _latest_completed_wrong_retry_session(self.connection, 11, 3)
        )


if __name__ == "__main__":
    unittest.main()
