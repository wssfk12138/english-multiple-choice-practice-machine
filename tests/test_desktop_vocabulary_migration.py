import sqlite3
import unittest
from datetime import datetime, timedelta
from backend.app.database import SCHEMA, _run_migrations
from backend.app.schemas import VocabularyReview
from backend.app.services.vocabulary import review_entry, _serialize_entry
from backend.app.routers.vocabulary import list_entries


class DesktopVocabularyMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,synonyms) VALUES(1,'word','word','ready',?)", ('[{"word":"term","note":"test"}]',))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_scores_and_reinforcement(self):
        review_entry(self.db, 1, 'know')
        result = review_entry(self.db, 1, 'fluent')
        self.assertEqual(result['review_stage'], 3)
        before = dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone())
        review_entry(self.db, 1, 'hard', mode='reinforcement')
        self.assertEqual(dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone()), before)
        self.assertEqual(self.db.execute('SELECT mode FROM vocabulary_reviews ORDER BY id DESC').fetchone()[0], 'reinforcement')
        with self.assertRaises(ValueError):
            review_entry(self.db, 1, 'invalid')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM vocabulary_reviews').fetchone()[0], 3)

    def test_list_returns_structured_learning_fields(self):
        result = list_entries(connection=self.db)
        self.assertEqual(result['items'][0]['synonyms'], [{'word':'term', 'note':'test'}])
        self.assertEqual(result['items'][0]['review_revision'], _serialize_entry(self.db,1)['review_revision'])

    def test_legacy_mastered_keeps_seven_day_schedule(self):
        before = datetime.now()
        result = review_entry(self.db, 1, 'mastered')
        after = datetime.now()
        due = datetime.fromisoformat(result['next_review_at'])
        self.assertGreaterEqual(due, before + timedelta(days=7, seconds=-1))
        self.assertLessEqual(due, after + timedelta(days=7))
        self.assertEqual(result['study_status'], 'mastered')
        self.assertEqual(tuple(self.db.execute('SELECT rating,mode FROM vocabulary_reviews').fetchone()), ('mastered','scheduled'))

    def test_additive_migration_preserves_existing_schedule_and_reviews(self):
        self.db.execute("INSERT INTO vocabulary_reviews(entry_id,rating,next_review_at) VALUES(1,'mastered','2030-01-01')")
        self.db.execute("UPDATE vocabulary_entries SET next_review_at='2030-01-01',note='keep me',study_status='mastered'")
        for field in ('review_stage', 'last_result', 'lapse_count'):
            self.db.execute('ALTER TABLE vocabulary_entries DROP COLUMN ' + field)
        self.db.execute('ALTER TABLE vocabulary_reviews DROP COLUMN mode')
        self.db.commit()
        _run_migrations(self.db)
        _run_migrations(self.db)
        row = self.db.execute('SELECT note,next_review_at,study_status,review_stage FROM vocabulary_entries').fetchone()
        self.assertEqual(tuple(row), ('keep me','2030-01-01','mastered',0))
        self.assertEqual(tuple(self.db.execute('SELECT rating,next_review_at,mode FROM vocabulary_reviews').fetchone()), ('mastered','2030-01-01','scheduled'))


if __name__ == '__main__':
    unittest.main()
