from __future__ import annotations

import json
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.app.lan_tls import load_or_create_identity, tls_server_config


class LanTlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_persistent_identity_and_protected_key(self):
        first = load_or_create_identity(self.directory)
        again = load_or_create_identity(self.directory)
        self.assertEqual(first.public_descriptor(), again.public_descriptor())
        self.assertNotIn(first._password, repr(first))
        files = b"".join(p.read_bytes() for p in first.directory.iterdir())
        self.assertNotIn(first._password.encode(), files)
        self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", files)
        self.assertNotIn(b"BEGIN PRIVATE KEY", files)
        self.assertNotIn("protected_password", first.public_descriptor())
        self.assertEqual(first.server_context().minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_damaged_identity_is_not_replaced(self):
        identity = load_or_create_identity(self.directory)
        metadata = identity.directory / "identity.json"
        metadata.write_text("invalid", encoding="utf-8")
        original_key = (identity.directory / "private-key.pem").read_bytes()
        with self.assertRaises(ValueError):
            load_or_create_identity(self.directory)
        self.assertEqual(metadata.read_text(), "invalid")
        self.assertEqual(original_key, (identity.directory / "private-key.pem").read_bytes())

    def test_protection_failure_publishes_nothing(self):
        with patch("backend.app.lan_tls.protect_text", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                load_or_create_identity(self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_existing_protection_failure_never_regenerates(self):
        identity = load_or_create_identity(self.directory)
        before = {p.name: p.read_bytes() for p in identity.directory.iterdir()}
        with patch("backend.app.lan_tls.unprotect_text", side_effect=OSError("wrong user")):
            with self.assertRaises(OSError):
                load_or_create_identity(self.directory)
        self.assertEqual(before, {p.name: p.read_bytes() for p in identity.directory.iterdir()})

    def test_host_id_certificate_and_key_must_agree(self):
        identity = load_or_create_identity(self.directory)
        metadata_path = identity.directory / "identity.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["host_id"] = "00000000-0000-4000-8000-000000000000"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_or_create_identity(self.directory)

    def test_concurrent_creation_reuses_one_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            identities = list(pool.map(lambda _: load_or_create_identity(self.directory), range(8)))
        self.assertEqual(len({i.host_id for i in identities}), 1)

    def test_expired_identity_never_regenerates(self):
        identity = load_or_create_identity(self.directory)
        before = {p.name: p.read_bytes() for p in identity.directory.iterdir()}
        with patch("backend.app.lan_tls.datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=3651)
            with self.assertRaisesRegex(ValueError, "expired"):
                load_or_create_identity(self.directory)
        self.assertEqual(before, {p.name: p.read_bytes() for p in identity.directory.iterdir()})

    def test_mismatched_private_key_never_regenerates(self):
        identity = load_or_create_identity(self.directory)
        key_path = identity.directory / "private-key.pem"
        key_path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(identity._password.encode("ascii")),
        ))
        before = {p.name: p.read_bytes() for p in identity.directory.iterdir()}
        with self.assertRaises(ssl.SSLError):
            load_or_create_identity(self.directory)
        self.assertEqual(before, {p.name: p.read_bytes() for p in identity.directory.iterdir()})

    def test_real_tls_listener_rejects_http_untrusted_and_wrong_hostname(self):
        identity = load_or_create_identity(self.directory)
        app = FastAPI()
        received = []

        @app.post("/probe")
        def probe():
            received.append(True)
            return {"ok": True}

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        config = tls_server_config(app, identity, "127.0.0.1", port)
        self.assertFalse(config.proxy_headers)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.started)
            for url in (f"http://127.0.0.1:{port}/probe", f"https://127.0.0.1:{port}/probe"):
                with self.assertRaises(httpx.TransportError):
                    httpx.post(url, json={}, trust_env=False, timeout=2)
            trusted = ssl.create_default_context(cadata=identity.certificate_pem)
            with self.assertRaises(ssl.SSLCertVerificationError):
                with socket.create_connection(("127.0.0.1", port)) as raw:
                    trusted.wrap_socket(raw, server_hostname="wrong.invalid")
            self.assertEqual(received, [])
            with socket.create_connection(("127.0.0.1", port)) as raw:
                with trusted.wrap_socket(raw, server_hostname=identity.server_name) as secure:
                    self.assertIn(secure.version(), ("TLSv1.2", "TLSv1.3"))
                    secure.sendall((f"POST /probe HTTP/1.1\r\nHost: {identity.server_name}\r\n"
                                    "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
                    self.assertIn(b"200 OK", secure.recv(4096))
            self.assertEqual(received, [True])
        finally:
            server.should_exit = True
            thread.join(5)
            listener.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
