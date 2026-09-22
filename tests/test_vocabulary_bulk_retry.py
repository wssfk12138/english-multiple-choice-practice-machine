import sqlite3
import unittest
from fastapi import BackgroundTasks
from backend.app.database import SCHEMA
from backend.app.routers.vocabulary import retry_translation


class BulkRetryTest(unittest.TestCase):
    def test_all_pending_jobs_and_failed_enrichments_are_queued(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SCHEMA)
        for entry_id,status,edited in [(1,'pending',0),(2,'failed',0),(3,'ready',1),(4,'ready',0),(5,'pending',1)]:
            db.execute('INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,user_edited) VALUES(?,?,?,?,?)',(entry_id,str(entry_id),str(entry_id),status,edited))
        db.execute("INSERT INTO vocabulary_enrichment_jobs(entry_id,state) VALUES(3,'failed')")
        db.commit()
        background=BackgroundTasks()
        retry_translation(background,1,db)
        states=dict(db.execute('SELECT id,translation_status FROM vocabulary_entries').fetchall())
        self.assertEqual(states,{1:'queued',2:'queued',3:'ready',4:'ready',5:'pending'})
        self.assertEqual(db.execute('SELECT state FROM vocabulary_enrichment_jobs WHERE entry_id=3').fetchone()[0],'queued')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM vocabulary_enrichment_jobs').fetchone()[0],1)
        self.assertEqual({task.func.__name__ for task in background.tasks},{'translate_queued_vocabulary','process_vocabulary_enrichments'})
        db.close()


if __name__=='__main__':unittest.main()
