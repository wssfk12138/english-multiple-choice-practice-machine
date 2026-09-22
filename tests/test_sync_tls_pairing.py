from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.database import get_db
from backend.app.routers.sync import public_router
from backend.app.services import sync


class TlsPairingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)')
        sync.set_passcode(self.db, 'TEST-ONLY')

    def tearDown(self):
        sync.disable_sync(self.db)
        self.db.close()

    def invite(self):
        return sync.create_pairing_invitation(self.db)['pairing_code']

    def pair(self, code='', device='android-a', token=''):
        return sync.authenticate_device(self.db, 'TEST-ONLY', token, ['Bank A'], device,
                                        transport='tls-v2', pairing_code=code)

    def test_code_required_single_use_and_stored_token_reconnect(self):
        self.assertIsNone(self.pair())
        code = self.invite()
        session, token = self.pair(code)
        self.assertIsNone(self.pair(code, 'android-b'))
        self.assertIsNotNone(self.pair(token=token))
        self.assertEqual(sync.session_profiles(session, 'tls-v2'), ['Bank A'])
        self.assertIsNone(sync.session_profiles(session, 'legacy'))
        stored = self.db.execute('SELECT pairing_token_hash FROM sync_paired_devices').fetchone()[0]
        self.assertNotEqual(stored, token)

    def test_replacement_expiry_and_invalidations(self):
        old = self.invite()
        new = self.invite()
        self.assertIsNone(self.pair(old))
        with patch.object(sync.time, 'monotonic', return_value=10**15):
            self.assertIsNone(self.pair(new))
        for invalidate in (lambda: sync.revoke_device(self.db, 'android-a'),
                           lambda: sync.revoke_all_devices(self.db),
                           lambda: sync.reset_passcode(self.db),
                           lambda: sync.disable_sync(self.db)):
            code = self.invite()
            invalidate()
            self.assertIsNone(self.pair(code))
            sync.set_passcode(self.db, 'TEST-ONLY')

    def test_migration_preserves_device_and_rejects_http_credentials(self):
        old_session, old_secret = sync.authenticate_device(self.db, 'TEST-ONLY', '', [], 'android-a')
        self.db.execute("UPDATE sync_paired_devices SET paired_at='2020-01-01'")
        self.assertIsNone(self.pair(token=old_secret))
        _, secret = self.pair(self.invite())
        self.assertFalse(sync.verify_token(old_session))
        for token in (old_secret, secret, ''):
            self.assertIsNone(sync.authenticate_device(self.db, 'TEST-ONLY', token, [], 'android-a'))
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['total'], 1)
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['items'][0]['paired_at'], '2020-01-01')

    def test_concurrent_code_consumption_has_one_winner(self):
        code = self.invite()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda device: self.pair(code, device), ['android-a', 'android-b']))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_reinstall_replacement_is_explicit_atomic_and_revokes_old_session(self):
        session, token = self.pair(self.invite())
        code = sync.create_pairing_invitation(self.db, 'android-a')['pairing_code']
        self.assertIsNone(self.pair(code, 'android-a'))
        self.assertIsNotNone(self.pair(token=token))
        self.assertTrue(sync.verify_token(session))
        self.assertIsNone(self.pair('invalid', 'new-install'))
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['total'], 1)
        replacement = self.pair(code, 'new-install')
        self.assertIsNotNone(replacement)
        self.assertFalse(sync.verify_token(session))
        self.assertIsNone(self.pair(token=token))
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['items'][0]['device_id'], 'new-install')
        self.assertIsNone(self.pair(code, 'third-install'))
        with self.assertRaises(ValueError):
            sync.create_pairing_invitation(self.db, 'missing-device')

    def test_replacement_commit_failure_retains_old_binding_and_invitation(self):
        session, token = self.pair(self.invite())
        code = sync.create_pairing_invitation(self.db, 'android-a')['pairing_code']
        from unittest.mock import Mock
        failing = Mock(wraps=self.db)
        failing.commit.side_effect = sqlite3.OperationalError('synthetic commit failure')
        with self.assertRaises(sqlite3.OperationalError):
            sync.authenticate_device(failing, '', '', [], 'new-install', transport='tls-v2', pairing_code=code)
        self.assertTrue(sync.verify_token(session))
        self.assertIsNotNone(self.pair(token=token))
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['total'], 1)
        self.assertIsNotNone(self.pair(code, 'new-install'))

    def test_replacement_sql_failure_rolls_back_partial_insert(self):
        session, token = self.pair(self.invite())
        code = sync.create_pairing_invitation(self.db, 'android-a')['pairing_code']
        self.db.execute("CREATE TRIGGER fail_replacement BEFORE DELETE ON sync_paired_devices "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic delete failure'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.pair(code, 'new-install')
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(sync.paired_devices(self.db, 20, 0)['total'], 1)
        self.assertTrue(sync.verify_token(session))
        self.assertIsNotNone(self.pair(token=token))
        self.db.execute('DROP TRIGGER fail_replacement')
        self.assertIsNotNone(self.pair(code, 'new-install'))

    def test_routes_bind_scheme_identity_and_sessions(self):
        app = FastAPI()
        app.state.lan_tls_host_id = 'synthetic-host'
        app.include_router(public_router)
        app.dependency_overrides[get_db] = lambda: self.db
        payload = {'device_id': 'android-a', 'host_id': 'synthetic-host', 'pairing_code': self.invite()}
        with TestClient(app) as http:
            response = http.post('/lan-sync/handshake', json=payload, headers={'X-Forwarded-Proto': 'https'})
            self.assertEqual(response.status_code, 401)
        with TestClient(app, base_url='https://testserver') as https:
            self.assertEqual(https.post('/lan-sync/handshake', json={**payload, 'host_id': 'wrong'}).status_code, 401)
            response = https.post('/lan-sync/handshake', json=payload)
            self.assertEqual(response.status_code, 200)
            session = response.json()['token']
            self.assertEqual(response.json()['version'], 2)
            legacy = FastAPI()
            legacy.include_router(public_router)
            legacy.dependency_overrides[get_db] = lambda: self.db
            with TestClient(legacy) as client:
                self.assertEqual(client.post('/lan-sync/handshake', json=payload).status_code, 401)
                for operation in ('pull', 'push'):
                    self.assertEqual(client.post('/lan-sync/' + operation, json={'token': session}).status_code, 401)
            sync.revoke_device(self.db, 'android-a')
            self.assertEqual(https.post('/lan-sync/pull', json={'token': session}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
