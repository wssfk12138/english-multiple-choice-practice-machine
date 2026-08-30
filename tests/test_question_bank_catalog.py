from __future__ import annotations

import hashlib
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.services.esq import MAX_PACKAGE_BYTES
from backend.app.services.question_bank_catalog import (
    MAX_CATALOG_BYTES, QuestionBankCatalogError, download_package, fetch_catalog,
    find_package, validate_catalog, validate_remote_url,
)


class Response(BytesIO):
    def __init__(self, content: bytes, url: str):
        super().__init__(content)
        self.url = url

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class QuestionBankCatalogTests(unittest.TestCase):
    def package(self, **overrides):
        value = {
            "packageId": "cet4-2026", "title": "CET4 2026",
            "contentVersion": "1.0.0", "fileName": "bank.esq",
            "downloadUrl": "https://example.com/bank.esq", "sha256": "a" * 64,
            "size": 1234, "license": "redistributable", "years": [2026, 2025, 2026],
        }
        value.update(overrides)
        return value

    def test_url_requires_public_credential_free_https(self):
        self.assertEqual(validate_remote_url("https://example.com/catalog.json"), "https://example.com/catalog.json")
        for url in ("http://example.com/catalog.json", "https://user:secret@example.com/x",
                    "https://127.0.0.1/x", "https://192.168.1.2/x", "https://localhost/x"):
            with self.subTest(url=url), self.assertRaises(QuestionBankCatalogError):
                validate_remote_url(url)

    def test_catalog_rejects_invalid_identity_path_size_and_duplicates(self):
        valid = validate_catalog({"catalogVersion": 1, "packages": [self.package()]})
        self.assertEqual(valid["packages"][0]["years"], [2025, 2026])
        for field, value in (("contentVersion", "v1"), ("fileName", "../bank.esq"),
                             ("sha256", "abc"), ("size", MAX_PACKAGE_BYTES + 1)):
            with self.subTest(field=field), self.assertRaises(QuestionBankCatalogError):
                validate_catalog({"catalogVersion": 1, "packages": [self.package(**{field: value})]})
        with self.assertRaises(QuestionBankCatalogError):
            validate_catalog({"catalogVersion": 1, "packages": [self.package(), self.package()]})
        with self.assertRaises(QuestionBankCatalogError):
            validate_catalog({"catalogVersion": 1, "packages": [self.package(packageId=f"bank-{i}") for i in range(501)]})

    def test_find_package_uses_current_catalog_identity(self):
        catalog = validate_catalog({"catalogVersion": 1, "packages": [self.package()]})
        self.assertEqual(find_package(catalog, "cet4-2026", "1.0.0")["fileName"], "bank.esq")
        with self.assertRaises(QuestionBankCatalogError):
            find_package(catalog, "cet4-2026", "0.9.0")

    def test_fetch_limits_catalog_bytes(self):
        response = Response(b"x" * (MAX_CATALOG_BYTES + 1), "https://example.com/catalog.json")
        with patch("backend.app.services.question_bank_catalog._open_remote", return_value=response), \
             patch("backend.app.services.question_bank_catalog._assert_public_host"):
            with self.assertRaises(QuestionBankCatalogError):
                fetch_catalog("https://example.com/catalog.json")

    def test_download_verifies_exact_size_and_sha256_and_cleans_failure(self):
        content = b"valid esq bytes"
        package = self.package(size=len(content), sha256=hashlib.sha256(content).hexdigest())
        with TemporaryDirectory() as temporary, \
             patch("backend.app.services.question_bank_catalog._open_remote", return_value=Response(content, package["downloadUrl"])), \
             patch("backend.app.services.question_bank_catalog._assert_public_host"):
            target = download_package(package, Path(temporary))
            self.assertEqual(target.read_bytes(), content)
        bad = self.package(size=len(content), sha256="0" * 64)
        with TemporaryDirectory() as temporary, \
             patch("backend.app.services.question_bank_catalog._open_remote", return_value=Response(content, bad["downloadUrl"])), \
             patch("backend.app.services.question_bank_catalog._assert_public_host"):
            with self.assertRaises(QuestionBankCatalogError):
                download_package(bad, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

if __name__ == "__main__":
    unittest.main()
