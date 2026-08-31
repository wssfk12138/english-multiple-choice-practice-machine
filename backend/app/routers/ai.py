from __future__ import annotations

import json
import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import (
    AiAnalyzeRequest,
    AiChatRequest,
    AiCorrectionRequest,
    AiLabelBatchRequest,
    AiModelListRequest,
    AiModelsVisibilityUpdate,
    AiModelVisibilityUpdate,
    AiProfileTestRequest,
    AiProfileWrite,
    AiQuestionLabelUpdate,
    AiSettingsUpdate,
)
from ..security import protect_text
from ..services.ai_client import (
    chat_completion,
    ensure_ai_model_catalog,
    get_ai_profile,
    get_ai_settings,
    list_available_models,
    parse_json_response,
)
from ..services.ai_adapters import normalize_adapter, normalize_reasoning_effort
from ..services.docx_parser import validate_draft
from ..services.question_labeling import (
    _update_run_status,
    label_next_unit,
    labeling_status,
    pause_label_run,
    update_question_label,
)
from ..services.wrong_analysis import (
    aggregate_diagnoses,
    diagnose_wrong_answers,
    write_anonymous_report,
)


router = APIRouter(prefix="/ai", tags=["ai"])


def _profile_payload(row: sqlite3.Row) -> dict:
    payload = dict(row)
    payload["has_api_key"] = bool(payload.pop("api_key_encrypted", None))
    payload["enabled"] = bool(payload["enabled"])
    payload["is_default"] = bool(payload["is_default"])
    return payload


def _profile_or_404(connection: sqlite3.Connection, profile_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM ai_profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "API 配置不存在")
    return row


def _ensure_default_profile(connection: sqlite3.Connection) -> None:
    default = connection.execute(
        "SELECT id FROM ai_profiles WHERE is_default = 1"
    ).fetchone()
    if default is None:
        first = connection.execute(
            "SELECT id FROM ai_profiles ORDER BY enabled DESC, id LIMIT 1"
        ).fetchone()
        if first is not None:
            connection.execute(
                "UPDATE ai_profiles SET is_default = 1 WHERE id = ?",
                (first["id"],),
            )


