from __future__ import annotations

import base64
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
DEMO_PACKAGE = ROOT / "examples" / "demo-bank.esq"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _write_media_package(path: Path) -> None:
    package_id = "org.example.english.media"
    paper_key = f"{package_id}.2030"
    asset_id = f"{paper_key}.image.01"
    paper = {
        "paperKey": paper_key,
        "year": 2030,
        "title": "ESQ 媒体导出演示",
        "subject": "英语演示题",
        "units": [
            {
                "unitKey": f"{paper_key}.reading.text1",
                "type": "reading",
                "subtype": "reading_a",
                "title": "Reading Text 1",
                "sequence": 1,
                "passage": {
                    "blocks": [
                        {
                            "blockKey": "image01",
                            "type": "image",
                            "assetId": asset_id,
                            "alt": "一像素测试图片",
                        },
                        {
                            "blockKey": "paragraph01",
                            "type": "paragraph",
                            "text": "The image is used only to verify media round trips.",
                        },
                    ]
                },
                "questions": [
                    {
                        "questionKey": f"{paper_key}.q01",
                        "number": 1,
                        "type": "single_choice",
                        "stem": "Why is the image included?",
                        "options": [
                            {"key": "A", "content": "To test media export."},
                            {"key": "B", "content": "To explain grammar."},
                            {"key": "C", "content": "To provide an answer."},
                            {"key": "D", "content": "To replace the passage."},
                        ],
                        "score": 2,
                    }
                ],
            }
        ],
    }
    answers = {
        "paperKey": paper_key,
        "answers": {
            f"{paper_key}.q01": {
                "correctOption": "A",
                "score": 2,
            }
        },
    }
    manifest = {
        "format": "esq",
        "schemaVersion": "1.0",
        "packageId": package_id,
        "contentVersion": "1.0.0",
        "title": "ESQ 媒体演示题库",
        "subject": "英语演示题",
        "language": "en",
        "locale": "zh-CN",
        "publisher": "英语刷题机项目",
        "license": {
            "spdx": "CC0-1.0",
            "notice": "演示内容为项目自建文本，可自由用于格式测试。",
        },
        "source": {
            "type": "demo",
            "description": "用于验证 ESQ 图片资源导入和导出。",
        },
        "papers": [
            {
                "paperKey": paper_key,
                "year": 2030,
                "path": "papers/2030.json",
                "answerPath": "answers/2030.json",
            }
        ],
        "features": {
            "hasAnswers": True,
            "hasAiLabels": False,
            "hasAssets": True,
        },
        "generator": {"name": "英语刷题机测试", "version": "0.1.0"},
    }
    assets = {
        "assets": [
            {
                "assetId": asset_id,
                "path": "assets/images/test.png",
                "mediaType": "image/png",
                "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "alt": "一像素测试图片",
            }
        ]
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("papers/2030.json", json.dumps(paper, ensure_ascii=False))
        archive.writestr("answers/2030.json", json.dumps(answers, ensure_ascii=False))
        archive.writestr("assets/index.json", json.dumps(assets, ensure_ascii=False))
        archive.writestr("assets/images/test.png", PNG_1X1)


class EsqRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        cls.temp_root = Path(cls.temp.name)
        cls.upload_dir = cls.temp_root / "uploads"
        cls.question_bank_dir = cls.temp_root / "question_banks"
        cls.upload_dir.mkdir(parents=True)
        cls.question_bank_dir.mkdir(parents=True)

        cls.patches = [
            patch("backend.app.config.DATA_DIR", cls.temp_root),
            patch("backend.app.config.UPLOAD_DIR", cls.upload_dir),
            patch("backend.app.config.QUESTION_BANK_DIR", cls.question_bank_dir),
            patch("backend.app.database.DATABASE_PATH", cls.temp_root / "test.db"),
            patch("backend.app.services.esq.QUESTION_BANK_DIR", cls.question_bank_dir),
            patch("backend.app.routers.question_banks.UPLOAD_DIR", cls.upload_dir),
            patch(
                "backend.app.routers.question_banks.QUESTION_BANK_DIR",
                cls.question_bank_dir,
            ),
            patch(
                "backend.app.main.install_bundled_question_banks",
                return_value=[],
            ),
        ]
        for active_patch in cls.patches:
            active_patch.start()

        from backend.app.main import app

        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        for active_patch in reversed(cls.patches):
            active_patch.stop()
        cls.temp.cleanup()

    def _upload(self, package_path: Path) -> dict:
        with package_path.open("rb") as package:
            response = self.client.post(
                "/api/question-banks/imports",
                files={
                    "file": (
                        package_path.name,
                        package,
                        "application/vnd.english-study-question-bank",
                    )
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_import_conflict_replace_and_export_round_trip(self) -> None:
        from backend.app.database import connect
        from backend.app.services.esq import load_esq_package

        first = self._upload(DEMO_PACKAGE)
        self.assertEqual(first["preview"]["totals"]["questions"], 1)
        published = self.client.post(
            f"/api/question-banks/imports/{first['id']}/publish",
            json={"resolutions": [], "import_ai_labels": True},
        )
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["labelsImported"], 1)

        with connect() as connection:
            original_question = connection.execute(
                """
                SELECT id FROM questions
                WHERE external_key = 'org.example.english.demo.2026.q01'
                """
            ).fetchone()
            self.assertIsNotNone(original_question)
            original_question_id = original_question["id"]
            connection.execute(
                """
                INSERT INTO options
                    (question_id, stable_key, original_label, content, sequence)
                VALUES (?, 'E', 'E', 'obsolete option', 5)
                """,
                (original_question_id,),
            )
            connection.commit()

        conflict = self._upload(DEMO_PACKAGE)
        unresolved = self.client.post(
            f"/api/question-banks/imports/{conflict['id']}/publish",
            json={"resolutions": [], "import_ai_labels": True},
        )
        self.assertEqual(unresolved.status_code, 409)
        self.assertEqual(
            unresolved.json()["detail"]["code"],
            "CONFLICT_RESOLUTION_REQUIRED",
        )
        kept = self.client.post(
            f"/api/question-banks/imports/{conflict['id']}/publish",
            json={
                "resolutions": [
                    {
                        "paper_key": "org.example.english.demo.2026",
                        "action": "keep_existing",
                    }
                ],
                "import_ai_labels": True,
            },
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(kept.json()["skippedPapers"], 1)

        replacement = self._upload(DEMO_PACKAGE)
        replaced = self.client.post(
            f"/api/question-banks/imports/{replacement['id']}/publish",
            json={
                "resolutions": [
                    {
                        "paper_key": "org.example.english.demo.2026",
                        "action": "replace_with_imported",
                    }
                ],
                "import_ai_labels": True,
            },
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        with connect() as connection:
            current_question = connection.execute(
                """
                SELECT id FROM questions
                WHERE external_key = 'org.example.english.demo.2026.q01'
                """
            ).fetchone()
            option_keys = {
                row["stable_key"]
                for row in connection.execute(
                    "SELECT stable_key FROM options WHERE question_id = ?",
                    (current_question["id"],),
                )
            }
        self.assertEqual(current_question["id"], original_question_id)
        self.assertEqual(option_keys, {"A", "B", "C", "D"})

        exported = self.client.get(
            "/api/question-banks/export?years=2026&include_labels=true"
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        exported_path = self.temp_root / "round-trip.esq"
        exported_path.write_bytes(exported.content)
        loaded = load_esq_package(exported_path)
        self.assertEqual(len(loaded["papers"]), 1)
        self.assertEqual(len(loaded["papers"][0]["units"][0]["questions"]), 1)

    def test_media_round_trip_and_path_traversal_rejection(self) -> None:
        from backend.app.services.esq import EsqValidationError, load_esq_package

        media_path = self.temp_root / "media.esq"
        _write_media_package(media_path)
        uploaded = self._upload(media_path)
        self.assertEqual(uploaded["preview"]["totals"]["assets"], 1)
        published = self.client.post(
            f"/api/question-banks/imports/{uploaded['id']}/publish",
            json={"resolutions": [], "import_ai_labels": False},
        )
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["assetsImported"], 1)

        exported = self.client.get("/api/question-banks/export?years=2030")
        self.assertEqual(exported.status_code, 200, exported.text)
        exported_path = self.temp_root / "media-round-trip.esq"
        exported_path.write_bytes(exported.content)
        loaded = load_esq_package(exported_path)
        self.assertEqual(len(loaded["assets"]), 1)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names = set(archive.namelist())
            self.assertIn("assets/index.json", names)
            self.assertTrue(any(name.startswith("assets/images/") for name in names))
            declared = json.loads(archive.read("assets/index.json"))["assets"][0]
            self.assertEqual(declared["size"], len(archive.read(declared["path"])))

        malicious = self.temp_root / "malicious.esq"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../outside.txt", "blocked")
            archive.writestr("manifest.json", "{}")
        with self.assertRaises(EsqValidationError) as raised:
            load_esq_package(malicious)
        self.assertTrue(
            any("路径" in item["reason"] for item in raised.exception.details)
        )


if __name__ == "__main__":
    unittest.main()
