"""Install the public starter question banks on first launch.

Bundled packages are ordinary ESQ files.  Keeping installation on top of the
normal ESQ publisher means the starter data follows exactly the same schema,
answer handling, and label import rules as a user-imported package.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ..config import BUNDLED_BANK_DIR
from ..database import connect
from .esq import load_esq_package, publish_package


BUNDLED_BANKS: tuple[tuple[str, str], ...] = (
    ("postgraduate-english-one.esq", "考研英语一"),
    ("postgraduate-english-two.esq", "考研英语二"),
)


def _read_package_identity(path: Path) -> tuple[str, str]:
    """Read only the manifest to get (package_id, content_version).

    Avoids fully parsing and validating a large ESQ package on every startup
    when it is already installed.
    """
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return str(manifest["packageId"]), str(manifest["contentVersion"])


def _get_or_create_profile(connection: Any, name: str) -> int:
    row = connection.execute(
        """
        SELECT id
        FROM question_bank_profiles
        WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if row:
        return int(row["id"])
    cursor = connection.execute(
        "INSERT INTO question_bank_profiles(name, description, is_default) VALUES (?, ?, 0)",
        (name, "随程序提供的公开入门题库"),
    )
    return int(cursor.lastrowid)


def _conflict_resolutions(
    connection: Any,
    profile_id: int,
    package: dict[str, Any],
) -> dict[str, str]:
    """Only replace papers that were previously installed by this package."""

    package_id = package["manifest"]["packageId"]
    resolutions: dict[str, str] = {}
    for paper in package["papers"]:
        paper_key = str(paper["paperKey"])
        existing = connection.execute(
            """
            SELECT package_id
            FROM papers
            WHERE profile_id = ? AND external_key = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            (profile_id, paper_key),
        ).fetchone()
        if existing and existing["package_id"] != package_id:
            resolutions[paper_key] = "keep_existing"
    return resolutions


def install_bundled_question_banks() -> list[dict[str, Any]]:
    """Install starter banks once, returning a compact status summary.

    The package identity and content version are the idempotency key. Existing
    user data is never replaced, and a partially failed package is rolled back.
    """

    results: list[dict[str, Any]] = []
    for filename, profile_name in BUNDLED_BANKS:
        package_path = BUNDLED_BANK_DIR / filename
        if not package_path.is_file():
            raise RuntimeError(f"内置题库文件缺失：{filename}")
        # Fast path: read only the manifest to check whether this package
        # version is already installed, skipping the full parse on later runs.
        package_id, content_version = _read_package_identity(package_path)
        with connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM question_bank_packages WHERE package_id = ? AND content_version = ? LIMIT 1",
                (package_id, content_version),
            ).fetchone()
        if existing:
            results.append(
                {
                    "packageId": package_id,
                    "contentVersion": content_version,
                    "status": "already_installed",
                }
            )
            continue
        package = load_esq_package(package_path)
        with connect() as connection:
            profile_id = _get_or_create_profile(connection, profile_name)
            resolutions = _conflict_resolutions(connection, profile_id, package)
            result = publish_package(
                connection,
                package,
                package_path,
                resolutions,
                import_ai_labels=True,
                profile_id=profile_id,
            )
            connection.commit()
            results.append(
                {
                    "packageId": package_id,
                    "contentVersion": content_version,
                    "status": "installed",
                    "profileId": profile_id,
                    "paperCount": len(result.get("papers", [])),
                    "questionCount": sum(
                        int(item.get("questionCount", 0) or 0)
                        for item in result.get("papers", [])
                    ),
                    "labelsImported": int(result.get("labelsImported", 0) or 0),
                }
            )
    return results