def _conversation_payload(
    connection: sqlite3.Connection,
    conversation_id: int,
) -> dict:
    row = connection.execute(
        "SELECT * FROM ai_conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "对话不存在")
    messages = connection.execute(
        """
        SELECT id, role, content, attachments, profile_id, model_id, created_at
        FROM ai_messages
        WHERE conversation_id = ?
        ORDER BY id
        """,
        (conversation_id,),
    ).fetchall()
    payload = dict(row)
    serialized = []
    for message in messages:
        item = dict(message)
        item["attachments"] = json.loads(item.pop("attachments") or "[]")
        serialized.append(item)
    payload["messages"] = serialized
    return payload


@router.get("/settings")
def read_settings(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_ai_settings(connection)


@router.put("/settings")
def update_settings(
    request: AiSettingsUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    profile = get_ai_profile(connection)
    current = connection.execute(
        "SELECT api_key_encrypted FROM ai_profiles WHERE id = ?",
        (profile["id"],),
    ).fetchone()
    encrypted = current["api_key_encrypted"] if current else None
    if request.api_key:
        encrypted = protect_text(request.api_key)
    connection.execute("UPDATE ai_profiles SET is_default = 0 WHERE id <> ?", (profile["id"],))
    connection.execute(
        """
        UPDATE ai_profiles
        SET name = ?, adapter = ?, base_url = ?, api_key_encrypted = ?, default_model = ?,
            temperature = ?, max_tokens = ?, reasoning_effort = ?, system_prompt = ?, is_default = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            request.name,
            normalize_adapter(request.adapter),
            request.base_url.rstrip("/"),
            encrypted,
            request.model,
            request.temperature,
            request.max_tokens,
            normalize_reasoning_effort(request.reasoning_effort),
            request.system_prompt,
            profile["id"],
        ),
    )
    if request.model.strip():
        connection.execute(
            """
            INSERT INTO ai_profile_models
                (profile_id, model_id, display_name, is_visible, is_available)
            VALUES (?, ?, ?, 1, 1)
            ON CONFLICT(profile_id, model_id) DO UPDATE SET
                is_available = 1, updated_at = CURRENT_TIMESTAMP
            """,
            (profile["id"], request.model.strip(), request.model.strip()),
        )
    connection.commit()
    return get_ai_settings(connection)


@router.post("/models")
def read_available_models(
    request: AiModelListRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return list_available_models(
            connection,
            base_url=request.base_url,
            api_key=request.api_key,
            use_saved_api_key=request.use_saved_api_key,
            profile_id=request.profile_id,
            adapter=request.adapter,
        )
    except (ValueError, LookupError, httpx.HTTPError) as error:
        raise HTTPException(400, str(error)) from error


@router.post("/test")
def test_connection(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        content = chat_completion(
            connection,
            [
                {
                    "role": "user",
                    "content": "只回复“连接成功”，不要补充其他内容。",
                }
            ],
        )
        return {"ok": True, "message": content.strip()}
    except (ValueError, LookupError, httpx.HTTPError) as error:
        raise HTTPException(400, f"连接失败：{error}") from error


@router.get("/profiles")
def list_profiles(connection: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = connection.execute(
        """
        SELECT * FROM ai_profiles
        ORDER BY is_default DESC, enabled DESC, id
        """
    ).fetchall()
    profiles: list[dict] = []
    for row in rows:
        payload = _profile_payload(row)
        models = connection.execute(
            """
            SELECT model_id, display_name, owned_by, provider,
                   is_visible, is_available, updated_at
            FROM ai_profile_models
            WHERE profile_id = ?
            ORDER BY model_id COLLATE NOCASE
            """,
            (row["id"],),
        ).fetchall()
        payload["models"] = [
            {
                **dict(model),
                "is_visible": bool(model["is_visible"]),
                "is_available": bool(model["is_available"]),
            }
            for model in models
        ]
        profiles.append(payload)
    return profiles


@router.post("/profiles")
def create_profile(
    request: AiProfileWrite,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not request.name.strip():
        raise HTTPException(400, "请填写配置名称")
    if not request.base_url.strip():
        raise HTTPException(400, "请填写 API Base URL")
    encrypted = protect_text(request.api_key.strip()) if request.api_key else None
    has_profiles = connection.execute(
        "SELECT 1 FROM ai_profiles LIMIT 1"
    ).fetchone()
    make_default = request.is_default or has_profiles is None
    if make_default:
        connection.execute("UPDATE ai_profiles SET is_default = 0")
    cursor = connection.execute(
        """
        INSERT INTO ai_profiles
            (name, adapter, base_url, api_key_encrypted, enabled, is_default,
             default_model, temperature, max_tokens, reasoning_effort, system_prompt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.name.strip(),
            normalize_adapter(request.adapter),
            request.base_url.strip().rstrip("/"),
            encrypted,
            int(request.enabled),
            int(make_default),
            request.default_model.strip(),
            request.temperature,
            request.max_tokens,
            normalize_reasoning_effort(request.reasoning_effort),
            request.system_prompt,
        ),
    )
    profile_id = int(cursor.lastrowid)
    if request.default_model.strip():
        connection.execute(
            """
            INSERT OR IGNORE INTO ai_profile_models
                (profile_id, model_id, display_name, is_visible, is_available)
            VALUES (?, ?, ?, 1, 1)
            """,
            (profile_id, request.default_model.strip(), request.default_model.strip()),
        )
    connection.commit()
    return get_ai_profile(connection, profile_id)


@router.put("/profiles/{profile_id}")
def update_profile(
    profile_id: int,
    request: AiProfileWrite,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    current = _profile_or_404(connection, profile_id)
    if not request.name.strip() or not request.base_url.strip():
        raise HTTPException(400, "配置名称和 API Base URL 不能为空")
    encrypted = current["api_key_encrypted"]
    if request.clear_api_key:
        encrypted = None
    elif request.api_key:
        encrypted = protect_text(request.api_key.strip())
    make_default = request.is_default or bool(current["is_default"])
    if make_default:
        connection.execute(
            "UPDATE ai_profiles SET is_default = 0 WHERE id <> ?",
            (profile_id,),
        )
    connection.execute(
        """
        UPDATE ai_profiles
        SET name = ?, adapter = ?, base_url = ?, api_key_encrypted = ?, enabled = ?,
            is_default = ?, default_model = ?, temperature = ?,
            max_tokens = ?, reasoning_effort = ?, system_prompt = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            request.name.strip(),
            normalize_adapter(request.adapter),
            request.base_url.strip().rstrip("/"),
            encrypted,
            int(request.enabled),
            int(make_default),
            request.default_model.strip(),
            request.temperature,
            request.max_tokens,
            normalize_reasoning_effort(request.reasoning_effort),
            request.system_prompt,
            profile_id,
        ),
    )
    if request.default_model.strip():
        connection.execute(
            """
            INSERT INTO ai_profile_models
                (profile_id, model_id, display_name, is_visible, is_available)
            VALUES (?, ?, ?, 1, 1)
            ON CONFLICT(profile_id, model_id) DO UPDATE SET
                is_available = 1, updated_at = CURRENT_TIMESTAMP
            """,
            (profile_id, request.default_model.strip(), request.default_model.strip()),
        )
    _ensure_default_profile(connection)
    connection.commit()
    return get_ai_profile(connection, profile_id)


@router.delete("/profiles/{profile_id}")
def delete_profile(
    profile_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    _profile_or_404(connection, profile_id)
    count = connection.execute("SELECT COUNT(*) AS total FROM ai_profiles").fetchone()
    if count["total"] <= 1:
        raise HTTPException(409, "至少保留一个 API 配置")
    connection.execute("DELETE FROM ai_profiles WHERE id = ?", (profile_id,))
    _ensure_default_profile(connection)
    connection.commit()
    return {"ok": True}


@router.post("/profiles/{profile_id}/models/sync")
def sync_profile_models(
    profile_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    profile = _profile_or_404(connection, profile_id)
    try:
        result = list_available_models(
            connection,
            base_url=profile["base_url"],
            use_saved_api_key=bool(profile["api_key_encrypted"]),
            profile_id=profile_id,
            adapter=profile["adapter"],
        )
    except (ValueError, LookupError, httpx.HTTPError) as error:
        raise HTTPException(400, str(error)) from error
    connection.execute(
        "UPDATE ai_profile_models SET is_available = 0 WHERE profile_id = ?",
        (profile_id,),
    )
    for model in result["models"]:
        connection.execute(
            """
            INSERT INTO ai_profile_models
                (profile_id, model_id, display_name, owned_by, provider,
                 is_visible, is_available, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(profile_id, model_id) DO UPDATE SET
                owned_by = excluded.owned_by,
                provider = excluded.provider,
                is_available = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                profile_id,
                model["id"],
                model["id"],
                model.get("owned_by", ""),
                result.get("source", ""),
            ),
        )
    connection.commit()
    return {
        **result,
        "profile_id": profile_id,
        "models": [
            {
                **model,
                "is_visible": True,
                "is_available": True,
            }
            for model in result["models"]
        ],
    }


@router.put("/profiles/{profile_id}/models")
def set_model_visibility(
    profile_id: int,
    request: AiModelVisibilityUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    _profile_or_404(connection, profile_id)
    cursor = connection.execute(
        """
        UPDATE ai_profile_models
        SET is_visible = ?, updated_at = CURRENT_TIMESTAMP
        WHERE profile_id = ? AND model_id = ?
        """,
        (int(request.is_visible), profile_id, request.model_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "模型不存在，请先同步模型列表")
    connection.commit()
    return {"ok": True}


@router.put("/profiles/{profile_id}/models/visibility")
def set_all_model_visibility(
    profile_id: int,
    request: AiModelsVisibilityUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    _profile_or_404(connection, profile_id)
    connection.execute(
        """
        UPDATE ai_profile_models
        SET is_visible = ?, updated_at = CURRENT_TIMESTAMP
        WHERE profile_id = ?
        """,
        (int(request.is_visible), profile_id),
    )
    connection.commit()
    return {"ok": True}


@router.post("/profiles/{profile_id}/test")
def test_profile(
    profile_id: int,
    request: AiProfileTestRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        content = chat_completion(
            connection,
            [{"role": "user", "content": "只回复“连接成功”，不要补充其他内容。"}],
            profile_id=profile_id,
            model=request.model,
        )
        return {"ok": True, "message": content.strip()}
    except (ValueError, LookupError, httpx.HTTPError) as error:
        raise HTTPException(400, f"连接失败：{error}") from error


@router.get("/selector-models")
def selector_models(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    ensure_ai_model_catalog(connection)
    rows = connection.execute(
        """
        SELECT m.profile_id, p.name AS profile_name, p.is_default,
               m.model_id, m.display_name, m.owned_by
        FROM ai_profile_models AS m
        JOIN ai_profiles AS p ON p.id = m.profile_id
        WHERE p.enabled = 1 AND m.is_visible = 1 AND m.is_available = 1
        ORDER BY p.is_default DESC, p.name COLLATE NOCASE, m.model_id COLLATE NOCASE
        """
    ).fetchall()
    return {
        "models": [
            {
                **dict(row),
                "is_default": bool(row["is_default"]),
            }
            for row in rows
        ]
    }


@router.get("/conversations")
def list_conversations(
    connection: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM ai_messages m WHERE m.conversation_id = c.id)
                   AS message_count
        FROM ai_conversations c
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT 50
        """
    ).fetchall()
    return [dict(row) for row in rows]


@router.post("/conversations")
def create_conversation(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    cursor = connection.execute("INSERT INTO ai_conversations DEFAULT VALUES")
    connection.commit()
    return _conversation_payload(connection, int(cursor.lastrowid))


@router.get("/conversations/{conversation_id}")
def read_conversation(
    conversation_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    return _conversation_payload(connection, conversation_id)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    cursor = connection.execute(
        "DELETE FROM ai_conversations WHERE id = ?",
        (conversation_id,),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "对话不存在")
    connection.commit()
    return {"ok": True}


@router.post("/chat")
def chat(
    request: AiChatRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    profile = _profile_or_404(connection, request.profile_id)
    ensure_ai_model_catalog(connection)
    selected = connection.execute(
        """
        SELECT 1 FROM ai_profile_models
        WHERE profile_id = ? AND model_id = ?
          AND is_visible = 1 AND is_available = 1
        """,
        (request.profile_id, request.model),
    ).fetchone()
    if not profile["enabled"] or selected is None:
        raise HTTPException(400, "所选模型当前不可用于对话")

    conversation_id = request.conversation_id
    if conversation_id is None:
        cursor = connection.execute("INSERT INTO ai_conversations DEFAULT VALUES")
        conversation_id = int(cursor.lastrowid)
    else:
        _conversation_payload(connection, conversation_id)

    attachments = [
        item
        for item in (request.attachments or [])
        if isinstance(item.data_url, str) and item.data_url.startswith("data:image/")
    ][:4]
    # data URL 为 ASCII base64，长度近似字节数；与前端附件条的上限保持一致。
    max_attachment_chars = 8 * 1024 * 1024
    for item in attachments:
        if len(item.data_url) > max_attachment_chars:
            raise HTTPException(422, "单张图片附件过大（超过 8 MiB），请裁剪或压缩后再试")
    user_text = request.message.strip()
    if not user_text and not attachments:
        raise HTTPException(400, "请输入问题或添加图片")

    history = connection.execute(
        """
        SELECT role, content FROM ai_messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT 24
        """,
        (conversation_id,),
    ).fetchall()
    messages = [dict(row) for row in reversed(history)]
    if attachments:
        content_parts = []
        if user_text:
            content_parts.append({"type": "text", "text": user_text})
        for item in attachments:
            content_parts.append({"type": "image_url", "image_url": {"url": item.data_url}})
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": user_text})
    system_prompt = (
        "你是英语刷题机中的考研英语学习助手。回答要准确、清晰、直接。"
        "用户可能在做题时提问，但除非用户明确要求，不要主动泄露当前题目的标准答案。"
    )
    if profile["system_prompt"].strip():
        system_prompt += "\n" + profile["system_prompt"].strip()
    try:
        content = chat_completion(
            connection,
            [{"role": "system", "content": system_prompt}, *messages],
            profile_id=request.profile_id,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
        )
    except (ValueError, LookupError, httpx.HTTPError) as error:
        raise HTTPException(400, f"对话失败：{error}") from error

    connection.execute(
        """
        INSERT INTO ai_messages
            (conversation_id, role, content, attachments, profile_id, model_id)
        VALUES (?, 'user', ?, ?, ?, ?)
        """,
        (
            conversation_id,
            user_text or "(图片)",
            json.dumps(
                [{"name": item.name, "data_url": item.data_url} for item in attachments],
                ensure_ascii=False,
            )
            if attachments
            else None,
            request.profile_id,
            request.model,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_messages
            (conversation_id, role, content, profile_id, model_id)
        VALUES (?, 'assistant', ?, ?, ?)
        """,
        (conversation_id, content, request.profile_id, request.model),
    )
    existing = connection.execute(
        "SELECT title FROM ai_conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    title = existing["title"]
    if title == "新对话":
        title = user_text.replace("\n", " ")[:28] or "图片提问"
    connection.execute(
        """
        UPDATE ai_conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (title, conversation_id),
    )
    connection.commit()
    return {
        "conversation_id": conversation_id,
        "title": title,
        "message": {
            "role": "assistant",
            "content": content,
            "profile_id": request.profile_id,
            "model_id": request.model,
        },
    }


def _scope_key(unit_ids: list[int]) -> str:
    return ",".join(str(value) for value in sorted(set(unit_ids)))


def _latest_wrong_snapshot(
    connection: sqlite3.Connection,
    unit_ids: list[int],
    question_ids: list[int],
) -> dict[str, object]:
    """Capture each unit's most recent submitted wrong answers for comparison."""
    placeholders = ",".join("?" for _ in question_ids)
    snapshot: dict[str, object] = {}
    for unit_id in unit_ids:
        rows = connection.execute(
            f"""
            SELECT q.id, q.number,
                   (SELECT pa.user_answer
                    FROM practice_answers AS pa
                    JOIN practice_sessions AS ps ON ps.id = pa.session_id
                    WHERE pa.question_id = q.id
                      AND pa.is_correct IS NOT NULL
                      AND TRIM(pa.user_answer) <> ''
                    ORDER BY COALESCE(ps.submitted_at, pa.answered_at) DESC,
                             pa.id DESC
                    LIMIT 1) AS user_answer
            FROM questions AS q
            WHERE q.unit_id = ? AND q.id IN ({placeholders})
            ORDER BY q.sequence
            """,
            (unit_id, *question_ids),
        ).fetchall()
        errors = [
            {
                "question_id": int(row["id"]),
                "number": int(row["number"]),
                "selected": row["user_answer"],
            }
            for row in rows
            if row["user_answer"]
        ]
        snapshot[str(unit_id)] = {"errors": errors}
    return snapshot


@router.post("/analyze-wrong")
def analyze_wrong(
    request: AiAnalyzeRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    question_ids = request.question_ids
    if not question_ids:
        question_ids = [
            row["question_id"]
            for row in connection.execute(
                """
                SELECT question_id FROM wrong_stats
                WHERE wrong_count >= 3 OR manually_frequent = 1
                ORDER BY wrong_count DESC, last_wrong_at DESC
                LIMIT 20
                """
            ).fetchall()
        ]
    if not question_ids:
        raise HTTPException(400, "没有可分析的错题")
    question_ids = list(dict.fromkeys(int(value) for value in question_ids))
    placeholders = ",".join("?" for _ in question_ids)
    unit_rows = connection.execute(
        f"SELECT DISTINCT unit_id FROM questions WHERE id IN ({placeholders})",
        question_ids,
    ).fetchall()
    unit_ids = [int(row["unit_id"]) for row in unit_rows]
    if not unit_ids:
        raise HTTPException(400, "没有可分析的篇目")
    scope_key = _scope_key(unit_ids)

    report = connection.execute(
        """
        SELECT * FROM wrong_analysis_reports
        WHERE scope_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (scope_key,),
    ).fetchone()
    states = connection.execute(
        f"""
        SELECT unit_id, report_id, analyzed_session_id
        FROM wrong_analysis_states
        WHERE unit_id IN ({",".join("?" for _ in unit_ids)})
        """,
        unit_ids,
    ).fetchall()
    states_by_unit = {int(row["unit_id"]): row for row in states}

    all_retried = True
    locked_report_ids: list[int] = []
    for unit_id in unit_ids:
        state = states_by_unit.get(unit_id)
        if state is None:
            continue
        completed = connection.execute(
            """
            SELECT 1 FROM practice_unit_submissions
            WHERE unit_id = ? AND session_id > ?
            LIMIT 1
            """,
            (unit_id, state["analyzed_session_id"]),
        ).fetchone()
        if not completed:
            all_retried = False
            locked_report_ids.append(int(state["report_id"]))

    cached_report = report if report is not None and not all_retried else None
    if cached_report is None and locked_report_ids:
        cached_report = connection.execute(
            """
            SELECT * FROM wrong_analysis_reports
            WHERE id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (locked_report_ids[0],),
        ).fetchone()
    if cached_report is not None:
        try:
            aggregate = json.loads(cached_report["aggregate_data"] or "{}")
        except json.JSONDecodeError:
            aggregate = {}
        return {
            "analysis": cached_report["report"],
            "aggregate": aggregate,
            "report_id": int(cached_report["id"]),
            "scope_title": cached_report["scope_title"],
            "cached": True,
            "locked": True,
            "reanalyze_after_retry": True,
        }

    previous_snapshot: dict[str, object] = {}
    if report is not None:
        try:
            previous_snapshot = json.loads(report["input_snapshot"] or "{}")
        except json.JSONDecodeError:
            previous_snapshot = {}
    if not previous_snapshot:
        for unit_id in unit_ids:
            state = states_by_unit.get(unit_id)
            if state is None:
                continue
            previous = connection.execute(
                "SELECT input_snapshot FROM wrong_analysis_reports WHERE id = ?",
                (state["report_id"],),
            ).fetchone()
            if previous is None:
                continue
            try:
                parsed = json.loads(previous["input_snapshot"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict) and str(unit_id) in parsed:
                previous_snapshot.setdefault(str(unit_id), parsed[str(unit_id)])

    try:
        diagnoses, _ = diagnose_wrong_answers(
            connection,
            question_ids,
            previous_snapshot=previous_snapshot or None,
        )
        aggregate = aggregate_diagnoses(diagnoses)
        content = write_anonymous_report(connection, aggregate)
        profile = get_ai_profile(connection)
        input_snapshot = _latest_wrong_snapshot(
            connection, unit_ids, question_ids
        )
        cursor = connection.execute(
            """
            INSERT INTO wrong_analysis_reports
                (scope_key, unit_ids, input_snapshot, scope_title,
                 question_count, aggregate_data, report, model_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_key,
                json.dumps(unit_ids),
                json.dumps(input_snapshot, ensure_ascii=False),
                request.scope_title or request.focus[:80],
                aggregate["question_count"],
                json.dumps(aggregate, ensure_ascii=False),
                content,
                profile["default_model"],
            ),
        )
        report_id = int(cursor.lastrowid)
        max_session_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM practice_sessions"
        ).fetchone()["max_id"]
        for unit_id in unit_ids:
            connection.execute(
                """
                INSERT OR REPLACE INTO wrong_analysis_states
                    (unit_id, report_id, analyzed_session_id, analyzed_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (unit_id, report_id, max_session_id),
            )
        connection.commit()
    except (ValueError, LookupError, httpx.HTTPError, json.JSONDecodeError) as error:
        raise HTTPException(400, f"分析失败：{error}") from error
    return {
        "analysis": content,
        "aggregate": aggregate,
        "report_id": report_id,
        "scope_title": request.scope_title,
        "cached": False,
        "locked": True,
        "reanalyze_after_retry": True,
    }


@router.get("/wrong-analysis-status")
def wrong_analysis_status(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = connection.execute(
        """
        SELECT s.unit_id, s.report_id, s.analyzed_session_id,
               r.scope_key, r.scope_title, r.report, r.aggregate_data
        FROM wrong_analysis_states s
        JOIN wrong_analysis_reports r ON r.id = s.report_id
        ORDER BY s.unit_id
        """
    ).fetchall()
    units = []
    for row in rows:
        completed = connection.execute(
            """
            SELECT 1 FROM practice_unit_submissions
            WHERE unit_id = ? AND session_id > ?
            LIMIT 1
            """,
            (row["unit_id"], row["analyzed_session_id"]),
        ).fetchone()
        try:
            aggregate = json.loads(row["aggregate_data"] or "{}")
        except json.JSONDecodeError:
            aggregate = {}
        units.append(
            {
                "unit_id": int(row["unit_id"]),
                "report_id": int(row["report_id"]),
                "scope_title": row["scope_title"],
                "scope_key": row["scope_key"],
                "locked": not bool(completed),
                "can_reanalyze": bool(completed),
                "report": row["report"],
                "aggregate": aggregate,
            }
        )
    return {"units": units}


@router.get("/question-labels/status")
def question_labels_status(
    year: int | None = None,
    paper_ids: str = "",
    question_bank_profile_id: int | None = None,
    run_id: str = "",
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        selected_paper_ids = [
            int(value.strip())
            for value in paper_ids.split(",")
            if value.strip()
        ]
    except ValueError as error:
        raise HTTPException(422, "paper_ids 必须是逗号分隔的数字") from error
    if len(selected_paper_ids) > 100 or any(value <= 0 for value in selected_paper_ids):
        raise HTTPException(422, "paper_ids 最多包含 100 个正整数")
    try:
        return labeling_status(
            connection,
            year,
            selected_paper_ids,
            question_bank_profile_id=question_bank_profile_id,
            run_id=run_id.strip(),
        )
    except ValueError as error:
        raise HTTPException(400, f"读取标注进度失败：{error}") from error


@router.post("/question-labels/runs/{run_id}/pause")
def pause_question_labels_run(
    run_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return pause_label_run(connection, run_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/question-labels/next")
def label_questions_next(
    request: AiLabelBatchRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    if any(value <= 0 for value in request.paper_ids):
        raise HTTPException(422, "paper_ids 只能包含正整数")
    try:
        return label_next_unit(
            connection,
            year=request.year,
            paper_ids=request.paper_ids,
            overwrite_unlocked=request.overwrite_unlocked,
            run_id=request.run_id.strip(),
            question_bank_profile_id=request.question_bank_profile_id,
            profile_id=request.profile_id,
            model=request.model.strip() or None,
            max_tokens=request.max_tokens,
        )
    except (ValueError, LookupError, httpx.HTTPError, json.JSONDecodeError) as error:
        if request.run_id.strip():
            _update_run_status(connection, request.run_id.strip(), "failed", str(error))
        raise HTTPException(400, f"标注失败：{error}") from error


@router.get("/question-labels")
def list_question_labels(
    year: int | None = None,
    paper_ids: str = "",
    search: str = "",
    limit: int = 100,
    connection: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    conditions = ["u.unit_type <> 'listening'"]
    params: list = []
    if year is not None:
        conditions.append("p.year = ?")
        params.append(year)
    try:
        selected_paper_ids = [
            int(value.strip())
            for value in paper_ids.split(",")
            if value.strip()
        ]
    except ValueError as error:
        raise HTTPException(422, "paper_ids 必须是逗号分隔的数字") from error
    if len(selected_paper_ids) > 100 or any(value <= 0 for value in selected_paper_ids):
        raise HTTPException(422, "paper_ids 最多包含 100 个正整数")
    if selected_paper_ids:
        conditions.append(
            f"p.id IN ({','.join('?' for _ in selected_paper_ids)})"
        )
        params.extend(selected_paper_ids)
    if search.strip():
        conditions.append(
            "(CAST(q.number AS TEXT) LIKE ? OR u.title LIKE ? OR l.primary_skill LIKE ?)"
        )
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern, pattern])
    params.append(max(1, min(limit, 300)))
    rows = connection.execute(
        f"""
        SELECT q.id AS question_id, q.number, p.year, u.title AS unit_title,
               l.primary_skill, l.secondary_skills, l.trap_types,
               l.attention_points, l.vocabulary_demand,
               l.context_dependency, l.grammar_dependency, l.confidence,
               l.locked, l.user_edited, l.model_name, l.updated_at
        FROM questions AS q
        JOIN units AS u ON u.id = q.unit_id
        JOIN papers AS p ON p.id = u.paper_id
        LEFT JOIN question_ai_labels AS l ON l.question_id = q.id
        WHERE {' AND '.join(conditions)}
        ORDER BY p.year DESC, u.sequence, q.sequence
        LIMIT ?
        """,
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for field in ("secondary_skills", "trap_types", "attention_points"):
            item[field] = json.loads(item[field] or "[]")
        item["primary_skill"] = item["primary_skill"] or ""
        item["vocabulary_demand"] = item["vocabulary_demand"] or "medium"
        item["context_dependency"] = item["context_dependency"] or "medium"
        item["grammar_dependency"] = item["grammar_dependency"] or "medium"
        item["confidence"] = float(item["confidence"] or 0)
        item["model_name"] = item["model_name"] or ""
        item["locked"] = bool(item["locked"])
        item["user_edited"] = bool(item["user_edited"])
        result.append(item)
    return result


@router.put("/question-labels/{question_id}")
def edit_question_label(
    question_id: int,
    request: AiQuestionLabelUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return update_question_label(
            connection,
            question_id,
            request.model_dump(),
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/imports/{job_id}/suggest-correction")
def suggest_correction(
    job_id: int,
    request: AiCorrectionRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    if row["status"] == "published":
        raise HTTPException(409, "已发布题库不能由模型直接编辑")
    draft = json.loads(row["draft_data"])
    instructions = f"""
你负责校正考研英语真题的结构化导入草稿。
只修复明显的 OCR、断行、题号、选项归属和结构问题，不能凭空改写文章。
标准答案来自文档答案表，除非草稿内部存在显而易见的错位，否则不要修改答案。
如果修改任何答案，必须把题号加入 answer_changes 数组，并说明原因。
处理范围：{request.scope}
用户补充要求：{request.instructions or '无'}

请只返回 JSON 对象，格式：
{{
  "draft": <完整修订后的原草稿对象>,
  "summary": "修订摘要",
  "answer_changes": [{{"number": 1, "old": "A", "new": "B", "reason": "..."}}]
}}
"""
    try:
        content = chat_completion(
            connection,
            [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(draft, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )
        suggestion = parse_json_response(content)
    except (ValueError, LookupError, json.JSONDecodeError, httpx.HTTPError) as error:
        raise HTTPException(400, f"模型校正失败：{error}") from error

    corrected = suggestion.get("draft")
    if not isinstance(corrected, dict):
        raise HTTPException(400, "模型没有返回完整草稿")
    corrected["warnings"] = validate_draft(corrected)
    connection.execute(
        """
        INSERT INTO revision_log
            (import_job_id, entity_type, entity_ref, field_name,
             old_value, new_value, source, model_name, approved)
        VALUES (?, 'draft', ?, 'all', ?, ?, 'ai', ?, 0)
        """,
        (
            job_id,
            str(job_id),
            json.dumps(draft, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
            get_ai_settings(connection)["model"],
        ),
    )
    connection.commit()
    return {
        "suggested_draft": corrected,
        "summary": suggestion.get("summary", ""),
        "answer_changes": suggestion.get("answer_changes", []),
        "warnings": corrected["warnings"],
        "requires_answer_confirmation": bool(suggestion.get("answer_changes")),
    }
