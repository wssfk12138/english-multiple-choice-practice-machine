import json
import sqlite3
import unittest
from backend.app.database import SCHEMA, _run_migrations, get_active_profile_id
from backend.app.services.trash import trash_wrong_units, restore_trash, purge_trash, trash_paper
from backend.app.services.wrong_analysis_history import history
from backend.app.routers.ai import wrong_analysis_status

class WrongHistoryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)
        _run_migrations(self.db)
        active=get_active_profile_id(self.db)
        self.db.execute("INSERT INTO papers(id,profile_id,year,title,status) VALUES(1,?,2025,'Synthetic paper','published')",(active,))
        for uid in (1,2):
            self.db.execute("INSERT INTO units(id,paper_id,unit_type,title,sequence) VALUES(?,1,'reading',?,?)",(uid,'Unit '+str(uid),uid))
            self.db.execute("INSERT INTO questions(id,unit_id,number,answer,score,sequence) VALUES(?,?,?,'A',2,?)",(uid,uid,uid,uid))
            self.db.execute('INSERT INTO wrong_stats(question_id,wrong_count) VALUES(?,1)',(uid,))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='wrong_current_questions'").fetchone():
                self.db.execute('INSERT INTO wrong_current_questions(unit_id,question_id) VALUES(?,?)',(uid,uid))
        for rid,ids in ((1,[1]),(2,[1]),(3,[1,2])):
            self.db.execute("INSERT INTO wrong_analysis_reports(id,scope_key,unit_ids,scope_title,question_count,aggregate_data,report,model_name) VALUES(?,?,?,?,1,'{}',?,'fixture')",(rid,','.join(map(str,ids)),json.dumps(ids),'Test', 'Full report '+str(rid)))
        self.db.execute('INSERT INTO wrong_analysis_states(unit_id,report_id,analyzed_session_id) VALUES(1,2,0)')
        self.db.commit()
    def tearDown(self): self.db.close()
    def archive(self):
        result=trash_wrong_units(self.db,[1]);self.db.commit()
        return self.db.execute('SELECT id FROM trash_entries WHERE deletion_batch_id=?',(result['batch_id'],)).fetchone()[0]
    def test_pagination_profile_isolation_and_status(self):
        page=history(self.db,1,1,1)
        self.assertEqual(page['total'],3);self.assertEqual(page['items'][0]['id'],2)
        self.assertEqual(wrong_analysis_status(connection=self.db)['units'][0]['unit_id'],1)
        other=self.db.execute("INSERT INTO question_bank_profiles(name) VALUES('Other')").lastrowid
        self.db.execute("UPDATE app_settings SET value=? WHERE key='active_question_bank_profile_id'",(str(other),));self.db.commit()
        with self.assertRaises(LookupError): history(self.db,1)
        self.assertEqual(wrong_analysis_status(connection=self.db)['units'],[])
    def test_archive_restore_and_permanent_cleanup(self):
        tid=self.archive()
        self.assertEqual(history(self.db,1)['total'],0)
        self.assertEqual(history(self.db,2)['total'],1)
        restore_trash(self.db,tid);self.db.commit()
        self.assertEqual(history(self.db,1)['total'],3)
        tid=self.archive();purge_trash(self.db,tid);self.db.commit()
        self.assertEqual(history(self.db,1)['total'],0)
        self.assertEqual(history(self.db,2)['total'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wrong_analysis_reports').fetchone()[0],1)
    def test_shared_report_does_not_cross_bank(self):
        other=self.db.execute("INSERT INTO question_bank_profiles(name) VALUES('Other')").lastrowid
        self.db.execute("INSERT INTO papers(id,profile_id,year,title,status) VALUES(2,?,2025,'Other','published')",(other,))
        self.db.execute("UPDATE units SET paper_id=2 WHERE id=2")
        self.db.execute("UPDATE wrong_analysis_states SET report_id=3 WHERE unit_id=1")
        self.assertEqual(history(self.db,1)['total'],2)
        self.assertEqual(wrong_analysis_status(connection=self.db)['units'],[])
    def test_restore_preserves_new_analysis(self):
        tid=self.archive()
        self.db.execute("INSERT INTO wrong_analysis_reports(id,scope_key,unit_ids,scope_title,question_count,aggregate_data,report,model_name) VALUES(4,'1','[1]','New',1,'{}','New report','fixture')")
        self.db.execute("INSERT INTO wrong_analysis_states(unit_id,report_id,analyzed_session_id) VALUES(1,4,0)")
        restore_trash(self.db,tid)
        self.assertEqual(self.db.execute("SELECT report_id FROM wrong_analysis_states WHERE unit_id=1").fetchone()[0],4)
        self.assertEqual(history(self.db,1)['total'],4)
    def test_paper_trash_restore_purge(self):
        result=trash_paper(self.db,1);self.db.commit()
        tid=self.db.execute('SELECT id FROM trash_entries WHERE deletion_batch_id=?',(result['batch_id'],)).fetchone()[0]
        with self.assertRaises(LookupError):history(self.db,1)
        restore_trash(self.db,tid);self.db.commit()
        self.assertEqual(history(self.db,1)['total'],3)
        result=trash_paper(self.db,1);self.db.commit()
        tid=self.db.execute('SELECT id FROM trash_entries WHERE deletion_batch_id=?',(result['batch_id'],)).fetchone()[0]
        purge_trash(self.db,tid);self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wrong_analysis_reports').fetchone()[0],0)

if __name__=='__main__':unittest.main()
