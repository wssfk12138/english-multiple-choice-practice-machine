from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from backend.app.routers import remote_question_banks
from backend.app.schemas import QuestionBankDownloadRequest
from backend.app.services.esq import EsqValidationError
from backend.app.services.question_bank_catalog import validate_catalog


class RemoteQuestionBankRouterTests(unittest.TestCase):
    def test_download_uses_stored_catalog_identity_and_removes_failed_file(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (remote_question_banks.SETTING_KEY, "https://example.com/catalog.json"),
        )
        package = {
            "packageId": "cet4-2026", "title": "CET4 2026",
            "contentVersion": "1.0.0", "fileName": "bank.esq",
            "downloadUrl": "https://example.com/bank.esq", "sha256": "a" * 64,
            "size": 1234, "license": "redistributable", "years": [2026],
        }
        catalog = validate_catalog({"catalogVersion": 1, "packages": [package]})
        payload = QuestionBankDownloadRequest(package_id="cet4-2026", content_version="1.0.0")
        with TemporaryDirectory() as temporary:
            downloaded = Path(temporary) / "download.esq"
            downloaded.write_bytes(b"invalid")
            with patch.object(remote_question_banks, "fetch_catalog", return_value=catalog) as fetch, \
                 patch.object(remote_question_banks, "download_package", return_value=downloaded) as download, \
                 patch.object(remote_question_banks, "create_question_bank_import",
                              side_effect=EsqValidationError([{"message": "invalid"}])):
                with self.assertRaises(HTTPException) as raised:
                    remote_question_banks.download_question_bank(payload, connection)
            self.assertEqual(raised.exception.status_code, 422)
            self.assertFalse(downloaded.exists())
            fetch.assert_called_once_with("https://example.com/catalog.json")
            download.assert_called_once_with(catalog["packages"][0], remote_question_banks.UPLOAD_DIR)
        connection.close()


if __name__ == "__main__":
    unittest.main()
