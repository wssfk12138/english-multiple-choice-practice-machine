from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_BANK_DIR = ROOT / "examples" / "bundled-banks"


class BundledQuestionBankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "bundled-test.db"
        self.patches = [
            patch("backend.app.database.DATABASE_PATH", self.database_path),
            patch("backend.app.services.bundled_banks.BUNDLED_BANK_DIR", BUNDLED_BANK_DIR),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def test_first_launch_installs_both_banks_and_is_idempotent(self) -> None:
        from backend.app.database import (
            connect,
            initialize_database,
            set_active_profile_id,
        )
        from backend.app.schemas import PracticeCreate
        from backend.app.services.bundled_banks import install_bundled_question_banks
        from backend.app.services.practice import create_session

        initialize_database()
        first = install_bundled_question_banks()
        self.assertEqual([item["status"] for item in first], ["installed", "installed"])
        self.assertEqual([item["questionCount"] for item in first], [805, 720])
        self.assertEqual([item["labelsImported"] for item in first], [360, 320])

        with connect() as connection:
            profiles = {
                row["name"]: int(row["id"])
                for row in connection.execute(
                    "SELECT id, name FROM question_bank_profiles WHERE deleted_at IS NULL"
                )
            }
            expected = {"考研英语一": (18, 805), "考研英语二": (16, 720)}
            for profile_name, (paper_count, question_count) in expected.items():
                profile_id = profiles[profile_name]
                actual_papers = connection.execute(
                    "SELECT COUNT(*) AS count FROM papers WHERE profile_id = ? AND status = 'published' AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone()["count"]
                actual_questions = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM questions
                    JOIN units ON units.id = questions.unit_id
                    JOIN papers ON papers.id = units.paper_id
                    WHERE papers.profile_id = ? AND papers.status = 'published' AND papers.deleted_at IS NULL
                    """,
                    (profile_id,),
                ).fetchone()["count"]
                self.assertEqual((actual_papers, actual_questions), (paper_count, question_count))
                set_active_profile_id(connection, profile_id)
                session = create_session(
                    connection,
                    PracticeCreate(mode="random", unit_type="reading", count=1, shuffle_options=True),
                )
                self.assertEqual(len(session["units"]), 1)
                self.assertGreater(len(session["units"][0]["questions"]), 0)
            label_counts = connection.execute(
                """
                SELECT p.name,
                       COUNT(l.question_id) AS total,
                       SUM(CASE WHEN l.locked = 1 THEN 1 ELSE 0 END) AS locked
                FROM question_bank_profiles AS p
                JOIN papers AS paper ON paper.profile_id = p.id
                JOIN units AS u ON u.paper_id = paper.id
                JOIN questions AS q ON q.unit_id = u.id
                LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
                GROUP BY p.id, p.name
                """
            ).fetchall()
            self.assertEqual(
                {
                    row["name"]: (int(row["total"]), int(row["locked"]))
                    for row in label_counts
                },
                {"考研英语一": (360, 360), "考研英语二": (320, 320)},
            )

        second = install_bundled_question_banks()
        self.assertEqual(
            [item["status"] for item in second],
            ["already_installed", "already_installed"],
        )
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS count FROM papers").fetchone()["count"], 34)
            self.assertEqual(connection.execute("SELECT COUNT(*) AS count FROM questions").fetchone()["count"], 1525)

    def test_existing_user_paper_is_not_replaced(self) -> None:
        from backend.app.database import connect, initialize_database
        from backend.app.services.bundled_banks import install_bundled_question_banks

        initialize_database()
        with connect() as connection:
            profile_id = connection.execute(
                "SELECT id FROM question_bank_profiles WHERE name = '考研英语一'"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO papers
                    (profile_id, year, subject, title, source_file, status, external_key)
                VALUES (?, 2010, '英语一', '用户自己的 2010 试卷', '', 'published',
                        'local.english-practice.2010')
                """,
                (profile_id,),
            )
            connection.commit()

        result = install_bundled_question_banks()
        self.assertEqual(result[0]["status"], "installed")

        with connect() as connection:
            paper = connection.execute(
                """
                SELECT title, package_id
                FROM papers
                WHERE profile_id = ? AND external_key = 'local.english-practice.2010'
                """,
                (profile_id,),
            ).fetchone()
            self.assertEqual(paper["title"], "用户自己的 2010 试卷")
            self.assertIsNone(paper["package_id"])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM papers WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()["count"],
                18,
            )


if __name__ == "__main__":
    unittest.main()
