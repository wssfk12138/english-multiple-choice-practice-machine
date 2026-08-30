from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend.app.routers.updates import download_update
from backend.app.services.updates import (
    MAX_MANIFEST_BYTES,
    MAX_PACKAGE_BYTES,
    OFFICIAL_MANIFEST_URL,
    UpdateError,
    download_official_package,
    fetch_official_manifest,
    open_verified_package,
    validate_manifest,
)


def manifest_for(payload: bytes = b"abc") -> dict:
    return {
        "schemaVersion": 1,
        "windows": {
            "versionName": "0.1.1",
            "versionCode": 2,
            "packageUrl": "https://github.com/wssfk12138/english-multiple-choice-practice-machine/releases/download/v0.1.1/app.exe",
            "packageSize": len(payload),
            "packageSha256": hashlib.sha256(payload).hexdigest(),
            "packageType": "exe",
            "releaseNotes": "Public beta update",
        },
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class UpdateSecurityTests(unittest.TestCase):
    def test_manifest_is_strict_and_rejects_non_official_sources(self) -> None:
        normalized = validate_manifest(manifest_for())
        self.assertEqual(normalized["packageSha256"], hashlib.sha256(b"abc").hexdigest())

        for invalid_url in (
            "http://github.com/project/app.exe",
            "https://example.com/app.exe",
            "https://github.com/project/app.exe?token=secret",
        ):
            candidate = manifest_for()
            candidate["windows"]["packageUrl"] = invalid_url
            with self.assertRaises(UpdateError):
                validate_manifest(candidate)

        candidate = manifest_for()
        candidate["windows"]["mirrorUrl"] = "https://github.com/unused"
        with self.assertRaises(UpdateError):
            validate_manifest(candidate)

    def test_manifest_rejects_invalid_types_and_package_limits(self) -> None:
        for field, value in (
            ("versionCode", True),
            ("packageSize", MAX_PACKAGE_BYTES + 1),
            ("packageType", "bat"),
            ("packageSha256", "short"),
        ):
            candidate = manifest_for()
            candidate["windows"][field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError):
                validate_manifest(candidate)

    def test_manifest_fetch_is_fixed_and_bounded(self) -> None:
        with patch("backend.app.services.updates._open_url", return_value=Response(b"x" * (MAX_MANIFEST_BYTES + 1))) as opener:
            with self.assertRaises(UpdateError):
                fetch_official_manifest()
        opener.assert_called_once_with(OFFICIAL_MANIFEST_URL, timeout=20)

    def test_download_writes_receipt_without_source_url(self) -> None:
        payload = b"verified package"
        with tempfile.TemporaryDirectory() as temp:
            update_dir = Path(temp)
            with (
                patch("backend.app.services.updates.UPDATE_DIR", update_dir),
                patch("backend.app.services.updates.fetch_official_manifest", return_value=validate_manifest(manifest_for(payload))),
                patch("backend.app.services.updates._open_url", return_value=Response(payload)),
            ):
                result = download_official_package()
            receipt = json.loads((update_dir / f"{result['fileName']}.receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["packageSha256"], hashlib.sha256(payload).hexdigest())
            self.assertNotIn("packageUrl", receipt)
            self.assertNotIn("sourceUrl", receipt)

    def test_hash_or_size_mismatch_leaves_no_package_or_receipt(self) -> None:
        payload = b"wrong package"
        manifest = validate_manifest(manifest_for(b"expected package"))
        with tempfile.TemporaryDirectory() as temp:
            update_dir = Path(temp)
            with (
                patch("backend.app.services.updates.UPDATE_DIR", update_dir),
                patch("backend.app.services.updates.fetch_official_manifest", return_value=manifest),
                patch("backend.app.services.updates._open_url", return_value=Response(payload)),
            ):
                with self.assertRaises(UpdateError):
                    download_official_package()
            self.assertEqual(list(update_dir.iterdir()), [])

    def test_open_rejects_traversal_and_rehashes_before_start(self) -> None:
        with self.assertRaises(UpdateError):
            open_verified_package("../app.exe")

        payload = b"original"
        with tempfile.TemporaryDirectory() as temp:
            update_dir = Path(temp)
            target = update_dir / "app.exe"
            target.write_bytes(b"changed")
            receipt = {
                "schemaVersion": 1,
                "fileName": "app.exe",
                "versionName": "0.1.1",
                "versionCode": 2,
                "packageType": "exe",
                "packageSize": len(payload),
                "packageSha256": hashlib.sha256(payload).hexdigest(),
                "verifiedAt": "2026-08-29T00:00:00+00:00",
            }
            (update_dir / "app.exe.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            with patch("backend.app.services.updates.UPDATE_DIR", update_dir):
                with self.assertRaises(UpdateError):
                    open_verified_package("app.exe")

    def test_download_endpoint_rejects_any_client_body(self) -> None:
        body = json.dumps({"url": "https://example.com/payload.exe"}).encode()
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/api/updates/download", "headers": []}, receive)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(download_update(request))
        self.assertEqual(caught.exception.status_code, 422)

    def test_no_diagnostic_upload_or_receiver_routes_exist(self) -> None:
        from backend.app.main import app

        paths = {route.path for route in app.routes}
        self.assertNotIn("/api/updates/diagnostics/send", paths)
        self.assertNotIn("/api/updates/settings", paths)
        self.assertNotIn("/api/updates/diagnostics/entries", paths)


if __name__ == "__main__":
    unittest.main()
