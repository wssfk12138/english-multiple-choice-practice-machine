from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime
from typing import Any

from ..database import get_active_profile_id
from ..schemas import PracticeCreate
from .questions import parse_json, serialize_unit
from .listening import listening_unit_has_audio_sql


class IncompleteSubmissionError(ValueError):
    def __init__(
        self,
        *,
        unit_id: int,
        unit_title: str,
        question_id: int,
        question_number: int,
    ) -> None:
        super().__init__(f"{unit_title}的第 {question_number} 题还未作答")
        self.unit_id = unit_id
        self.unit_title = unit_title
        self.question_id = question_id
        self.question_number = question_number


def _select_unit_ids(
    connection: sqlite3.Connection, request: PracticeCreate
) -> tuple[list[int], int | None]:
    active_profile_id = get_active_profile_id(connection)
    if request.mode == "paper":
        if request.paper_id is None:
            raise ValueError("按年份练习需要选择试卷")
        paper = connection.execute(
            """
            SELECT id FROM papers
            WHERE id = ? AND profile_id = ? AND deleted_at IS NULL
            """,
            (request.paper_id, active_profile_id),
        ).fetchone()
        if paper is None:
            raise ValueError("试卷不存在或不属于当前题库配置")
        rows = connection.execute(
            """
            SELECT units.id FROM units
            JOIN papers ON papers.id = units.paper_id
            WHERE units.paper_id = ? AND papers.deleted_at IS NULL
            ORDER BY units.sequence
            """,
            (request.paper_id,),
        ).fetchall()
        return [row["id"] for row in rows], request.paper_id

    if request.mode == "unit":
        if not request.unit_ids:
            raise ValueError("请选择练习篇目")
        placeholders = ",".join("?" for _ in request.unit_ids)
        owned = connection.execute(
            f"""
            SELECT COUNT(*) AS count FROM units
            JOIN papers ON papers.id = units.paper_id
            WHERE units.id IN ({placeholders})
              AND papers.profile_id = ?
              AND papers.deleted_at IS NULL
            """,
            [*request.unit_ids, active_profile_id],
        ).fetchone()["count"]
        if owned != len(request.unit_ids):
            raise ValueError("部分篇目不存在或不属于当前题库配置")
        return request.unit_ids, request.paper_id

    if request.mode == "random":
        if request.selection_scope == "paper_unit_type":
            if not request.unit_type:
                raise ValueError("整套题型练习需要指定题型")
            paper_query = """
                SELECT DISTINCT papers.id
                FROM papers
                JOIN units ON units.paper_id = papers.id
                JOIN questions ON questions.unit_id = units.id
                WHERE papers.status = 'published'
                  AND papers.deleted_at IS NULL
                  AND papers.profile_id = ?
                  AND units.unit_type = ?
            """
            paper_params: list[Any] = [active_profile_id, request.unit_type]
            if request.unit_type == "listening":
                paper_query += f" AND ({listening_unit_has_audio_sql('units')})"
            if request.paper_id:
                paper_query += " AND papers.id = ?"
                paper_params.append(request.paper_id)
            paper_rows = connection.execute(paper_query, paper_params).fetchall()
            paper_ids = [int(row["id"]) for row in paper_rows]
            if not paper_ids:
                raise LookupError("当前题库配置中没有符合条件的完整题型")
            selected_paper_id = random.choice(paper_ids)
            unit_rows = connection.execute(
                """
                SELECT id FROM units
                WHERE paper_id = ? AND unit_type = ?
                ORDER BY sequence, id
                """,
                (selected_paper_id, request.unit_type),
            ).fetchall()
            return [int(row["id"]) for row in unit_rows], selected_paper_id

        query = """
            SELECT DISTINCT units.id
            FROM units
            JOIN papers ON papers.id = units.paper_id
            JOIN questions ON questions.unit_id = units.id
            WHERE papers.status = 'published'
              AND papers.deleted_at IS NULL
              AND papers.profile_id = ?
        """
        params: list[Any] = [active_profile_id]
        if request.unit_type:
            query += " AND units.unit_type = ?"
            params.append(request.unit_type)
            if request.unit_type == "listening":
                query += f" AND ({listening_unit_has_audio_sql('units')})"
        if request.paper_id:
            query += " AND units.paper_id = ?"
            params.append(request.paper_id)
        rows = connection.execute(query, params).fetchall()
        ids = [row["id"] for row in rows]
        random.shuffle(ids)
        return ids[: max(1, request.count)], request.paper_id

    if request.mode == "wrong":
        query = """
            SELECT DISTINCT questions.unit_id
            FROM wrong_stats
            JOIN questions ON questions.id = wrong_stats.question_id
            JOIN units ON units.id = questions.unit_id
            JOIN papers ON papers.id = units.paper_id
            WHERE wrong_stats.wrong_count > 0
              AND papers.profile_id = ?
              AND papers.deleted_at IS NULL
        """
        params = [active_profile_id]
        if request.unit_ids:
            placeholders = ",".join("?" for _ in request.unit_ids)
            query += f" AND questions.unit_id IN ({placeholders})"
            params.extend(request.unit_ids)
        if request.question_ids:
            placeholders = ",".join("?" for _ in request.question_ids)
            query += f" AND questions.id IN ({placeholders})"
            params.extend(request.question_ids)
        if request.unit_type:
            query += " AND questions.unit_id IN (SELECT id FROM units WHERE unit_type = ?)"
            params.append(request.unit_type)
        rows = connection.execute(query, params).fetchall()
        ids = [row["unit_id"] for row in rows]
        random.shuffle(ids)
        return ids[: max(1, request.count)], request.paper_id

    raise ValueError("不支持的练习模式")


