from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import AnswerUpdate, PracticeCreate
from ..services.practice import (
    IncompleteSubmissionError,
    abandon_session_if_empty,
    create_session,
    get_session,
    save_answer,
    submit_session,
    submit_unit,
)


router = APIRouter(prefix="/practice", tags=["practice"])


def translate_error(error: Exception) -> HTTPException:
    if isinstance(error, IncompleteSubmissionError):
        return HTTPException(
            409,
            {
                "code": "incomplete_submission",
                "message": str(error),
                "unit_id": error.unit_id,
                "unit_title": error.unit_title,
                "question_id": error.question_id,
                "question_number": error.question_number,
            },
        )
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    return HTTPException(400, str(error))


@router.post("/sessions")
def create(
    request: PracticeCreate, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        return create_session(connection, request)
    except (ValueError, LookupError) as error:
        raise translate_error(error) from error


@router.post("/sessions/{session_id}/abandon-if-empty")
def abandon_if_empty(
    session_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    result = abandon_session_if_empty(connection, session_id)
    connection.commit()
    return result


@router.get("/sessions/{session_id}")
def detail(
    session_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        return get_session(connection, session_id)
    except (ValueError, LookupError) as error:
        raise translate_error(error) from error


@router.put("/sessions/{session_id}/answers/{question_id}")
def update_answer(
    session_id: int,
    question_id: int,
    request: AnswerUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, bool]:
    try:
        save_answer(
            connection,
            session_id,
            question_id,
            request.answer,
            request.option_order,
        )
        return {"saved": True}
    except (ValueError, LookupError) as error:
        raise translate_error(error) from error


@router.post("/sessions/{session_id}/submit")
def submit(
    session_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        return submit_session(connection, session_id)
    except (ValueError, LookupError) as error:
        raise translate_error(error) from error


@router.post("/sessions/{session_id}/units/{unit_id}/submit")
def submit_current_unit(
    session_id: int,
    unit_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return submit_unit(connection, session_id, unit_id)
    except (ValueError, LookupError) as error:
        raise translate_error(error) from error
