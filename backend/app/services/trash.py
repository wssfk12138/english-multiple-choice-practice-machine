from __future__ import annotations

from .wrong_analysis_history import archive_reports, purge_orphan_reports

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..database import get_active_profile_id, new_trash_batch


RESOURCE_TABLES = {
    "profile": "question_bank_profiles",
    "paper": "papers",
    "import_job": "import_jobs",
}


def _trash_row(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    resource_type: str,
    resource_id: int,
    resource_name: str,
    profile_id: int | None,
    purge_after: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO trash_entries
            (deletion_batch_id, resource_type, resource_id, resource_name,
             profile_id, metadata, purge_after)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            resource_type,
            resource_id,
            resource_name,
            profile_id,
            json.dumps(metadata or {}, ensure_ascii=False),
            purge_after,
        ),
    )


def trash_paper(connection: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, profile_id, year, title, status FROM papers
        WHERE id = ? AND deleted_at IS NULL
        """,
        (paper_id,),
    ).fetchone()
    if row is None:
        raise ValueError("试卷不存在或已经在回收站")
    batch_id, purge_after = new_trash_batch()
    connection.execute(
        "UPDATE papers SET deleted_at = CURRENT_TIMESTAMP, status = 'trashed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (paper_id,),
    )
    _trash_row(
        connection,
        batch_id=batch_id,
        resource_type="paper",
        resource_id=paper_id,
        resource_name=f"{row['year']} 年 {row['title']}",
        profile_id=int(row["profile_id"]),
        purge_after=purge_after,
        metadata={"previous_status": row["status"]},
    )
    return {"batch_id": batch_id, "purge_after": purge_after}


def trash_import_job(connection: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, profile_id, filename, status
        FROM import_jobs
        WHERE id = ? AND deleted_at IS NULL
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError("导入草稿不存在或已经在回收站")
    if row["status"] == "published":
        raise ValueError("已发布的导入记录不能按草稿删除")
    batch_id, purge_after = new_trash_batch()
    connection.execute(
        "UPDATE import_jobs SET deleted_at = CURRENT_TIMESTAMP, status = 'trashed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job_id,),
    )
    _trash_row(
        connection,
        batch_id=batch_id,
        resource_type="import_job",
        resource_id=job_id,
        resource_name=row["filename"],
        profile_id=int(row["profile_id"]),
        purge_after=purge_after,
        metadata={"previous_status": row["status"]},
    )
    return {"batch_id": batch_id, "purge_after": purge_after}


def trash_wrong_units(connection, unit_ids):
    ids = sorted({int(unit_id) for unit_id in unit_ids if int(unit_id) > 0})
    if not ids:
        raise ValueError("请选择需要删除的篇目")
    profile_id = get_active_profile_id(connection)
    batch_id, purge_after = new_trash_batch()
    archived = 0
    for unit_id in ids:
        unit = connection.execute(
            """
            SELECT units.id, units.title
            FROM units JOIN papers ON papers.id = units.paper_id
            WHERE units.id = ? AND papers.profile_id = ?
              AND papers.deleted_at IS NULL
            """,
            (unit_id, profile_id),
        ).fetchone()
        if unit is None:
            continue
        current = [
            dict(row) for row in connection.execute(
                "SELECT * FROM wrong_current_questions WHERE unit_id = ?",
                (unit_id,),
            ).fetchall()
        ]
        rounds = [
            dict(row) for row in connection.execute(
                "SELECT * FROM wrong_retry_rounds WHERE unit_id = ?",
                (unit_id,),
            ).fetchall()
        ]
        round_ids = [int(row["id"]) for row in rounds]
        round_questions: list[dict[str, Any]] = []
        if round_ids:
            placeholders = ",".join("?" for _ in round_ids)
            round_questions = [
                dict(row) for row in connection.execute(
                    f"SELECT * FROM wrong_retry_round_questions WHERE round_id IN ({placeholders})",
                    round_ids,
                ).fetchall()
            ]
        state_row = connection.execute(
            "SELECT * FROM wrong_analysis_states WHERE unit_id = ?", (unit_id,)
        ).fetchone()
        _trash_row(
            connection,
            batch_id=batch_id,
            resource_type="wrong_archive",
            resource_id=unit_id,
            resource_name=str(unit["title"]),
            profile_id=profile_id,
            purge_after=purge_after,
            metadata={
                "current": current,
                "rounds": rounds,
                "round_questions": round_questions,
                "state": dict(state_row) if state_row else None,
            },
        )
        trash_id = connection.execute("SELECT id FROM trash_entries WHERE deletion_batch_id=? AND resource_type='wrong_archive' AND resource_id=?", (batch_id, unit_id)).fetchone()[0]
        archive_reports(connection, unit_id, trash_id)
        connection.execute(
            "DELETE FROM wrong_current_questions WHERE unit_id = ?", (unit_id,)
        )
        connection.execute(
            "DELETE FROM wrong_retry_rounds WHERE unit_id = ?", (unit_id,)
        )
        connection.execute(
            "DELETE FROM wrong_analysis_states WHERE unit_id = ?", (unit_id,)
        )
        archived += 1
    if archived == 0:
        raise ValueError("未选篇目不存在或已被删除")
    return {
        "archived": archived,
        "batch_id": batch_id,
        "purge_after": purge_after,
    }


def trash_profile(connection: sqlite3.Connection, profile_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, name FROM question_bank_profiles
        WHERE id = ? AND deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ValueError("题库配置不存在或已经在回收站")
    active_id = get_active_profile_id(connection)
    candidates = connection.execute(
        """
        SELECT id FROM question_bank_profiles
        WHERE deleted_at IS NULL AND id <> ?
        ORDER BY is_default DESC, updated_at DESC, id
        """,
        (profile_id,),
    ).fetchall()
    if not candidates:
        name = "默认题库"
        suffix = 2
        while connection.execute(
            "SELECT 1 FROM question_bank_profiles WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL",
            (name,),
        ).fetchone():
            name = f"默认题库（{suffix}）"
            suffix += 1
        cursor = connection.execute(
            "INSERT INTO question_bank_profiles(name, is_default) VALUES (?, 1)",
            (name,),
        )
        candidates = [{"id": int(cursor.lastrowid)}]
    batch_id, purge_after = new_trash_batch()
    paper_rows = connection.execute(
        """
        SELECT id, year, title, status FROM papers
        WHERE profile_id = ? AND deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchall()
    job_rows = connection.execute(
        """
        SELECT id, filename, status FROM import_jobs
        WHERE profile_id = ? AND deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchall()
    connection.execute(
        "UPDATE question_bank_profiles SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (profile_id,),
    )
    for paper in paper_rows:
        connection.execute(
            "UPDATE papers SET deleted_at = CURRENT_TIMESTAMP, status = 'trashed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (paper["id"],),
        )
        _trash_row(
            connection,
            batch_id=batch_id,
            resource_type="paper",
            resource_id=int(paper["id"]),
            resource_name=f"{paper['year']} 年 {paper['title']}",
            profile_id=profile_id,
            purge_after=purge_after,
            metadata={
                "parent_profile_id": profile_id,
                "previous_status": paper["status"],
            },
        )
    for job in job_rows:
        connection.execute(
            "UPDATE import_jobs SET deleted_at = CURRENT_TIMESTAMP, status = 'trashed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )
        _trash_row(
            connection,
            batch_id=batch_id,
            resource_type="import_job",
            resource_id=int(job["id"]),
            resource_name=job["filename"],
            profile_id=profile_id,
            purge_after=purge_after,
            metadata={
                "parent_profile_id": profile_id,
                "previous_status": job["status"],
            },
        )
    _trash_row(
        connection,
        batch_id=batch_id,
        resource_type="profile",
        resource_id=profile_id,
        resource_name=row["name"],
        profile_id=profile_id,
        purge_after=purge_after,
        metadata={"active_profile": active_id == profile_id},
    )
    if active_id == profile_id:
        connection.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES ('active_question_bank_profile_id', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(candidates[0]["id"]),),
        )
    return {
        "batch_id": batch_id,
        "purge_after": purge_after,
        "moved_papers": len(paper_rows),
        "moved_import_jobs": len(job_rows),
    }


def list_trash(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT t.*, p.name AS profile_name
        FROM trash_entries AS t
        LEFT JOIN question_bank_profiles AS p ON p.id = t.profile_id
        WHERE t.restored_at IS NULL
          AND (
            t.resource_type = 'profile'
            OR NOT EXISTS (
                SELECT 1 FROM trash_entries AS root
                WHERE root.deletion_batch_id = t.deletion_batch_id
                  AND root.resource_type = 'profile'
                  AND root.restored_at IS NULL
            )
          )
        ORDER BY t.purge_after, t.deleted_at DESC, t.id DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["metadata"] = json.loads(payload.pop("metadata") or "{}")
        result.append(payload)
    return result


def _restore_one(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    target_profile_id: int | None,
) -> None:
    resource_type = row["resource_type"]
    resource_id = int(row["resource_id"])
    if resource_type == "profile":
        profile = connection.execute(
            "SELECT name FROM question_bank_profiles WHERE id = ?",
            (resource_id,),
        ).fetchone()
        if profile is None:
            raise ValueError("待恢复的题库配置已不存在")
        base_name = str(profile["name"])
        restored_name = base_name
        suffix = 1
        while connection.execute(
            """
            SELECT 1 FROM question_bank_profiles
            WHERE name = ? COLLATE NOCASE
              AND deleted_at IS NULL
              AND id <> ?
            """,
            (restored_name, resource_id),
        ).fetchone():
            suffix += 1
            restored_name = (
                f"{base_name}（恢复）"
                if suffix == 2
                else f"{base_name}（恢复 {suffix}）"
            )
        connection.execute(
            """
            UPDATE question_bank_profiles
            SET name = ?, deleted_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (restored_name, resource_id),
        )
    elif resource_type == "paper":
        preferred_profile_id = target_profile_id or row["profile_id"]
        profile = connection.execute(
            "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
            (preferred_profile_id,),
        ).fetchone()
        profile_id = (
            int(preferred_profile_id)
            if profile and preferred_profile_id is not None
            else get_active_profile_id(connection)
        )
        metadata = json.loads(row["metadata"] or "{}")
        previous_status = str(metadata.get("previous_status") or "published")
        paper = connection.execute(
            "SELECT title, external_key FROM papers WHERE id = ?",
            (resource_id,),
        ).fetchone()
        if paper is None:
            raise ValueError("待恢复的试卷已不存在")
        title = str(paper["title"])
        external_key = str(paper["external_key"] or "")
        duplicate = connection.execute(
            """
            SELECT 1 FROM papers
            WHERE profile_id = ?
              AND deleted_at IS NULL
              AND id <> ?
              AND (
                title = ? COLLATE NOCASE
                OR (? <> '' AND external_key = ?)
              )
            LIMIT 1
            """,
            (profile_id, resource_id, title, external_key, external_key),
        ).fetchone()
        if duplicate:
            base_title = title
            suffix = 2
            candidate = f"{base_title}（恢复）"
            while connection.execute(
                """
                SELECT 1 FROM papers
                WHERE profile_id = ? AND title = ? COLLATE NOCASE
                  AND deleted_at IS NULL AND id <> ?
                """,
                (profile_id, candidate, resource_id),
            ).fetchone():
                suffix += 1
                candidate = f"{base_title}（恢复 {suffix}）"
            title = candidate
            if external_key:
                external_key = f"{external_key}:restored:{resource_id}:{suffix}"
        connection.execute(
            """
            UPDATE papers
            SET profile_id = ?, title = ?, external_key = ?,
                deleted_at = NULL, status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (profile_id, title, external_key or None, previous_status, resource_id),
        )
    elif resource_type == "import_job":
        preferred_profile_id = target_profile_id or row["profile_id"]
        profile = connection.execute(
            "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
            (preferred_profile_id,),
        ).fetchone()
        profile_id = (
            int(preferred_profile_id)
            if profile and preferred_profile_id is not None
            else get_active_profile_id(connection)
        )
        metadata = json.loads(row["metadata"] or "{}")
        previous_status = str(metadata.get("previous_status") or "draft")
        connection.execute(
            """
            UPDATE import_jobs
            SET profile_id = ?, deleted_at = NULL, status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (profile_id, previous_status, resource_id),
        )
    elif resource_type == "wrong_archive":
        if connection.execute('SELECT 1 FROM units WHERE id=?', (resource_id,)).fetchone() is None:
            raise ValueError('原篇目已被删除，无法恢复错题本')
        connection.execute('DELETE FROM wrong_analysis_hidden WHERE trash_id=?', (row['id'],))
        metadata = json.loads(row["metadata"] or "{}")
        restored_round_ids = {}
        for retry_round in metadata.get("rounds", []):
            columns = (
                "id", "unit_id", "session_id", "round_number", "question_count",
                "correct_count", "wrong_count", "submitted_at", "deleted_at",
            )
            old_id = retry_round['id']
            if connection.execute('SELECT 1 FROM wrong_retry_rounds WHERE id=?', (old_id,)).fetchone():
                retry_round['id'] = None
            if connection.execute('SELECT 1 FROM wrong_retry_rounds WHERE unit_id=? AND round_number=?', (resource_id, retry_round['round_number'])).fetchone():
                retry_round['round_number'] = connection.execute('SELECT COALESCE(MAX(round_number),0)+1 FROM wrong_retry_rounds WHERE unit_id=?', (resource_id,)).fetchone()[0]
            cursor = connection.execute(
                f"INSERT INTO wrong_retry_rounds ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(retry_round.get(column) for column in columns),
            )
            restored_round_ids[old_id] = cursor.lastrowid
        for question in metadata.get("round_questions", []):
            connection.execute(
                """
                INSERT OR IGNORE INTO wrong_retry_round_questions
                    (round_id, question_id, user_answer, is_correct)
                VALUES (?, ?, ?, ?)
                """,
                (
                    restored_round_ids.get(question.get("round_id"), question.get("round_id")), question.get("question_id"),
                    question.get("user_answer", ""), question.get("is_correct", 0),
                ),
            )
        for question in metadata.get("current", []):
            connection.execute(
                """
                INSERT OR IGNORE INTO wrong_current_questions
                    (unit_id, question_id, since_round_id, deleted_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    question.get("unit_id"), question.get("question_id"),
                    restored_round_ids.get(question.get("since_round_id"), question.get("since_round_id")), question.get("deleted_at"),
                ),
            )
        state = metadata.get("state")
        if state:
            connection.execute(
                """
                INSERT OR IGNORE INTO wrong_analysis_states
                    (unit_id, report_id, analyzed_session_id, analyzed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    state.get("unit_id"), state.get("report_id"),
                    state.get("analyzed_session_id", 0), state.get("analyzed_at"),
                ),
            )
    else:
        raise ValueError("不支持的回收站资源类型")


def restore_trash(
    connection: sqlite3.Connection,
    trash_id: int,
    target_profile_id: int | None = None,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM trash_entries WHERE id = ? AND restored_at IS NULL",
        (trash_id,),
    ).fetchone()
    if row is None:
        raise ValueError("回收站项目不存在")
    group = connection.execute(
        """
        SELECT * FROM trash_entries
        WHERE deletion_batch_id = ? AND restored_at IS NULL
        ORDER BY CASE resource_type WHEN 'profile' THEN 0 ELSE 1 END, id
        """,
        (row["deletion_batch_id"],),
    ).fetchall()
    contains_profile = any(item["resource_type"] == "profile" for item in group)
    effective_target_profile_id = None if contains_profile else target_profile_id
    for item in group:
        _restore_one(connection, item, effective_target_profile_id)
    connection.execute(
        "UPDATE trash_entries SET restored_at = CURRENT_TIMESTAMP WHERE deletion_batch_id = ? AND restored_at IS NULL",
        (row["deletion_batch_id"],),
    )
    return {"restored": True, "batch_id": row["deletion_batch_id"], "count": len(group)}


def _purge_job_files(row: sqlite3.Row) -> None:
    paths: set[str] = set()
    for key in ("stored_path", "answer_stored_path"):
        value = str(row[key] or "").strip() if key in row.keys() else ""
        if value:
            paths.add(value)
    if "parse_context" in row.keys():
        try:
            context = json.loads(row["parse_context"] or "{}")
        except json.JSONDecodeError:
            context = {}
        for key in ("answer_paths", "audio_paths"):
            for value in context.get(key, []):
                cleaned = str(value or "").strip()
                if cleaned:
                    paths.add(cleaned)
    for value in paths:
        Path(value).unlink(missing_ok=True)


def purge_trash(
    connection: sqlite3.Connection,
    trash_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM trash_entries WHERE id = ? AND restored_at IS NULL",
        (trash_id,),
    ).fetchone()
    if row is None:
        raise ValueError("回收站项目不存在")
    group = connection.execute(
        """
        SELECT t.*, j.stored_path, j.answer_stored_path, j.parse_context
        FROM trash_entries AS t
        LEFT JOIN import_jobs AS j
          ON t.resource_type = 'import_job' AND j.id = t.resource_id
        WHERE t.deletion_batch_id = ? AND t.restored_at IS NULL
        ORDER BY CASE t.resource_type
            WHEN 'paper' THEN 0
            WHEN 'import_job' THEN 1
            WHEN 'profile' THEN 2
            ELSE 1 END
        """,
        (row["deletion_batch_id"],),
    ).fetchall()
    for item in group:
        resource_type = item["resource_type"]
        resource_id = int(item["resource_id"])
        if resource_type == "wrong_archive":
            connection.execute('UPDATE wrong_analysis_hidden SET trash_id=NULL WHERE trash_id=?', (item['id'],))
        elif resource_type == "paper":
            connection.execute("DELETE FROM papers WHERE id = ?", (resource_id,))
        elif resource_type == "import_job":
            _purge_job_files(item)
            connection.execute("DELETE FROM import_jobs WHERE id = ?", (resource_id,))
        elif resource_type == "profile":
            connection.execute("DELETE FROM question_bank_profiles WHERE id = ?", (resource_id,))
    connection.execute(
        "DELETE FROM trash_entries WHERE deletion_batch_id = ?",
        (row["deletion_batch_id"],),
    )
    purge_orphan_reports(connection)
    return {"purged": True, "batch_id": row["deletion_batch_id"], "count": len(group)}


def purge_expired(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT id FROM trash_entries
        WHERE restored_at IS NULL AND purge_after <= CURRENT_TIMESTAMP
        ORDER BY id
        """
    ).fetchall()
    count = 0
    for row in rows:
        try:
            purge_trash(connection, int(row["id"]))
            count += 1
        except ValueError:
            continue
    connection.commit()
    return count
