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
        UPDATE practice_sessions SET status = 'abandoned'
        WHERE mode = 'paper' AND status = 'active'
          AND NOT EXISTS (SELECT 1 FROM practice_answers pa
            WHERE pa.session_id = practice_sessions.id
              AND TRIM(COALESCE(pa.user_answer, '')) <> '')
          AND NOT EXISTS (SELECT 1 FROM practice_unit_submissions pus
            WHERE pus.session_id = practice_sessions.id)
          AND practice_sessions.started_at
            < datetime('now', '-7 days')
        """
    )
    connection.commit()
    rows = connection.execute(
        """
        SELECT papers.*,
               COUNT(DISTINCT units.id) AS unit_count,
               COUNT(questions.id) AS question_count,
               (SELECT practice_sessions.id FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'active'
                  AND practice_sessions.mode = 'paper'
                  AND (EXISTS (SELECT 1 FROM practice_answers pa_progress
                       WHERE pa_progress.session_id = practice_sessions.id
                         AND TRIM(COALESCE(pa_progress.user_answer, '')) <> '')
                    OR EXISTS (SELECT 1 FROM practice_unit_submissions pus_progress
                       WHERE pus_progress.session_id = practice_sessions.id))
                ORDER BY practice_sessions.id DESC LIMIT 1) AS active_session_id,
               (SELECT COUNT(*) FROM practice_unit_submissions
                WHERE practice_unit_submissions.session_id = (
                    SELECT id FROM practice_sessions
                    WHERE paper_id = papers.id AND status = 'active' AND mode = 'paper'
                      AND (EXISTS (SELECT 1 FROM practice_answers pa_progress
                           WHERE pa_progress.session_id = practice_sessions.id
                             AND TRIM(COALESCE(pa_progress.user_answer, '')) <> '')
                        OR EXISTS (SELECT 1 FROM practice_unit_submissions pus_progress
                           WHERE pus_progress.session_id = practice_sessions.id))
                    ORDER BY id DESC LIMIT 1
                )) AS active_done,
               (SELECT practice_sessions.score FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'submitted'
                  AND practice_sessions.mode = 'paper'
                ORDER BY practice_sessions.id DESC LIMIT 1) AS last_score,
               (SELECT practice_sessions.max_score FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'submitted'
                  AND practice_sessions.mode = 'paper'
                ORDER BY practice_sessions.id DESC LIMIT 1) AS last_max_score
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
               (SELECT practice_sessions.id FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'active'
                  AND practice_sessions.mode = 'paper'
                  AND (EXISTS (SELECT 1 FROM practice_answers pa_progress
                       WHERE pa_progress.session_id = practice_sessions.id
                         AND TRIM(COALESCE(pa_progress.user_answer, '')) <> '')
                    OR EXISTS (SELECT 1 FROM practice_unit_submissions pus_progress
                       WHERE pus_progress.session_id = practice_sessions.id))
                ORDER BY practice_sessions.id DESC LIMIT 1) AS active_session_id,
               (SELECT COUNT(*) FROM practice_unit_submissions
                WHERE practice_unit_submissions.session_id = (
                    SELECT id FROM practice_sessions
                    WHERE paper_id = papers.id AND status = 'active' AND mode = 'paper'
                      AND (EXISTS (SELECT 1 FROM practice_answers pa_progress
                           WHERE pa_progress.session_id = practice_sessions.id
                             AND TRIM(COALESCE(pa_progress.user_answer, '')) <> '')
                        OR EXISTS (SELECT 1 FROM practice_unit_submissions pus_progress
                           WHERE pus_progress.session_id = practice_sessions.id))
                    ORDER BY id DESC LIMIT 1
                )) AS active_done,
               (SELECT practice_sessions.score FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'submitted'
                  AND practice_sessions.mode = 'paper'
                ORDER BY practice_sessions.id DESC LIMIT 1) AS last_score,
               (SELECT practice_sessions.max_score FROM practice_sessions
                WHERE practice_sessions.paper_id = papers.id
                  AND practice_sessions.status = 'submitted'
                  AND practice_sessions.mode = 'paper'
                ORDER BY practice_sessions.id DESC LIMIT 1) AS last_max_score,
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
