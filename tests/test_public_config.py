from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import config


class PublicConfigTests(unittest.TestCase):
    def test_public_ports_do_not_collide_with_internal(self):
        from backend.app import lan_sync_host, lan_tls_host
        self.assertEqual(lan_sync_host.LAN_SYNC_PORT, 8776)
        self.assertEqual(lan_tls_host.LAN_TLS_PORT, 8777)
        self.assertEqual(config.APP_VARIANT, 'public')

    def test_source_data_path_is_unchanged(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(config.sys, 'frozen', False, create=True):
            self.assertEqual(config.resolve_data_dir(), config.SOURCE_ROOT / 'backend' / 'data')

    def test_frozen_public_data_is_not_internal_data(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'LOCALAPPDATA': root}, clear=True), patch.object(config.sys, 'frozen', True, create=True):
            self.assertEqual(config.resolve_data_dir(), Path(root).resolve() / 'EnglishPracticeMachinePublic')
            self.assertNotEqual(config.APP_DATA_DIR_NAME, 'EnglishPracticeMachine')

    def test_explicit_qa_directory_takes_priority(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {config.DATA_DIR_ENV: root}, clear=True), patch.object(config.sys, 'frozen', True, create=True):
            self.assertEqual(config.resolve_data_dir(), Path(root).resolve())

    def test_missing_local_app_data_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(config.sys, 'frozen', True, create=True):
            with self.assertRaisesRegex(RuntimeError, 'LOCALAPPDATA'):
                config.resolve_data_dir()
