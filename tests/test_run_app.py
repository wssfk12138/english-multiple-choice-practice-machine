from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import run_app


class RunAppLifecycleTests(unittest.TestCase):
    def test_existing_app_only_opens_browser(self) -> None:
        with patch.object(run_app, "app_is_running", return_value=True), patch.object(
            run_app.webbrowser, "open"
        ) as open_browser, patch.object(run_app.uvicorn, "run") as run_server:
            self.assertEqual(run_app.main(), 0)

        open_browser.assert_called_once_with(run_app.URL)
        run_server.assert_not_called()

    def test_occupied_web_port_returns_two(self) -> None:
        with patch.object(run_app, "app_is_running", return_value=False), patch.object(
            run_app, "port_is_available", return_value=False
        ), patch.object(run_app.threading, "Thread") as ready_thread, patch.object(
            run_app.uvicorn, "run"
        ) as run_server:
            self.assertEqual(run_app.main(), 2)

        ready_thread.assert_not_called()
        run_server.assert_not_called()

    def test_main_stops_lan_sync_host_after_web_server_exits(self) -> None:
        ready_thread = Mock()
        with patch.object(run_app, "app_is_running", return_value=False
        ), patch.object(run_app, "port_is_available", return_value=True), patch.object(
            run_app.threading, "Thread", return_value=ready_thread
        ), patch.object(run_app.uvicorn, "run") as run_server, patch(
            "backend.app.lan_sync_host.start_lan_sync_host_if_enabled"
        ) as start_lan, patch(
            "backend.app.lan_sync_host.stop_lan_sync_host"
        ) as stop_lan:
            self.assertEqual(run_app.main(), 0)

        ready_thread.start.assert_called_once_with()
        start_lan.assert_called_once_with()
        run_server.assert_called_once_with(
            "backend.app.main:app", host="127.0.0.1", port=8765
        )
        stop_lan.assert_called_once_with()

    def test_main_stops_lan_sync_host_when_web_server_fails(self) -> None:
        with patch.object(run_app, "app_is_running", return_value=False
        ), patch.object(run_app, "port_is_available", return_value=True), patch.object(
            run_app.threading, "Thread"
        ), patch.object(
            run_app.uvicorn, "run", side_effect=RuntimeError("server failed")
        ), patch(
            "backend.app.lan_sync_host.start_lan_sync_host_if_enabled"
        ), patch(
            "backend.app.lan_sync_host.stop_lan_sync_host"
        ) as stop_lan:
            with self.assertRaisesRegex(RuntimeError, "server failed"):
                run_app.main()

        stop_lan.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
