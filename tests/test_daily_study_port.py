import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from fastapi import HTTPException
from backend.app import database
from backend.app.services import daily_study, daily_study_identity, sync, vocabulary


class DailyStudyPortTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        # Existing rows before the new column exists exercise the real migration.
        for index, term in enumerate(('apple', 'berry', 'cherry', 'date', 'elder', 'fig', 'grape')):
            self.db.execute('INSERT INTO vocabulary_entries(term,normalized_term,created_at) VALUES(?,?,?)', (term, term, f'2026-10-{index + 10:02} 00:00:00'))
        database._run_migrations(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def entry(self, term):
        return self.db.execute('SELECT * FROM vocabulary_entries WHERE term=?', (term,)).fetchone()

    def review(self, term, rating, date='2026-10-21T01:00:00Z', mode='scheduled'):
        self.db.execute('INSERT INTO vocabulary_reviews(entry_id,rating,mode,reviewed_at) VALUES(?,?,?,?)', (self.entry(term)['id'], rating, mode, date))

    def terms(self, status='new', **kwargs):
        return [item['term'] for item in daily_study.daily_study_catalog(self.db, status=status, **kwargs)['items']]

    def test_new_membership_uses_only_latest_formal_rating(self):
        self.review('apple', 'fluent')
        self.review('apple', 'again', '2026-10-21T02:00:00Z', 'reinforcement')
        self.review('berry', 'again')
        self.review('cherry', 'know')
        self.review('date', 'hard')
        self.review('elder', 'fluent')
        self.review('elder', 'again', '2026-10-22T01:00:00Z')
        self.review('fig', 'fluent', mode='reinforcement')
        self.db.execute("UPDATE vocabulary_entries SET study_status='mastered' WHERE term='grape'")
        self.assertEqual(self.terms(), ['grape', 'fig', 'elder', 'date', 'cherry', 'berry'])

    def test_dates_offsets_ties_and_invalid_values_are_conservative(self):
        self.review('apple', 'fluent', '2026-10-21T08:00:00+08:00')
        self.review('apple', 'again', '2026-10-21T00:00:01Z')
        self.review('berry', 'again', '2026-10-21 00:00:00')
        self.review('berry', 'fluent', '2026-10-21T00:00:00Z')
        self.review('cherry', 'fluent')
        self.review('cherry', 'again', '')
        self.review('date', 'again', 'not-a-time')
        self.review('date', 'fluent', '2026-10-22T00:00:00Z')
        self.review('elder', 'fluent', '2026-10-21T00:00:00.900Z')
        self.review('elder', 'again', '2026-10-21T00:00:00.100Z')
        terms = self.terms()
        self.assertIn('apple', terms)
        self.assertNotIn('berry', terms)
        self.assertIn('cherry', terms)
        self.assertIn('date', terms)
        self.assertNotIn('elder', terms)

    def test_new_search_and_unknown_filters(self):
        self.assertEqual(self.terms(search=' berr '), ['berry'])
        for status, scope in [('typo', 'all'), ('new', 'typo')]:
            with self.assertRaises(HTTPException) as caught:
                daily_study.daily_study_catalog(self.db, status=status, scope=scope)
            self.assertEqual(caught.exception.status_code, 400)

    def test_migration_is_repeatable_preserves_learning_and_read_is_pure(self):
        before = [dict(row) for row in self.db.execute('SELECT * FROM vocabulary_entries')]
        identity = daily_study_identity.namespace(self.db)
        database._run_migrations(self.db)
        self.assertEqual(before, [dict(row) for row in self.db.execute('SELECT * FROM vocabulary_entries')])
        self.assertEqual(identity, daily_study_identity.namespace(self.db))
        self.db.commit()
        changes = self.db.total_changes
        daily_study.daily_study_catalog(self.db)
        daily_study.daily_study_metadata(self.db)
        vocabulary._serialize_entry(self.db, self.entry('apple')['id'])
        self.assertEqual(changes, self.db.total_changes)

    def test_delete_recreate_even_same_numeric_id_changes_identity(self):
        old = self.entry('apple')
        self.db.execute('DELETE FROM vocabulary_entries WHERE id=?', (old['id'],))
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term) VALUES(?,'apple','apple')", (old['id'],))
        new = self.entry('apple')
        self.assertNotEqual(old['daily_study_identity'], new['daily_study_identity'])
        detail = vocabulary._serialize_entry(self.db, new['id'])
        member = next(item for item in daily_study.daily_study_catalog(self.db)['items'] if item['id'] == new['id'])
        self.assertEqual(member['identity'], detail['daily_study_identity'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE vocabulary_entries SET daily_study_identity='changed' WHERE id=?", (new['id'],))

    def test_edit_review_delete_increment_revision_but_noop_does_not(self):
        meta = lambda: daily_study.daily_study_metadata(self.db)
        first = meta()
        self.db.execute("UPDATE vocabulary_entries SET common_meaning='editable' WHERE term='apple'")
        second = meta()
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(first['scope_key'], second['scope_key'])
        self.db.execute("UPDATE vocabulary_entries SET common_meaning='editable' WHERE term='apple'")
        self.assertEqual(second, meta())
        self.review('apple', 'fluent')
        third = meta()
        self.assertNotEqual(second['revision'], third['revision'])
        self.db.execute("DELETE FROM vocabulary_reviews")
        self.assertNotEqual(third['revision'], meta()['revision'])

    def test_same_second_new_order_is_stable(self):
        self.db.execute("UPDATE vocabulary_entries SET created_at='2026-10-21T00:00:00Z'")
        catalog = daily_study.daily_study_catalog(self.db)
        self.assertEqual([item['identity'] for item in catalog['items']], sorted([item['identity'] for item in catalog['items']], reverse=True))
        self.assertEqual(catalog, daily_study.daily_study_catalog(self.db))

    def test_profile_recreation_and_database_copy_do_not_reuse_namespace(self):
        profile = database.get_active_profile_id(self.db)
        old = daily_study.daily_study_metadata(self.db, scope='current')['scope_key']
        self.db.execute('DELETE FROM question_bank_profiles WHERE id=?', (profile,))
        self.db.execute("INSERT INTO question_bank_profiles(id,name,is_default) VALUES(?,'recreated',1)", (profile,))
        self.assertNotEqual(old, daily_study.daily_study_metadata(self.db, scope='current')['scope_key'])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'one.db'
            target = Path(directory) / 'two.db'
            self.db.commit()
            with closing(sqlite3.connect(source)) as destination:
                self.db.backup(destination)
            shutil.copyfile(source, target)
            with closing(sqlite3.connect(source)) as one, closing(sqlite3.connect(target)) as two:
                self.assertNotEqual(daily_study_identity.namespace(one), daily_study_identity.namespace(two))

    def test_review_lookup_uses_entry_mode_index(self):
        plan = self.db.execute("EXPLAIN QUERY PLAN SELECT rating FROM vocabulary_reviews WHERE entry_id=1 AND mode='scheduled' ORDER BY reviewed_at DESC,id DESC LIMIT 1").fetchall()
        self.assertTrue(any('idx_daily_study_scheduled_review' in row[3] for row in plan), [tuple(row) for row in plan])
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_local_identity_never_sent_or_accepted_by_lan(self):
        sync._ensure_sync_columns(self.db)
        row = self.entry('apple')
        payload = sync._serialize_row(self.db, 'vocabulary_entries', row)
        self.assertNotIn('daily_study_identity', payload)
        payload['daily_study_identity'] = 'remote-must-not-win'
        payload['common_meaning'] = 'received'
        payload['updated_at'] = '2099-01-01T00:00:00Z'
        sync._apply_row(self.db, 'vocabulary_entries', payload)
        self.assertEqual(self.entry('apple')['daily_study_identity'], row['daily_study_identity'])


if __name__ == '__main__':
    unittest.main()
