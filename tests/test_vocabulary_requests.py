import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import database
from backend.app.services import vocabulary_requests as requests

CONFIG = {'hash': 'synthetic-configuration', 'summary': '{"model":"synthetic"}'}


class VocabularyRequestTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        requests.migrate(self.db)
        for index in range(1, 42):
            self.db.execute('INSERT INTO vocabulary_entries(id,term,normalized_term) VALUES(?,?,?)', (index, 'word'+str(index), 'word'+str(index)))
        self.db.execute("INSERT INTO vocabulary_enrichment_jobs(entry_id) VALUES(41)")
        self.db.execute("UPDATE vocabulary_entries SET translation_status='ready',user_edited=1 WHERE id=41")
        self.db.commit()
        self.config = patch.object(requests, 'configuration', return_value=CONFIG)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.db.close()

    def run_command(self, ids=None, **kwargs):
        return requests.create_run(self.db, 'translation', ids, config=CONFIG, **kwargs)

    def finish_success(self, claim):
        execution = requests.Execution(claim)
        execution.before_send(self.db)
        execution.after_response(self.db)
        for entry_id in claim['ids']:
            self.db.execute("UPDATE vocabulary_entries SET translation_status='ready' WHERE id=?", (entry_id,))
        return requests.finish(self.db, execution)

    def test_idempotent_command_reuses_frozen_members_and_conflicts_on_payload(self):
        run = self.run_command([1,2], command_id='synthetic-command-0001', explicit=True)
        self.assertEqual(run, self.run_command([1,2], command_id='synthetic-command-0001', explicit=True))
        with self.assertRaises(HTTPException) as caught:
            self.run_command([3], command_id='synthetic-command-0001', explicit=True)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNone(self.run_command([3], source='vocabulary_open'))
        self.assertEqual([row[0] for row in self.db.execute('SELECT entry_id FROM vocabulary_request_members')], [1,2])

    def test_shared_budget_stops_after_four_requests_and_eight_words_per_batch(self):
        self.run_command(list(range(1,41)))
        for index in range(4):
            claim = requests.claim(self.db, CONFIG)
            self.assertEqual(len(claim['ids']), 8)
            self.assertEqual(self.finish_success(claim), 8)
        self.assertIsNone(requests.claim(self.db, CONFIG))
        self.assertEqual(requests.status(self.db)['reason'], 'budget')
        self.assertEqual(requests.status(self.db)['used'], 4)
        self.assertEqual(len(requests.eligible(self.db, 'translation')), 8)

    def test_crash_after_intent_or_send_never_automatically_replays(self):
        for sent in (False, True):
            with self.subTest(sent=sent):
                if sent:
                    requests.acknowledge(self.db, self.db.execute("SELECT id FROM vocabulary_request_attempts WHERE state='unknown'").fetchone()[0])
                self.run_command([2 if sent else 1], explicit=True, retry=True)
                claim = requests.claim(self.db, CONFIG)
                if sent:
                    requests.Execution(claim).before_send(self.db)
                requests.recover(self.db)
                status = requests.status(self.db)
                self.assertEqual(status['reason'], 'unknown')
                self.assertEqual(len(status['unknown']), 1)
                self.assertIsNone(self.run_command([3]))
                requests.recover(self.db)
                self.assertEqual(len(requests.status(self.db)['unknown']), 1)

    def test_unknown_scoped_retry_cannot_requeue_other_members(self):
        self.run_command([1,2,3])
        claim = requests.claim(self.db, CONFIG)
        requests.Execution(claim).before_send(self.db)
        requests.recover(self.db)
        self.run_command([2], explicit=True, retry=True, source='retry')
        next_claim = requests.claim(self.db, CONFIG)
        self.assertEqual(next_claim['ids'], [2])
        self.assertEqual(self.db.execute('SELECT translation_status FROM vocabulary_entries WHERE id=1').fetchone()[0], 'failed')
        self.assertEqual(self.db.execute('SELECT translation_status FROM vocabulary_entries WHERE id=3').fetchone()[0], 'failed')

    def test_acknowledgement_only_clears_the_selected_unknown_and_never_sends(self):
        requests.recover(self.db)
        self.db.execute("INSERT INTO vocabulary_request_attempts(id,task,state,ids,created_at) VALUES('one','translation','unknown','[1]',1),('two','translation','unknown','[2]',2)")
        self.db.commit()
        requests.acknowledge(self.db,'one')
        self.assertEqual([row['id'] for row in requests.status(self.db)['unknown']], ['two'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM vocabulary_request_runs').fetchone()[0], 0)

    def test_content_edit_and_configuration_change_abort_before_send(self):
        self.run_command([1])
        claim = requests.claim(self.db, CONFIG)
        self.db.execute("UPDATE vocabulary_entries SET note='manual same-second edit' WHERE id=1")
        self.db.commit()
        execution = requests.Execution(claim)
        with self.assertRaises(requests.RequestAbort):
            execution.before_send(self.db)
        self.assertFalse(execution.sent)
        requests.finish(self.db, execution, 'content_changed')
        self.assertEqual(self.db.execute('SELECT note FROM vocabulary_entries WHERE id=1').fetchone()[0], 'manual same-second edit')
        self.assertEqual(requests.status(self.db)['used'], 1, 'reserved request is not refunded')
        self.assertEqual(requests.status(self.db)['reason'], 'content_changed')

    def test_content_and_attempt_confirm_in_one_transaction(self):
        self.run_command([1])
        claim = requests.claim(self.db, CONFIG)
        execution = requests.Execution(claim)
        execution.before_send(self.db)
        execution.after_response(self.db)
        self.db.execute("UPDATE vocabulary_entries SET common_meaning='new meaning',translation_status='ready' WHERE id=1")
        self.db.execute("CREATE TEMP TRIGGER reject_attempt BEFORE UPDATE ON vocabulary_request_attempts BEGIN SELECT RAISE(ABORT,'synthetic save failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            requests.finish(self.db, execution)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT common_meaning FROM vocabulary_entries WHERE id=1').fetchone()[0], '')
        self.assertEqual(self.db.execute('SELECT state FROM vocabulary_request_attempts').fetchone()[0], 'inflight')

    def test_bulk_retry_merges_without_renewing_budget_and_keeps_other_task_slot(self):
        run = requests.create_run(self.db, 'enrichment', [41], config=CONFIG)
        claim = requests.claim(self.db, CONFIG)
        execution = requests.Execution(claim)
        execution.before_send(self.db)
        target = self.run_command(None, explicit=True, retry=True, source='retry-all')
        self.assertNotEqual(run, target)
        self.assertEqual(requests.status(self.db)['used'],1)
        self.assertEqual(self.db.execute("SELECT state FROM vocabulary_request_attempts WHERE id=?", (claim['id'],)).fetchone()[0], 'inflight')
        execution.after_response(self.db)
        requests.finish(self.db,execution)
        next_claim=requests.claim(self.db,CONFIG)
        self.assertEqual(next_claim['task'],'translation')
        self.assertEqual(requests.status(self.db)['used'],2)


if __name__ == '__main__':
    unittest.main()
