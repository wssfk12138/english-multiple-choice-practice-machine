import sqlite3
import unittest
from backend.app.database import SCHEMA
from backend.app.services import vocabulary as service
from backend.app.services.vocabulary import ConflictError


class ReviewRevisionTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status) VALUES(1,'went','went','ready')")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_same_card_can_only_be_rated_once_per_revision(self):
        revision = service._serialize_entry(self.db, 1)['review_revision']
        service.review_entry(self.db, 1, 'hard', expected_revision=revision)
        state = dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone())
        with self.assertRaisesRegex(ConflictError, '复习记录已更新'):
            service.review_entry(self.db, 1, 'again', expected_revision=revision)
        self.assertEqual(dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone()), state)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM vocabulary_reviews').fetchone()[0], 1)
        fresh = service._serialize_entry(self.db, 1)['review_revision']
        self.assertNotEqual(revision, fresh)
        service.review_entry(self.db, 1, 'hard', expected_revision=fresh)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM vocabulary_reviews').fetchone()[0], 2)

    def test_ledger_failure_rolls_back_schedule(self):
        revision = service._serialize_entry(self.db, 1)['review_revision']
        self.db.executescript("CREATE TRIGGER reject_review BEFORE INSERT ON vocabulary_reviews BEGIN SELECT RAISE(ABORT,'ledger unavailable'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            service.review_entry(self.db, 1, 'hard', expected_revision=revision)
        self.assertEqual(service._serialize_entry(self.db, 1)['review_revision'], revision)
        self.assertFalse(self.db.in_transaction)

    def test_external_update_is_not_overwritten(self):
        revision = service._serialize_entry(self.db, 1)['review_revision']
        self.db.execute("UPDATE vocabulary_entries SET next_review_at='2030-01-01' WHERE id=1")
        self.db.commit()
        with self.assertRaises(ConflictError):
            service.review_entry(self.db, 1, 'hard', expected_revision=revision)
        self.assertEqual(self.db.execute('SELECT next_review_at FROM vocabulary_entries').fetchone()[0], '2030-01-01')


if __name__ == '__main__':
    unittest.main()
