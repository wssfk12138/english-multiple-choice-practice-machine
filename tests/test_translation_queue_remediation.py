from __future__ import annotations
import json
import sqlite3
import unittest
from unittest.mock import patch
from tests.vocabulary_execution_fixture import migrate, reply
from backend.app.services import vocabulary_requests as requests
import httpx
from backend.app.database import SCHEMA
from backend.app.services import vocabulary as service

class TranslationQueueRemediationTests(unittest.TestCase):
    @staticmethod
    def valid_translation(entry_id, term, meaning='meaning'):
        return {'entryId': entry_id, 'commonMeaning': meaning,
                'morphology': {'lemma': term, 'currentForm': 'base', 'ambiguous': False, 'note': '', 'forms': []},
                'generatedExample': {'sentence': f'We discussed {term} after class.',
                                     'translation': '我们课后讨论了这个词。'}}

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        migrate(self.db)
        self.terms = {i: 'word' + chr(97 + (i-1)//26) + chr(97 + (i-1)%26) for i in range(1, 251)}
        self.db.executemany("INSERT INTO vocabulary_entries(id, term, normalized_term, translation_status) VALUES (?, ?, ?, 'pending')", [(i, term, term) for i, term in self.terms.items()])
        self.db.execute("UPDATE ai_profiles SET enabled=1, default_model='test'")
        self.db.commit()
    def tearDown(self): self.db.close()
    def test_250_words_require_bounded_explicit_segments_without_duplicate_translations(self):
        ids = service.queue_vocabulary_translations(self.db, [], include_all_pending=True)
        self.assertEqual(len(ids), 250)
        calls = []
        def completion(connection, messages, **kwargs):
            entries = json.loads(messages[1]['content'])['items']
            calls.extend(item['entryId'] for item in entries)
            self.assertLessEqual(len(calls), 250, 'Queue reclaimed an already handled word')
            service.queue_vocabulary_translations(connection, list(range(1,251)), include_all_pending=True)
            return json.dumps({'translations': [self.valid_translation(item['entryId'], item['term']) for item in entries]})
        with patch.object(service, 'connect', return_value=self.db), patch.object(service, 'chat_completion', side_effect=reply(operation=completion)):
            result = service.translate_queued_vocabulary()
            self.assertEqual(result, {'translated':32, 'remaining':218})
            service.translate_queued_vocabulary()
            self.assertEqual(len(calls),32, 'Automatic events cannot renew the exhausted budget')
            total=result['translated']
            while result['remaining']:
                requests.create_run(self.db,'translation',explicit=True,source='continue')
                result=requests.work()
                self.assertLessEqual(result['translated'],32)
                total+=result['translated']
            result={'translated':total,'remaining':0}
        self.assertEqual(result, {'translated':250, 'remaining':0})
        self.assertEqual(sorted(calls), list(range(1,251)))
    def test_partial_and_duplicate_response_does_not_retranslate_success(self):
        service.queue_vocabulary_translations(self.db, [1,2])
        response=json.dumps({'translations':[self.valid_translation(1,self.terms[1],'first'),self.valid_translation(1,self.terms[1],'duplicate'),self.valid_translation(999,'foreign')]})
        with patch.object(service,'connect',return_value=self.db), patch.object(service,'chat_completion',side_effect=reply(response)) as call:
            service.translate_queued_vocabulary()
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.db.execute('SELECT common_meaning FROM vocabulary_entries WHERE id=1').fetchone()[0],'first')
        self.assertEqual(self.db.execute('SELECT translation_status FROM vocabulary_entries WHERE id=2').fetchone()[0],'failed')
    def test_provider_failure_is_bounded_and_manual_content_protected(self):
        self.db.execute("UPDATE vocabulary_entries SET user_edited=1, contextual_meaning='manual' WHERE id=250")
        service.queue_vocabulary_translations(self.db, [], include_all_pending=True)
        with patch.object(service,'connect',return_value=self.db), patch.object(service,'chat_completion',side_effect=reply(error=httpx.ConnectError('offline'))) as call:
            service.translate_queued_vocabulary()
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM vocabulary_entries WHERE translation_status='failed'").fetchone()[0],8)
        self.assertEqual(requests.status(self.db)['reason'],'unknown')
        self.assertEqual(len(requests.status(self.db)['unknown']),1)
        self.assertEqual(self.db.execute('SELECT contextual_meaning FROM vocabulary_entries WHERE id=250').fetchone()[0],'manual')
    def test_missing_model_waits_without_spinning_or_marking_unattempted_words_failed(self):
        self.db.execute("UPDATE ai_profiles SET default_model=''");self.db.commit()
        service.queue_vocabulary_translations(self.db, [], include_all_pending=True)
        with patch.object(service,'connect',return_value=self.db), patch.object(service,'_has_translation_model',return_value=False), patch.object(service,'chat_completion') as call:
            service.translate_queued_vocabulary()
        call.assert_not_called()
        self.assertEqual(self.db.execute("SELECT count(*) FROM vocabulary_entries WHERE translation_status='queued'").fetchone()[0],250)

if __name__ == '__main__': unittest.main()
