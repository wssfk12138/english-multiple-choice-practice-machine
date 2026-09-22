from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.database import get_db
from backend.app.lan_sync_host import lan_sync_app
from backend.app.routers.sync import public_router, router
from backend.app.services import sync


class SyncDeviceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)')
        sync.set_passcode(self.db, 'TEST-ONLY')
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.include_router(public_router, prefix="/remote")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def tearDown(self):
        sync.disable_sync(self.db)
        self.client.close()
        self.db.close()

    def pair(self, device):
        return sync.authenticate_device(self.db, 'TEST-ONLY', '', ['Bank A'], device)

    def test_repeat_pairing_preserves_identity_and_replaces_sessions(self):
        first, secret = self.pair('android-a')
        self.db.execute("UPDATE sync_paired_devices SET paired_at='2020-01-01'")
        again, no_secret = sync.authenticate_device(self.db, '', secret, ['Bank A'], 'android-a')
        self.assertIsNone(no_secret)
        self.assertTrue(sync.verify_token(first))
        replacement, new_secret = self.pair('android-a')
        self.assertFalse(sync.verify_token(first))
        self.assertFalse(sync.verify_token(again))
        self.assertTrue(sync.verify_token(replacement))
        self.assertNotEqual(secret, new_secret)
        listing = self.client.get('/lan-sync/devices').json()
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['paired_at'], '2020-01-01')

    def test_single_revoke_isolated_idempotent_and_private(self):
        first, secret = self.pair('android-a')
        other, _ = self.pair('android-b')
        listing = self.client.get('/lan-sync/devices?limit=1&offset=1').json()
        self.assertEqual(listing['total'], 2)
        self.assertEqual(len(listing['items']), 1)
        self.assertEqual(set(listing['items'][0]), {'device_id', 'paired_at', 'last_seen_at'})
        self.assertEqual(self.client.delete('/lan-sync/devices/android-a').json(), {'revoked': 1})
        self.assertFalse(sync.verify_token(first))
        self.assertIsNone(sync.session_profiles(first))
        for operation in ("pull", "push"):
            response = self.client.post("/remote/lan-sync/" + operation, json={"token": first})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(sync.session_profiles(other), ['Bank A'])
        self.assertIsNone(sync.authenticate_device(self.db, '', secret, [], 'android-a'))
        self.assertIsNone(sync.authenticate_device(self.db, '', secret, [], 'android-b'))
        self.assertEqual(self.client.delete('/lan-sync/devices/android-a').json(), {'revoked': 0})
        paths = {route.path for route in lan_sync_app.routes}
        self.assertNotIn('/api/lan-sync/devices', paths)
        self.assertNotIn('/api/lan-sync/devices/{device_id}', paths)

    def test_validation_expiry_and_global_revocation(self):
        for query in ('limit=0', 'limit=101', 'offset=-1'):
            self.assertEqual(self.client.get('/lan-sync/devices?' + query).status_code, 422)
        token, _ = self.pair('android-a')
        with patch.object(sync.time, 'time', return_value=10**15):
            self.assertIsNone(sync.session_profiles(token))
        self.assertNotIn(token, sync._token_devices)
        token, _ = self.pair('android-a')
        sync.reset_passcode(self.db)
        self.assertFalse(sync.verify_token(token))
        self.assertEqual(sync._token_devices, {})
        self.assertEqual(sync.revoke_all_devices(self.db), 1)

    def test_legacy_secret_never_leaves_or_overwrites_local_store(self):
        self.db.execute('CREATE TABLE ai_profiles(id INTEGER PRIMARY KEY, name TEXT, api_key_encrypted TEXT, updated_at TEXT, sync_id TEXT, is_default INTEGER)')
        self.db.execute("INSERT INTO ai_profiles VALUES (1, 'Model', 'encrypted-local-only', '2020', 'id1', 1)")
        row = self.db.execute('SELECT rowid AS _rowid, * FROM ai_profiles').fetchone()
        with patch('backend.app.security.unprotect_text', side_effect=AssertionError('must not decrypt')):
            data = sync._serialize_row(self.db, 'ai_profiles', row)
        self.assertNotIn('api_key_encrypted', data)
        sync._apply_row(self.db, 'ai_profiles', {
            'name': 'Model', 'api_key_encrypted': 'untrusted-secret', 'updated_at': '2030',
        })
        self.assertEqual(self.db.execute('SELECT api_key_encrypted FROM ai_profiles').fetchone()[0], 'encrypted-local-only')


if __name__ == '__main__':
    unittest.main()
