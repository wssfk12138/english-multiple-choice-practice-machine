from __future__ import annotations

import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from collections.abc import Generator
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import database, lan_sync_host
from backend.app.database import get_db
from backend.app.routers import sync as sync_router
from backend.app.services import sync


class LanSyncHostSurfaceTests(unittest.TestCase):
    def test_public_host_only_exposes_sync_routes(self) -> None:
        paths = {route.path for route in lan_sync_host.lan_sync_app.routes}
        self.assertTrue({
            "/api/lan-sync/handshake",
            "/api/lan-sync/pull",
            "/api/lan-sync/push",
        }.issubset(paths))
        for private_path in (
            "/api/settings",
            "/api/imports",
            "/api/ai/profiles",
            "/api/diagnostics",
            "/docs",
            "/openapi.json",
        ):
            self.assertNotIn(private_path, paths)
            self.assertEqual(TestClient(lan_sync_host.lan_sync_app).get(private_path).status_code, 404)


class LanSyncHostHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "lan-sync-http.sqlite3"
        with closing(self.connect()) as connection:
            connection.executescript(database.SCHEMA)
            database._run_migrations(connection)
            sync._ensure_sync_columns(connection)
            connection.execute(
                "INSERT INTO vocabulary_entries(term, normalized_term, updated_at) "
                "VALUES ('existing', 'existing', '2026-08-31T00:00:00Z')"
            )
            connection.commit()

        def override_get_db() -> Generator[sqlite3.Connection, None, None]:
            connection = self.connect()
            try:
                yield connection
            finally:
                connection.close()

        lan_sync_host.lan_sync_app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(lan_sync_host.lan_sync_app)

    def tearDown(self) -> None:
        self.client.close()
        lan_sync_host.lan_sync_app.dependency_overrides.clear()
        with closing(self.connect()) as connection:
            sync.disable_sync(connection)
        self.temp_dir.cleanup()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def enable(self, passcode: str = "AAAA") -> None:
        with closing(self.connect()) as connection:
            sync.set_passcode(connection, passcode)

    def handshake(self, passcode: str = "AAAA", device_id: str = "tablet-1") -> str:
        response = self.client.post(
            "/api/lan-sync/handshake",
            json={
                "passcode": passcode,
                "device_id": device_id,
                "profile_names": ["Test Bank"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["token"])

    def test_pair_pull_push_disable_and_revoke_over_http(self) -> None:
        disabled = self.client.post(
            "/api/lan-sync/handshake",
            json={
                "passcode": "AAAA",
                "device_id": "tablet-1",
                "profile_names": ["Test Bank"],
            },
        )
        self.assertEqual(disabled.status_code, 401)

        self.enable()
        token = self.handshake()
        pulled = self.client.post(
            "/api/lan-sync/pull",
            json={
                "token": token,
                "tables": ["vocabulary_entries"],
                "cursor": {},
                "tombstone_cursor": {},
            },
        )
        self.assertEqual(pulled.status_code, 200, pulled.text)
        payload = pulled.json()
        self.assertEqual(
            [row["normalized_term"] for row in payload["changes"]["vocabulary_entries"]],
            ["existing"],
        )
        self.assertIn("updated_at", payload["cursor"]["vocabulary_entries"])
        self.assertIn("rowid", payload["cursor"]["vocabulary_entries"])
        self.assertEqual(payload["tombstones"], [])

        pushed = self.client.post(
            "/api/lan-sync/push",
            json={
                "token": token,
                "changes": {
                    "vocabulary_entries": [
                        {
                            "term": "routed",
                            "normalized_term": "routed",
                            "updated_at": "2026-08-31T00:00:01Z",
                        }
                    ]
                },
                "tombstones": [],
            },
        )
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.assertEqual(pushed.json()["applied"]["vocabulary_entries"], 1)
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT term FROM vocabulary_entries WHERE normalized_term = 'routed'"
            ).fetchone()
            self.assertEqual(row["term"], "routed")
            sync.disable_sync(connection)

        for route in ("pull", "push"):
            response = self.client.post(
                f"/api/lan-sync/{route}",
                json={
                    "token": token,
                    "tables": [],
                    "changes": {},
                    "tombstones": [],
                },
            )
            self.assertEqual(response.status_code, 401, response.text)

        self.enable("BBBB")
        replacement_token = self.handshake("BBBB")
        with closing(self.connect()) as connection:
            self.assertEqual(sync.revoke_all_devices(connection), 1)
        self.assertEqual(
            self.client.post(
                "/api/lan-sync/pull",
                json={"token": replacement_token, "tables": []},
            ).status_code,
            401,
        )

    def test_push_request_bounds_return_422_over_http(self) -> None:
        too_many = self.client.post(
            "/api/lan-sync/push",
            json={
                "token": "validation-runs-before-token-check",
                "changes": {
                    "practice_sessions": [
                        {"sync_id": f"session-{index}"}
                        for index in range(10_001)
                    ]
                },
            },
        )
        self.assertEqual(too_many.status_code, 422, too_many.text)

        too_large = self.client.post(
            "/api/lan-sync/push",
            json={
                "token": "validation-runs-before-token-check",
                "changes": {
                    "ai_profiles": [
                        {"system_prompt": "x" * (17 * 1024 * 1024)}
                    ]
                },
            },
        )
        self.assertEqual(too_large.status_code, 422, too_large.text)

    def test_raw_request_body_limit_rejects_chunked_payload_before_validation(self) -> None:
        def oversized_chunks():
            chunk = b"x" * (1024 * 1024)
            for _ in range(17):
                yield chunk

        response = self.client.post(
            "/api/lan-sync/push",
            content=oversized_chunks(),
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"],
            "局域网同步单次请求不能超过 16 MiB",
        )


class LanSyncHostLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        lan_sync_host.stop_lan_sync_host(timeout=2)

    def test_enabled_check_initializes_a_fresh_database_first(self) -> None:
        connection = MagicMock()
        initialized = False

        def initialize_database() -> None:
            nonlocal initialized
            initialized = True

        def connect_after_initialization():
            self.assertTrue(initialized)
            return connection

        with patch.object(lan_sync_host, "initialize_database") as initialize, patch.object(
            lan_sync_host, "connect"
        ) as connect, patch.object(lan_sync_host, "sync_enabled", return_value=False):
            initialize.side_effect = initialize_database
            connect.side_effect = connect_after_initialization
            connection.__enter__.return_value = connection
            self.assertFalse(lan_sync_host.start_lan_sync_host_if_enabled())

        initialize.assert_called_once_with()
        connect.assert_called_once_with()

    def test_start_rejects_an_occupied_port(self) -> None:
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        try:
            with patch.object(lan_sync_host, "LAN_SYNC_HOST", "127.0.0.1"), patch.object(
                lan_sync_host, "LAN_SYNC_PORT", occupied.getsockname()[1]
            ):
                self.assertFalse(lan_sync_host.start_lan_sync_host())
                self.assertFalse(lan_sync_host.lan_sync_host_running())
        finally:
            occupied.close()

    def test_start_stop_and_restart_clear_process_state(self) -> None:
        ready = threading.Event()

        class FakeServer:
            def __init__(self, _config):
                self.should_exit = False

            def run(self, sockets):
                self.sockets = sockets
                ready.set()
                while not self.should_exit:
                    ready.wait(0.01)

        with patch.object(lan_sync_host, "LAN_SYNC_HOST", "127.0.0.1"), patch.object(
            lan_sync_host, "LAN_SYNC_PORT", 0
        ), patch.object(lan_sync_host.uvicorn, "Server", FakeServer):
            self.assertTrue(lan_sync_host.start_lan_sync_host())
            self.assertTrue(ready.wait(1))
            self.assertTrue(lan_sync_host.lan_sync_host_running())
            self.assertFalse(lan_sync_host.start_lan_sync_host())
            self.assertTrue(lan_sync_host.stop_lan_sync_host(timeout=1))
            self.assertFalse(lan_sync_host.lan_sync_host_running())
            self.assertIsNone(lan_sync_host._server)
            self.assertIsNone(lan_sync_host._thread)
            ready.clear()
            self.assertTrue(lan_sync_host.start_lan_sync_host())
            self.assertTrue(ready.wait(1))
            self.assertTrue(lan_sync_host.stop_lan_sync_host(timeout=1))

    def test_passcode_generation_rolls_back_when_host_cannot_start(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(database.SCHEMA)
        try:
            with patch.object(
                lan_sync_host, "lan_sync_host_running", return_value=False
            ), patch.object(
                lan_sync_host, "start_lan_sync_host", return_value=False
            ):
                with self.assertRaises(HTTPException) as raised:
                    sync_router.create_passcode(connection)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertFalse(sync.sync_enabled(connection))
            status = sync_router.status(connection)
            self.assertFalse(status["enabled"])
            self.assertFalse(status["listening"])
            self.assertEqual(status["host_urls"], [])
        finally:
            connection.close()

    def test_management_http_returns_503_when_port_is_occupied(self) -> None:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.executescript(database.SCHEMA)
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()

        def override_get_db() -> Generator[sqlite3.Connection, None, None]:
            yield connection

        management_app = FastAPI()
        management_app.include_router(sync_router.router, prefix="/api")
        management_app.dependency_overrides[get_db] = override_get_db
        try:
            with patch.object(
                lan_sync_host, "LAN_SYNC_HOST", "127.0.0.1"
            ), patch.object(
                lan_sync_host, "LAN_SYNC_PORT", occupied.getsockname()[1]
            ), TestClient(management_app) as client:
                response = client.post("/api/lan-sync/passcode")
                self.assertEqual(response.status_code, 503, response.text)
                status = client.get("/api/lan-sync/status")
            self.assertEqual(status.status_code, 200, status.text)
            payload = status.json()
            self.assertFalse(payload["enabled"])
            self.assertFalse(payload["listening"])
            self.assertEqual(payload["host_urls"], [])
        finally:
            occupied.close()
            connection.close()


class LanSyncHostActualSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "actual-http.sqlite3"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH",
            self.database_path,
        )
        self.database_patch.start()
        database.initialize_database()

        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        self.host_patch = patch.object(lan_sync_host, "LAN_SYNC_HOST", "127.0.0.1")
        self.port_patch = patch.object(lan_sync_host, "LAN_SYNC_PORT", self.port)
        self.host_patch.start()
        self.port_patch.start()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=2)

    def tearDown(self) -> None:
        self.client.close()
        lan_sync_host.stop_lan_sync_host(timeout=2)
        self.port_patch.stop()
        self.host_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def port_is_open(self) -> bool:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.settimeout(0.2)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def wait_for_server(self) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                if self.client.get("/api/not-a-sync-route").status_code == 404:
                    return
            except httpx.HTTPError:
                time.sleep(0.02)
        self.fail("LAN sync host did not become ready")

    def test_real_http_lifecycle_uses_only_the_public_sync_surface(self) -> None:
        self.assertFalse(lan_sync_host.start_lan_sync_host_if_enabled())
        self.assertFalse(self.port_is_open())

        with closing(database.connect()) as connection:
            sync.set_passcode(connection, "ABCD")
        self.assertTrue(lan_sync_host.start_lan_sync_host_if_enabled())
        self.wait_for_server()
        self.assertEqual(self.client.get("/api/not-a-sync-route").status_code, 404)

        wrong = self.client.post(
            "/api/lan-sync/handshake",
            json={"passcode": "WRONG", "device_id": "actual-http-client"},
        )
        self.assertEqual(wrong.status_code, 401, wrong.text)
        paired = self.client.post(
            "/api/lan-sync/handshake",
            json={"passcode": "ABCD", "device_id": "actual-http-client"},
        )
        self.assertEqual(paired.status_code, 200, paired.text)
        token = paired.json()["token"]
        impersonated = self.client.post(
            "/api/lan-sync/handshake",
            json={"passcode": "WRONG", "device_id": "actual-http-client"},
        )
        self.assertEqual(impersonated.status_code, 401, impersonated.text)

        pushed = self.client.post(
            "/api/lan-sync/push",
            json={
                "token": token,
                "changes": {
                    "vocabulary_entries": [
                        {
                            "term": "actual",
                            "normalized_term": "actual",
                            "updated_at": "2026-08-31T00:00:00Z",
                        }
                    ]
                },
                "tombstones": [],
            },
        )
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.assertEqual(pushed.json()["applied"]["vocabulary_entries"], 1)
        pulled = self.client.post(
            "/api/lan-sync/pull",
            json={"token": token, "tables": ["vocabulary_entries"]},
        )
        self.assertEqual(pulled.status_code, 200, pulled.text)
        self.assertEqual(
            pulled.json()["changes"]["vocabulary_entries"][0]["normalized_term"],
            "actual",
        )

        with closing(database.connect()) as connection:
            new_passcode = sync.reset_passcode(connection)
        expired = self.client.post(
            "/api/lan-sync/pull",
            json={"token": token, "tables": []},
        )
        self.assertEqual(expired.status_code, 401, expired.text)
        old_passcode = self.client.post(
            "/api/lan-sync/handshake",
            json={"passcode": "ABCD", "device_id": "actual-http-client"},
        )
        self.assertEqual(old_passcode.status_code, 401, old_passcode.text)
        reconnected = self.client.post(
            "/api/lan-sync/handshake",
            json={"passcode": new_passcode, "device_id": "actual-http-client"},
        )
        self.assertEqual(reconnected.status_code, 200, reconnected.text)
        replacement_token = reconnected.json()["token"]

        with closing(database.connect()) as connection:
            self.assertEqual(sync.revoke_all_devices(connection), 1)
        revoked = self.client.post(
            "/api/lan-sync/pull",
            json={"token": replacement_token, "tables": []},
        )
        self.assertEqual(revoked.status_code, 401, revoked.text)

        with closing(database.connect()) as connection:
            sync.disable_sync(connection)
        disabled = self.client.post(
            "/api/lan-sync/push",
            json={"token": token, "changes": {}, "tombstones": []},
        )
        self.assertEqual(disabled.status_code, 401, disabled.text)
        self.assertTrue(lan_sync_host.stop_lan_sync_host(timeout=2))
        self.assertFalse(self.port_is_open())


if __name__ == "__main__":
    unittest.main()
