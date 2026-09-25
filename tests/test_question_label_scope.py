import sqlite3
import unittest

from fastapi import HTTPException
from backend.app import database
from backend.app.routers.ai import list_question_labels


class QuestionLabelScopeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        self.active = database.get_active_profile_id(self.db)
        self.other = self.db.execute("INSERT INTO question_bank_profiles(name) VALUES ('Other synthetic bank')").lastrowid
        self.papers = []
        self.questions = []
        for index, profile in enumerate([self.active, self.other, self.active]):
            paper = self.db.execute("INSERT INTO papers(profile_id,year,title,status) VALUES (?,2024,'Synthetic','published')", (profile,)).lastrowid
            unit = self.db.execute("INSERT INTO units(paper_id,unit_type,title,sequence) VALUES (?,'reading','Synthetic unit',1)", (paper,)).lastrowid
            question = self.db.execute("INSERT INTO questions(unit_id,number,sequence,stem,answer,score) VALUES (?,1,1,'Synthetic question','A',2)", (unit,)).lastrowid
            self.papers.append(paper)
            self.questions.append(question)
        self.db.execute("UPDATE papers SET deleted_at='2026-01-01' WHERE id=?", (self.papers[2],))

    def tearDown(self):
        self.db.close()

    def ids(self, **kwargs):
        return [row['question_id'] for row in list_question_labels(connection=self.db, **kwargs)]

    def test_default_scope_excludes_other_banks_and_trash(self):
        self.assertEqual(self.ids(), [self.questions[0]])

    def test_explicit_profile_survives_active_bank_difference(self):
        self.assertEqual(self.ids(question_bank_profile_id=self.other, year=2024), [self.questions[1]])

    def test_explicit_papers_remain_compatible_without_profile(self):
        self.assertEqual(self.ids(paper_ids=str(self.papers[1])), [self.questions[1]])

    def test_profile_and_papers_intersect(self):
        self.assertEqual(self.ids(question_bank_profile_id=self.active, paper_ids=str(self.papers[1])), [])

    def test_missing_profile_fails_clearly(self):
        with self.assertRaises(HTTPException) as raised:
            self.ids(question_bank_profile_id=999999)
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()

