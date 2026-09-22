from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import run_app


class RunAppLifecycleTests(unittest.TestCase):
    def test_health_requires_matching_variant_and_data_directory(self) -> None:
        healthy = {"status": "ok", "variant": run_app.APP_VARIANT,
                   "data_dir": str(run_app.DATA_DIR)}
        cases = [(healthy, True), ({**healthy, "variant": "internal"}, False),
                 ({**healthy, "data_dir": "another-directory"}, False),
                 ({"status": "ok"}, False), ([], False)]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                response = Mock(status=200)
                response.read.return_value = json.dumps(payload).encode()
                context = Mock()
                context.__enter__ = Mock(return_value=response)
                context.__exit__ = Mock(return_value=False)
                with patch.object(run_app.urllib.request, "urlopen", return_value=context):
                    self.assertEqual(run_app.app_is_running(), expected)

    def test_readiness_does_not_open_another_instance(self) -> None:
        with patch.object(run_app, "app_is_running", side_effect=[False, True]), patch.object(
            run_app.time, "sleep"
        ) as sleep, patch.object(run_app.webbrowser, "open") as open_browser:
            run_app.open_when_ready()
        sleep.assert_called_once_with(0.25)
        open_browser.assert_called_once_with(run_app.URL)

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
            "backend.app.main:app", host="127.0.0.1", port=run_app.PORT
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


class RunAppStdioTests(unittest.TestCase):
    def test_ensure_windowless_stdio_replaces_missing_streams(self) -> None:
        with patch.object(run_app.sys, "stdout", None), patch.object(
            run_app.sys, "stderr", None
        ):
            run_app.ensure_windowless_stdio()
            self.assertIsNotNone(run_app.sys.stdout)
            self.assertIsNotNone(run_app.sys.stderr)
            run_app.sys.stdout.close()
            run_app.sys.stderr.close()

    def test_ensure_windowless_stdio_keeps_real_streams(self) -> None:
        sentinel_out, sentinel_err = object(), object()
        with patch.object(run_app.sys, "stdout", sentinel_out), patch.object(
            run_app.sys, "stderr", sentinel_err
        ):
            run_app.ensure_windowless_stdio()
            self.assertIs(run_app.sys.stdout, sentinel_out)
            self.assertIs(run_app.sys.stderr, sentinel_err)


if __name__ == "__main__":
    unittest.main()
