import contextlib
import sqlite3
import unittest
from unittest.mock import patch
from tests.vocabulary_execution_fixture import migrate, reply
from backend.app.services import vocabulary_requests as requests
from backend.app.database import SCHEMA
from backend.app.services import vocabulary as service

class EnrichmentJobsTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA)
        migrate(self.db)
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,common_meaning,user_edited) VALUES(1,'went','went','ready','manual',1)")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_durable_retry_and_manual_fields(self):
        service.queue_vocabulary_enrichment(self.db,1)
        service.queue_vocabulary_enrichment(self.db,1)
        self.db.commit()
        with patch.object(service,'connect',return_value=contextlib.nullcontext(self.db)), patch.object(service,'enrich_vocabulary_entry',side_effect=reply(error=ValueError('offline'))) as call:
            service.process_vocabulary_enrichments()
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.db.execute('SELECT state FROM vocabulary_enrichment_jobs').fetchone()[0],'failed')
        service.queue_vocabulary_enrichment(self.db,1);self.db.commit()
        requests.create_run(self.db,'enrichment',[1],explicit=True,retry=True,source='retry')
        with patch.object(service,'connect',return_value=contextlib.nullcontext(self.db)), patch.object(service,'enrich_vocabulary_entry',side_effect=reply()) as call:
            service.process_vocabulary_enrichments()
            service.process_vocabulary_enrichments()
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.db.execute('SELECT state FROM vocabulary_enrichment_jobs').fetchone()[0],'ready')
        self.assertEqual(self.db.execute('SELECT common_meaning FROM vocabulary_entries').fetchone()[0],'manual')

    def test_new_context_during_model_call_stops_without_an_automatic_second_send(self):
        service.queue_vocabulary_enrichment(self.db,1);self.db.commit()
        calls=[]
        def model(connection, entry_id):
            calls.append(entry_id)
            if len(calls)==1:
                service.queue_vocabulary_enrichment(connection,entry_id,context_changed=True)
                connection.commit()
                raise ValueError('真题语境已更新，请重新补全')
        with patch.object(service,'connect',return_value=contextlib.nullcontext(self.db)), patch.object(service,'enrich_vocabulary_entry',side_effect=reply(operation=model)):
            service.process_vocabulary_enrichments()
        self.assertEqual(calls,[1])
        self.assertEqual(requests.status(self.db)['reason'],'unknown')
        self.assertEqual(self.db.execute('SELECT state FROM vocabulary_enrichment_jobs').fetchone()[0],'queued')

if __name__=='__main__': unittest.main()
