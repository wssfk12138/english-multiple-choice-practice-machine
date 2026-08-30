from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ..database import get_active_profile_id
from .ai_client import chat_completion, get_ai_profile, parse_json_response


LABEL_PROMPT = """
你负责为考研英语固定题库建立结构化考点标签，不需要写题目解析或翻译。
结合完整篇章、题干、选项和答案，为本批每道题输出标签。只输出合法 JSON：
{"labels":[{
 "question_id":1,
 "primary_skill":"上下文逻辑",
 "secondary_skills":["近义词辨析"],
 "trap_types":["局部语义成立但不符合上下文"],
 "attention_points":["先判断前后句逻辑关系"],
 "vocabulary_demand":"low|medium|high",
 "context_dependency":"low|medium|high",
 "grammar_dependency":"low|medium|high",
 "confidence":0.0
}]}
attention_points 必须是抽象方法，不得复述原题或透露答案。
每个输入 question_id 必须恰好输出一次。不要输出本批以外的题。
""".strip()


def labeling_status(
    connection: sqlite3.Connection,
    year: int | None = None,
    paper_ids: list[int] | None = None,
    question_bank_profile_id: int | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    run_context = _load_run_context(connection, run_id) if run_id.strip() else None
    if run_context:
        year = run_context["year"]
        paper_ids = run_context["paper_ids"]
        question_bank_profile_id = run_context["question_bank_profile_id"]
    params: list[Any] = []
    conditions: list[str] = [
        "p.deleted_at IS NULL",
        "u.unit_type <> 'listening'",
    ]
    if not paper_ids or question_bank_profile_id is not None:
        conditions.append("p.profile_id = ?")
        params.append(_resolve_question_bank_profile_id(connection, question_bank_profile_id))
    if year is not None:
        conditions.append("p.year = ?")
        params.append(year)
    normalized_paper_ids = sorted({int(value) for value in paper_ids or [] if int(value) > 0})
    if normalized_paper_ids:
        conditions.append(
            f"p.id IN ({','.join('?' for _ in normalized_paper_ids)})"
        )
        params.extend(normalized_paper_ids)
    scope_clause = f"WHERE {' AND '.join(conditions)}"
    row = connection.execute(
        f"""
        SELECT COUNT(q.id) AS total,
               SUM(CASE WHEN l.question_id IS NOT NULL THEN 1 ELSE 0 END) AS labeled,
               SUM(CASE WHEN l.locked = 1 THEN 1 ELSE 0 END) AS locked,
               SUM(CASE WHEN l.primary_skill = '题目结构待校正' THEN 1 ELSE 0 END)
                   AS review_pending
        FROM questions AS q
        JOIN units AS u ON u.id = q.unit_id
        JOIN papers AS p ON p.id = u.paper_id
        LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
        {scope_clause}
        """,
        params,
    ).fetchone()
    if normalized_paper_ids:
        year_params = list(normalized_paper_ids)
        profile_clause = ""
        if question_bank_profile_id is not None:
            profile_clause = " AND profile_id = ?"
            year_params.append(_resolve_question_bank_profile_id(connection, question_bank_profile_id))
        years = [
            item["year"]
            for item in connection.execute(
                f"""
                SELECT DISTINCT year FROM papers
                WHERE id IN ({','.join('?' for _ in normalized_paper_ids)}){profile_clause}
                ORDER BY year DESC
                """,
                year_params,
            ).fetchall()
        ]
    else:
        years = [
            item["year"]
            for item in connection.execute(
                """
                SELECT DISTINCT year FROM papers
                WHERE profile_id = ? AND deleted_at IS NULL
                ORDER BY year DESC
                """,
                (_resolve_question_bank_profile_id(connection, question_bank_profile_id),),
            ).fetchall()
        ]
    total = int(row["total"] or 0)
    labeled = int(row["labeled"] or 0)
    return {
        "year": year,
        "paper_ids": normalized_paper_ids,
        "years": years,
        "total": total,
        "labeled": labeled,
        "locked": int(row["locked"] or 0),
        "review_pending": int(row["review_pending"] or 0),
        "remaining": max(0, total - labeled),
        "percentage": round(labeled * 100 / total) if total else 0,
    }


def _effective_run_id(value: str) -> str:
    cleaned = value.strip()
    return cleaned[:80] if cleaned else uuid.uuid4().hex


def _normalize_paper_ids(paper_ids: list[int] | None) -> list[int]:
    return sorted({int(value) for value in paper_ids or [] if int(value) > 0})


def _resolve_question_bank_profile_id(
    connection: sqlite3.Connection,
    question_bank_profile_id: int | None,
) -> int:
    if question_bank_profile_id is None:
        return get_active_profile_id(connection)
    profile_id = int(question_bank_profile_id)
    exists = connection.execute(
        "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
        (profile_id,),
    ).fetchone()
    if not exists:
        raise ValueError("题库配置不存在或已在回收站")
    return profile_id


def _load_run_context(
    connection: sqlite3.Connection,
    run_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM question_label_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        stored_paper_ids = json.loads(row["paper_ids"] or "[]")
    except (TypeError, ValueError):
        stored_paper_ids = []
    return {
        "run_id": str(row["run_id"]),
        "question_bank_profile_id": int(row["question_bank_profile_id"]),
        "scope_kind": str(row["scope_kind"] or "all"),
        "year": int(row["year"]) if row["year"] is not None else None,
        "paper_ids": _normalize_paper_ids(stored_paper_ids),
        "overwrite_unlocked": bool(row["overwrite_unlocked"]),
        "status": str(row["status"] or "running"),
    }


def _ensure_run_context(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    year: int | None,
    paper_ids: list[int] | None,
    overwrite_unlocked: bool,
    question_bank_profile_id: int | None,
) -> tuple[str, dict[str, Any]]:
    effective_run_id = _effective_run_id(run_id)
    existing = _load_run_context(connection, effective_run_id)
    if existing:
        connection.execute(
            "UPDATE question_label_runs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE run_id = ? AND status IN ('paused', 'failed')",
            (effective_run_id,),
        )
        connection.commit()
        return effective_run_id, existing
    normalized_paper_ids = _normalize_paper_ids(paper_ids)
    profile_id = _resolve_question_bank_profile_id(connection, question_bank_profile_id)
    if normalized_paper_ids:
        placeholders = ",".join("?" for _ in normalized_paper_ids)
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM papers WHERE id IN ({placeholders}) AND profile_id = ? AND deleted_at IS NULL",
            [*normalized_paper_ids, profile_id],
        ).fetchone()
        if int(row["count"] or 0) != len(normalized_paper_ids):
            raise ValueError("标注试卷不属于当前绑定的题库配置")
    scope_kind = "papers" if normalized_paper_ids else ("year" if year is not None else "all")
    connection.execute(
        """
        INSERT OR IGNORE INTO question_label_runs
            (run_id, question_bank_profile_id, scope_kind, year, paper_ids, overwrite_unlocked, status)
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            effective_run_id,
            profile_id,
            scope_kind,
            year,
            json.dumps(normalized_paper_ids),
            int(overwrite_unlocked),
        ),
    )
    connection.commit()
    context = _load_run_context(connection, effective_run_id)
    if context is None:
        raise RuntimeError("无法创建智能标注运行上下文")
    return effective_run_id, context


def _update_run_status(
    connection: sqlite3.Connection,
    run_id: str,
    status: str,
    error: str = "",
) -> None:
    finished_at = "CURRENT_TIMESTAMP" if status in {"done", "failed", "cancelled"} else "NULL"
    connection.execute(
        f"UPDATE question_label_runs SET status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP, finished_at = {finished_at} WHERE run_id = ?",
        (status, error[:1000], run_id),
    )
    connection.commit()


def pause_label_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    context = _load_run_context(connection, run_id.strip())
    if context is None:
        raise LookupError("智能标注运行不存在")
    _update_run_status(connection, context["run_id"], "paused")
    return {"run_id": context["run_id"], "status": "paused"}


def _eligible_condition(overwrite_unlocked: bool) -> str:
    return "(l.question_id IS NULL OR l.locked = 0)" if overwrite_unlocked else "l.question_id IS NULL"


def _next_unit(
    connection: sqlite3.Connection,
    *,
    year: int | None,
    paper_ids: list[int] | None,
    overwrite_unlocked: bool,
    run_id: str,
    question_bank_profile_id: int,
) -> sqlite3.Row | None:
    params: list[Any] = [run_id]
    conditions = [
        _eligible_condition(overwrite_unlocked),
        "ri.question_id IS NULL",
        "p.deleted_at IS NULL",
        "u.unit_type <> 'listening'",
    ]
    if not paper_ids:
        conditions.append("p.profile_id = ?")
        params.append(question_bank_profile_id)
    if year is not None:
        conditions.append("p.year = ?")
        params.append(year)
    normalized_paper_ids = _normalize_paper_ids(paper_ids)
    if normalized_paper_ids:
        conditions.append(
            f"p.id IN ({','.join('?' for _ in normalized_paper_ids)})"
        )
        params.extend(normalized_paper_ids)
    return connection.execute(
        f"""
        SELECT u.id, u.title, u.unit_type, u.passage, p.year
        FROM units AS u
        JOIN papers AS p ON p.id = u.paper_id
        JOIN questions AS q ON q.unit_id = u.id
        LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
        LEFT JOIN question_label_run_items AS ri
          ON ri.question_id = q.id AND ri.run_id = ?
        WHERE {' AND '.join(conditions)}
        GROUP BY u.id
        ORDER BY p.year DESC, u.sequence
        LIMIT 1
        """,
        params,
    ).fetchone()


def _question_payload(
    connection: sqlite3.Connection,
    unit_id: int,
    *,
    overwrite_unlocked: bool,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT q.id, q.number, q.stem, q.answer, q.question_type
        FROM questions AS q
        LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
        LEFT JOIN question_label_run_items AS ri
          ON ri.question_id = q.id AND ri.run_id = ?
        WHERE q.unit_id = ?
          AND {_eligible_condition(overwrite_unlocked)}
          AND ri.question_id IS NULL
        ORDER BY q.sequence
        """,
        (run_id, unit_id),
    ).fetchall()
    result = []
    for question in rows:
        options = connection.execute(
            """
            SELECT stable_key, content
            FROM options WHERE question_id = ? ORDER BY sequence
            """,
            (question["id"],),
        ).fetchall()
        result.append(
            {
                **dict(question),
                "options": [dict(option) for option in options],
            }
        )
    return result


def _request_labels(
    connection: sqlite3.Connection,
    *,
    unit: sqlite3.Row,
    questions: list[dict[str, Any]],
    profile_id: int | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": LABEL_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "year": unit["year"],
                    "title": unit["title"],
                    "unit_type": unit["unit_type"],
                    "passage": unit["passage"][:9000],
                    "questions": questions,
                },
                ensure_ascii=False,
            ),
        },
    ]
    raw = chat_completion(
        connection,
        messages,
        response_format={"type": "json_object"},
        profile_id=profile_id,
        model=model,
        max_tokens=None,
    )
    parsed = parse_json_response(raw)
    labels = parsed.get("labels") if isinstance(parsed, dict) else None
    if not isinstance(labels, list):
        raise ValueError("模型没有返回有效的题目标签")
    expected_ids = {question["id"] for question in questions}
    cleaned = [
        label
        for label in labels
        if isinstance(label, dict) and label.get("question_id") in expected_ids
    ]
    returned_ids = {int(label["question_id"]) for label in cleaned}
    if returned_ids != expected_ids:
        raise ValueError("模型返回的题目标签不完整")
    return cleaned


