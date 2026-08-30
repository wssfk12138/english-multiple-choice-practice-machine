from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class PracticeAnswerPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "practice-privacy.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH",
            self.database_path,
        )
        self.database_patch.start()

        from backend.app.database import initialize_database

        initialize_database()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temp.cleanup()

    def _create_two_unit_paper(self, connection) -> tuple[int, list[int], list[int]]:
        paper_id = int(
            connection.execute(
                """
                INSERT INTO papers
                    (profile_id, year, subject, title, source_file, status)
                VALUES (1, 2099, '英语一', '答案隐私回归试卷',
                        'privacy-test.docx', 'published')
                """
            ).lastrowid
        )
        unit_ids: list[int] = []
        question_ids: list[int] = []
        for sequence in (1, 2):
            unit_id = int(
                connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage,
                         shared_data)
                    VALUES (?, 'reading', 'single_choice', ?, ?, '', '{}')
                    """,
                    (paper_id, f"阅读单元 {sequence}", sequence),
                ).lastrowid
            )
            question_id = int(
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, ?, 'A', 2, 1)
                    """,
                    (unit_id, f"测试题 {sequence}"),
                ).lastrowid
            )
            connection.executemany(
                """
                INSERT INTO options
                    (question_id, stable_key, original_label, content, sequence)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (question_id, "A", "A", "正确选项", 1),
                    (question_id, "B", "B", "错误选项", 2),
                ],
            )
            unit_ids.append(unit_id)
            question_ids.append(question_id)
        connection.commit()
        return paper_id, unit_ids, question_ids

    def _assert_graded_without_answer(
        self,
        payload: dict,
        expected_results: dict[int, bool],
    ) -> None:
        questions = {
            question["id"]: question
            for unit in payload["units"]
            for question in unit["questions"]
        }
        for question_id, expected_correct in expected_results.items():
            question = questions[question_id]
            self.assertNotIn("answer", question)
            self.assertIn("user_answer", question)
            self.assertEqual(question["is_correct"], expected_correct)

    def test_unit_and_session_submission_keep_correct_answers_private(self) -> None:
        from backend.app.database import connect
        from backend.app.schemas import PracticeCreate
        from backend.app.services.practice import (
            create_session,
            save_answer,
            submit_session,
            submit_unit,
        )

        with connect() as connection:
            paper_id, unit_ids, question_ids = self._create_two_unit_paper(connection)
            session = create_session(
                connection,
                PracticeCreate(
                    mode="paper",
                    paper_id=paper_id,
                    shuffle_options=False,
                ),
            )

            save_answer(connection, session["id"], question_ids[0], "A", ["A", "B"])
            submitted_unit = submit_unit(connection, session["id"], unit_ids[0])
            self._assert_graded_without_answer(
                submitted_unit,
                {question_ids[0]: True},
            )
            self.assertEqual(submitted_unit["units"][0]["submission"]["score"], 2)

            save_answer(connection, session["id"], question_ids[1], "B", ["A", "B"])
            submitted_session = submit_session(connection, session["id"])

        self._assert_graded_without_answer(
            submitted_session,
            {question_ids[0]: True, question_ids[1]: False},
        )
        self.assertEqual(submitted_session["score"], 2)
        self.assertEqual(submitted_session["max_score"], 4)
        self.assertEqual(submitted_session["result_summary"]["correct_count"], 1)
        self.assertEqual(submitted_session["result_summary"]["wrong_count"], 1)


if __name__ == "__main__":
    unittest.main()
