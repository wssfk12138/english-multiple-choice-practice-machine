from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..database import get_active_profile_id, get_db
from ..services.listening import listening_unit_has_audio_sql


router = APIRouter(tags=["dashboard"])


@router.get("/startup")
@router.get("/overview", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
def dashboard(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    profile_id = get_active_profile_id(connection)
    profile = connection.execute(
        "SELECT id, name FROM question_bank_profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    paper_count = connection.execute(
        """
        SELECT COUNT(*) AS count FROM papers
        WHERE status = 'published' AND profile_id = ? AND deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()["count"]
    unit_count = connection.execute(
        """
        SELECT COUNT(*) AS count FROM units
        JOIN papers ON papers.id = units.paper_id
        WHERE papers.profile_id = ? AND papers.deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()["count"]
    question_count = connection.execute(
        """
        SELECT COUNT(*) AS count FROM questions
        JOIN units ON units.id = questions.unit_id
        JOIN papers ON papers.id = units.paper_id
        WHERE papers.profile_id = ? AND papers.deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()["count"]
    wrong_count = connection.execute(
        """
        SELECT COUNT(*) AS count FROM wrong_stats
        JOIN questions ON questions.id = wrong_stats.question_id
        JOIN units ON units.id = questions.unit_id
        JOIN papers ON papers.id = units.paper_id
        WHERE wrong_stats.wrong_count > 0
          AND papers.profile_id = ? AND papers.deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()["count"]
    frequent_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM wrong_stats
        JOIN questions ON questions.id = wrong_stats.question_id
        JOIN units ON units.id = questions.unit_id
        JOIN papers ON papers.id = units.paper_id
        WHERE (
              manually_frequent = 1
           OR wrong_count >= 3
           OR (
                json_array_length(recent_results) >= 5
                AND (
                    SELECT COUNT(*)
                    FROM json_each(recent_results)
                    WHERE value = 0
                ) >= 3
           ))
          AND papers.profile_id = ? AND papers.deleted_at IS NULL
        """,
        (profile_id,),
    ).fetchone()["count"]
    recent = connection.execute(
        """
        SELECT practice_sessions.id, practice_sessions.mode,
               practice_sessions.status, practice_sessions.started_at,
               practice_sessions.submitted_at, practice_sessions.score,
               practice_sessions.max_score, papers.year
        FROM practice_sessions
        LEFT JOIN papers ON papers.id = practice_sessions.paper_id
        WHERE papers.profile_id = ? OR practice_sessions.paper_id IS NULL
        ORDER BY practice_sessions.id DESC
        LIMIT 5
        """,
        (profile_id,),
    ).fetchall()
    listening_audio_condition = listening_unit_has_audio_sql("units")
    unit_type_counts = {
        str(row["unit_type"]): int(row["count"])
        for row in connection.execute(
            f"""
            SELECT units.unit_type, COUNT(*) AS count
            FROM units
            JOIN papers ON papers.id = units.paper_id
            JOIN questions ON questions.unit_id = units.id
            WHERE papers.profile_id = ?
              AND papers.status = 'published'
              AND papers.deleted_at IS NULL
              AND (units.unit_type <> 'listening' OR ({listening_audio_condition}))
            GROUP BY units.unit_type
            """,
            (profile_id,),
        ).fetchall()
    }
    paper_type_counts = {
        str(row["unit_type"]): int(row["count"])
        for row in connection.execute(
            f"""
            SELECT units.unit_type, COUNT(DISTINCT papers.id) AS count
            FROM units
            JOIN papers ON papers.id = units.paper_id
            JOIN questions ON questions.unit_id = units.id
            WHERE papers.profile_id = ?
              AND papers.status = 'published'
              AND papers.deleted_at IS NULL
              AND (units.unit_type <> 'listening' OR ({listening_audio_condition}))
            GROUP BY units.unit_type
            """,
            (profile_id,),
        ).fetchall()
    }
    return {
        "active_profile": dict(profile) if profile else None,
        "paper_count": paper_count,
        "unit_count": unit_count,
        "question_count": question_count,
        "wrong_count": wrong_count,
        "frequent_count": frequent_count,
        "unit_type_counts": unit_type_counts,
        "paper_type_counts": paper_type_counts,
        "recent_sessions": [dict(row) for row in recent],
    }


@router.get("/study-todos")
def get_study_todos(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    from ..services.study_todos import study_todos
    return study_todos(connection)
