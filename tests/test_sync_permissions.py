import sqlite3
import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.app import database
from backend.app.database import get_db
from backend.app.routers.sync import public_router, router
from backend.app.services import sync, sync_permissions as permissions


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        sync._ensure_sync_columns(self.db)
        sync.set_passcode(self.db, 'TEST-ONLY')
        app = FastAPI()
        app.include_router(router)
        app.include_router(public_router, prefix='/remote')
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        sync.disable_sync(self.db)
        self.client.close()
        self.db.close()

    def handshake(self, categories=permissions.DEFAULT_CATEGORIES):
        body = {'passcode': 'TEST-ONLY', 'device_id': 'test-device', 'profile_names': []}
        if categories is not None:
            body['categories'] = categories
        return self.client.post('/remote/lan-sync/handshake', json=body)

    def push(self, token, category, changes=None, tombstones=None):
        return self.client.post('/remote/lan-sync/push', json={
            'token': token, 'category': category, 'changes': changes or {}, 'tombstones': tombstones or []})

    def test_intersection_and_mid_session_revocation(self):
        first = self.handshake(['vocabulary', 'wrong']).json()
        self.assertEqual(first['effective_categories'], ['vocabulary', 'wrong'])
        url = '/lan-sync/devices/test-device/permissions'
        self.assertEqual(self.client.put(url, json={'categories': ['vocabulary']}).status_code, 200)
        self.assertEqual(self.push(first['token'], 'wrong').status_code, 403)
        self.assertEqual(self.push(first['token'], 'vocabulary').status_code, 200)
        self.assertEqual(self.push(first['token'], 'models').status_code, 403)
        self.assertEqual(self.client.get(url).json()['effective_categories'], ['vocabulary'])
        self.client.delete('/lan-sync/devices/test-device')
        self.assertEqual(self.push(first['token'], 'vocabulary').status_code, 401)

    def test_unknown_forged_and_secret_payloads(self):
        self.assertEqual(self.handshake(['unknown']).status_code, 422)
        self.assertEqual(self.handshake(['wrong', 'wrong']).status_code, 422)
        token = self.handshake().json()['token']
        self.assertEqual(self.push(token, 'vocabulary', {'practice_sessions': []}).status_code, 403)
        self.assertEqual(self.push(token, 'vocabulary', tombstones=[{'table_name': 'practice_sessions'}]).status_code, 403)
        self.assertEqual(self.push(token, 'models', {'ai_profiles': [{'name': 'test', 'api_key': 'synthetic'}]}).status_code, 403)
        self.assertEqual(self.push(token, 'practice', {'practice_sessions': [{'_dependency': True}]}).status_code, 422)
        self.assertEqual(self.push(token, 'wrong', {'practice_sessions': [{'_dependency': True}]}).status_code, 403)
        response = self.client.post('/remote/lan-sync/pull', json={'token': token, 'category': 'wrong', 'tables': ['ai_profiles']})
        self.assertEqual(response.status_code, 403)

    def test_legacy_fails_closed_when_restricted_and_bank_is_one_way(self):
        token = self.handshake(None).json()['token']
        self.assertEqual(self.push(token, None).status_code, 200)
        permissions.set_preferences(self.db, 'test-device', ['wrong', 'question_bank'])
        self.assertEqual(self.push(token, None).status_code, 409)
        token = self.handshake(['question_bank']).json()['token']
        self.assertEqual(self.push(token, 'question_bank').status_code, 403)

    def test_category_tombstone_cursor_does_not_skip_other_category(self):
        self.db.execute("INSERT INTO sync_tombstones(table_name,object_key,profile_name,deleted_at) VALUES ('ai_profiles','old','','2020')")
        self.db.execute("INSERT INTO sync_tombstones(table_name,object_key,profile_name,deleted_at) VALUES ('vocabulary_entries','new','','2030')")
        vocabulary = permissions.snapshot(self.db, 'vocabulary', ['vocabulary_entries'], [], {}, {})
        models = permissions.snapshot(self.db, 'models', ['ai_profiles'], [], {}, {})
        self.assertEqual([r['object_key'] for r in vocabulary['tombstones']], ['new'])
        self.assertEqual(models['tombstones'], [], 'S4 never propagates model deletion')

    def test_minimal_dependency_and_delete_cascade_boundary(self):
        self.db.execute("INSERT INTO question_bank_profiles(id,name) VALUES (100,'Test Bank')")
        self.db.execute("INSERT INTO papers(id,profile_id,year,title,status,external_key) VALUES (100,100,2026,'Test','published','paper-1')")
        self.db.execute("INSERT INTO units(id,paper_id,unit_type,title,sequence,external_key) VALUES (100,100,'reading','Test',1,'unit-1')")
        self.db.execute("INSERT INTO practice_sessions(id,mode,unit_ids,sync_id,updated_at) VALUES (100,'unit','[100]','session-1','2020')")
        self.db.execute("INSERT INTO wrong_retry_rounds(unit_id,session_id,round_number,sync_id,updated_at) VALUES (100,100,1,'round-1','2020')")
        self.db.commit()
        batch = permissions.snapshot(self.db, 'wrong', list(permissions.CATEGORY_TABLES['wrong']), ['Test Bank'], {}, {})
        permissions.validate_batch('wrong', batch['changes'], batch['tombstones'])
        stub = batch['changes']['practice_sessions'][0]
        self.assertEqual(stub['mode'], 'wrong')
        self.assertNotIn('score', stub)
        self.assertEqual(stub['updated_at'], permissions.DEPENDENCY_TIMESTAMP)
        sync.merge(self.db, {'practice_sessions': [stub]}, [])
        self.assertEqual(self.db.execute('SELECT mode FROM practice_sessions WHERE id=100').fetchone()[0], 'unit')
        for deleted_at in ('2030', '2020', '', None):
            with self.subTest(deleted_at=deleted_at), self.assertRaises(ValueError):
                sync.merge(self.db, {}, [{'table_name': 'practice_sessions', 'object_key': 'session-1', 'deleted_at': deleted_at}], category='practice')
        self.assertEqual(self.db.execute('SELECT count(*) FROM wrong_retry_rounds').fetchone()[0], 1)
        update = {**stub, 'mode': 'unit', 'updated_at': '2040'}
        update.pop('_dependency')
        sync.merge(self.db, {'practice_sessions': [update]}, [{'table_name': 'practice_sessions', 'object_key': 'session-1', 'deleted_at': '2030'}], category='practice')
        self.assertEqual(self.db.execute('SELECT updated_at FROM practice_sessions WHERE id=100').fetchone()[0], '2040')
        sync.merge(self.db, {}, [{'table_name': 'practice_sessions', 'object_key': 'session-1', 'deleted_at': '2010'}], category='practice')
        self.assertEqual(self.db.execute('SELECT count(*) FROM wrong_retry_rounds').fetchone()[0], 1)
        stub['sync_id'] = 'stub-only'
        sync.merge(self.db, {'practice_sessions': [stub]}, [])
        sync._ensure_sync_columns(self.db)
        self.assertEqual(self.db.execute("SELECT updated_at FROM practice_sessions WHERE sync_id='stub-only'").fetchone()[0], permissions.DEPENDENCY_TIMESTAMP)
        practice = permissions.snapshot(self.db, 'practice', ['practice_sessions'], ['Test Bank'], {}, {})
        self.assertNotIn('stub-only', [r['sync_id'] for r in practice['changes']['practice_sessions']])
        real = {**stub, 'updated_at': '2021', 'mode': 'unit'}
        real.pop('_dependency')
        sync.merge(self.db, {'practice_sessions': [real]}, [])
        self.assertEqual(self.db.execute("SELECT mode FROM practice_sessions WHERE sync_id='stub-only'").fetchone()[0], 'unit')


if __name__ == '__main__':
    unittest.main()
