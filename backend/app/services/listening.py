from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..config import QUESTION_BANK_DIR


CONTENT_VERSION = "1.0.0"
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


def listening_unit_has_audio_sql(unit_alias: str = "units") -> str:
    """Return the shared eligibility predicate used by dashboard and practice."""

    return f"""
        json_valid({unit_alias}.shared_data)
        AND json_type({unit_alias}.shared_data, '$.audio_tracks') = 'array'
        AND json_array_length({unit_alias}.shared_data, '$.audio_tracks') > 0
    """


def _safe_audio_name(name: str, index: int, suffix: str) -> str:
    label = Path(name).stem.strip()
    return label[:80] if label else f"听力音频 {index}"


def _track_url(package_id: str, content_version: str, asset_id: str) -> str:
    return (
        "/api/question-banks/assets/"
        f"{package_id}/{content_version}/{asset_id}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relocate_moved_audio_assets(connection: sqlite3.Connection) -> int:
    """Repair audio asset paths after the whole application directory moves."""

    root = QUESTION_BANK_DIR.resolve()
    rows = connection.execute(
        """
        SELECT id, package_id, content_version, stored_path, sha256
        FROM question_bank_assets
        WHERE media_type LIKE 'audio/%'
        """
    ).fetchall()
    repaired = 0
    for row in rows:
        stored_path = Path(row["stored_path"])
        try:
            stored_path.resolve().relative_to(root)
            if stored_path.is_file():
                continue
        except ValueError:
            pass

        asset_root = (
            root / row["package_id"] / row["content_version"] / "assets"
        ).resolve()
        try:
            asset_root.relative_to(root)
        except ValueError:
            continue
        expected = asset_root / "audio" / stored_path.name
        candidates = [expected] if expected.is_file() else []
        if not candidates and asset_root.is_dir():
            candidates = [
                candidate
                for candidate in asset_root.rglob(stored_path.name)
                if candidate.is_file()
            ]
        matches = []
        for candidate in candidates:
            try:
                if _sha256(candidate) == row["sha256"]:
                    matches.append(candidate)
            except OSError:
                continue
        if len(matches) != 1:
            continue
        connection.execute(
            "UPDATE question_bank_assets SET stored_path = ? WHERE id = ?",
            (str(matches[0]), row["id"]),
        )
        repaired += 1
    return repaired


def _assign_tracks_to_units(
    connection: sqlite3.Connection,
    listening_units: list[sqlite3.Row],
    tracks: list[dict[str, Any]],
    package_id: str,
    content_version: str,
) -> None:
    one_track_per_unit = len(tracks) > 1 and len(tracks) == len(listening_units)
    for index, unit in enumerate(listening_units):
        try:
            shared_data = json.loads(unit["shared_data"] or "{}")
        except json.JSONDecodeError:
            shared_data = {}
        unit_tracks = [tracks[index]] if one_track_per_unit else tracks
        shared_data.update(
            {
                "content_package_id": package_id,
                "content_version": content_version,
                "audio_tracks": unit_tracks,
                "audio_mode": (
                    "per_unit"
                    if one_track_per_unit
                    else "continuous"
                    if len(tracks) == 1
                    else "playlist"
                ),
            }
        )
        connection.execute(
            "UPDATE units SET shared_data = ? WHERE id = ?",
            (json.dumps(shared_data, ensure_ascii=False), unit["id"]),
        )


def attach_listening_assets(
    connection: sqlite3.Connection,
    paper_id: int,
    audio_paths: Iterable[Path],
    audio_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Persist imported audio and associate it with listening units.

    A single file remains a complete track shared by all listening sections.
    When the number of files matches the number of sections, files are
    associated one-to-one in sequence order; no audio is cut into clips.
    """

    listening_units = connection.execute(
        """
        SELECT id, shared_data
        FROM units
        WHERE paper_id = ? AND unit_type = 'listening'
        ORDER BY sequence
        """,
        (paper_id,),
    ).fetchall()
    if not listening_units:
        return []

    paths = [Path(path) for path in audio_paths]
    names = list(audio_names or [])
    valid: list[tuple[Path, str, str]] = []
    for index, source in enumerate(paths, 1):
        suffix = source.suffix.lower()
        if suffix not in MEDIA_TYPES or not source.is_file() or source.stat().st_size <= 0:
            continue
        original_name = names[index - 1] if index - 1 < len(names) else source.name
        valid.append((source, original_name, suffix))
    if not valid:
        return []

    package_id = f"local.paper-{paper_id}"
    target_dir = (
        QUESTION_BANK_DIR
        / package_id
        / CONTENT_VERSION
        / "assets"
        / "audio"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    tracks: list[dict[str, Any]] = []

    for index, (source, original_name, suffix) in enumerate(valid, 1):
        asset_id = f"listening.track.{index}"
        target = target_dir / f"track-{index}{suffix}"
        shutil.copy2(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        label = _safe_audio_name(original_name, index, suffix)
        metadata = {
            "assetId": asset_id,
            "mediaType": MEDIA_TYPES[suffix],
            "originalName": original_name,
            "label": label,
            "size": target.stat().st_size,
        }
        connection.execute(
            """
            INSERT INTO question_bank_assets
                (package_id, content_version, asset_id, stored_path,
                 media_type, sha256, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, content_version, asset_id) DO UPDATE SET
                stored_path = excluded.stored_path,
                media_type = excluded.media_type,
                sha256 = excluded.sha256,
                metadata = excluded.metadata
            """,
            (
                package_id,
                CONTENT_VERSION,
                asset_id,
                str(target),
                MEDIA_TYPES[suffix],
                digest,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        tracks.append(
            {
                "asset_id": asset_id,
                "label": label,
                "media_type": MEDIA_TYPES[suffix],
                "url": _track_url(package_id, CONTENT_VERSION, asset_id),
            }
        )

    connection.execute(
        """
        INSERT INTO question_bank_packages
            (package_id, content_version, title, publisher, manifest_data,
             source_file, status)
        SELECT ?, ?, title, '本地导入', '{}', COALESCE(source_file, ''), 'published'
        FROM papers WHERE id = ?
        ON CONFLICT(package_id, content_version) DO UPDATE SET
            title = excluded.title,
            source_file = excluded.source_file,
            status = 'published',
            updated_at = CURRENT_TIMESTAMP
        """,
        (package_id, CONTENT_VERSION, paper_id),
    )

    _assign_tracks_to_units(
        connection,
        list(listening_units),
        tracks,
        package_id,
        CONTENT_VERSION,
    )
    return tracks


def _repair_existing_track_assignments(connection: sqlite3.Connection) -> int:
    repaired = 0
    paper_rows = connection.execute(
        """
        SELECT DISTINCT paper_id
        FROM units
        WHERE unit_type = 'listening'
        """
    ).fetchall()
    for paper_row in paper_rows:
        units = connection.execute(
            """
            SELECT id, shared_data
            FROM units
            WHERE paper_id = ? AND unit_type = 'listening'
            ORDER BY sequence, id
            """,
            (paper_row["paper_id"],),
        ).fetchall()
        if len(units) < 2:
            continue
        payloads: list[dict[str, Any]] = []
        for unit in units:
            try:
                payloads.append(json.loads(unit["shared_data"] or "{}"))
            except json.JSONDecodeError:
                payloads.append({})
        if any(payload.get("audio_mode") for payload in payloads):
            continue
        track_lists = [payload.get("audio_tracks") or [] for payload in payloads]
        if not track_lists[0] or any(track_list != track_lists[0] for track_list in track_lists[1:]):
            continue
        tracks = track_lists[0]
        if len(tracks) != len(units):
            continue
        package_id = str(payloads[0].get("content_package_id") or "")
        content_version = str(
            payloads[0].get("content_version") or CONTENT_VERSION
        )
        _assign_tracks_to_units(
            connection,
            list(units),
            tracks,
            package_id,
            content_version,
        )
        repaired += 1
    return repaired


def repair_published_listening_assets(connection: sqlite3.Connection) -> int:
    """Recover audio uploaded by older versions but never attached on publish."""

    repaired = _relocate_moved_audio_assets(connection)
    repaired += _repair_existing_track_assignments(connection)
    jobs = connection.execute(
        """
        SELECT parse_context
        FROM import_jobs
        WHERE status = 'published' AND parse_context IS NOT NULL
        """
    ).fetchall()
    for job in jobs:
        try:
            context = json.loads(job["parse_context"] or "{}")
        except json.JSONDecodeError:
            continue
        paper_ids = [
            int(value)
            for value in context.get("published_paper_ids", [])
            if isinstance(value, int) and value > 0
        ]
        paths = [Path(value) for value in context.get("audio_paths", []) if value]
        names = [str(value) for value in context.get("audio_names", []) if value]
        if not paper_ids or not paths:
            continue
        for paper_id in paper_ids:
            row = connection.execute(
                """
                SELECT shared_data
                FROM units
                WHERE paper_id = ? AND unit_type = 'listening'
                ORDER BY sequence LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            if row is None:
                continue
            try:
                shared_data = json.loads(row["shared_data"] or "{}")
            except json.JSONDecodeError:
                shared_data = {}
            if shared_data.get("audio_tracks"):
                continue
            if attach_listening_assets(connection, paper_id, paths, names):
                repaired += 1
    if repaired:
        connection.commit()
    return repaired
