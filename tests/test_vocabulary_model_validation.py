import json
import sqlite3
import unittest
from unittest.mock import patch
from backend.app.database import SCHEMA
from backend.app.services import vocabulary as service

class VocabularyModelValidationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.db.row_factory=sqlite3.Row; self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status) VALUES(1,'went','went','translating')")
        self.db.commit()
        self.valid={'entryId':1,'commonMeaning':'去','lemma':'go','morphology':{'lemma':'go','currentForm':'过去式','ambiguous':False,'note':'','forms':[]},'generatedExample':{'sentence':'She went home after school.','translation':'她放学后回家了。'}}
    def tearDown(self): self.db.close()
    def entry(self): return dict(self.db.execute('SELECT * FROM vocabulary_entries WHERE id=1').fetchone())
    def translate(self, response, callback=lambda:None):
        requested=service._translation_rows(self.db,[1])
        def reply(*args,**kwargs): callback(); return json.dumps(response)
        with patch.object(service,'chat_completion',side_effect=reply): service._translate_vocabulary_batch(self.db,requested)
    def test_partial_duplicate_unknown_id(self):
        self.translate({'translations':[{'entryId':1,'commonMeaning':'去'},self.valid,self.valid,{**self.valid,'entryId':2}]})
        self.assertEqual(self.entry()['translation_status'],'ready')
    def test_missing_or_wrong_example_rejected(self):
        for item in [{**self.valid,'morphology':{}},{**self.valid,'generatedExample':{'sentence':'She went home after school.'}},{**self.valid,'generatedExample':{'sentence':'She stayed home after school.','translation':'她待在家里。'}}]:
            self.db.execute("UPDATE vocabulary_entries SET translation_status='translating'"); self.db.commit()
            self.translate({'translations':[item]})
            self.assertEqual(self.entry()['translation_status'],'failed')
    def test_same_second_edit_preserved(self):
        self.translate({'translations':[self.valid]},lambda:self.db.execute("UPDATE vocabulary_entries SET common_meaning='same second'"))
        self.assertEqual(self.entry()['common_meaning'],'same second')
        self.assertEqual(self.entry()['morphology'],'{}')
    def test_manual_enrichment_preserves_meaning(self):
        self.db.execute("UPDATE vocabulary_entries SET common_meaning='manual',user_edited=1,translation_status='ready'"); self.db.commit()
        with patch.object(service,'chat_completion',return_value=json.dumps(self.valid)): service.enrich_vocabulary_entry(self.db,1)
        self.assertEqual(self.entry()['common_meaning'],'manual')
        self.assertEqual(json.loads(self.entry()['morphology'])['currentForm'],'过去式')
    def test_enrichment_rejects_edit_during_request(self):
        def reply(*args,**kwargs):
            self.db.execute("UPDATE vocabulary_entries SET common_meaning='manual',user_edited=1")
            return json.dumps(self.valid)
        with patch.object(service,'chat_completion',side_effect=reply), self.assertRaisesRegex(ValueError,'词条已修改'):
            service.enrich_vocabulary_entry(self.db,1)
        self.assertEqual(self.entry()['morphology'],'{}')

