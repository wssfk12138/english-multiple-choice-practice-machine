import sqlite3
import unittest
from backend.app import database
from backend.app.services.study_todos import study_todos

class ResumeQueryTests(unittest.TestCase):
    def test_production_query_preserves_identity_and_excludes_other_banks(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        try:
            db.executescript(database.SCHEMA);database._run_migrations(db)
            profile=database.get_active_profile_id(db)
            paper=db.execute("INSERT INTO papers(profile_id,year,title,status,external_key) VALUES(?,2016,'2016年考研英语一真题','published','p1')",(profile,)).lastrowid
            unit=db.execute("INSERT INTO units(paper_id,unit_type,title,sequence,external_key) VALUES(?,'reading','2016 年 · 阅读理解 Text 1',1,'u1')",(paper,)).lastrowid
            session=db.execute("INSERT INTO practice_sessions(mode,unit_ids) VALUES('paper',?)",('['+str(unit)+']',)).lastrowid
            result=study_todos(db)['resume_session']
            self.assertEqual(result['id'],session)
            self.assertEqual(result['title'],'2016 年 · 考研英语一真题')
            db.execute("UPDATE practice_sessions SET mode='random' WHERE id=?",(session,))
            self.assertEqual(study_todos(db)['resume_session']['title'],'2016 年 · 考研英语一真题 · 阅读理解 Text 1')
            db.execute("INSERT INTO practice_sessions(mode,unit_ids,started_at) VALUES('random','[999999]','2099-01-01')")
            self.assertEqual(study_todos(db)['resume_session']['id'],session)
            db.execute("UPDATE papers SET deleted_at='2026' WHERE id=?",(paper,))
            self.assertIsNone(study_todos(db)['resume_session'])
        finally: db.close()
