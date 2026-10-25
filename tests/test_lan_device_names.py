import sqlite3
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import database
from backend.app.database import get_db
from backend.app.routers import sync as router
from backend.app.services import sync


class DeviceNameTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        sync._ensure_sync_columns(self.db)
        sync.set_passcode(self.db, 'test-passcode')
        app = FastAPI()
        app.include_router(router.router, prefix='/api')
        app.include_router(router.public_router, prefix='/api')
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        sync.disable_sync(self.db)
        self.db.close()

    def pair(self, **extra):
        return self.client.post('/api/lan-sync/handshake', json={
            'device_id':'android-test-1', 'passcode':'test-passcode', 'profile_names':[], **extra})

    def test_authenticated_model_alias_persistence_and_old_client_reconnect(self):
        response = self.pair(device_model='OPPO Find X7')
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()['pairing_token']
        rename = self.client.put('/api/lan-sync/devices/android-test-1/name', json={'display_name':'日常手机'})
        self.assertEqual(rename.status_code, 200)
        sync._ensure_paired_devices_table(self.db)  # idempotent upgrade
        self.assertEqual(self.pair(passcode='', pairing_token=token).status_code, 200)
        item = self.client.get('/api/lan-sync/devices').json()['items'][0]
        self.assertEqual((item['device_id'],item['display_name'],item['device_model']),
                         ('android-test-1','日常手机','OPPO Find X7'))
        self.client.put('/api/lan-sync/devices/android-test-1/name',json={'display_name':''})
        self.assertEqual(self.client.get('/api/lan-sync/devices').json()['items'][0]['display_name'],'')

    def test_metadata_cannot_create_or_change_device_without_authentication(self):
        self.assertEqual(self.pair(passcode='wrong',device_model='Injected').status_code,401)
        self.assertEqual(self.client.get('/api/lan-sync/devices').json()['total'],0)
        self.assertEqual(self.client.put('/api/lan-sync/devices/missing/name',json={'display_name':'x'}).status_code,404)
        for name in ['x'*81,'x\ny','x\u202ey']:
            with self.subTest(name=repr(name)):
                self.assertEqual(self.pair(device_model=name).status_code,422)
                self.assertEqual(self.client.put('/api/lan-sync/devices/missing/name',json={'display_name':name}).status_code,422)

    def test_upgrade_existing_pairing_preserves_token_and_times(self):
        self.db.execute('DROP TABLE sync_paired_devices')
        self.db.execute("CREATE TABLE sync_paired_devices(device_id TEXT PRIMARY KEY, paired_at TEXT, last_seen_at TEXT, pairing_token_hash TEXT, transport TEXT)")
        self.db.execute("INSERT INTO sync_paired_devices VALUES ('old-id','old-paired','old-seen','hash-value','tls-v2')")
        self.db.commit()
        sync.paired_devices(self.db,10,0)
        item = dict(self.db.execute('SELECT * FROM sync_paired_devices').fetchone())
        self.assertEqual(item,{'device_id':'old-id','paired_at':'old-paired','last_seen_at':'old-seen',
                               'pairing_token_hash':'hash-value','transport':'tls-v2','display_name':'','device_model':''})

    def test_name_management_is_not_exposed_on_lan_host(self):
        from backend.app.lan_sync_host import lan_sync_app
        self.assertNotIn('/api/lan-sync/devices/{device_id}/name',{r.path for r in lan_sync_app.routes})
