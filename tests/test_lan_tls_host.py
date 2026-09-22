from contextlib import ExitStack, closing
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import tempfile
import unittest
from unittest.mock import patch

from backend.app import database, lan_tls_host
from backend.app.services import sync


class ProductionTlsHostTests(unittest.TestCase):
    def test_isolated_listener_pair_pull_push_restart_and_identity_failure(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            path = Path(temp) / 'synthetic.sqlite3'
            stack.enter_context(patch.object(database, 'DATABASE_PATH', path))
            stack.enter_context(patch.object(database, 'ensure_directories'))
            stack.enter_context(patch.object(lan_tls_host, 'LAN_TLS_HOST', '127.0.0.1'))
            stack.enter_context(patch.object(lan_tls_host, 'LAN_TLS_PORT', 0))
            stack.enter_context(patch.object(lan_tls_host, 'LanAdvertisement'))
            with closing(database.connect()) as db:
                db.executescript(database.SCHEMA)
                database._run_migrations(db)
                sync._ensure_sync_columns(db)
                sync.set_passcode(db, 'SYNTHETIC')
                invitation = sync.create_pairing_invitation(db)['pairing_code']
            try:
                self.assertTrue(lan_tls_host.start_tls_host())
                descriptor = lan_tls_host.tls_status()['tls_identity']
                self.assertTrue(lan_tls_host.start_tls_host(), 'start must be idempotent')
                context = ssl.create_default_context(cadata=descriptor['certificate_pem'])
                context.minimum_version = ssl.TLSVersion.TLSv1_2

                def request(route, payload=None):
                    port = lan_tls_host._server.servers[0].sockets[0].getsockname()[1]
                    body = json.dumps(payload or {}).encode()
                    with socket.create_connection(('127.0.0.1', port), timeout=5) as tcp:
                        with context.wrap_socket(tcp, server_hostname=descriptor['server_name']) as tls:
                            head = (f'POST {route} HTTP/1.1\r\nHost: {descriptor["server_name"]}\r\n'
                                    f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n')
                            tls.sendall(head.encode() + body)
                            response = http.client.HTTPResponse(tls)
                            response.begin()
                            return response.status, json.loads(response.read())

                for private in ('/api/lan-sync/pairing', '/api/lan-sync/devices', '/api/settings', '/openapi.json'):
                    self.assertEqual(request(private)[0], 404)
                status, paired = request('/api/lan-sync/handshake', {
                    'host_id': descriptor['host_id'], 'pairing_code': invitation,
                    'device_id': 'synthetic-android', 'profile_names': [],
                })
                self.assertEqual(status, 200, paired)
                token = paired['token']
                status, pushed = request('/api/lan-sync/push', {'token': token, 'changes': {
                    'vocabulary_entries': [{'term': 'synthetic', 'normalized_term': 'synthetic', 'updated_at': '2026-09-07T00:00:00Z'}],
                }, 'tombstones': []})
                self.assertEqual(status, 200, pushed)
                status, pulled = request('/api/lan-sync/pull', {'token': token, 'tables': ['vocabulary_entries']})
                self.assertEqual(status, 200, pulled)
                self.assertEqual(pulled['changes']['vocabulary_entries'][0]['term'], 'synthetic')
                self.assertTrue(lan_tls_host.stop_tls_host())
                self.assertFalse(lan_tls_host.tls_status()['tls_listening'])
                self.assertTrue(lan_tls_host.start_tls_host())
                self.assertEqual(lan_tls_host.tls_status()['tls_identity'], descriptor)
                status, _ = request('/api/lan-sync/handshake', {'host_id': descriptor['host_id'],
                    'device_id': 'synthetic-android', 'pairing_token': paired['pairing_token']})
                self.assertEqual(status, 200)
                self.assertTrue(lan_tls_host.stop_tls_host())
                with patch.object(lan_tls_host, 'load_or_create_identity', side_effect=ValueError('corrupt identity')):
                    self.assertFalse(lan_tls_host.start_tls_host())
                self.assertFalse(lan_tls_host.tls_status()['tls_listening'])
            finally:
                lan_tls_host.stop_tls_host()
                with closing(database.connect()) as db:
                    sync.disable_sync(db)


if __name__ == '__main__':
    unittest.main()