def _normalize_listening_audio(units: list[dict[str, Any]]) -> None:
    listening_units = [unit for unit in units if unit.get("unit_type") == "listening"]
    if len(listening_units) < 2:
        return
    shared_payloads = [unit.get("shared_data") or {} for unit in listening_units]
    if any(payload.get("audio_mode") for payload in shared_payloads):
        return
    track_lists = [payload.get("audio_tracks") or [] for payload in shared_payloads]
    if not track_lists[0] or any(tracks != track_lists[0] for tracks in track_lists[1:]):
        return
    if len(track_lists[0]) != len(listening_units):
        return
    for index, payload in enumerate(shared_payloads):
        payload["audio_tracks"] = [track_lists[0][index]]
        payload["audio_mode"] = "per_unit"


def create_session(
    connection: sqlite3.Connection, request: PracticeCreate
) -> dict[str, Any]:
    unit_ids, paper_id = _select_unit_ids(connection, request)
    if not unit_ids:
        raise LookupError("当前题库配置中没有已发布且包含题目的练习篇目")

    cursor = connection.execute(
        """
        INSERT INTO practice_sessions (mode, paper_id, unit_ids, shuffle_options)
        VALUES (?, ?, ?, ?)
        """,
        (
            request.mode,
            paper_id,
            json.dumps(unit_ids),
            int(request.shuffle_options),
        ),
    )
    session_id = cursor.lastrowid

    only_by_unit: dict[int, set[int]] = {}
    if request.mode == "wrong":
        placeholders = ",".join("?" for _ in unit_ids)
        rows = connection.execute(
            f"""
            SELECT questions.id, questions.unit_id
            FROM wrong_stats
            JOIN questions ON questions.id = wrong_stats.question_id
            WHERE wrong_stats.wrong_count > 0
              AND questions.unit_id IN ({placeholders})
              {
                  f"AND questions.id IN ({','.join('?' for _ in request.question_ids)})"
                  if request.question_ids
                  else ""
              }
            """,
            [*unit_ids, *request.question_ids],
        ).fetchall()
        for row in rows:
            only_by_unit.setdefault(row["unit_id"], set()).add(row["id"])

    units = []
    for unit_id in unit_ids:
        unit = serialize_unit(
            connection,
            unit_id,
            shuffle_options=request.shuffle_options,
            only_question_ids=only_by_unit.get(unit_id) if request.mode == "wrong" else None,
        )
        units.append(unit)
        for question in unit["questions"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO practice_answers
                    (session_id, question_id, user_answer, option_order)
                VALUES (?, ?, '', ?)
                """,
                (
                    session_id,
                    question["id"],
                    json.dumps(question["option_order"], ensure_ascii=False),
                ),
            )
    _normalize_listening_audio(units)
    connection.commit()
    return {
        "id": session_id,
        "mode": request.mode,
        "paper_id": paper_id,
        "status": "active",
        "shuffle_options": request.shuffle_options,
        "units": units,
        "progress": {"answered": 0, "total": sum(len(unit["questions"]) for unit in units)},
    }


def get_session(connection: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError("练习记录不存在")

    unit_ids = parse_json(session["unit_ids"], [])
    answers = connection.execute(
        """
        SELECT practice_answers.*, questions.unit_id, questions.score AS question_score
        FROM practice_answers
        JOIN questions ON questions.id = practice_answers.question_id
        WHERE practice_answers.session_id = ?
        """,
        (session_id,),
    ).fetchall()
    answer_map = {row["question_id"]: row for row in answers}
    order_map = {
        row["question_id"]: parse_json(row["option_order"], []) for row in answers
    }
    submission_rows = connection.execute(
        """
        SELECT unit_id, submitted_at, score, max_score
        FROM practice_unit_submissions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()
    submission_map = {row["unit_id"]: row for row in submission_rows}

    units = []
    only_by_unit: dict[int, set[int]] = {}
    if session["mode"] == "wrong":
        # answers already carries questions.unit_id from the JOIN above.
        for row in answers:
            only_by_unit.setdefault(row["unit_id"], set()).add(row["question_id"])
    for unit_id in unit_ids:
        unit = serialize_unit(
            connection,
            unit_id,
            shuffle_options=bool(session["shuffle_options"]),
            answer_orders=order_map,
            include_answers=session["status"] == "submitted",
            only_question_ids=only_by_unit.get(unit_id)
            if session["mode"] == "wrong"
            else None,
        )
        for question in unit["questions"]:
            answer = answer_map.get(question["id"])
            question["user_answer"] = answer["user_answer"] if answer else ""
            unit_submission = submission_map.get(unit_id)
            if (session["status"] == "submitted" or unit_submission) and answer:
                question["is_correct"] = bool(answer["is_correct"])
                question["answer"] = question.get("answer") or connection.execute(
                    "SELECT answer FROM questions WHERE id = ?",
                    (question["id"],),
                ).fetchone()["answer"]
        unit_submission = submission_map.get(unit_id)
        unit_answer_rows = [
            answer_map[question["id"]]
            for question in unit["questions"]
            if question["id"] in answer_map
        ]
        unit_is_submitted = bool(unit_submission or session["status"] == "submitted")
        if unit_is_submitted:
            correct_count = sum(row["is_correct"] == 1 for row in unit_answer_rows)
            wrong_count = sum(row["is_correct"] == 0 for row in unit_answer_rows)
            computed_score = sum(
                row["question_score"]
                for row in unit_answer_rows
                if row["is_correct"] == 1
            )
            unit["submission"] = {
                "submitted": True,
                "submitted_at": (
                    unit_submission["submitted_at"]
                    if unit_submission
                    else session["submitted_at"]
                ),
                "score": (
                    unit_submission["score"]
                    if unit_submission
                    else computed_score
                ),
                "max_score": (
                    unit_submission["max_score"]
                    if unit_submission
                    else unit["max_score"]
                ),
                "wrong_count": wrong_count,
                "correct_count": correct_count,
                "question_count": len(unit_answer_rows),
            }
        else:
            unit["submission"] = {"submitted": False}
        units.append(unit)

    _normalize_listening_audio(units)
    answered = sum(bool(row["user_answer"]) for row in answers)
    result_summary = None
    if session["status"] == "submitted":
        correct_count = sum(row["is_correct"] == 1 for row in answers)
        wrong_count = sum(row["is_correct"] == 0 for row in answers)
        result_summary = {
            "score": session["score"],
            "max_score": session["max_score"],
            "wrong_count": wrong_count,
            "correct_count": correct_count,
            "question_count": len(answers),
        }
    payload = {
        "id": session["id"],
        "mode": session["mode"],
        "paper_id": session["paper_id"],
        "status": session["status"],
        "shuffle_options": bool(session["shuffle_options"]),
        "started_at": session["started_at"],
        "submitted_at": session["submitted_at"],
        "score": session["score"],
        "max_score": session["max_score"],
        "result_summary": result_summary,
        "units": units,
        "progress": {"answered": answered, "total": len(answers)},
    }
    return payload


def save_answer(
    connection: sqlite3.Connection,
    session_id: int,
    question_id: int,
    user_answer: str,
    option_order: list[str],
) -> None:
    session = connection.execute(
        "SELECT status FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError("练习记录不存在")
    if session["status"] != "active":
        raise ValueError("已经提交的练习不能修改")
    unit = connection.execute(
        "SELECT unit_id FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    if unit is None:
        raise LookupError("题目不存在")
    submitted_unit = connection.execute(
        """
        SELECT 1 FROM practice_unit_submissions
        WHERE session_id = ? AND unit_id = ?
        """,
        (session_id, unit["unit_id"]),
    ).fetchone()
    if submitted_unit:
        raise ValueError("这一篇已经提交，不能继续修改")

    previous = connection.execute(
        """
        SELECT user_answer FROM practice_answers
        WHERE session_id = ? AND question_id = ?
        """,
        (session_id, question_id),
    ).fetchone()
    cursor = connection.execute(
        """
        UPDATE practice_answers
        SET user_answer = ?, option_order = ?, answered_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND question_id = ?
        """,
        (
            user_answer,
            json.dumps(option_order, ensure_ascii=False),
            session_id,
            question_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError("题目不属于该练习")
    if user_answer and (previous is None or previous["user_answer"] != user_answer):
        connection.execute(
            """
            INSERT INTO practice_answer_events
                (session_id, question_id, user_answer, option_order)
            VALUES (?, ?, ?, ?)
            """,
            (
                session_id,
                question_id,
                user_answer,
                json.dumps(option_order, ensure_ascii=False),
            ),
        )
    connection.commit()


def _update_wrong_stat(
    connection: sqlite3.Connection, question_id: int, is_correct: bool
) -> None:
    row = connection.execute(
        "SELECT * FROM wrong_stats WHERE question_id = ?", (question_id,)
    ).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if row is None:
        recent = [is_correct]
        connection.execute(
            """
            INSERT INTO wrong_stats
                (question_id, attempt_count, wrong_count, recent_results,
                 consecutive_correct, last_wrong_at, last_attempt_at)
            VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                0 if is_correct else 1,
                json.dumps(recent),
                1 if is_correct else 0,
                None if is_correct else now,
                now,
            ),
        )
        return

    recent = parse_json(row["recent_results"], [])
    recent = (recent + [is_correct])[-10:]
    connection.execute(
        """
        UPDATE wrong_stats
        SET attempt_count = attempt_count + 1,
            wrong_count = wrong_count + ?,
            recent_results = ?,
            consecutive_correct = ?,
            last_wrong_at = ?,
            last_attempt_at = ?
        WHERE question_id = ?
        """,
        (
            0 if is_correct else 1,
            json.dumps(recent),
            row["consecutive_correct"] + 1 if is_correct else 0,
            row["last_wrong_at"] if is_correct else now,
            now,
            question_id,
        ),
    )


def _grade_answer_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> tuple[float, float]:
    score = 0.0
    max_score = 0.0
    for row in rows:
        max_score += row["score"]
        normalized_user = "".join(sorted(row["user_answer"].strip().upper()))
        normalized_answer = "".join(sorted(row["answer"].strip().upper()))
        is_correct = bool(normalized_user) and normalized_user == normalized_answer
        if is_correct:
            score += row["score"]
        already_graded = row["is_correct"] is not None
        connection.execute(
            "UPDATE practice_answers SET is_correct = ? WHERE id = ?",
            (int(is_correct), row["id"]),
        )
        if not already_graded:
            _update_wrong_stat(connection, row["question_id"], is_correct)
    return score, max_score


def submit_unit(
    connection: sqlite3.Connection,
    session_id: int,
    unit_id: int,
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError("练习记录不存在")
    if session["status"] != "active":
        raise ValueError("整份练习已经提交")
    if session["mode"] != "paper":
        raise ValueError("只有按年份练习支持单篇提交")
    unit_ids = parse_json(session["unit_ids"], [])
    if unit_id not in unit_ids:
        raise LookupError("篇目不属于该练习")
    existing = connection.execute(
        """
        SELECT 1 FROM practice_unit_submissions
        WHERE session_id = ? AND unit_id = ?
        """,
        (session_id, unit_id),
    ).fetchone()
    if existing:
        return get_session(connection, session_id)
    rows = connection.execute(
        """
        SELECT practice_answers.*, questions.answer, questions.score,
               questions.number, units.title AS unit_title
        FROM practice_answers
        JOIN questions ON questions.id = practice_answers.question_id
        JOIN units ON units.id = questions.unit_id
        WHERE practice_answers.session_id = ? AND questions.unit_id = ?
        ORDER BY questions.sequence
        """,
        (session_id, unit_id),
    ).fetchall()
    if not rows:
        raise ValueError("篇目中没有题目")
    missing = next((row for row in rows if not row["user_answer"].strip()), None)
    if missing:
        raise IncompleteSubmissionError(
            unit_id=unit_id,
            unit_title=missing["unit_title"],
            question_id=missing["question_id"],
            question_number=missing["number"],
        )
    score, max_score = _grade_answer_rows(connection, rows)
    connection.execute(
        """
        INSERT INTO practice_unit_submissions
            (session_id, unit_id, score, max_score)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, unit_id, score, max_score),
    )
    connection.commit()
    return get_session(connection, session_id)


def submit_session(
    connection: sqlite3.Connection, session_id: int
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError("练习记录不存在")
    if session["status"] == "submitted":
        return get_session(connection, session_id)

    rows = connection.execute(
        """
        SELECT practice_answers.*, questions.answer, questions.score,
               questions.unit_id, questions.number,
               units.title AS unit_title
        FROM practice_answers
        JOIN questions ON questions.id = practice_answers.question_id
        JOIN units ON units.id = questions.unit_id
        WHERE practice_answers.session_id = ?
        ORDER BY units.sequence, questions.sequence
        """,
        (session_id,),
    ).fetchall()
    if not rows:
        raise ValueError("练习中没有题目")
    missing = next((row for row in rows if not row["user_answer"].strip()), None)
    if missing:
        raise IncompleteSubmissionError(
            unit_id=missing["unit_id"],
            unit_title=missing["unit_title"],
            question_id=missing["question_id"],
            question_number=missing["number"],
        )
    rows_by_unit: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        rows_by_unit.setdefault(row["unit_id"], []).append(row)

    score = 0.0
    max_score = 0.0
    for unit_id, unit_rows in rows_by_unit.items():
        unit_score, unit_max_score = _grade_answer_rows(connection, unit_rows)
        score += unit_score
        max_score += unit_max_score
        connection.execute(
            """
            INSERT INTO practice_unit_submissions
                (session_id, unit_id, score, max_score)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, unit_id) DO UPDATE SET
                score = excluded.score,
                max_score = excluded.max_score
            """,
            (session_id, unit_id, unit_score, unit_max_score),
        )

    connection.execute(
        """
        UPDATE practice_sessions
        SET status = 'submitted',
            submitted_at = CURRENT_TIMESTAMP,
            score = ?,
            max_score = ?
        WHERE id = ?
        """,
        (score, max_score, session_id),
    )
    connection.commit()
    return get_session(connection, session_id)
