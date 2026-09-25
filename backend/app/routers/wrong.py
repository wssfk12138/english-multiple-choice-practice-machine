from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..database import get_active_profile_id, get_db
from ..services.trash import trash_wrong_units
from ..services.study_todos import frequent_question_ids


router = APIRouter(prefix="/wrong", tags=["wrong"])


class WrongArchiveRequest(BaseModel):
    unit_ids: list[int] = Field(default_factory=list)


@router.get("")
def list_wrong(
    view: Literal["current", "mastered", "all", "frequent"] = Query("current"),
    connection: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    profile_id = get_active_profile_id(connection)
    rows = connection.execute(
        """
        SELECT units.id AS unit_id, units.title AS unit_title,
               units.unit_type, papers.year,
               json_extract(units.shared_data, '$.content_revision') AS content_revision,
               COUNT(DISTINCT wrong_current_questions.question_id) AS current_count,
               COUNT(DISTINCT wrong_retry_rounds.id) AS retry_count
        FROM units
        JOIN papers ON papers.id = units.paper_id
        LEFT JOIN wrong_current_questions
          ON wrong_current_questions.unit_id = units.id
         AND wrong_current_questions.deleted_at IS NULL
        LEFT JOIN wrong_retry_rounds
          ON wrong_retry_rounds.unit_id = units.id
         AND wrong_retry_rounds.deleted_at IS NULL
        WHERE papers.profile_id = ? AND papers.deleted_at IS NULL
        GROUP BY units.id
        HAVING current_count > 0 OR retry_count > 0
        ORDER BY papers.year DESC, units.sequence, units.id
        """,
        (profile_id,),
    ).fetchall()
    frequent_ids = set(frequent_question_ids(connection)) if view == "frequent" else None
    result: list[dict] = []
    for row in rows:
        current_count = int(row["current_count"])
        retry_count = int(row["retry_count"])
        if view == "current" and current_count == 0:
            continue
        if view == "mastered" and (current_count > 0 or retry_count == 0):
            continue
        current_ids = [
            int(item["question_id"])
            for item in connection.execute(
                """
                SELECT question_id FROM wrong_current_questions
                WHERE unit_id = ? AND deleted_at IS NULL
                ORDER BY question_id
                """,
                (row["unit_id"],),
            ).fetchall()
        ]
        if frequent_ids is not None:
            current_ids = [qid for qid in current_ids if qid in frequent_ids]
            if not current_ids:
                continue
        round_rows = connection.execute(
            """
            SELECT id, round_number, question_count, correct_count, wrong_count,
                   submitted_at,
                   CASE WHEN question_count > 0
                     THEN ROUND(correct_count * 100.0 / question_count, 1)
                     ELSE 0 END AS accuracy
            FROM wrong_retry_rounds
            WHERE unit_id = ? AND deleted_at IS NULL
            ORDER BY submitted_at, id
            """,
            (row["unit_id"],),
        ).fetchall()
        rounds = []
        for retry_round in round_rows:
            remaining_ids = [
                int(item["question_id"])
                for item in connection.execute(
                    """
                    SELECT question_id FROM wrong_retry_round_questions
                    WHERE round_id = ? AND is_correct = 0
                    ORDER BY question_id
                    """,
                    (retry_round["id"],),
                ).fetchall()
            ]
            rounds.append({
                **dict(retry_round),
                "remaining_question_ids": remaining_ids,
            })
        result.append({
            **dict(row),
            "current_count": len(current_ids),
            "current_question_ids": current_ids,
            "is_mastered": current_count == 0,
            "rounds": rounds,
        })
    return result


@router.post("/archive-delete")
def archive_wrong_units(
    request: WrongArchiveRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = trash_wrong_units(connection, request.unit_ids)
        connection.commit()
        return result
    except ValueError as exc:
        connection.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.put("/{question_id}/frequent")
def mark_frequent(
    question_id: int,
    enabled: bool = True,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, bool]:
    cursor = connection.execute(
        "UPDATE wrong_stats SET manually_frequent = ? WHERE question_id = ?",
        (int(enabled), question_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "错题不存在")
    connection.commit()
    return {"updated": True}
