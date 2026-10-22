import copy
import json
import sqlite3
import unittest

from backend.app import database
from backend.app.services import content_remediation


class ContentRemediationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        profile = database.get_default_profile_id(self.db)
        paper = self.db.execute(
            "INSERT INTO papers(profile_id,external_key,year,title,status) VALUES(?,'p',2016,'Exam','published')",
            (profile,),
        ).lastrowid
        self.unit_id = self.db.execute(
            "INSERT INTO units(paper_id,external_key,unit_type,title,sequence,passage) VALUES(?,'u1','reading','Text',1,'Original passage')",
            (paper,),
        ).lastrowid
        question = self.db.execute(
            "INSERT INTO questions(unit_id,external_key,number,answer,score,sequence,stem) VALUES(?,'q1',1,'A',2,1,'Stem')",
            (self.unit_id,),
        ).lastrowid
        for index, label in enumerate(('A', 'B'), 1):
            self.db.execute(
                'INSERT INTO options(question_id,stable_key,original_label,content,sequence) VALUES(?,?,?,?,?)',
                (question, label, label, 'Option ' + label, index),
            )
        self.db.commit()
        unit = self.db.execute('SELECT * FROM units WHERE id=?', (self.unit_id,)).fetchone()
        self.before = content_remediation.read_unit(self.db, unit)
        self.after = copy.deepcopy(self.before)
        self.after['passage'] = 'Corrected passage'
        self.variant = copy.deepcopy(self.before)
        self.variant['passage'] = 'Reviewed passage'
        self.manifest = {
            'version': 1,
            'revision': 'test-r1',
            'units': [{
                'unitKey': 'u1',
                'before': self.before,
                'after': self.after,
                'reviewedHashes': [content_remediation.digest(self.variant)],
                'questionHashes': {q['key']: '0' * 64 for q in self.after['questions']},
            }],
        }

    def tearDown(self):
        self.db.close()

    def _apply(self):
        self.db.execute('BEGIN')
        try:
            result = content_remediation.apply(self.db, self.manifest)
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def test_reviewed_hash_is_accepted_without_overwrite(self):
        self.db.execute('UPDATE units SET passage=? WHERE id=?', (self.variant['passage'], self.unit_id))
        self.db.commit()
        record = content_remediation.preflight(self.db, self.manifest)[0]
        self.assertEqual(record['conflicts'], [])
        self.assertTrue(record['accepted'])
        self.assertTrue(record['needed'])

        result = self._apply()
        self.assertEqual(result['accepted'], 1)
        self.assertEqual(result['units'], 0)
        unit = self.db.execute('SELECT * FROM units WHERE id=?', (self.unit_id,)).fetchone()
        self.assertEqual(unit['passage'], self.variant['passage'])
        self.assertEqual(json.loads(unit['shared_data'] or '{}').get('content_revision'), 'test-r1')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM content_remediation_receipts').fetchone()[0], 1)

        record = content_remediation.preflight(self.db, self.manifest)[0]
        self.assertTrue(record['accepted'])
        self.assertFalse(record['needed'])

    def test_unreviewed_before_is_updated_to_after(self):
        record = content_remediation.preflight(self.db, self.manifest)[0]
        self.assertFalse(record['accepted'])
        self.assertTrue(record['needed'])

        result = self._apply()
        self.assertEqual(result['accepted'], 0)
        self.assertEqual(result['units'], 1)
        unit = self.db.execute('SELECT * FROM units WHERE id=?', (self.unit_id,)).fetchone()
        self.assertEqual(unit['passage'], 'Corrected passage')
        self.assertEqual(json.loads(unit['shared_data'] or '{}').get('content_revision'), 'test-r1')

    def test_unreviewed_variant_still_conflicts(self):
        self.db.execute('UPDATE units SET passage=? WHERE id=?', ('Unreviewed passage', self.unit_id))
        self.db.commit()
        record = content_remediation.preflight(self.db, self.manifest)[0]
        self.assertEqual(record['conflicts'], ['passage'])
        with self.assertRaises(ValueError):
            self._apply()


if __name__ == '__main__':
    unittest.main()
