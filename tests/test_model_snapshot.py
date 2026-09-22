import base64
import json
import unittest
from unittest.mock import patch
from tests import test_model_keys as fixtures
from backend.app.services import model_keys as keys, model_snapshot as snapshot, model_key_transaction as tx


class SnapshotTests(unittest.TestCase):
    setUp = fixtures.ModelKeyTests.setUp
    tearDown = fixtures.ModelKeyTests.tearDown
    profile = fixtures.ModelKeyTests.profile
    remote = fixtures.ModelKeyTests.remote

    def packet(self, **overrides):
        p = self.remote(enabled=False, is_default=True, models=[dict(model_id='second', display_name='Second',
            owned_by='owner', provider='provider', is_visible=False, is_available=True)])
        p['selected'] = p['keys'][0]['id']
        return p | overrides

    def fingerprint(self):
        return ([tuple(r) for r in self.db.execute('SELECT * FROM ai_profiles ORDER BY id')],
                [tuple(r) for r in self.db.execute('SELECT * FROM ai_profile_models ORDER BY profile_id,model_id')],
                keys.sidecar_snapshot(self.db)[1])

    def test_complete_snapshot_matching_creation_replay_and_private_preservation(self):
        private = self.profile('Private', legacy='synthetic-private')
        keys.summary(self.db, private)
        original = self.profile('Remote', url='https://old.test', adapter='anthropic', legacy='synthetic-old')
        self.db.execute('UPDATE ai_profiles SET is_default=1 WHERE id=?', (private['id'],))
        self.db.commit()
        batch = [self.packet(), self.packet(name='Same endpoint', is_default=False, selected='', keys=[])]
        for _ in range(2):
            self.assertEqual(snapshot.receive(self.db, batch), 2)
        rows = self.db.execute('SELECT * FROM ai_profiles ORDER BY id').fetchall()
        self.assertEqual(len(rows), 3)
        current = next(r for r in rows if r['name']=='Remote')
        self.assertEqual(current['id'], original['id'])
        self.assertEqual((current['adapter'], current['enabled'], current['is_default']), ('openai-responses',0,1))
        self.assertIsNone(current['api_key_encrypted'])
        self.assertEqual(keys.selected(self.db,current),'synthetic-only-key')
        self.assertEqual(keys.selected(self.db,private),'synthetic-private')
        model = self.db.execute('SELECT * FROM ai_profile_models WHERE profile_id=?',(current['id'],)).fetchone()
        self.assertEqual((model['model_id'],model['owned_by'],model['provider'],model['is_visible']),('second','owner','provider',0))
        self.assertFalse(tx.journal_path(self.db).exists())
        for path in __import__('pathlib').Path(self.tmp.name).iterdir():
            self.assertNotIn(b'synthetic-only-key',path.read_bytes())

    def test_entire_batch_invalid_or_ambiguous_is_unchanged(self):
        self.profile('Remote')
        self.profile('Remote')
        before = self.fingerprint()
        with self.assertRaises(ValueError): snapshot.receive(self.db,[self.packet()])
        self.assertEqual(self.fingerprint(),before)
        for batch in [[], [self.packet(),self.packet()], [self.packet(selected='0'*64)],
                      [self.packet(keys=self.packet()['keys']*2)], [self.packet(models=self.packet()['models']*2)]]:
            with self.assertRaises(ValueError): snapshot.receive(self.db,batch)
            self.assertEqual(self.fingerprint(),before)

    def test_sql_and_sidecar_failures_rollback_exactly(self):
        p = self.profile('Remote',legacy='synthetic-before')
        keys.summary(self.db,p)
        before = self.fingerprint()
        self.db.execute("CREATE TRIGGER fail_models BEFORE INSERT ON ai_profile_models BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.db.commit()
        with self.assertRaises(Exception): snapshot.receive(self.db,[self.packet()])
        self.assertEqual(self.fingerprint(),before)
        self.db.execute('DROP TRIGGER fail_models')
        self.db.commit()
        original = keys.save
        def write_then_fail(*args):
            original(*args)
            raise OSError('injected after replace')
        with patch.object(keys,'save',side_effect=write_then_fail):
            with self.assertRaises(OSError): snapshot.receive(self.db,[self.packet()])
        self.assertEqual(self.fingerprint(),before)
        self.assertFalse(tx.journal_path(self.db).exists())

    def make_pending(self, committed):
        p = self.profile('Remote',legacy='synthetic-before')
        keys.summary(self.db,p)
        old = keys.sidecar_snapshot(self.db)[1]
        snapshot.receive(self.db,[self.packet()])
        marker = self.db.execute('SELECT transaction_id FROM model_key_commit').fetchone()[0]
        journal = dict(version=1, transaction=marker if committed else 'uncommitted', previous=base64.b64encode(old).decode('ascii'))
        tx.journal_path(self.db).write_text(keys.protect_text(json.dumps(journal)),encoding='utf-8')
        return p, old

    def test_restart_before_commit_restores_previous_secret_file(self):
        _, old = self.make_pending(False)
        keys.load(self.db)
        self.assertEqual(keys.sidecar_snapshot(self.db)[1],old)
        self.assertFalse(tx.journal_path(self.db).exists())

    def test_restart_after_commit_keeps_new_secret_file(self):
        p, _ = self.make_pending(True)
        self.assertEqual(keys.selected(self.db,p),'synthetic-only-key')
        self.assertFalse(tx.journal_path(self.db).exists())

    def test_unrelated_transaction_is_not_committed(self):
        self.profile('Remote')
        self.db.execute("UPDATE ai_profiles SET name='Uncommitted'")
        with self.assertRaises(ValueError): snapshot.receive(self.db,[self.packet()])
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT name FROM ai_profiles').fetchone()[0],'Remote')

    def test_commit_failure_restores_both_stores(self):
        self.profile('Remote')
        before = self.fingerprint()
        class FailingCommit:
            def __getattr__(_, name): return getattr(self.db, name)
            def commit(_): raise OSError('injected commit failure')
        with self.assertRaises(OSError): snapshot.receive(FailingCommit(),[self.packet()])
        self.assertEqual(self.fingerprint(),before)

    def test_pending_recovery_fails_closed_inside_transaction(self):
        self.make_pending(False)
        self.db.execute("UPDATE ai_profiles SET name='Uncommitted'")
        with self.assertRaises(ValueError): keys.load(self.db)
        self.assertTrue(tx.journal_path(self.db).exists())
        self.db.rollback()
        keys.load(self.db)

    def test_route_requires_complete_versioned_packet_and_permission(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app.database import get_db
        from backend.app.routers.sync import public_router
        from backend.app.services import sync, sync_permissions as permissions
        sync.set_passcode(self.db,'TEST-ONLY')
        invitation = sync.create_pairing_invitation(self.db)
        token, _ = sync.authenticate_device(self.db,'','','','test-device',transport='tls-v2',pairing_code=invitation['pairing_code'])
        permissions.negotiate(self.db,token,['models'])
        app = FastAPI(); app.state.lan_tls_host_id='test-host'; app.include_router(public_router)
        app.dependency_overrides[get_db] = lambda: self.db
        body = dict(token=token,operation='authoritative_push',snapshot_version=1,profiles=[self.packet()])
        with TestClient(app,base_url='https://testserver') as client:
            response=client.post('/lan-sync/model-keys',json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json(),dict(version=1,snapshot_version=1,applied_profiles=1))
            self.assertEqual(response.headers['cache-control'],'no-store')
            for bad in [dict(body,snapshot_version=2), {k:v for k,v in body.items() if k!='snapshot_version'},dict(body,profiles=[self.remote()])]:
                response=client.post('/lan-sync/model-keys',json=bad)
                self.assertEqual(response.status_code,422)
                self.assertNotIn('synthetic-only-key',response.text)
            permissions.set_preferences(self.db,'test-device',[])
            self.assertEqual(client.post('/lan-sync/model-keys',json=body).status_code,403)
        sync.disable_sync(self.db)

    def test_required_snapshot_fields_and_no_defaults(self):
        for field in ['enabled','is_default','models','keys','selected','default_model']:
            p=self.packet(); del p[field]
            with self.assertRaises(ValueError): snapshot.receive(self.db,[p])
        self.profile('Private')
        snapshot.receive(self.db,[self.packet(is_default=False,keys=[],selected='',models=[])])
        self.assertEqual(self.db.execute('SELECT SUM(is_default) FROM ai_profiles').fetchone()[0],0)


if __name__ == '__main__': unittest.main()
