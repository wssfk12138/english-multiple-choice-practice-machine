import sqlite3
import unittest
from backend.app.database import SCHEMA
from backend.app.services.vocabulary import add_vocabulary


class VocabularyIdentityTests(unittest.TestCase):
    def test_legacy_collision_and_distinct_forms(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,encounter_count,common_meaning) VALUES(1,'Claims','claim',4,'legacy manual')")
        existing = add_vocabulary(db, {'term':'claims'})
        self.assertEqual(existing['entry_id'], 1)
        base = add_vocabulary(db, {'term':'claim'})
        self.assertNotEqual(base['entry_id'], 1)
        for term in ['claimed','claiming','went','go']:
            a = add_vocabulary(db, {'term':term})
            b = add_vocabulary(db, {'term':term.upper()})
            self.assertEqual(a['entry_id'], b['entry_id'])
            self.assertTrue(a['is_new'])
            self.assertFalse(b['is_new'])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM vocabulary_entries').fetchone()[0], 6)
        legacy = db.execute('SELECT * FROM vocabulary_entries WHERE id=1').fetchone()
        self.assertEqual(legacy['normalized_term'], 'claim')
        self.assertEqual(legacy['common_meaning'], 'legacy manual')
        self.assertEqual(db.execute('SELECT normalized_term FROM vocabulary_entries WHERE id=?', (base['entry_id'],)).fetchone()[0], 'surface:v1:claim')
