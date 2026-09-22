from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_active_profile_id, get_db
from ..services.trash import trash_wrong_units
from pydantic import BaseModel, Field


router = APIRouter(prefix="/wrong", tags=["wrong"])


class WrongArchiveRequest(BaseModel):
    unit_ids: list[int] = Field(min_length=1, max_length=500)


@router.post('/archive-delete')
def archive_wrong(request: WrongArchiveRequest, connection: sqlite3.Connection = Depends(get_db)):
    try:
        result = trash_wrong_units(connection, request.unit_ids)
        connection.commit()
        return result
    except ValueError as error:
        connection.rollback()
        raise HTTPException(400, str(error)) from error


@router.get("")
def list_wrong(connection: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    profile_id = get_active_profile_id(connection)
    rows = connection.execute(
        """
        SELECT questions.id AS question_id, questions.number, questions.stem,
               units.id AS unit_id, units.title AS unit_title, units.unit_type,
               papers.year, wrong_stats.*,
               json_extract(units.shared_data, '$.content_revision') AS content_revision
        FROM wrong_stats
        JOIN questions ON questions.id = wrong_stats.question_id
        JOIN units ON units.id = questions.unit_id
        JOIN papers ON papers.id = units.paper_id
        WHERE wrong_stats.wrong_count > 0
          AND papers.profile_id = ?
          AND papers.deleted_at IS NULL
        ORDER BY wrong_stats.manually_frequent DESC,
                 wrong_stats.wrong_count DESC,
                 wrong_stats.last_wrong_at DESC
        """,
        (profile_id,),
    ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        recent = json.loads(payload.pop("recent_results") or "[]")
        recent_wrong = sum(not value for value in recent)
        payload["recent_results"] = recent
        payload["is_frequent"] = bool(
            payload["manually_frequent"]
            or payload["wrong_count"] >= 3
            or (len(recent) >= 5 and recent_wrong >= 3)
        )
        result.append(payload)
    return result


@router.put("/{question_id}/frequent")
def mark_frequent(
    question_id: int,
    enabled: bool = True,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, bool]:
    cursor = connection.execute(
        """
        UPDATE wrong_stats
        SET manually_frequent = ?
        WHERE question_id = ?
        """,
        (int(enabled), question_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "错题不存在")
    connection.commit()
    return {"updated": True}