def _request_batch_with_fallback(
    connection: sqlite3.Connection,
    *,
    unit: sqlite3.Row,
    questions: list[dict[str, Any]],
    profile_id: int | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    try:
        return _request_labels(
            connection,
            unit=unit,
            questions=questions,
            profile_id=profile_id,
            model=model,
            max_tokens=max_tokens,
        )
    except (ValueError, json.JSONDecodeError) as error:
        if len(questions) <= 1:
            raise ValueError(f"模型未能完成题目标签：{error}") from error
        midpoint = max(1, len(questions) // 2)
        return [
            *_request_batch_with_fallback(
                connection,
                unit=unit,
                questions=questions[:midpoint],
                profile_id=profile_id,
                model=model,
                max_tokens=max_tokens,
            ),
            *_request_batch_with_fallback(
                connection,
                unit=unit,
                questions=questions[midpoint:],
                profile_id=profile_id,
                model=model,
                max_tokens=max_tokens,
            ),
        ]


def _normalized_label_list(value: Any, *, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    result = []
    for item in candidates:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _save_labels(
    connection: sqlite3.Connection,
    labels: list[dict[str, Any]],
    *,
    model_name: str,
    run_id: str,
) -> int:
    processed = 0
    for label in labels:
        question_id = int(label["question_id"])
        existing = connection.execute(
            "SELECT locked FROM question_ai_labels WHERE question_id = ?",
            (question_id,),
        ).fetchone()
        if not (existing and existing["locked"]):
            try:
                confidence = max(0, min(1, float(label.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0
            connection.execute(
                """
                INSERT INTO question_ai_labels
                    (question_id, primary_skill, secondary_skills, trap_types,
                     attention_points, vocabulary_demand, context_dependency,
                     grammar_dependency, confidence, locked, user_edited,
                     model_name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(question_id) DO UPDATE SET
                    primary_skill = excluded.primary_skill,
                    secondary_skills = excluded.secondary_skills,
                    trap_types = excluded.trap_types,
                    attention_points = excluded.attention_points,
                    vocabulary_demand = excluded.vocabulary_demand,
                    context_dependency = excluded.context_dependency,
                    grammar_dependency = excluded.grammar_dependency,
                    confidence = excluded.confidence,
                    locked = 1,
                    model_name = excluded.model_name,
                    label_version = question_ai_labels.label_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE question_ai_labels.locked = 0
                """,
                (
                    question_id,
                    str(label.get("primary_skill") or "待补充")[:80],
                    json.dumps(
                        _normalized_label_list(label.get("secondary_skills")),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        _normalized_label_list(label.get("trap_types")),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        _normalized_label_list(label.get("attention_points")),
                        ensure_ascii=False,
                    ),
                    label.get("vocabulary_demand")
                    if label.get("vocabulary_demand") in {"low", "medium", "high"}
                    else "medium",
                    label.get("context_dependency")
                    if label.get("context_dependency") in {"low", "medium", "high"}
                    else "medium",
                    label.get("grammar_dependency")
                    if label.get("grammar_dependency") in {"low", "medium", "high"}
                    else "medium",
                    confidence,
                    model_name,
                ),
            )
            processed += 1
        connection.execute(
            """
            INSERT OR REPLACE INTO question_label_run_items (run_id, question_id)
            VALUES (?, ?)
            """,
            (run_id, question_id),
        )
    connection.commit()
    return processed


def label_next_unit(
    connection: sqlite3.Connection,
    *,
    year: int | None,
    paper_ids: list[int] | None = None,
    overwrite_unlocked: bool,
    run_id: str = "",
    profile_id: int | None = None,
    question_bank_profile_id: int | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    effective_run_id, run_context = _ensure_run_context(
        connection,
        run_id=run_id,
        year=year,
        paper_ids=paper_ids,
        overwrite_unlocked=overwrite_unlocked,
        question_bank_profile_id=question_bank_profile_id,
    )
    scoped_year = run_context["year"]
    scoped_paper_ids = run_context["paper_ids"]
    scoped_overwrite_unlocked = run_context["overwrite_unlocked"]
    unit = _next_unit(
        connection,
        year=scoped_year,
        paper_ids=scoped_paper_ids,
        overwrite_unlocked=scoped_overwrite_unlocked,
        run_id=effective_run_id,
        question_bank_profile_id=run_context["question_bank_profile_id"],
    )
    if unit is None:
        _update_run_status(connection, effective_run_id, "done")
        return {
            "done": True,
            "processed": 0,
            "run_id": effective_run_id,
            "question_bank_profile_id": run_context["question_bank_profile_id"],
            **labeling_status(
                connection,
                scoped_year,
                scoped_paper_ids,
                question_bank_profile_id=run_context["question_bank_profile_id"],
                run_id=effective_run_id,
            ),
        }
    questions = _question_payload(
        connection,
        unit["id"],
        overwrite_unlocked=scoped_overwrite_unlocked,
        run_id=effective_run_id,
    )
    if not questions:
        return {
            "done": False,
            "processed": 0,
            "run_id": effective_run_id,
            "unit_id": unit["id"],
            "unit_title": f"{unit['year']} 年 {unit['title']}",
            "question_bank_profile_id": run_context["question_bank_profile_id"],
            **labeling_status(
                connection,
                scoped_year,
                scoped_paper_ids,
                question_bank_profile_id=run_context["question_bank_profile_id"],
                run_id=effective_run_id,
            ),
        }
    profile = get_ai_profile(connection, profile_id)
    processed = 0
    # Each call keeps the complete passage but emits at most five labels.
    # Successful sub-batches are committed immediately.
    for start in range(0, len(questions), 5):
        batch = questions[start : start + 5]
        labels = _request_batch_with_fallback(
            connection,
            unit=unit,
            questions=batch,
            profile_id=profile_id,
            model=model,
            max_tokens=max_tokens,
        )
        processed += _save_labels(
            connection,
            labels,
            model_name=profile["default_model"],
            run_id=effective_run_id,
        )
    return {
        "done": False,
        "processed": processed,
        "run_id": effective_run_id,
        "question_bank_profile_id": run_context["question_bank_profile_id"],
        "unit_id": unit["id"],
        "unit_title": f"{unit['year']} 年 {unit['title']}",
        **labeling_status(
            connection,
            scoped_year,
            scoped_paper_ids,
            question_bank_profile_id=run_context["question_bank_profile_id"],
            run_id=effective_run_id,
        ),
    }


def update_question_label(
    connection: sqlite3.Connection,
    question_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    if row is None:
        raise LookupError("题目不存在")
    connection.execute(
        """
        INSERT INTO question_ai_labels
            (question_id, primary_skill, secondary_skills, trap_types,
             attention_points, vocabulary_demand, context_dependency,
             grammar_dependency, confidence, locked, user_edited, model_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '人工校正')
        ON CONFLICT(question_id) DO UPDATE SET
            primary_skill = excluded.primary_skill,
            secondary_skills = excluded.secondary_skills,
            trap_types = excluded.trap_types,
            attention_points = excluded.attention_points,
            vocabulary_demand = excluded.vocabulary_demand,
            context_dependency = excluded.context_dependency,
            grammar_dependency = excluded.grammar_dependency,
            confidence = excluded.confidence,
            locked = excluded.locked,
            user_edited = 1,
            model_name = '人工校正',
            label_version = question_ai_labels.label_version + 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            question_id,
            payload["primary_skill"],
            json.dumps(payload["secondary_skills"], ensure_ascii=False),
            json.dumps(payload["trap_types"], ensure_ascii=False),
            json.dumps(payload["attention_points"], ensure_ascii=False),
            payload["vocabulary_demand"],
            payload["context_dependency"],
            payload["grammar_dependency"],
            payload["confidence"],
            int(payload["locked"]),
        ),
    )
    connection.commit()
    return {"updated": True, "locked": bool(payload["locked"])}
