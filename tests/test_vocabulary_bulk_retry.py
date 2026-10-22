import sqlite3
import unittest
from fastapi import BackgroundTasks
from backend.app.database import SCHEMA
from backend.app.routers.vocabulary import retry_translation
from tests.vocabulary_execution_fixture import migrate

class BulkRetryTest(unittest.TestCase):
    def test_single_retry_and_bulk_retry_have_distinct_scopes(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SCHEMA);migrate(db)
        try:
            for entry_id,status,edited in [(1,'pending',0),(2,'failed',0),(3,'ready',1),(4,'ready',0),(5,'pending',1)]:
                db.execute('INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,user_edited) VALUES(?,?,?,?,?)',(entry_id,str(entry_id),str(entry_id),status,edited))
            db.execute("INSERT INTO vocabulary_enrichment_jobs(entry_id,state) VALUES(3,'failed')");db.commit()
            background=BackgroundTasks();retry_translation(background,1,db)
            self.assertEqual(dict(db.execute('SELECT id,translation_status FROM vocabulary_entries').fetchall()),{1:'queued',2:'failed',3:'ready',4:'ready',5:'pending'})
            self.assertEqual(db.execute('SELECT state FROM vocabulary_enrichment_jobs WHERE entry_id=3').fetchone()[0],'failed')
            retry_translation(background,0,db)
            self.assertEqual(dict(db.execute('SELECT id,translation_status FROM vocabulary_entries').fetchall()),{1:'queued',2:'queued',3:'ready',4:'ready',5:'pending'})
            self.assertEqual({task.func.__name__ for task in background.tasks},{'work'})
        finally: db.close()
