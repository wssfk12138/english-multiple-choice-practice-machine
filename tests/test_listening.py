from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ListeningAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.database_path = root / "test.db"
        cls.question_bank_dir = root / "question_banks"
        cls.question_bank_dir.mkdir()
        cls.patches = [
            patch("backend.app.database.DATABASE_PATH", cls.database_path),
            patch(
                "backend.app.services.listening.QUESTION_BANK_DIR",
                cls.question_bank_dir,
            ),
        ]
        for active_patch in cls.patches:
            active_patch.start()
        from backend.app.database import initialize_database

        initialize_database()

    @classmethod
    def tearDownClass(cls) -> None:
        for active_patch in reversed(cls.patches):
            active_patch.stop()
        try:
            cls.temp.cleanup()
        except PermissionError:
            time.sleep(0.1)
            try:
                cls.temp.cleanup()
            except PermissionError:
                pass

    def _paper_with_unit(self, connection, *, year: int) -> tuple[int, int]:
        paper = connection.execute(
            """
            INSERT INTO papers
                (profile_id, year, subject, title, source_file, status, external_key)
            VALUES (1, ?, '大学英语四级', ?, 'paper.docx', 'published', ?)
            """,
            (year, f"{year} 年四级真题", f"test:{year}"),
        )
        paper_id = int(paper.lastrowid)
        unit = connection.execute(
            """
            INSERT INTO units
                (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
            VALUES (?, 'listening', 'news_report', '听力 Section A', 1, '', '{}')
            """,
            (paper_id,),
        )
        return paper_id, int(unit.lastrowid)

    def test_audio_is_persisted_and_exposed_by_serialized_unit(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import attach_listening_assets
        from backend.app.services.questions import serialize_unit

        audio = Path(self.temp.name) / "cet-listening.mp3"
        audio.write_bytes(b"ID3-test-audio")
        with connect() as connection:
            paper_id, unit_id = self._paper_with_unit(connection, year=2091)
            tracks = attach_listening_assets(
                connection,
                paper_id,
                [audio],
                ["2091 年四级听力.mp3"],
            )
            connection.commit()
            unit = serialize_unit(
                connection,
                unit_id,
                shuffle_options=False,
            )
            asset = connection.execute(
                """
                SELECT stored_path, media_type
                FROM question_bank_assets
                WHERE package_id = ? AND asset_id = 'listening.track.1'
                """,
                (f"local.paper-{paper_id}",),
            ).fetchone()

        self.assertEqual(len(tracks), 1)
        self.assertEqual(asset["media_type"], "audio/mpeg")
        self.assertTrue(Path(asset["stored_path"]).is_file())
        self.assertEqual(
            unit["shared_data"]["audio_tracks"][0]["url"],
            f"/api/question-banks/assets/local.paper-{paper_id}/1.0.0/listening.track.1",
        )

    def test_older_published_import_can_recover_uploaded_audio(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import repair_published_listening_assets

        audio = Path(self.temp.name) / "legacy.wav"
        audio.write_bytes(b"RIFF-test-audio")
        with connect() as connection:
            paper_id, unit_id = self._paper_with_unit(connection, year=2092)
            context = {
                "published_paper_ids": [paper_id],
                "audio_paths": [str(audio)],
                "audio_names": ["旧版听力.wav"],
            }
            connection.execute(
                """
                INSERT INTO import_jobs
                    (profile_id, filename, stored_path, status, draft_data,
                     warnings, parse_context)
                VALUES (1, 'legacy.docx', 'legacy.docx', 'published', '{}', '[]', ?)
                """,
                (json.dumps(context, ensure_ascii=False),),
            )
            connection.commit()
            repaired = repair_published_listening_assets(connection)
            shared = json.loads(
                connection.execute(
                    "SELECT shared_data FROM units WHERE id = ?",
                    (unit_id,),
                ).fetchone()["shared_data"]
            )

        self.assertEqual(repaired, 1)
        self.assertEqual(shared["audio_tracks"][0]["media_type"], "audio/wav")

    def test_moved_application_relocates_audio_asset_by_hash(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import (
            attach_listening_assets,
            repair_published_listening_assets,
        )

        audio = Path(self.temp.name) / "relocated.mp3"
        audio.write_bytes(b"ID3-relocated-audio")
        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2099)
            attach_listening_assets(connection, paper_id, [audio], [audio.name])
            old_path = Path("C:/old-install/question_banks/track-1.mp3")
            connection.execute(
                """
                UPDATE question_bank_assets
                SET stored_path = ?
                WHERE package_id = ? AND asset_id = 'listening.track.1'
                """,
                (str(old_path), f"local.paper-{paper_id}"),
            )
            connection.commit()

            repaired = repair_published_listening_assets(connection)
            stored_path = Path(
                connection.execute(
                    """
                    SELECT stored_path FROM question_bank_assets
                    WHERE package_id = ? AND asset_id = 'listening.track.1'
                    """,
                    (f"local.paper-{paper_id}",),
                ).fetchone()["stored_path"]
            )

        self.assertEqual(repaired, 1)
        self.assertTrue(stored_path.is_file())
        self.assertEqual(stored_path.read_bytes(), audio.read_bytes())
        self.assertTrue(stored_path.is_relative_to(self.question_bank_dir))

    def test_moved_application_does_not_relocate_wrong_audio(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import (
            attach_listening_assets,
            repair_published_listening_assets,
        )

        audio = Path(self.temp.name) / "wrong-hash.mp3"
        audio.write_bytes(b"ID3-original-audio")
        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2100)
            attach_listening_assets(connection, paper_id, [audio], [audio.name])
            persisted = (
                self.question_bank_dir
                / f"local.paper-{paper_id}"
                / "1.0.0"
                / "assets"
                / "audio"
                / "track-1.mp3"
            )
            persisted.write_bytes(b"ID3-different-audio")
            old_path = Path("C:/old-install/question_banks/track-1.mp3")
            connection.execute(
                """
                UPDATE question_bank_assets
                SET stored_path = ?
                WHERE package_id = ? AND asset_id = 'listening.track.1'
                """,
                (str(old_path), f"local.paper-{paper_id}"),
            )
            connection.commit()

            repaired = repair_published_listening_assets(connection)
            stored_path = connection.execute(
                """
                SELECT stored_path FROM question_bank_assets
                WHERE package_id = ? AND asset_id = 'listening.track.1'
                """,
                (f"local.paper-{paper_id}",),
            ).fetchone()["stored_path"]

        self.assertEqual(repaired, 0)
        self.assertEqual(stored_path, str(old_path))

    def test_moved_application_rejects_asset_path_outside_bank_root(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import repair_published_listening_assets

        outside = Path(self.temp.name) / "outside" / "assets" / "audio"
        outside.mkdir(parents=True)
        audio = outside / "track-1.mp3"
        audio.write_bytes(b"ID3-outside-audio")
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO question_bank_assets
                    (package_id, content_version, asset_id, stored_path,
                     media_type, sha256, metadata)
                VALUES ('../outside', '.', 'listening.track.1', ?,
                        'audio/mpeg', ?, '{}')
                """,
                (
                    "C:/old-install/question_banks/track-1.mp3",
                    hashlib.sha256(audio.read_bytes()).hexdigest(),
                ),
            )
            connection.commit()

            repaired = repair_published_listening_assets(connection)
            stored_path = connection.execute(
                """
                SELECT stored_path FROM question_bank_assets
                WHERE package_id = '../outside'
                """
            ).fetchone()["stored_path"]

        self.assertEqual(repaired, 0)
        self.assertEqual(
            stored_path,
            "C:/old-install/question_banks/track-1.mp3",
        )

    def test_multiple_audio_tracks_are_mapped_to_listening_sections(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import attach_listening_assets

        audio_files = []
        for index in range(1, 4):
            audio = Path(self.temp.name) / f"section-{index}.mp3"
            audio.write_bytes(f"ID3-section-{index}".encode())
            audio_files.append(audio)

        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2093)
            for sequence, title in ((2, "听力 Section B"), (3, "听力 Section C")):
                connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
                    VALUES (?, 'listening', 'passage', ?, ?, '', '{}')
                    """,
                    (paper_id, title, sequence),
                )
            attach_listening_assets(
                connection,
                paper_id,
                audio_files,
                [path.name for path in audio_files],
            )
            rows = connection.execute(
                """
                SELECT shared_data FROM units
                WHERE paper_id = ? AND unit_type = 'listening'
                ORDER BY sequence
                """,
                (paper_id,),
            ).fetchall()

        payloads = [json.loads(row["shared_data"]) for row in rows]
        self.assertEqual(
            [payload["audio_tracks"][0]["asset_id"] for payload in payloads],
            ["listening.track.1", "listening.track.2", "listening.track.3"],
        )
        self.assertTrue(
            all(payload["audio_mode"] == "per_unit" for payload in payloads)
        )

    def test_complete_listening_practice_selects_all_sections_of_one_paper(self) -> None:
        from backend.app.database import connect
        from backend.app.routers.dashboard import dashboard
        from backend.app.schemas import PracticeCreate
        from backend.app.services.listening import attach_listening_assets
        from backend.app.services.practice import create_session

        audio = Path(self.temp.name) / "complete-listening.mp3"
        audio.write_bytes(b"ID3-complete-listening")
        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2094)
            for sequence, title in ((2, "听力 Section B"), (3, "听力 Section C")):
                connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
                    VALUES (?, 'listening', 'passage', ?, ?, '', '{}')
                    """,
                    (paper_id, title, sequence),
                )
            for unit in connection.execute(
                "SELECT id FROM units WHERE paper_id = ? ORDER BY sequence",
                (paper_id,),
            ).fetchall():
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, '听力题', 'A', 1, 1)
                    """,
                    (unit["id"],),
                )
            attach_listening_assets(connection, paper_id, [audio], [audio.name])
            session = create_session(
                connection,
                PracticeCreate(
                    mode="random",
                    paper_id=paper_id,
                    unit_type="listening",
                    selection_scope="paper_unit_type",
                    count=1,
                    shuffle_options=True,
                ),
            )
            overview = dashboard(connection)

        self.assertEqual(session["paper_id"], paper_id)
        self.assertEqual(len(session["units"]), 3)
        self.assertTrue(
            all(unit["unit_type"] == "listening" for unit in session["units"])
        )
        self.assertGreaterEqual(overview["paper_type_counts"]["listening"], 1)
        self.assertGreaterEqual(overview["unit_type_counts"]["listening"], 3)

    def test_listening_without_audio_is_hidden_and_cannot_start(self) -> None:
        from backend.app.database import (
            connect,
            get_active_profile_id,
            set_active_profile_id,
        )
        from backend.app.routers.dashboard import dashboard
        from backend.app.schemas import PracticeCreate
        from backend.app.services.practice import create_session

        with connect() as connection:
            original_profile_id = get_active_profile_id(connection)
            profile_id = int(
                connection.execute(
                    "INSERT INTO question_bank_profiles(name) VALUES ('No audio')"
                ).lastrowid
            )
            try:
                set_active_profile_id(connection, profile_id)
                paper_id, unit_id = self._paper_with_unit(connection, year=2096)
                connection.execute(
                    "UPDATE papers SET profile_id = ? WHERE id = ?",
                    (profile_id, paper_id),
                )
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, 'question', 'A', 1, 1)
                    """,
                    (unit_id,),
                )
                overview = dashboard(connection)
                with self.assertRaises(LookupError):
                    create_session(
                        connection,
                        PracticeCreate(
                            mode="random",
                            paper_id=paper_id,
                            unit_type="listening",
                            selection_scope="paper_unit_type",
                            count=1,
                            shuffle_options=True,
                        ),
                    )
            finally:
                set_active_profile_id(connection, original_profile_id)

        self.assertNotIn("listening", overview["paper_type_counts"])
        self.assertNotIn("listening", overview["unit_type_counts"])

    def test_dashboard_counts_follow_active_profile(self) -> None:
        from backend.app.database import (
            connect,
            get_active_profile_id,
            set_active_profile_id,
        )
        from backend.app.routers.dashboard import dashboard
        from backend.app.services.listening import attach_listening_assets

        audio = Path(self.temp.name) / "profile-listening.mp3"
        audio.write_bytes(b"ID3-profile-listening")
        with connect() as connection:
            original_profile_id = get_active_profile_id(connection)
            text_profile_id = int(
                connection.execute(
                    "INSERT INTO question_bank_profiles(name) VALUES ('Text profile')"
                ).lastrowid
            )
            audio_profile_id = int(
                connection.execute(
                    "INSERT INTO question_bank_profiles(name) VALUES ('Audio profile')"
                ).lastrowid
            )
            try:
                text_paper_id, text_unit_id = self._paper_with_unit(
                    connection,
                    year=2097,
                )
                connection.execute(
                    "UPDATE papers SET profile_id = ? WHERE id = ?",
                    (text_profile_id, text_paper_id),
                )
                connection.execute(
                    "UPDATE units SET unit_type = 'cloze' WHERE id = ?",
                    (text_unit_id,),
                )
                for sequence, unit_type in enumerate(("reading", "part_b"), 2):
                    unit_id = int(
                        connection.execute(
                            """
                            INSERT INTO units
                                (paper_id, unit_type, subtype, title, sequence,
                                 passage, shared_data)
                            VALUES (?, ?, '', ?, ?, 'passage', '{}')
                            """,
                            (text_paper_id, unit_type, unit_type, sequence),
                        ).lastrowid
                    )
                    connection.execute(
                        """
                        INSERT INTO questions
                            (unit_id, number, stem, answer, score, sequence)
                        VALUES (?, ?, 'question', 'A', 1, 1)
                        """,
                        (unit_id, sequence),
                    )
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, 'question', 'A', 1, 1)
                    """,
                    (text_unit_id,),
                )

                audio_paper_id, audio_unit_id = self._paper_with_unit(
                    connection,
                    year=2098,
                )
                connection.execute(
                    "UPDATE papers SET profile_id = ? WHERE id = ?",
                    (audio_profile_id, audio_paper_id),
                )
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, 'question', 'A', 1, 1)
                    """,
                    (audio_unit_id,),
                )
                attach_listening_assets(
                    connection,
                    audio_paper_id,
                    [audio],
                    [audio.name],
                )

                set_active_profile_id(connection, text_profile_id)
                text_overview = dashboard(connection)
                set_active_profile_id(connection, audio_profile_id)
                audio_overview = dashboard(connection)
            finally:
                set_active_profile_id(connection, original_profile_id)

        self.assertEqual(
            set(text_overview["unit_type_counts"]),
            {"cloze", "reading", "part_b"},
        )
        self.assertNotIn("listening", text_overview["unit_type_counts"])
        self.assertEqual(set(audio_overview["unit_type_counts"]), {"listening"})
        self.assertEqual(audio_overview["paper_type_counts"]["listening"], 1)

    def test_random_practice_ignores_units_without_questions(self) -> None:
        from backend.app.database import connect
        from backend.app.schemas import PracticeCreate
        from backend.app.services.listening import attach_listening_assets
        from backend.app.services.practice import create_session

        audio = Path(self.temp.name) / "single-section.mp3"
        audio.write_bytes(b"ID3-single-section")
        with connect() as connection:
            paper_id, unit_id = self._paper_with_unit(connection, year=2095)
            connection.execute(
                """
                INSERT INTO units
                    (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
                VALUES (?, 'listening', 'passage', 'empty section', 2, '', '{}')
                """,
                (paper_id,),
            )
            connection.execute(
                """
                INSERT INTO questions
                    (unit_id, number, stem, answer, score, sequence)
                VALUES (?, 1, 'question', 'A', 1, 1)
                """,
                (unit_id,),
            )
            attach_listening_assets(connection, paper_id, [audio], [audio.name])
            session = create_session(
                connection,
                PracticeCreate(
                    mode="random",
                    paper_id=paper_id,
                    unit_type="listening",
                    selection_scope="unit",
                    count=1,
                    shuffle_options=True,
                ),
            )

        self.assertEqual(session["units"][0]["id"], unit_id)
        self.assertEqual(session["progress"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
