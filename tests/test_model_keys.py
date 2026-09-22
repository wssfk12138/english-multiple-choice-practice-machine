import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import database
from backend.app.database import get_db
from backend.app.routers.sync import public_router
from backend.app.routers.ai import router as ai_router
from backend.app.services import model_keys as keys, model_key_sync as exchange, sync, sync_permissions as permissions


class ModelKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(str(Path(self.tmp.name) / 'test.db'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        sync._ensure_sync_columns(self.db)
        self.db.execute('DELETE FROM ai_profiles')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def profile(self, name='Receiver', url='https://EXAMPLE.test/v1', adapter='openai-chat', legacy=''):
        cur = self.db.execute('INSERT INTO ai_profiles(name,base_url,adapter,api_key_encrypted) VALUES (?,?,?,?)',
                              (name,url,adapter,keys.protect_text(legacy)))
        self.db.commit()
        return self.db.execute('SELECT * FROM ai_profiles WHERE id=?', (cur.lastrowid,)).fetchone()

    def remote(self, value='synthetic-only-key', **overrides):
        return exchange.Profile(name='Remote', adapter='openai-responses', base_url='https://example.test/v1',
                                keys=[exchange.Credential(id=keys.key_id(value),name='Remote key',value=value)]).model_dump() | overrides

    def test_identity_and_auth(self):
        self.assertEqual(keys.base_identity('https://[ABCD::1]:443/V1/'), 'https://[abcd::1]:443/V1/')
        for invalid in ['https://example.test:bad', 'https://example.test:99999']:
            with self.assertRaises(ValueError): keys.base_identity(invalid)
        self.assertEqual(keys.base_identity(' https://EXAMPLE.test/v1 '), 'https://example.test/v1')
        for url in ['http://example.test/v1','https://example.test/V1','https://example.test','https://example.test/v1/']:
            self.assertNotEqual(keys.base_identity(url), 'https://example.test/v1')
        for url in ['https://example.test/?token=x','https://u:p@example.test','https://example.test\\evil','https://exa\nmple.test']:
            with self.assertRaises(ValueError): keys.base_identity(url)

    def test_migration_durable_before_cleanup_and_no_plaintext(self):
        profile = self.profile(legacy='synthetic-migration-key')
        with patch.object(keys.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError): keys.summary(self.db, profile)
        self.assertTrue(self.db.execute('SELECT api_key_encrypted FROM ai_profiles').fetchone()[0])
        self.assertEqual(keys.selected(self.db, profile),'synthetic-migration-key')
        self.assertIsNone(self.db.execute('SELECT api_key_encrypted FROM ai_profiles').fetchone()[0])
        self.assertNotIn('synthetic-migration-key', keys._path(self.db).read_text())
        self.assertNotIn('synthetic-migration-key', json.dumps(keys.summary(self.db,profile)))
        self.assertEqual(len(keys.summary(self.db,profile)['keys']),1)

    def test_add_only_manual_selection_and_suppression(self):
        profile = self.profile(legacy='synthetic-current')
        remote = self.remote()
        for _ in range(2): self.assertEqual(exchange.receive(self.db,[remote]),[])
        self.assertEqual(keys.selected(self.db,profile),'synthetic-current')
        self.assertEqual(len(keys.summary(self.db,profile)['keys']),2)
        self.assertEqual(self.db.execute('SELECT name,adapter FROM ai_profiles').fetchone()[:],('Receiver','openai-chat'))
        identity = remote['keys'][0]['id']
        keys.edit(self.db,profile,'delete',identity=identity)
        exchange.receive(self.db,[remote])
        self.assertEqual(len(keys.summary(self.db,profile)['keys']),1)
        self.assertIn(identity,exchange.inventory(self.db)['https://example.test/v1'])
        keys.edit(self.db,profile,'select',identity='')
        exchange.receive(self.db,[self.remote('synthetic-third')])
        self.assertEqual(keys.selected(self.db,profile),'')

    def test_conflict_and_new_name_and_replay(self):
        self.profile(adapter='anthropic')
        self.assertTrue(exchange.receive(self.db,[self.remote()]))
        self.assertEqual(len(keys.load(self.db)['profiles']),0)
        self.profile(name='Remote',url='https://other.test')
        incoming = self.remote(base_url='https://new.test', default_model='synthetic-model')
        with patch.object(keys,'save',side_effect=OSError('injected')):
            with self.assertRaises(OSError): exchange.receive(self.db,[incoming])
        exchange.receive(self.db,[incoming])
        exchange.receive(self.db,[incoming])
        created = self.db.execute("SELECT * FROM ai_profiles WHERE base_url='https://new.test'").fetchall()
        self.assertEqual(len(created),1)
        self.assertEqual(self.db.execute('SELECT model_id FROM ai_profile_models WHERE profile_id=?', (created[0]['id'],)).fetchone()[0], 'synthetic-model')
        self.assertEqual(created[0]['name'],'Remote (2)')
        self.assertEqual(keys.selected(self.db,created[0]),'')
        self.assertEqual(len(keys.summary(self.db,created[0])['keys']),1)

    def test_entire_batch_validation_before_mutation(self):
        profile = self.profile()
        invalid = self.remote('other-synthetic')
        invalid['keys'][0]['id'] = '0'*64
        with self.assertRaises(ValueError): exchange.receive(self.db,[self.remote(),invalid])
        self.assertEqual(keys.summary(self.db,profile)['keys'],[])

    def test_profile_deletion_and_legacy_tombstones(self):
        profile = self.profile(legacy='synthetic-deleted')
        keys.suppress_profile(self.db, profile)
        self.db.execute('DELETE FROM ai_profiles WHERE id=?', (profile['id'],))
        self.db.commit()
        with self.assertRaises(ValueError): keys.summary(self.db, profile)
        exchange.receive(self.db, [self.remote()])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_profiles').fetchone()[0], 0)
        self.db.execute("INSERT INTO sync_tombstones(table_name,object_key,profile_name,deleted_at) VALUES ('ai_profiles','old','','2020')")
        snapshot = sync.snapshot(self.db, tables=['ai_profiles'], profile_names=[])
        self.assertEqual(snapshot['tombstones'], [])

    def test_tls_permission_revocation_and_secret_errors(self):
        self.profile()
        sync.set_passcode(self.db,'TEST-ONLY')
        invitation = sync.create_pairing_invitation(self.db)
        token, _ = sync.authenticate_device(self.db,'','','','test-device',transport='tls-v2',pairing_code=invitation['pairing_code'])
        permissions.negotiate(self.db,token,['models'])
        app = FastAPI()
        app.state.lan_tls_host_id = 'test-host'
        app.include_router(public_router)
        app.include_router(ai_router)
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app,base_url='https://testserver') as client:
            body = {'token':token,'operation':'pull','known':{}}
            self.assertEqual(client.post('/lan-sync/model-keys',json=body).status_code,200)
            invalid = client.post('/lan-sync/model-keys',json={**body,'profiles':[{'keys':'synthetic-error-secret'}]})
            self.assertEqual(invalid.status_code,422)
            self.assertNotIn('synthetic-error-secret',invalid.text)
            invalid = client.post('/ai/profiles/1/keys',json={'action':'add','value':{'secret':'synthetic-error-secret'}})
            self.assertNotIn('synthetic-error-secret',invalid.text)
            permissions.set_preferences(self.db,'test-device',[])
            self.assertEqual(client.post('/lan-sync/model-keys',json=body).status_code,403)
            sync.revoke_device(self.db,'test-device')
            self.assertEqual(client.post('/lan-sync/model-keys',json=body).status_code,401)
        del app.state.lan_tls_host_id
        with TestClient(app) as client:
            self.assertEqual(client.post('/lan-sync/model-keys',json=body).status_code,403)
        sync.disable_sync(self.db)


if __name__ == '__main__':
    unittest.main()
