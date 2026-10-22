import contextlib
import sqlite3
import unittest
from unittest.mock import patch
from tests.vocabulary_execution_fixture import migrate, reply
from backend.app.database import SCHEMA
from backend.app.services import vocabulary as service
from backend.app.services import vocabulary_maintenance as maintenance

class VocabularyMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA); migrate(self.db)
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,common_meaning,user_edited) VALUES(1,'word','word','ready','manual',1)")
        self.db.commit()
    def tearDown(self):
        self.db.close()
    def test_status_is_read_only_inventory_is_idempotent(self):
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'], 0)
        maintenance.update_enrichment_run(self.db, 'start')
        maintenance.update_enrichment_run(self.db, 'start')
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'], 1)
        self.db.execute("UPDATE vocabulary_enrichment_jobs SET state='ready'")
        self.db.commit()
        maintenance.update_enrichment_run(self.db, 'start')
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'], 0)
        self.assertEqual(self.db.execute('SELECT common_meaning FROM vocabulary_entries').fetchone()[0], 'manual')
    def test_pause_worker_and_retry(self):
        maintenance.update_enrichment_run(self.db, 'start')
        maintenance.update_enrichment_run(self.db, 'pause')
        with patch.object(service,'connect',return_value=contextlib.nullcontext(self.db)), patch.object(service,'enrich_vocabulary_entry') as call:
            service.process_vocabulary_enrichments()
            call.assert_not_called()
        self.db.execute("UPDATE vocabulary_enrichment_jobs SET state='failed'")
        self.db.commit()
        maintenance.update_enrichment_run(self.db,'retry')
        self.assertFalse(maintenance.enrichment_status(self.db)['paused'])
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'],1)
        maintenance.update_enrichment_run(self.db,'pause')
        maintenance.update_enrichment_run(self.db,'resume')
        self.assertFalse(maintenance.enrichment_status(self.db)['paused'])
    def test_missing_example_is_queued_but_complete_entry_is_not(self):
        self.db.execute('UPDATE vocabulary_entries SET morphology=? WHERE id=1', ('{"lemma":"word"}',))
        maintenance.update_enrichment_run(self.db, 'start')
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'],1)
        self.db.execute('DELETE FROM vocabulary_enrichment_jobs')
        self.db.execute('UPDATE vocabulary_entries SET generated_example=? WHERE id=1', ('{"sentence":"A word."}',))
        maintenance.update_enrichment_run(self.db, 'start')
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'],0)
    def test_unknown_action_changes_nothing(self):
        with self.assertRaises(ValueError): maintenance.update_enrichment_run(self.db,'delete')
        self.assertEqual(maintenance.enrichment_status(self.db)['queued'],0)

if __name__ == '__main__': unittest.main()
