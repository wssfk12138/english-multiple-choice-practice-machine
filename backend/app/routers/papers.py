from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_active_profile_id, get_db
from ..services.trash import trash_paper


router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("")
def list_papers(connection: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    profile_id = get_active_profile_id(connection)
    connection.execute(
        """
        UPDATE practice_sessions SET status = 'abandoned', updated_at = CURRENT_TIMESTAMP
        WHERE mode = 'paper' AND status = 'active'
          AND NOT EXISTS (SELECT 1 FROM practice_answers pa
            WHERE pa.session_id = practice_sessions.id
              AND TRIM(COALESCE(pa.user_answer, '')) <> '')
          AND NOT EXISTS (SELECT 1 FROM practice_unit_submissions pus
            WHERE pus.session_id = practice_sessions.id)
          AND COALESCE(practice_sessions.updated_at, practice_sessions.started_at)
            < datetime('now', '-7 days')
        """
    )
    connection.commit()
    rows = connection.execute(
        """
        SELECT papers.*,
               COUNT(DISTINCT units.id) AS unit_count,
               COUNT(questions.id) AS question_count
        FROM papers
        LEFT JOIN units ON units.paper_id = papers.id
        LEFT JOIN questions ON questions.unit_id = units.id
        WHERE papers.profile_id = ? AND papers.deleted_at IS NULL
        GROUP BY papers.id
        ORDER BY papers.year DESC, papers.title
        """,
        (profile_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@router.get("/{paper_id}")
def get_paper(
    paper_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    profile_id = get_active_profile_id(connection)
    paper = connection.execute(
        """
        SELECT * FROM papers
        WHERE id = ? AND profile_id = ? AND deleted_at IS NULL
        """,
        (paper_id, profile_id),
    ).fetchone()
    if paper is None:
        raise HTTPException(404, "试卷不存在或不属于当前题库配置")
    units = connection.execute(
        """
        SELECT units.*,
               COUNT(questions.id) AS question_count,
               COALESCE(SUM(questions.score), 0) AS max_score
        FROM units
        LEFT JOIN questions ON questions.unit_id = units.id
        WHERE units.paper_id = ?
        GROUP BY units.id
        ORDER BY units.sequence
        """,
        (paper_id,),
    ).fetchall()
    return {**dict(paper), "units": [dict(row) for row in units]}


@router.delete("/{paper_id}")
def delete_paper(
    paper_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = trash_paper(connection, paper_id)
        connection.commit()
        return {"trashed": True, **result}
    except ValueError as error:
        connection.rollback()
        raise HTTPException(404, str(error)) from error
