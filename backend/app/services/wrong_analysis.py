from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from .ai_client import chat_completion, parse_json_response


CAUSE_LABELS = {
    "vocabulary": "词汇基础与词义辨析",
    "collocation": "固定搭配",
    "grammar": "语法结构",
    "context": "上下文逻辑",
    "discourse": "篇章衔接",
    "detail": "细节定位",
    "inference": "推理判断",
    "main_idea": "主旨概括",
    "attitude": "作者态度",
    "trap": "干扰项排除",
    "carelessness": "审题与细节疏忽",
    "uncertain": "证据不足，暂不能确定",
}

CAUSE_GUIDANCE = {
    "vocabulary": "复习近义词、熟词僻义与语境词义，选择前先概括所需语义。",
    "collocation": "按语块积累固定搭配，并在完整句子中复习而非孤立背词。",
    "grammar": "先划分句子主干，再检查从句、修饰关系和谓语结构。",
    "context": "选择前先判断前后文的转折、因果、递进或对比关系。",
    "discourse": "关注代词指代、连接词、主题延续和段落间的逻辑顺序。",
    "detail": "先定位原文依据，再核对限定词、范围和比较关系。",
    "inference": "区分原文事实与合理推断，避免加入原文没有提供的前提。",
    "main_idea": "优先概括各段功能与共同主题，避免被局部细节带偏。",
    "attitude": "结合评价词、语气和转折位置判断态度，避免只看单个词。",
    "trap": "逐项核对选项是否偷换概念、扩大范围或只局部成立。",
    "carelessness": "提交前固定检查否定词、限定词、题干要求和改选依据。",
    "uncertain": "继续积累同类题的作答记录，暂不依据单次错误下结论。",
}


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _question_history(
    connection: sqlite3.Connection,
    question_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT pa.user_answer, pa.is_correct, pa.answered_at, ps.started_at,
               ps.submitted_at
        FROM practice_answers AS pa
        JOIN practice_sessions AS ps ON ps.id = pa.session_id
        WHERE pa.question_id = ? AND pa.is_correct IS NOT NULL
              AND TRIM(pa.user_answer) <> ''
        ORDER BY COALESCE(ps.submitted_at, pa.answered_at) DESC, pa.id DESC
        LIMIT 12
        """,
        (question_id,),
    ).fetchall()
    history = [
        {
            "selected": row["user_answer"],
            "correct": bool(row["is_correct"]),
            "answered_at": row["answered_at"],
            "submitted_at": row["submitted_at"],
            # The model is explicitly told that earlier items have greater
            # diagnostic weight.
            "recency_rank": index + 1,
        }
        for index, row in enumerate(rows)
    ]
    event_rows = connection.execute(
        """
        SELECT user_answer, changed_at
        FROM practice_answer_events
        WHERE question_id = ?
        ORDER BY changed_at DESC, id DESC
        LIMIT 20
        """,
        (question_id,),
    ).fetchall()
    if event_rows:
        history_changes = [
            {"selected": row["user_answer"], "changed_at": row["changed_at"]}
            for row in event_rows
        ]
        for item in history:
            item["recording_note"] = "旧记录可能只有最终答案"
        history.append({"answer_changes": history_changes, "recency_rank": 0})
    return history


def build_diagnostic_payload(
    connection: sqlite3.Connection,
    question_ids: list[int],
    previous_snapshot: dict[str, Any] | None = None,
    current_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not question_ids:
        return []
    placeholders = ",".join("?" for _ in question_ids)
    rows = connection.execute(
        f"""
        SELECT q.id, q.number, q.stem, q.answer, q.question_type,
               u.id AS unit_id, u.title, u.unit_type, u.passage,
               p.year, ws.attempt_count, ws.wrong_count,
               l.primary_skill, l.secondary_skills, l.trap_types,
               l.attention_points, l.vocabulary_demand,
               l.context_dependency, l.grammar_dependency,
               l.confidence AS label_confidence
        FROM questions AS q
        JOIN units AS u ON u.id = q.unit_id
        JOIN papers AS p ON p.id = u.paper_id
        JOIN wrong_stats AS ws ON ws.question_id = q.id
        LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
        WHERE q.id IN ({placeholders}) AND ws.wrong_count > 0
        ORDER BY p.year, u.sequence, q.sequence
        """,
        question_ids,
    ).fetchall()
    current_errors_by_question = {
        int(error["question_id"]): str(error.get("selected") or "")
        for unit in (current_snapshot or {}).values()
        if isinstance(unit, dict)
        for error in unit.get("errors", [])
        if isinstance(error, dict) and error.get("question_id")
    }
    units: dict[int, dict[str, Any]] = {}
    for row in rows:
        if current_snapshot is not None and int(row["id"]) not in current_errors_by_question:
            continue
        unit = units.setdefault(
            row["unit_id"],
            {
                "year": row["year"],
                "unit_title": row["title"],
                "unit_type": row["unit_type"],
                "passage": row["passage"][:7000],
                "previous_errors": (previous_snapshot or {}).get(
                    str(row["unit_id"]), {}
                ).get("errors", []),
                "questions": [],
            },
        )
        options = connection.execute(
            """
            SELECT stable_key, content
            FROM options WHERE question_id = ? ORDER BY sequence
            """,
            (row["id"],),
        ).fetchall()
        label = None
        if row["primary_skill"] and row["primary_skill"] != "题目结构待校正":
            label = {
                "primary_skill": row["primary_skill"],
                "secondary_skills": _json_list(row["secondary_skills"]),
                "trap_types": _json_list(row["trap_types"]),
                "attention_points": _json_list(row["attention_points"]),
                "vocabulary_demand": row["vocabulary_demand"],
                "context_dependency": row["context_dependency"],
                "grammar_dependency": row["grammar_dependency"],
                "confidence": row["label_confidence"],
            }
        unit["questions"].append(
            {
                "question_id": row["id"],
                "number": row["number"],
                "stem": row["stem"],
                "options": [dict(option) for option in options],
                "correct_answer": row["answer"],
                "current_wrong_answer": current_errors_by_question.get(row["id"]),
                "attempt_count": row["attempt_count"],
                "wrong_count": row["wrong_count"],
                "history_newest_first": _question_history(connection, row["id"]),
                "pre_label": label,
            }
        )
    return list(units.values())


def diagnose_wrong_answers(
    connection: sqlite3.Connection,
    question_ids: list[int],
    previous_snapshot: dict[str, Any] | None = None,
    current_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    payload = build_diagnostic_payload(
        connection,
        question_ids,
        previous_snapshot=previous_snapshot,
        current_snapshot=current_snapshot,
    )
    if not payload:
        raise ValueError("没有可分析的错题记录")
    prompt = """
你是考研英语错题诊断器。你可以读取原题、选项、正确答案、用户历次选择和预标注，
但你的输出只用于程序后台，绝不能写逐题解析、翻译原文，也不要复述题目或选项。
请判断每道题的主要错误原因。越新的作答记录权重越高；旧记录可能只有最终选择。
证据不足时必须使用 uncertain，不要强行归因。
如果提供了 previous_errors（上一次分析时的错误选项），你可以对比两次作答，
判断用户是否仍然选择同一错误选项，或薄弱环节是否发生变化；对比结果只能用于
归因和复习建议，绝不能复述选项内容或暴露选项文字。

只输出合法 JSON：
{"diagnoses":[
  {
    "question_id": 1,
    "primary_cause": "vocabulary|collocation|grammar|context|discourse|detail|inference|main_idea|attitude|trap|carelessness|uncertain",
    "secondary_causes": ["context"],
    "confidence": 0.0,
    "reason_codes": ["近义词边界不清"],
    "recommended_actions": ["先判断空格需要的语义关系再比较选项"]
  }
]}
每道输入题必须恰好出现一次。reason_codes 和 recommended_actions 必须抽象、简短，
不得包含题号、原文、选项文字、答案字母或能唤起原题记忆的具体线索。
""".strip()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = ""
    parsed: Any = None
    last_error: Exception | None = None
    for limit in (6000, 7800):
        try:
            raw = chat_completion(
                connection,
                messages,
                response_format={"type": "json_object"},
                max_tokens=limit,
            )
            parsed = parse_json_response(raw)
            break
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
            if limit == 7800:
                raise ValueError(f"模型未能完成结构化错题诊断：{error}") from error
    if parsed is None:
        raise ValueError(f"模型未能完成结构化错题诊断：{last_error}")
    diagnoses = parsed.get("diagnoses") if isinstance(parsed, dict) else None
    if not isinstance(diagnoses, list):
        raise ValueError("模型没有返回有效的逐题诊断")
    allowed_ids = {
        question["question_id"]
        for unit in payload
        for question in unit["questions"]
    }
    cleaned: list[dict[str, Any]] = []
    for item in diagnoses:
        if not isinstance(item, dict) or item.get("question_id") not in allowed_ids:
            continue
        cause = str(item.get("primary_cause") or "uncertain")
        if cause not in CAUSE_LABELS:
            cause = "uncertain"
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        cleaned.append(
            {
                "question_id": item["question_id"],
                "primary_cause": cause,
                "secondary_causes": [
                    value
                    for value in item.get("secondary_causes", [])
                    if value in CAUSE_LABELS
                ][:3],
                "confidence": confidence,
                "reason_codes": [
                    str(value)[:60] for value in item.get("reason_codes", [])
                ][:4],
                "recommended_actions": [
                    str(value)[:90] for value in item.get("recommended_actions", [])
                ][:4],
            }
        )
    diagnosed_ids = {item["question_id"] for item in cleaned}
    for missing_id in allowed_ids - diagnosed_ids:
        cleaned.append(
            {
                "question_id": missing_id,
                "primary_cause": "uncertain",
                "secondary_causes": [],
                "confidence": 0,
                "reason_codes": ["模型未提供足够证据"],
                "recommended_actions": ["继续积累该题型的作答记录后再判断"],
            }
        )
    return cleaned, raw


def aggregate_diagnoses(diagnoses: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(diagnoses)
    counts = Counter(item["primary_cause"] for item in diagnoses)
    confidence_by_cause: dict[str, list[float]] = defaultdict(list)
    for item in diagnoses:
        confidence_by_cause[item["primary_cause"]].append(item["confidence"])
    categories = [
        {
            "code": code,
            "label": CAUSE_LABELS[code],
            "count": count,
            "percentage": round(count * 100 / total) if total else 0,
            "average_confidence": round(
                sum(confidence_by_cause[code]) / len(confidence_by_cause[code]),
                2,
            ),
        }
        for code, count in counts.most_common()
    ]
    return {
        "question_count": total,
        "categories": categories,
        # The final report receives only locally whitelisted, abstract guidance.
        # Free-form text produced while the model could see the original
        # questions is deliberately discarded here.
        "recommended_actions": [
            CAUSE_GUIDANCE[code] for code, _ in counts.most_common()
        ],
        "uncertain_count": counts.get("uncertain", 0),
    }


def write_anonymous_report(
    connection: sqlite3.Connection,
    aggregate: dict[str, Any],
) -> str:
    prompt = """
你是学习规划助手。输入只有匿名统计，不包含原题。
请写一份简洁中文报告，只输出：
1. 当前主要短板（可写数量和比例）；
2. 下次做题时的答题方法；
3. 未来一周复习建议；
4. 如果存在不确定归因，明确说明需要更多作答记录。

严禁出现题号、题目、选项、答案、翻译、逐题解析或虚构的原题例子。
不要解释分析过程，控制在 500 至 900 个汉字。
""".strip()
    attempts = (2200, 4200)
    last_error: Exception | None = None
    for limit in attempts:
        try:
            return chat_completion(
                connection,
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(aggregate, ensure_ascii=False),
                    },
                ],
                max_tokens=limit,
            )
        except ValueError as error:
            last_error = error
            if "没有返回可显示的正文" not in str(error):
                raise
    raise ValueError(f"生成匿名报告失败：{last_error}")
