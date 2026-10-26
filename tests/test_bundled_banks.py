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
        # 计数由当前 examples/bundled-banks 资产决定。英语一 1.2.0 起随包提供
        # 767 条与内容哈希一致的锁定标注（旧 1.1.0 资产只有 390 条可用）。
        self.assertEqual([item["labelsImported"] for item in first], [767, 344])

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
                {"考研英语一": (767, 767), "考研英语二": (344, 344)},
            )

        second = install_bundled_question_banks()
        self.assertEqual(
            [item["status"] for item in second],
            ["already_installed", "already_installed"],
        )
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS count FROM papers").fetchone()["count"], 34)
            self.assertEqual(connection.execute("SELECT COUNT(*) AS count FROM questions").fetchone()["count"], 1525)

    def test_missing_package_files_are_skipped_without_failing(self) -> None:
        from backend.app.database import connect, initialize_database
        from backend.app.services.bundled_banks import install_bundled_question_banks

        initialize_database()
        with TemporaryDirectory(ignore_cleanup_errors=True) as empty_dir:
            with patch("backend.app.services.bundled_banks.BUNDLED_BANK_DIR", Path(empty_dir)):
                results = install_bundled_question_banks()

        self.assertEqual(
            results,
            [
                {"fileName": "postgraduate-english-one.esq", "status": "missing"},
                {"fileName": "postgraduate-english-two.esq", "status": "missing"},
            ],
        )
        with connect() as connection:
            profile_names = [
                row["name"]
                for row in connection.execute("SELECT name FROM question_bank_profiles").fetchall()
            ]
            self.assertEqual(profile_names, ["考研英语一"])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM papers").fetchone()["count"],
                0,
            )

    def test_stale_locked_labels_are_retired_but_manual_edits_kept(self) -> None:
        from backend.app.database import connect, initialize_database
        from backend.app.services.bundled_banks import (
            install_bundled_question_banks,
            retire_stale_labels,
        )
        from backend.app.services.esq import load_esq_package

        initialize_database()
        install_bundled_question_banks()
        package_path = BUNDLED_BANK_DIR / 'postgraduate-english-one.esq'
        package = load_esq_package(package_path)
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT q.id AS question_id, q.external_key, p.year, p.profile_id
                FROM questions AS q
                JOIN units AS u ON u.id = q.unit_id
                JOIN papers AS p ON p.id = u.paper_id
                WHERE p.package_id = 'wssfk.postgraduate-english-one.2010-2026'
                  AND p.deleted_at IS NULL
                ORDER BY q.id
                """
            ).fetchall()
            shipped = {
                key
                for paper in package['papers']
                for key in (paper.get('labels') or {}).get('labels', {})
            }
            stale = [row for row in rows if row['external_key'] not in shipped]
            manual = [row for row in rows if row['external_key'] in shipped][0]
            connection.execute(
                'UPDATE question_ai_labels SET user_edited = 1 WHERE question_id = ?',
                (manual['question_id'],),
            )
            # Simulate an upgraded library: the earlier package shipped a
            # locked label for every question, including the ones this package
            # no longer describes.
            connection.executemany(
                """
                INSERT INTO question_ai_labels
                    (question_id, primary_skill, locked, model_name)
                VALUES (?, '旧版本标注', 1, 'legacy')
                ON CONFLICT(question_id) DO NOTHING
                """,
                [(row['question_id'],) for row in stale],
            )
            connection.commit()
        self.assertGreater(len(stale), 0)
        with connect() as connection:
            retired = retire_stale_labels(connection, package)
            connection.commit()
        self.assertEqual(retired, len(stale))
        with connect() as connection:
            remaining = {
                row[0]
                for row in connection.execute(
                    'SELECT question_id FROM question_ai_labels'
                )
            }
            kept = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM question_ai_labels AS l
                JOIN questions AS q ON q.id = l.question_id
                JOIN units AS u ON u.id = q.unit_id
                JOIN papers AS p ON p.id = u.paper_id
                WHERE p.package_id = 'wssfk.postgraduate-english-one.2010-2026'
                """
            ).fetchone()['count']
        self.assertTrue(remaining.isdisjoint({row['question_id'] for row in stale}))
        self.assertIn(manual['question_id'], remaining)
        self.assertEqual(kept, 767)

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
