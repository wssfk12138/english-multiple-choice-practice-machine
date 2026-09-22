import json
import sqlite3
import unittest

from backend.app import database
from backend.app.services import practice, sync
from backend.app.services.sync_versions import ensure_versions, sequence, version
from backend.app.services.attempt_stats import aggregate


class SyncConvergenceTests(unittest.TestCase):
    def test_attempt_ledger_legacy_baseline_order_replay_and_validation(self):
        baseline = dict(attempt_count=7, wrong_count=3, consecutive_correct=1, recent_results='[false,true]')
        a = aggregate(baseline, attempt=('a', '2026-09-08T00:00:00.000Z', False))
        b = aggregate(baseline, attempt=('b', '2026-09-08T00:00:00.000Z', True))
        merged = aggregate(a, b)
        self.assertEqual(merged, aggregate(b, a))
        self.assertEqual(merged, aggregate(merged, a))
        self.assertEqual((merged['attempt_count'], merged['wrong_count'], merged['consecutive_correct']), (9, 4, 1))
        for i in range(12):
            merged = aggregate(merged, attempt=(f'c{i:02d}', '2026-09-09T00:00:00.000Z', True))
        self.assertEqual(merged['recent_results'], '[true,true,true,true,true,true,true,true,true,true]')
        with self.assertRaises(ValueError):
            aggregate({'attempt_ledger': '{"v":1,"base":{},"attempts":{"a":["today",1]}}'})

    def fixture(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        connection.executescript(database.SCHEMA)
        database._run_migrations(connection)
        sync._ensure_sync_columns(connection)
        self.addCleanup(connection.close)
        return connection

    def test_attempt_ledger_rejects_extra_fields_overflow_and_deduplicates_session(self):
        a = aggregate({}, attempt=('session', '2026-09-08T00:00:00.000Z', False))
        b = aggregate({}, attempt=('session', '2026-09-08T00:00:00.000Z', True))
        merged = aggregate(a, b)
        self.assertEqual(merged, aggregate(b, a))
        self.assertEqual(merged['attempt_count'], 1)
        value = json.loads(a['attempt_ledger'])
        value['base']['unexpected_column'] = 'unsafe'
        with self.assertRaises(ValueError):
            aggregate({'attempt_ledger': json.dumps(value)})
        with self.assertRaises(ValueError):
            aggregate({'attempt_count': 9007199254740991}, attempt=('new', '2026', True))

    def insert(self, connection, meaning):
        connection.execute(
            'INSERT INTO vocabulary_entries(term, normalized_term, note, updated_at) VALUES (?, ?, ?, ?)',
            ('word', 'word', meaning, '2026-09-08 10:00:00'),
        )
        connection.commit()

    def test_same_second_conflicts_converge(self):
        a, b = self.fixture(), self.fixture()
        self.insert(a, 'alpha')
        self.insert(b, 'zulu')
        left = sync.snapshot(a, ['vocabulary_entries'], [])
        right = sync.snapshot(b, ['vocabulary_entries'], [])
        sync.merge(a, right['changes'], [])
        sync.merge(b, left['changes'], [])
        self.assertEqual(a.execute('SELECT note FROM vocabulary_entries').fetchone()[0],
                         b.execute('SELECT note FROM vocabulary_entries').fetchone()[0])

    def test_consumed_cursor_sends_same_row_mutation(self):
        connection = self.fixture()
        self.insert(connection, 'before')
        first = sync.snapshot(connection, ['vocabulary_entries'], [])
        connection.execute("UPDATE vocabulary_entries SET note='after'")
        connection.commit()
        next_batch = sync.snapshot(connection, ['vocabulary_entries'], [], first['cursor'])
        self.assertEqual(len(next_batch['changes']['vocabulary_entries']), 1)
        self.assertEqual(next_batch['changes']['vocabulary_entries'][0]['note'], 'after')
        replay = sync.snapshot(connection, ['vocabulary_entries'], [], next_batch['cursor'])
        self.assertEqual(replay['changes']['vocabulary_entries'], [])

    def test_causal_edit_and_replay(self):
        a, b = self.fixture(), self.fixture()
        self.insert(a, 'zulu')
        first = sync.snapshot(a, ['vocabulary_entries'], [])
        sync.merge(b, first['changes'], [])
        b.execute("UPDATE vocabulary_entries SET note='alpha'")
        second = sync.snapshot(b, ['vocabulary_entries'], [])
        sync.merge(a, second['changes'], [])
        self.assertEqual(a.execute('SELECT note FROM vocabulary_entries').fetchone()[0], 'alpha')
        settled = sync.snapshot(a, ['vocabulary_entries'], [])
        for _ in range(3):
            sync.merge(a, second['changes'], [])
        self.assertEqual(sync.snapshot(a, ['vocabulary_entries'], [], settled['cursor'])['changes']['vocabulary_entries'], [])

    def test_migration_recursive_triggers_and_rollback(self):
        c = self.fixture()
        c.execute('PRAGMA recursive_triggers=ON')
        self.insert(c, 'before')
        first = sync.snapshot(c, ['vocabulary_entries'], [])
        ensure_versions(c, ['vocabulary_entries', 'sync_tombstones'])
        self.assertEqual(sync.snapshot(c, ['vocabulary_entries'], [], first['cursor'])['changes']['vocabulary_entries'], [])
        c.execute('SAVEPOINT edit')
        c.execute("UPDATE vocabulary_entries SET note='after'")
        c.execute('ROLLBACK TO edit')
        c.execute('RELEASE edit')
        self.assertEqual(sync.snapshot(c, ['vocabulary_entries'], [], first['cursor'])['changes']['vocabulary_entries'], [])
        c.execute("UPDATE vocabulary_entries SET note='after'")
        self.assertEqual(len(sync.snapshot(c, ['vocabulary_entries'], [], first['cursor'])['changes']['vocabulary_entries']), 1)
        legacy = {'vocabulary_entries': {'updated_at': '9999', 'rowid': 999}}
        self.assertEqual(len(sync.snapshot(c, ['vocabulary_entries'], [], legacy)['changes']['vocabulary_entries']), 1)

    def test_tombstone_replay_does_not_echo_or_resurrect(self):
        a, b = self.fixture(), self.fixture()
        self.insert(a, 'old')
        old = sync.snapshot(a, ['vocabulary_entries'], [])
        tomb = {'table_name': 'vocabulary_entries', 'object_key': 'word', 'profile_name': '', 'deleted_at': '2026-09-08 11:00:00'}
        sync.merge(b, {}, [tomb])
        first = sync.snapshot(b, [], [])
        sync.merge(b, old['changes'], [tomb])
        self.assertEqual(b.execute('SELECT COUNT(*) FROM vocabulary_entries').fetchone()[0], 0)
        self.assertEqual(sync.snapshot(b, [], [], tombstone_cursor=first['tombstone_cursor'])['tombstones'], [])
        self.assertEqual(first['tombstones'][0]['deleted_at'], tomb['deleted_at'])

    def test_metadata_rejected_and_batch_rollback(self):
        a, b = self.fixture(), self.fixture()
        self.insert(a, 'old')
        changes = sync.snapshot(a, ['vocabulary_entries'], [])['changes']
        tomb = dict(table_name='vocabulary_entries', object_key='word', profile_name='Legacy Bank', deleted_at='2099')
        sync.merge(b, {}, [tomb])
        sync.merge(b, changes, [])
        self.assertEqual(b.execute('SELECT COUNT(*) FROM vocabulary_entries').fetchone()[0], 0)
        for bad in (None, '', ' ', 1):
            with self.assertRaises(ValueError):
                sync.merge(a, {}, [dict(tomb, deleted_at=bad)])
        self.assertEqual(a.execute('SELECT COUNT(*) FROM vocabulary_entries').fetchone()[0], 1)
        for bad in (None, -1, 1.5, True, '1', 9007199254740992):
            with self.assertRaises(ValueError):
                sequence({'seq': bad})
            with self.assertRaises(ValueError):
                version({'_sync_rev': bad})
        a, b = self.fixture(), self.fixture()
        self.insert(a, 'value')
        changes = sync.snapshot(a, ['vocabulary_entries'], [])['changes']
        invalid = dict(changes['vocabulary_entries'][0], normalized_term='other', _sync_origin='invalid')
        changes['vocabulary_entries'].append(invalid)
        with self.assertRaises(ValueError):
            sync.merge(b, changes, [])
        self.assertEqual(b.execute('SELECT COUNT(*) FROM vocabulary_entries').fetchone()[0], 0)

    def test_legacy_backfill_and_same_second_tombstone_rewrite(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        self.addCleanup(c.close)
        c.executescript("CREATE TABLE legacy(id INTEGER PRIMARY KEY, note TEXT, updated_at TEXT); INSERT INTO legacy VALUES(1,'keep','2020')")
        ensure_versions(c, ['legacy'])
        row = c.execute('SELECT * FROM legacy').fetchone()
        self.assertEqual((row['note'], row['updated_at']), ('keep', '2020'))
        self.assertGreater(row['_sync_seq'], 0)
        self.assertEqual(row['_sync_rev'], 1)
        ensure_versions(c, ['legacy'])
        self.assertEqual(dict(row), dict(c.execute('SELECT * FROM legacy').fetchone()))
        db = self.fixture()
        sync._record_tombstone(db, 'vocabulary_entries', 'word')
        first = sync.snapshot(db, [], [])
        sync._record_tombstone(db, 'vocabulary_entries', 'word')
        second = sync.snapshot(db, [], [], tombstone_cursor=first['tombstone_cursor'])
        self.assertEqual(len(second['tombstones']), 1)

    def test_production_submissions_aggregate_independent_attempts(self):
        a, b = self.fixture(), self.fixture()
        for c, identity in ((a, 'left'), (b, 'right')):
            profile = c.execute("INSERT INTO question_bank_profiles(name) VALUES('Test Bank')").lastrowid
            paper = c.execute("INSERT INTO papers(profile_id,year,title,status,external_key) VALUES(?,2026,'Test','published','paper')", (profile,)).lastrowid
            unit = c.execute("INSERT INTO units(paper_id,unit_type,title,sequence,external_key) VALUES(?,'reading','Unit',1,'unit')", (paper,)).lastrowid
            question = c.execute("INSERT INTO questions(unit_id,number,answer,score,sequence,external_key) VALUES(?,1,'A',1,1,'question')", (unit,)).lastrowid
            session = c.execute("INSERT INTO practice_sessions(mode,unit_ids,sync_id) VALUES('unit',?,?)", (f'[{unit}]', identity)).lastrowid
            c.execute("INSERT INTO practice_answers(session_id,question_id,user_answer) VALUES(?,?,'')", (session, question))
            c.commit()
            practice.save_answer(c, session, question, 'B', ['A', 'B'])
            practice.submit_session(c, session)
            practice.submit_session(c, session)
            self.assertEqual(c.execute('SELECT attempt_count FROM wrong_stats').fetchone()[0], 1)
        tables = ['practice_sessions', 'practice_answers', 'practice_answer_events', 'practice_unit_submissions', 'wrong_stats']
        left = sync.snapshot(a, tables, ['Test Bank'])
        right = sync.snapshot(b, tables, ['Test Bank'])
        for _ in range(2):
            sync.merge(a, right['changes'], [])
            sync.merge(b, left['changes'], [])
        for c in (a, b):
            for table in tables[:-1]:
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 2, table)
            self.assertEqual(c.execute('SELECT attempt_count FROM wrong_stats').fetchone()[0], 2)
        settled = sync.snapshot(a, tables, ['Test Bank'])
        sync.merge(a, right['changes'], [])
        self.assertEqual(sync.snapshot(a, tables, ['Test Bank'], settled['cursor'])['changes']['wrong_stats'], [])
        old = dict(right['changes']['wrong_stats'][0])
        old.pop('attempt_ledger')
        with self.assertRaisesRegex(ValueError, 'Legacy peer'):
            sync.merge(a, {'wrong_stats': [old]}, [])
        tomb = dict(table_name='wrong_stats', object_key='question', profile_name='Test Bank', deleted_at='2099')
        sync.merge(a, {}, [tomb])
        sync.merge(a, right['changes'], [])
        self.assertEqual(a.execute('SELECT count(*) FROM wrong_stats').fetchone()[0], 0)
