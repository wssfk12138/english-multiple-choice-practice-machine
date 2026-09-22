import json
import sqlite3
import unittest
from backend.app import database
from backend.app.schemas import PracticeCreate
from backend.app.services import practice, practice_snapshots, sync

class PracticeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript(database.SCHEMA);database._run_migrations(self.db);sync._ensure_sync_columns(self.db)
        profile=database.get_active_profile_id(self.db)
        self.p=self.db.execute("INSERT INTO papers(profile_id,external_key,year,title,status) VALUES(?,'p',2016,'Exam','published')",(profile,)).lastrowid
        self.u=self.db.execute("INSERT INTO units(paper_id,external_key,unit_type,title,sequence,passage) VALUES(?,'u','reading','Text',1,'Old passage')",(self.p,)).lastrowid
        self.q=self.db.execute("INSERT INTO questions(unit_id,external_key,number,answer,score,sequence,stem) VALUES(?,'q',1,'A',2,1,'Old stem')",(self.u,)).lastrowid
        for i,label in enumerate('AB'):
            self.db.execute('INSERT INTO options(question_id,stable_key,original_label,content,sequence) VALUES(?,?,?,?,?)',(self.q,label,label,'Old '+label,i))
        self.db.commit()
        self.s=practice.create_session(self.db,PracticeCreate(mode='paper',paper_id=self.p,shuffle_options=False))['id']

    def tearDown(self): self.db.close()

    def test_upgrade_snapshot_retains_content_order_and_scoring(self):
        practice_snapshots.capture_session(self.db,self.s,'test-revision')
        frozen=self.db.execute('SELECT content_snapshot FROM practice_sessions').fetchone()[0]
        self.db.execute("UPDATE units SET passage='New passage'")
        self.db.execute("UPDATE questions SET stem='New stem',answer='B',score=8")
        self.db.execute("UPDATE options SET content='New option'")
        practice_snapshots.capture_session(self.db,self.s,'second-revision')
        self.assertEqual(self.db.execute('SELECT content_snapshot FROM practice_sessions').fetchone()[0],frozen)
        response=practice.get_session(self.db,self.s)
        unit=response['units'][0];question=unit['questions'][0]
        self.assertEqual(unit['passage'],'Old passage');self.assertEqual(question['stem'],'Old stem')
        self.assertEqual(question['option_order'],['A','B']);self.assertEqual(question['options'][0]['content'],'Old A')
        self.assertNotIn('answer',question)
        self.assertTrue(response['content_choice_required'])
        with self.assertRaisesRegex(ValueError,'先选择'):
            practice.save_answer(self.db,self.s,self.q,'A',['A','B'])
        with self.assertRaisesRegex(ValueError,'先选择'): practice.submit_session(self.db,self.s)
        with self.assertRaisesRegex(ValueError,'先选择'): practice.submit_unit(self.db,self.s,self.u)
        self.db.commit()
        practice_snapshots.choose_snapshot(self.db,self.s,'continue')
        self.assertFalse(practice.get_session(self.db,self.s)['content_choice_required'])
        practice.save_answer(self.db,self.s,self.q,'A',['A','B'])
        result=practice.submit_session(self.db,self.s)
        self.assertEqual(result['score'],2);self.assertEqual(result['max_score'],2)
        self.assertEqual(result['units'][0]['submission']['score'],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wrong_stats').fetchone()[0],0)
        self.assertNotIn('answer',result['units'][0]['questions'][0])
        fresh=practice.create_session(self.db,PracticeCreate(mode='paper',paper_id=self.p,shuffle_options=False,force_new=True))
        self.assertEqual(fresh['units'][0]['passage'],'New passage')

    def test_capture_rollback_and_missing_mapping_fail_closed(self):
        self.db.commit();self.db.execute('BEGIN IMMEDIATE')
        practice_snapshots.capture_session(self.db,self.s,'rollback')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT content_snapshot FROM practice_sessions').fetchone()[0],'{}')
        practice_snapshots.capture_session(self.db,self.s,'test')
        self.db.execute("UPDATE questions SET external_key='different'")
        with self.assertRaisesRegex(ValueError,'映射'):
            practice.get_session(self.db,self.s)

    def test_cross_device_snapshot_uses_keys_not_ids(self):
        practice_snapshots.capture_session(self.db,self.s,'test')
        snapshot=json.loads(self.db.execute('SELECT content_snapshot FROM practice_sessions').fetchone()[0])
        self.assertNotIn('id',snapshot['units'][0]['payload'])
        self.assertNotIn('id',snapshot['units'][0]['questions'][0]['payload'])
        self.db.commit();self.db.execute('PRAGMA foreign_keys=OFF')
        self.db.execute('UPDATE questions SET id=999 WHERE id=?',(self.q,))
        unit=practice_snapshots.restore_unit(self.db,snapshot,self.u)
        self.assertEqual(unit['questions'][0]['id'],999)

    def test_sync_rejects_missing_or_changed_snapshot_without_losing_answers(self):
        practice_snapshots.capture_session(self.db,self.s,'test')
        self.db.execute("UPDATE practice_sessions SET sync_id='snapshot-session' WHERE id=?",(self.s,))
        self.db.commit()
        saved=dict(self.db.execute('SELECT * FROM practice_sessions WHERE id=?',(self.s,)).fetchone())
        for snapshot in [None, '{}', json.dumps({'version':1,'revision':'different','units':[]})]:
            payload={'sync_id':'snapshot-session','mode':'paper','unit_ids_keys':['u'],'updated_at':'2099-01-01'}
            if snapshot is not None: payload['content_snapshot']=snapshot
            with self.assertRaisesRegex(ValueError,'快照不一致'):
                sync.merge(self.db,{'practice_sessions':[payload]},[])
            self.assertEqual(self.db.execute('SELECT content_snapshot FROM practice_sessions WHERE id=?',(self.s,)).fetchone()[0],saved['content_snapshot'])
        payload['content_snapshot']=saved['content_snapshot']
        sync.merge(self.db,{'practice_sessions':[payload]},[])
        self.assertEqual(self.db.execute('SELECT content_snapshot FROM practice_sessions WHERE id=?',(self.s,)).fetchone()[0],saved['content_snapshot'])

    def test_sync_accepts_metadata_only_snapshot_drift(self):
        practice_snapshots.capture_session(self.db, self.s, 'local-r1')
        self.db.execute("UPDATE practice_sessions SET sync_id='snapshot-session' WHERE id=?", (self.s,))
        self.db.commit()
        remote = json.loads(self.db.execute('SELECT content_snapshot FROM practice_sessions').fetchone()[0])
        remote['revision'] = 'remote-r3'
        remote['units'][0]['payload'].setdefault('shared_data', {}).update(content_package_id='package', content_version='r3')
        remote['units'][0]['questions'][0]['payload']['score'] = 2.0
        sync.merge(self.db, {'practice_sessions': [{
            'sync_id': 'snapshot-session', 'mode': 'paper', 'unit_ids_keys': ['u'],
            'updated_at': '2099-01-01', 'content_snapshot': json.dumps(remote),
        }]}, [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM practice_sessions').fetchone()[0], 1)
        self.assertEqual(practice.get_session(self.db, self.s)['units'][0]['passage'], 'Old passage')

    def test_snapshot_equivalence_keeps_material_and_json_types_strict(self):
        base = {'version': 1, 'revision': 'r1', 'units': [{'payload': {
            'passage': 'Original', 'shared_data': {'candidates': ['A', 'B']},
            'score': 2, 'enabled': True, 'large': 9007199254740993,
        }}]}
        other = json.loads(json.dumps(base))
        other['revision'] = 'r3'
        other['units'][0]['payload']['score'] = 2.0
        self.assertTrue(practice_snapshots.snapshots_equivalent(base, other))
        for key, value in [('passage', 'Changed'), ('score', 3), ('enabled', 1),
                           ('large', 9007199254740992), ('shared_data', {'candidates': ['B', 'A']})]:
            changed = json.loads(json.dumps(base))
            changed['units'][0]['payload'][key] = value
            with self.subTest(key=key):
                self.assertFalse(practice_snapshots.snapshots_equivalent(base, changed))

    def test_restart_preserves_old_answers_and_cannot_repeat_or_submit_abandoned(self):
        practice.save_answer(self.db,self.s,self.q,'A',['A','B'])
        practice_snapshots.capture_session(self.db,self.s,'upgrade')
        self.db.execute("UPDATE units SET passage='New passage'");self.db.commit()
        result=practice_snapshots.choose_snapshot(self.db,self.s,'restart')
        self.assertNotEqual(result['id'],self.s)
        old=practice.get_session(self.db,self.s);new=practice.get_session(self.db,result['id'])
        self.assertEqual(old['units'][0]['questions'][0]['user_answer'],'A')
        self.assertEqual(old['units'][0]['passage'],'Old passage')
        self.assertEqual(new['units'][0]['passage'],'New passage')
        self.assertEqual(new['units'][0]['questions'][0]['user_answer'],'')
        with self.assertRaisesRegex(ValueError,'已结束'): practice_snapshots.choose_snapshot(self.db,self.s,'restart')
        with self.assertRaisesRegex(ValueError,'已结束'): practice.submit_session(self.db,self.s)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM practice_sessions').fetchone()[0],2)

    def test_corrected_content_rejects_new_legacy_sessions(self):
        self.db.execute('UPDATE units SET shared_data=? WHERE id=?',(json.dumps({'content_revision':'r1'}),self.u))
        self.db.commit()
        payload={'sync_id':'legacy-new','mode':'unit','unit_ids_keys':['u'],'updated_at':'2099-01-01'}
        count=self.db.execute('SELECT COUNT(*) FROM practice_sessions').fetchone()[0]
        with self.assertRaisesRegex(ValueError,'缺少修正前内容快照'):
            sync.merge(self.db,{'practice_sessions':[payload]},[])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM practice_sessions').fetchone()[0],count)
        payload['content_revisions']=json.dumps({'u':'r1'})
        sync.merge(self.db,{'practice_sessions':[payload]},[])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM practice_sessions').fetchone()[0],count+1)
        self.assertEqual(json.loads(practice_snapshots.current_content_revisions(self.db,[self.u])),{'u':'r1'})
