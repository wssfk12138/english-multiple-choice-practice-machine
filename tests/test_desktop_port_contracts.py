import sqlite3
import unittest
from backend.app import database
from backend.app.routers.papers import list_papers
from backend.app.schemas import PracticeCreate
from backend.app.services import practice, vocabulary_requests


class DesktopPortContracts(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript(database.SCHEMA);database._run_migrations(self.db)
        profile=database.get_active_profile_id(self.db)
        self.p=self.db.execute("INSERT INTO papers(profile_id,external_key,year,title,status) VALUES(?,'port-paper',2026,'Synthetic exam','published')",(profile,)).lastrowid
        self.u=self.db.execute("INSERT INTO units(paper_id,external_key,unit_type,title,sequence,passage) VALUES(?,'port-unit','reading','Synthetic reading',1,'A synthetic passage.')",(self.p,)).lastrowid
        self.q=[]
        for i in range(1,5):
            q=self.db.execute("INSERT INTO questions(unit_id,external_key,number,answer,score,sequence,stem) VALUES(?,?,?,'A',2,?,'Synthetic?')",(self.u,str(i),i,i)).lastrowid;self.q.append(q)
            for j,label in enumerate('AB'):
                self.db.execute('INSERT INTO options(question_id,stable_key,original_label,content,sequence) VALUES(?,?,?,?,?)',(q,label,label,'Synthetic '+label,j))
        self.db.commit()

    def tearDown(self):self.db.close()

    def test_fresh_schema_paper_progress_resume_and_explicit_restart(self):
        self.assertEqual(list_papers(self.db)[0]['active_session_id'],None)
        request=PracticeCreate(mode='paper',paper_id=self.p,shuffle_options=False)
        old=practice.create_session(self.db,request)['id']
        practice.save_answer(self.db,old,self.q[0],'B',['A','B'])
        self.assertEqual(list_papers(self.db)[0]['active_session_id'],old)
        self.assertEqual(practice.create_session(self.db,request)['id'],old)
        new=practice.create_session(self.db,PracticeCreate(mode='paper',paper_id=self.p,force_new=True))['id']
        self.assertNotEqual(old,new)
        self.assertEqual(self.db.execute('SELECT status FROM practice_sessions WHERE id=?',(old,)).fetchone()[0],'abandoned')

    def test_real_legacy_label_migration_empty_orphan_and_explicit_unlock(self):
        self.db.execute("DELETE FROM app_migrations WHERE migration_key='question-label-auto-lock-v1'")
        for q,skill,model,edited in zip(self.q,['reading','','','  '],['','model','','  '],[0,0,1,0]):
            self.db.execute('INSERT INTO question_ai_labels(question_id,primary_skill,model_name,user_edited,locked) VALUES(?,?,?,?,0)',(q,skill,model,edited))
        self.db.commit();database._run_migrations(self.db);self.db.commit()
        self.assertEqual([r[0] for r in self.db.execute('SELECT locked FROM question_ai_labels ORDER BY question_id')],[1,1,1,0])
        self.db.execute('UPDATE question_ai_labels SET locked=0 WHERE question_id=?',(self.q[0],));self.db.commit()
        database._run_migrations(self.db);self.db.commit()
        self.assertEqual(self.db.execute('SELECT locked FROM question_ai_labels WHERE question_id=?',(self.q[0],)).fetchone()[0],0)

    def test_request_migration_carries_recent_legacy_budget_once(self):
        self.db.execute("DELETE FROM app_migrations WHERE migration_key='vocabulary-requests-v1'")
        from backend.app.services import vocabulary_tasks
        vocabulary_tasks.ensure_ledger(self.db)
        self.db.executemany("INSERT INTO vocabulary_ai_requests(task,at) VALUES('translation',CURRENT_TIMESTAMP)",[()]*4)
        vocabulary_requests.migrate(self.db);self.db.commit()
        gate=self.db.execute('SELECT used,reason FROM vocabulary_request_gate').fetchone()
        self.assertEqual(tuple(gate),(4,'budget'))
        self.db.execute("UPDATE vocabulary_request_gate SET used=1,reason='user'");self.db.commit()
        vocabulary_requests.migrate(self.db);self.db.commit()
        self.assertEqual(tuple(self.db.execute('SELECT used,reason FROM vocabulary_request_gate').fetchone()),(1,'user'))


if __name__=='__main__':unittest.main()
