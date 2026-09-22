import hashlib
import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from fastapi import HTTPException
from backend.app.routers.sync import shared_bank_download, BankAccess
from backend.app import database
from backend.app.routers.sync import public_router, router
from backend.app.services import lan_question_banks as banks, sync, sync_permissions


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        sync._ensure_sync_columns(self.db)
        self.db.execute("INSERT INTO question_bank_profiles(id,name) VALUES (100,'Synthetic bank')")
        self.db.execute("INSERT INTO papers(id,profile_id,year,title,status,external_key) VALUES (100,100,2026,'Synthetic paper','published','paper-1')")
        self.db.execute("INSERT INTO units(id,paper_id,unit_type,title,sequence,external_key) VALUES (100,100,'reading','Synthetic',1,'unit-1')")
        self.db.commit()
        self.root_patch = patch.object(banks, 'root', return_value=self.storage / 'share')
        self.root_patch.start()
        sync.set_passcode(self.db, 'SYNTHETIC-ONLY')
        invitation = sync.create_pairing_invitation(self.db)
        self.app = FastAPI()
        self.app.state.lan_tls_host_id = 'test-host'
        self.app.include_router(router)
        self.app.include_router(public_router, prefix='/remote')
        self.app.dependency_overrides[database.get_db] = lambda: self.db
        self.client = TestClient(self.app, base_url='https://testserver')
        response = self.client.post('/remote/lan-sync/handshake', json={
            'host_id': 'test-host', 'pairing_code': invitation['pairing_code'],
            'device_id': 'test-device', 'profile_names': [], 'categories': ['question_bank']})
        self.assertEqual(response.status_code, 200, response.text)
        self.token = response.json()['token']
        sync_permissions.set_preferences(self.db, 'test-device', ['question_bank'])

    def tearDown(self):
        sync.disable_sync(self.db)
        self.client.close()
        self.db.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def catalog(self):
        return self.client.post('/remote/lan-sync/question-banks/catalog', json={'token': self.token})

    def test_stable_snapshot_and_update(self):
        first = banks.publish(self.db, True, [100])
        second = banks.publish(self.db, True, [100])
        self.assertEqual(first, second)
        item = first['packages'][0]
        data = (banks.root() / item['fileName']).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['packageId'], item['packageId'])
            self.assertEqual(manifest['papers'][0]['paperKey'], 'paper-1')
        self.db.execute("UPDATE papers SET title='Synthetic update' WHERE id=100")
        self.db.commit()
        changed = banks.publish(self.db, True, [100])['packages'][0]
        self.assertEqual(changed['packageId'], item['packageId'])
        self.assertNotEqual(changed['contentVersion'], item['contentVersion'])
        self.assertNotEqual(changed['sha256'], item['sha256'])
        stale = self.client.post('/remote/lan-sync/question-banks/download', json={'token': self.token, 'sha256': item['sha256']})
        self.assertEqual(stale.status_code, 409)

    def test_disabled_revoked_and_legacy_access(self):
        self.assertEqual(self.catalog().status_code, 403)
        banks.publish(self.db, True, [100])
        self.assertEqual(self.catalog().status_code, 200)
        sync_permissions.set_preferences(self.db, 'test-device', [])
        self.assertEqual(self.catalog().status_code, 403)
        sync_permissions.set_preferences(self.db, 'test-device', ['question_bank'])
        banks.publish(self.db, False, [])
        self.assertEqual(self.catalog().status_code, 403)
        banks.publish(self.db, True, [100])
        sync.revoke_device(self.db, 'test-device')
        self.assertEqual(self.catalog().status_code, 401)
        del self.app.state.lan_tls_host_id
        self.assertEqual(self.catalog().status_code, 403)

    def test_failed_publish_keeps_previous_and_scope_is_explicit(self):
        first = banks.publish(self.db, True, [100])
        with patch.object(banks.esq, 'export_package', side_effect=OSError('synthetic')):
            response = self.client.put('/lan-sync/question-banks/sharing', json={'enabled': True, 'profile_ids': [100]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(banks.state(), first)
        self.assertNotIn('synthetic', response.text)
        self.assertEqual(self.client.put('/lan-sync/question-banks/sharing', json={'enabled': True, 'profile_ids': [999]}).status_code, 422)
        self.assertEqual(self.client.post('/remote/lan-sync/question-banks/download', json={'token': self.token, 'sha256': '../private'}).status_code, 422)

    def test_streamed_content_and_retry(self):
        item = banks.publish(self.db, True, [100])['packages'][0]
        snapshot = self.storage / 'synthetic.db'
        other = sqlite3.connect(snapshot)
        self.db.backup(other)
        other.close()
        def connect():
            connection = sqlite3.connect(snapshot, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            return connection
        with patch.object(database, 'connect', side_effect=connect):
            for _ in range(2):
                response = self.client.post('/remote/lan-sync/question-banks/download', json={'token': self.token, 'sha256': item['sha256']})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(hashlib.sha256(response.content).hexdigest(), item['sha256'])
                self.assertEqual(response.headers['cache-control'], 'no-store')


    def test_disable_during_export_cancels_publication(self):
        first = banks.publish(self.db, True, [100])
        original = banks.esq.export_package
        def export(*args, **kwargs):
            banks.publish(self.db, False, [])
            return original(*args, **kwargs)
        with patch.object(banks.esq, 'export_package', side_effect=export):
            with self.assertRaises(HTTPException) as error:
                banks.publish(self.db, True, [100])
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(banks.state()['enabled'])
        self.assertEqual(banks.state()['packages'], first['packages'])

    def test_content_identity_survives_host_and_title_changes(self):
        raw, _ = banks.esq.export_package(self.db, profile_id=100)
        one, manifest = banks.canonical_package(raw, 'lan.bank.a', 'One')
        two, other = banks.canonical_package(raw, 'lan.bank.b', 'Two')
        self.assertNotEqual(one, two)
        self.assertEqual(manifest['contentVersion'], other['contentVersion'])

    def test_midstream_disable_rejects_next_chunk(self):
        item = banks.publish(self.db, True, [100])['packages'][0]
        request = Request({'type': 'http', 'scheme': 'https', 'server': ('testserver', 443),
                           'path': '/', 'headers': [], 'query_string': b'', 'app': self.app})
        snapshot = self.storage / 'stream.db'
        other = sqlite3.connect(snapshot)
        self.db.backup(other)
        other.close()
        def connect():
            connection = sqlite3.connect(snapshot, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            return connection
        async def consume():
            response = shared_bank_download(BankAccess(token=self.token, sha256=item['sha256']), request, self.db)
            first = await anext(response.body_iterator)
            self.assertTrue(first)
            banks.publish(self.db, False, [])
            with self.assertRaises(HTTPException) as error:
                await anext(response.body_iterator)
            self.assertEqual(error.exception.status_code, 403)
        with patch.object(database, 'connect', side_effect=connect):
            asyncio.run(consume())


if __name__ == '__main__':
    unittest.main()
