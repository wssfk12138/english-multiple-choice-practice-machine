import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from backend.app import database
from backend.app.services import ai_client, vocabulary, vocabulary_requests as requests, vocabulary_tasks


class VocabularyTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'synthetic.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        self.db.execute("UPDATE ai_profiles SET enabled=1,base_url='https://synthetic.invalid',default_model='synthetic'")
        self.db.execute("INSERT INTO vocabulary_entries(term,normalized_term) VALUES('went','went')")
        self.db.commit()
        self.settings = {'id':1,'adapter':'openai-chat','enabled':True,'base_url':'https://synthetic.invalid','model':'synthetic','temperature':0.2,'api_key':''}
        self.settings_patch = patch.object(ai_client, '_settings_with_key', return_value=self.settings)
        self.settings_patch.start()
        self.connect_patch = patch.object(vocabulary, 'connect', return_value=self.db)
        self.connect_patch.start()
        self.client_type = httpx.Client
        self.calls = 0

    def tearDown(self):
        self.settings_patch.stop(); self.connect_patch.stop()
        self.db.close(); self.temporary.cleanup()

    def valid(self):
        return {'choices':[{'message':{'content':json.dumps({'translations':[{'entryId':1,'commonMeaning':'去','morphology':{'lemma':'go','currentForm':'past tense','ambiguous':False,'note':'','forms':[]},'generatedExample':{'sentence':'She went home.','translation':'她回家了。'}}]})},'finish_reason':'stop'}]}

    def execute(self, responder):
        def transport(request):
            self.calls += 1
            self.assertFalse(self.db.in_transaction, 'HTTP must not hold the writer transaction')
            observer = sqlite3.connect(self.path)
            try:
                self.assertEqual(observer.execute("SELECT COUNT(*) FROM vocabulary_request_attempts WHERE state='inflight'").fetchone()[0], 1, 'send intent must be committed before transport')
            finally:
                observer.close()
            return responder(request)
        with patch.object(ai_client.httpx, 'Client', side_effect=lambda **kwargs:self.client_type(transport=httpx.MockTransport(transport), **kwargs)):
            return requests.automatic('translation')

    def test_real_client_writes_content_and_receipt_atomically(self):
        result=self.execute(lambda req:httpx.Response(200,json=self.valid()))
        self.assertEqual(result['translated'],1)
        row=self.db.execute('SELECT translation_status,morphology,generated_example FROM vocabulary_entries').fetchone()
        self.assertEqual(row['translation_status'],'ready')
        self.assertEqual(json.loads(row['morphology'])['currentForm'],'过去式')
        self.assertEqual(json.loads(row['generated_example'])['sentence'],'She went home.')
        self.assertEqual(self.db.execute('SELECT state FROM vocabulary_request_attempts').fetchone()[0],'done')
        self.assertEqual(self.calls,1)

    def test_network_failure_becomes_unknown_and_is_not_replayed(self):
        def lost(req): raise httpx.ReadTimeout('synthetic lost response')
        self.execute(lost)
        self.assertEqual(requests.status(self.db)['reason'],'unknown')
        self.execute(lost)
        self.assertEqual(self.calls,1)
        self.assertEqual(len(requests.status(self.db)['unknown']),1)

    def test_http_error_and_redirect_never_retry(self):
        for code in (302,429,503):
            with self.subTest(code=code):
                if self.calls:
                    requests.create_run(self.db,'translation',[1],explicit=True,retry=True)
                self.execute(lambda req:httpx.Response(code,headers={'Location':'https://never-contact.invalid'},text='untrusted body'))
                self.assertEqual(requests.status(self.db)['reason'],'http')
        self.assertEqual(self.calls,3)
        self.assertNotIn('untrusted body',str(self.db.execute('SELECT result FROM vocabulary_request_attempts').fetchall()))

    def test_same_second_edit_during_network_preserves_manual_content(self):
        def response(req):
            other=sqlite3.connect(self.path)
            try:
                other.execute("UPDATE vocabulary_entries SET phonetic='manually edited',user_edited=1 WHERE id=1")
                other.commit()
            finally:other.close()
            return httpx.Response(200,json=self.valid())
        self.execute(response)
        row=self.db.execute('SELECT phonetic,common_meaning FROM vocabulary_entries').fetchone()
        self.assertEqual(tuple(row),('manually edited',''))
        self.assertEqual(requests.status(self.db)['reason'],'content_changed')

    def test_save_failure_rolls_back_word_and_stops(self):
        self.db.execute("CREATE TRIGGER reject_done BEFORE UPDATE ON vocabulary_request_attempts WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'synthetic save failure'); END")
        self.db.commit()
        self.execute(lambda req:httpx.Response(200,json=self.valid()))
        self.assertEqual(self.db.execute('SELECT common_meaning FROM vocabulary_entries').fetchone()[0],'')
        self.assertEqual(requests.status(self.db)['reason'],'save')
        self.assertEqual(self.calls,1)

    def test_oversized_stream_stops_before_parse(self):
        with patch.object(vocabulary_tasks,'RESPONSE_BYTES_LIMIT',64):
            self.execute(lambda req:httpx.Response(200,content=b'x'*128))
        self.assertEqual(requests.status(self.db)['reason'],'truncated')
        self.assertEqual(self.calls,1)

    def test_status_is_read_only_even_before_credential_file_exists(self):
        changes=self.db.total_changes
        requests.status(self.db)
        self.assertEqual(changes,self.db.total_changes)
        self.assertEqual(list(self.path.parent.glob('*.json')),[])


if __name__ == '__main__':unittest.main()
