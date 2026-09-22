import json
import sqlite3
import unittest
from backend.app import database
from backend.app.services import sync

class VocabularySyncEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        sync._ensure_sync_columns(self.db)
        self.db.execute("INSERT INTO vocabulary_entries(term,normalized_term,morphology,generated_example,contextual_occurrence_key,updated_at) VALUES('walked','surface:v1:walked',?,?,?,'2026-01-01')",
            (json.dumps({'lemma':'walk','currentForm':'past'}),json.dumps({'sentence':'They walked home yesterday.','translation':'Example'}),'occurrence-1'))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_legacy_missing_fields_preserves_new_fields(self):
        before=dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone())
        sync.merge(self.db,{'vocabulary_entries':[{'term':'walked','normalized_term':'surface:v1:walked','common_meaning':'legacy change','updated_at':'2098-01-01'}]},[])
        after=dict(self.db.execute('SELECT * FROM vocabulary_entries').fetchone())
        self.assertEqual(after['common_meaning'],'legacy change')
        for key in ['morphology','generated_example','contextual_occurrence_key']:
            self.assertEqual(after[key],before[key])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_enrichment_jobs').fetchone()[0],0)

    def test_source_kind_and_offset_preserved(self):
        payload={'entry_id_key':'surface:v1:walked','sync_id':'occurrence-1','surface_form':'walked','source_kind':'passage','selection_start':23,'context_sentence':'They walked home yesterday.','updated_at':'2026-01-01'}
        sync.merge(self.db,{'vocabulary_occurrences':[payload]},[])
        legacy={key:value for key,value in payload.items() if key not in ['source_kind','selection_start']}
        legacy['updated_at']='2098-01-01'
        sync.merge(self.db,{'vocabulary_occurrences':[legacy]},[])
        row=self.db.execute('SELECT * FROM vocabulary_occurrences').fetchone()
        self.assertEqual(row['source_kind'],'passage')
        self.assertEqual(row['selection_start'],23)
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_occurrences').fetchone()[0],1)

    def test_new_peer_fields_applied(self):
        payload={'term':'walked','normalized_term':'surface:v1:walked','morphology':json.dumps({'lemma':'walk','currentForm':'past participle'}),'generated_example':'{}','contextual_occurrence_key':'new','updated_at':'2098-01-01'}
        sync.merge(self.db,{'vocabulary_entries':[payload]},[])
        row=self.db.execute('SELECT * FROM vocabulary_entries').fetchone()
        for key in ['morphology','generated_example','contextual_occurrence_key']:
            self.assertEqual(row[key],payload[key])

    def test_surface_alias_reference_delete_and_no_resurrection(self):
        sync.merge(self.db,{'vocabulary_entries':[{'term':'Walked','normalized_term':'walked','updated_at':'2090-01-01'}]},[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_entries').fetchone()[0],1)
        sync.merge(self.db,{'vocabulary_occurrences':[{'entry_id_key':'walked','sync_id':'alias-occ','surface_form':'walked','updated_at':'2090-01-01'}]},[])
        self.assertEqual(self.db.execute('SELECT entry_id FROM vocabulary_occurrences').fetchone()[0],1)
        sync.merge(self.db,{},[{'table_name':'vocabulary_entries','object_key':'walked','profile_name':'','deleted_at':'2099-01-01'}])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_entries').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_occurrences').fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sync_tombstones WHERE table_name='vocabulary_entries'").fetchone()[0],2)
        for key in ['walked','surface:v1:walked']:
            sync.merge(self.db,{'vocabulary_entries':[{'term':'walked','normalized_term':key,'updated_at':'2090-01-01'}]},[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_entries').fetchone()[0],0)

    def test_new_key_matches_legacy_surface_without_changing_identity(self):
        self.db.execute("UPDATE vocabulary_entries SET term='Walked',normalized_term='walked'")
        self.db.commit()
        sync.merge(self.db,{'vocabulary_entries':[{'term':'walked','normalized_term':'surface:v1:walked','common_meaning':'updated','updated_at':'2090-01-01'}]},[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_entries').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT normalized_term FROM vocabulary_entries').fetchone()[0],'walked')
        self.assertEqual(sync._resolve_id(self.db,'entry','surface:v1:walked'),1)

    def test_stem_collision_does_not_merge_distinct_surfaces(self):
        self.db.execute("INSERT INTO vocabulary_entries(term,normalized_term,updated_at) VALUES('Claims','claim','2026-01-01')")
        self.db.commit()
        sync.merge(self.db,{'vocabulary_entries':[{'term':'claim','normalized_term':'surface:v1:claim','updated_at':'2090-01-01'}]},[])
        for term,key in [('claim','claim'),('claims','surface:v1:claim')]:
            with self.assertRaises(ValueError):
                sync.merge(self.db,{'vocabulary_entries':[{'term':term,'normalized_term':key,'updated_at':'2099-01-01'}]},[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM vocabulary_entries').fetchone()[0],3)
        self.assertEqual(self.db.execute("SELECT term FROM vocabulary_entries WHERE normalized_term='claim'").fetchone()[0],'Claims')
