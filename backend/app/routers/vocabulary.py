from __future__ import annotations

import sqlite3
import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ..database import get_active_profile_id, get_db
from ..services.study_todos import VOCABULARY_PROFILE_CONDITION
from ..schemas import (
    VocabularyCreate,
    VocabularyEnrichmentRunRequest,
    VocabularyReview,
    VocabularyTranslationRunRequest,
    VocabularyUpdate,
)
from ..services.vocabulary import (
    _serialize_entry,
    _discrimination_list,
    add_vocabulary,
    ConflictError,
    local_similar_matches,
    queue_vocabulary_translations,
    review_entry,
    translate_queued_vocabulary,
)


router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


@router.get("/enrichment-status")
def enrichment_status(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    from ..services.vocabulary_maintenance import enrichment_status as status
    return status(connection)


@router.post("/enrichment-runs")
def enrichment_run(request: VocabularyEnrichmentRunRequest, background_tasks: BackgroundTasks,
                   connection: sqlite3.Connection = Depends(get_db)) -> dict:
    from ..services.vocabulary_maintenance import update_enrichment_run
    from ..services.vocabulary import process_vocabulary_enrichments
    status = update_enrichment_run(connection, request.action)
    # inventory 只建队不启动 worker；start/resume/retry 才进入后台处理。
    if request.action != "inventory" and not status['paused'] and not status['waiting_configuration']:
        background_tasks.add_task(process_vocabulary_enrichments)
    return status


@router.get("/status")
def vocabulary_status(scope: str = "all", connection: sqlite3.Connection = Depends(get_db)) -> dict:
    from datetime import datetime
    measured_at = datetime.now().isoformat(timespec="seconds")
    conditions = VOCABULARY_PROFILE_CONDITION if scope == "current" else "1 = 1"
    params = [get_active_profile_id(connection)] if scope == "current" else []
    counts = connection.execute(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END), 0) AS frequent,
               COALESCE(SUM(CASE WHEN study_status = 'mastered' THEN 1 ELSE 0 END), 0) AS mastered,
               COALESCE(SUM(CASE WHEN translation_status != 'ready' THEN 1 ELSE 0 END), 0) AS pending,
               COALESCE(SUM(CASE WHEN translation_status = 'ready'
                              AND study_status != 'mastered'
                              AND (next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP)
                        THEN 1 ELSE 0 END), 0) AS review
        FROM vocabulary_entries
        WHERE {conditions}
        """,
        params,
    ).fetchone()
    revision = connection.execute("SELECT COALESCE(MAX(id), 0) FROM vocabulary_reviews").fetchone()[0]
    return {
        "scope_key": f"profile:{get_active_profile_id(connection)}" if scope == "current" else "all",
        "revision": str(revision),
        "counts": dict(counts),
        "measured_at": measured_at,
    }

@router.post("")
def create_entry(
    request: VocabularyCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = add_vocabulary(connection, request.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return result


@router.get("")
def list_entries(
    status: str = "all",
    search: str = "",
    scope: str = "all",
    limit: int = 0,
    offset: int = 0,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    conditions = ["1 = 1"]
    params: list[object] = []
    if scope == "current":
        conditions.append(VOCABULARY_PROFILE_CONDITION)
        params.append(get_active_profile_id(connection))
    if status == "frequent":
        conditions.append("(encounter_count >= 2 OR manually_frequent = 1)")
    elif status == "review":
        conditions.append(
            "(next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP)"
        )
        conditions.append("translation_status = 'ready'")
        conditions.append("study_status != 'mastered'")
    elif status == "learning":
        conditions.append("study_status = 'learning'")
    elif status == "mastered":
        conditions.append("study_status = 'mastered'")
    elif status == "pending":
        conditions.append("translation_status != 'ready'")
    if search.strip():
        conditions.append(
            "(term LIKE ? OR lemma LIKE ? OR contextual_meaning LIKE ? OR common_meaning LIKE ?)"
        )
        needle = f"%{search.strip()}%"
        params.extend([needle] * 4)
    rows = connection.execute(
        f"""
        SELECT *,
               (SELECT rating FROM vocabulary_reviews WHERE entry_id = vocabulary_entries.id
                ORDER BY datetime(reviewed_at) DESC, id DESC LIMIT 1) AS last_result,
               (
                   SELECT context_sentence
                   FROM vocabulary_occurrences
                   WHERE entry_id = vocabulary_entries.id
                   ORDER BY id DESC LIMIT 1
               ) AS latest_sentence,
               CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END AS is_frequent
        FROM vocabulary_entries
        WHERE {' AND '.join(conditions)}
        ORDER BY
                 CASE WHEN datetime(last_seen_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
                 is_frequent DESC, encounter_count DESC, last_seen_at DESC
        """,
        params,
    ).fetchall()
    if limit > 0:
        rows = connection.execute(
            f"""
            SELECT *,
               (SELECT rating FROM vocabulary_reviews WHERE entry_id = vocabulary_entries.id
                ORDER BY datetime(reviewed_at) DESC, id DESC LIMIT 1) AS last_result,
               (
                   SELECT context_sentence
                   FROM vocabulary_occurrences
                   WHERE entry_id = vocabulary_entries.id
                   ORDER BY id DESC LIMIT 1
               ) AS latest_sentence,
               CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END AS is_frequent
        FROM vocabulary_entries
        WHERE {' AND '.join(conditions)}
        ORDER BY
                 CASE WHEN datetime(last_seen_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
                 is_frequent DESC, encounter_count DESC, last_seen_at DESC
        LIMIT ? OFFSET ?
        """,
            [*params, limit, offset],
        ).fetchall()
    revision = connection.execute(
        "SELECT COALESCE(MAX(id), 0) FROM vocabulary_reviews"
    ).fetchone()[0]
    counts = connection.execute(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END), 0) AS frequent,
               COALESCE(SUM(CASE WHEN study_status = 'mastered' THEN 1 ELSE 0 END), 0) AS mastered,
               COALESCE(SUM(CASE WHEN translation_status != 'ready' THEN 1 ELSE 0 END), 0) AS pending,
               COALESCE(SUM(CASE WHEN translation_status = 'ready'
                              AND study_status != 'mastered'
                              AND (next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP)
                        THEN 1 ELSE 0 END), 0) AS review
        FROM vocabulary_entries
        WHERE {VOCABULARY_PROFILE_CONDITION if scope == "current" else "1 = 1"}
        """,
        [get_active_profile_id(connection)] if scope == "current" else [],
    ).fetchone()
    items = [dict(row) for row in rows]
    local_map = local_similar_matches(connection, [item["id"] for item in items])
    review_counts = dict(connection.execute('SELECT entry_id,COUNT(*) FROM vocabulary_reviews GROUP BY entry_id').fetchall())
    for item in items:
        item["review_revision"] = json.dumps([item.get("normalized_term"), item.get("last_reviewed_at"), item.get("next_review_at"), item.get("updated_at"), review_counts.get(item["id"], 0)], ensure_ascii=False, separators=(",", ":"))
        item["local_similar"] = local_map.get(item["id"], [])
        for field in ("synonyms", "antonyms", "similar_forms"):
            item[field] = _discrimination_list(item.get(field))
    result_counts = dict(counts)
    from ..services.vocabulary_context import project_vocabulary
    histories: dict[int, list[dict]] = {}
    for occurrence in connection.execute("SELECT * FROM vocabulary_occurrences"):
        histories.setdefault(occurrence["entry_id"], []).append(dict(occurrence))
    items = [project_vocabulary(item, histories.get(item["id"], [])) for item in items]
    return {"items": items, "counts": result_counts, "scope_key": f"profile:{get_active_profile_id(connection)}" if scope == "current" else "all", "revision": str(revision)}


@router.get("/home")
def home_words(
    limit: int = Query(20, ge=1, le=50),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = connection.execute(
        """
        SELECT id, term, lemma, contextual_meaning, common_meaning,
               encounter_count, study_status,
               CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END AS is_frequent
        FROM vocabulary_entries
        WHERE translation_status = 'ready'
        ORDER BY
                 CASE WHEN datetime(created_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
                 is_frequent DESC,
                 CASE WHEN next_review_at IS NULL OR next_review_at <= CURRENT_TIMESTAMP THEN 0 ELSE 1 END,
                 encounter_count DESC,
                 RANDOM()
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/translation-runs")
def create_translation_run(
    request: VocabularyTranslationRunRequest,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    queued_ids = queue_vocabulary_translations(
        connection,
        request.entry_ids,
        include_all_pending=request.trigger in ("practice_exit", "vocabulary_open"),
    )
    if queued_ids:
        background_tasks.add_task(translate_queued_vocabulary)
    return {
        "accepted": True,
        "trigger": request.trigger,
        "queuedCount": len(queued_ids),
        "workerStarted": bool(queued_ids),
    }


@router.get("/translation-status")
def translation_status(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    result = {row["status"]: row["count"] for row in connection.execute(
        "SELECT translation_status AS status, COUNT(*) AS count FROM vocabulary_entries "
        "WHERE user_edited = 0 GROUP BY translation_status"
    )}
    result.update({'enrichment_' + row['state']: row['count'] for row in connection.execute(
        'SELECT state,COUNT(*) AS count FROM vocabulary_enrichment_jobs GROUP BY state'
    )})
    return result


@router.get("/{entry_id}")
def read_entry(
    entry_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        return _serialize_entry(connection, entry_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/{entry_id}")
def update_entry(
    entry_id: int,
    request: VocabularyUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    fields = request.model_dump(exclude_none=True)
    if not fields:
        return _serialize_entry(connection, entry_id)
    allowed = {
        "contextual_meaning",
        "common_meaning",
        "phonetic",
        "part_of_speech",
        "note",
        "study_status",
        "manually_frequent",
    }
    assignments = []
    values: list[object] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        values.append(int(value) if key == "manually_frequent" else value)
    assignments.extend(["user_edited = 1", "updated_at = CURRENT_TIMESTAMP"])
    if "contextual_meaning" in fields:
        assignments.append("contextual_occurrence_key = ?")
        values.append(_serialize_entry(connection, entry_id)["context_key"])
    values.append(entry_id)
    connection.execute(
        f"UPDATE vocabulary_entries SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    connection.commit()
    return _serialize_entry(connection, entry_id)


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    connection.execute("DELETE FROM vocabulary_entries WHERE id = ?", (entry_id,))
    connection.commit()
    return {"ok": True}


@router.post("/{entry_id}/retry")
@router.post("/retry-pending")
def retry_translation(
    background_tasks: BackgroundTasks,
    entry_id: int = 0,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET translation_status = 'queued', translation_error = '',
            updated_at = CURRENT_TIMESTAMP
        WHERE user_edited = 0 AND translation_status IN ('pending', 'failed')
        """,
    )
    connection.execute("UPDATE vocabulary_enrichment_jobs SET state='queued',error='',updated_at=CURRENT_TIMESTAMP WHERE state='failed'")
    connection.commit()
    background_tasks.add_task(translate_queued_vocabulary)
    from ..services.vocabulary import process_vocabulary_enrichments
    background_tasks.add_task(process_vocabulary_enrichments)
    return {"ok": True}


@router.post("/{entry_id}/enrichment")
def enrich_entry(entry_id: int, connection: sqlite3.Connection = Depends(get_db)) -> dict:
    from ..services.vocabulary import queue_vocabulary_enrichment, process_vocabulary_enrichments
    try:
        _serialize_entry(connection, entry_id)
        queue_vocabulary_enrichment(connection, entry_id)
        connection.commit()
        process_vocabulary_enrichments()
        job = connection.execute('SELECT state,error FROM vocabulary_enrichment_jobs WHERE entry_id=?', (entry_id,)).fetchone()
        if job and job['state'] == 'failed':
            raise ValueError(job['error'])
        return _serialize_entry(connection, entry_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/{entry_id}/review")
def submit_review(
    entry_id: int,
    request: VocabularyReview,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return review_entry(connection, entry_id, request.rating, expected_revision=request.expected_revision, mode=request.mode, command_id=request.command_id or "")
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
