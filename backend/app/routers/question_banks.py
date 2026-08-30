from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..config import QUESTION_BANK_DIR, UPLOAD_DIR
from ..database import get_active_profile_id, get_db
from ..schemas import QuestionBankPublishRequest
from ..services.esq import (
    MAX_PACKAGE_BYTES,
    EsqValidationError,
    build_preview,
    export_package,
    load_esq_package,
    publish_package,
)
from ..services.trash import trash_import_job


router = APIRouter(prefix="/question-banks", tags=["question-banks"])


def _error(code: str, message: str, details: object | None = None) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": code, "message": message, "details": details or []},
    )


def _serialize_job(row: sqlite3.Row) -> dict:
    payload = dict(row)
    payload["warnings"] = json.loads(payload.get("warnings") or "[]")
    if "draft_data" in payload:
        payload["draft_data"] = json.loads(payload.get("draft_data") or "{}")
    try:
        parse_context = json.loads(payload.pop("parse_context", "{}") or "{}")
    except json.JSONDecodeError:
        parse_context = {}
    payload["published_paper_ids"] = [
        int(value)
        for value in parse_context.get("published_paper_ids", [])
        if isinstance(value, int) and value > 0
    ]
    payload["published_scope_title"] = str(
        parse_context.get("published_scope_title", "")
    ).strip()
    return payload


def create_question_bank_import(
    connection: sqlite3.Connection,
    stored_path: Path,
    filename: str,
    profile_id: int | None = None,
) -> dict:
    selected_profile_id = profile_id or get_active_profile_id(connection)
    if not connection.execute(
        "SELECT 1 FROM question_bank_profiles WHERE id = ? AND deleted_at IS NULL",
        (selected_profile_id,),
    ).fetchone():
        raise _error("PROFILE_NOT_FOUND", "目标题库配置不存在")
    package = load_esq_package(stored_path)
    preview = build_preview(connection, package, profile_id=selected_profile_id)
    serializable = {**package, "source_path": str(stored_path)}
    detected_year = package["papers"][0]["year"] if package["papers"] else None
    cursor = connection.execute(
        """
        INSERT INTO import_jobs
            (profile_id, filename, stored_path, detected_year, detected_format,
             status, draft_data, warnings)
        VALUES (?, ?, ?, ?, 'esq-1.0', 'draft', ?, '[]')
        """,
        (
            selected_profile_id,
            filename,
            str(stored_path),
            detected_year,
            json.dumps(serializable, ensure_ascii=False),
        ),
    )
    connection.commit()
    return {
        "id": cursor.lastrowid,
        "filename": filename,
        "format": "esq-1.0",
        "preview": preview,
        "warnings": [],
        "profile_id": selected_profile_id,
    }


@router.get("/imports")
def list_question_bank_imports(
    connection: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    profile_id = get_active_profile_id(connection)
    rows = connection.execute(
        """
        SELECT id, profile_id, filename, detected_year, detected_format, status,
               warnings, parse_context, created_at, updated_at
        FROM import_jobs
        WHERE detected_format = 'esq-1.0'
          AND profile_id = ? AND deleted_at IS NULL
        ORDER BY id DESC
        """,
        (profile_id,),
    ).fetchall()
    return [_serialize_job(row) for row in rows]


@router.post("/imports")
async def upload_question_bank(
    file: UploadFile = File(...),
    profile_id: int | None = Form(default=None),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not file.filename or Path(file.filename).suffix.lower() not in {".esq", ".zip"}:
        raise _error("UNSUPPORTED_FILE", "请选择 .esq 或 .zip 题库包")
    stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.esq"
    size = 0
    try:
        with stored_path.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PACKAGE_BYTES:
                    raise _error("FILE_TOO_LARGE", "ESQ 文件不能超过 100 MiB")
                target.write(chunk)
        return create_question_bank_import(connection, stored_path, file.filename, profile_id)
    except EsqValidationError as error:
        stored_path.unlink(missing_ok=True)
        raise _error("VALIDATION_ERROR", "题库包校验失败", error.details) from error
    except HTTPException:
        stored_path.unlink(missing_ok=True)
        raise
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        raise _error("IMPORT_FAILED", "题库包导入失败，请检查文件内容") from error


@router.get("/imports/{job_id}")
def question_bank_import_detail(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ? AND detected_format = 'esq-1.0'",
        (job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "题库导入任务不存在"})
    payload = _serialize_job(row)
    package = payload["draft_data"]
    preview = build_preview(connection, package, profile_id=int(row["profile_id"]))
    return {**payload, "preview": preview}


@router.delete("/imports/{job_id}")
def delete_question_bank_import(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = trash_import_job(connection, job_id)
        connection.commit()
        return {"trashed": True, **result}
    except ValueError as error:
        connection.rollback()
        raise _error("DELETE_FAILED", str(error)) from error


@router.post("/imports/{job_id}/publish")
def publish_question_bank(
    job_id: int,
    request: QuestionBankPublishRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ? AND detected_format = 'esq-1.0'",
        (job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "题库导入任务不存在"})
    payload = _serialize_job(row)
    package = payload["draft_data"]
    preview = build_preview(connection, package, profile_id=int(row["profile_id"]))
    existing_conflicts = {
        item["paperKey"]
        for item in preview["conflicts"]
        if item.get("existing")
    }
    resolutions = {item.paper_key: item.action for item in request.resolutions}
    missing = sorted(existing_conflicts - resolutions.keys())
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT_RESOLUTION_REQUIRED",
                "message": "请先决定重复年份或篇目的处理方式",
                "details": [{"paperKey": paper_key} for paper_key in missing],
            },
        )
    source_path = Path(package.get("source_path", ""))
    if not source_path.is_file():
        raise _error("SOURCE_FILE_MISSING", "导入源文件已经不存在")
    try:
        result = publish_package(
            connection,
            package,
            source_path,
            resolutions,
            import_ai_labels=request.import_ai_labels,
            profile_id=int(row["profile_id"]),
        )
        published_papers = [
            item
            for item in result.get("papers", [])
            if item.get("action") != "keep_existing" and item.get("paperId")
        ]
        published_paper_ids = [int(item["paperId"]) for item in published_papers]
        try:
            parse_context = json.loads(row["parse_context"] or "{}")
        except json.JSONDecodeError:
            parse_context = {}
        parse_context["published_paper_ids"] = published_paper_ids
        parse_context["published_scope_title"] = str(
            preview.get("title") or row["filename"]
        )
        connection.execute(
            """
            UPDATE import_jobs
            SET status = 'published', parse_context = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(parse_context, ensure_ascii=False), job_id),
        )
        connection.commit()
        question_count = sum(
            int(item.get("questionCount", 0) or 0) for item in published_papers
        )
        return {
            "published": True,
            **result,
            "paperIds": published_paper_ids,
            "scopeTitle": parse_context["published_scope_title"],
            "questionCount": question_count,
        }
    except Exception as error:
        connection.rollback()
        raise _error("PUBLISH_FAILED", "题库包发布失败，请检查冲突和数据完整性") from error


@router.get("/export")
def export_question_bank(
    years: str | None = Query(default=None),
    include_answers: bool = Query(default=True),
    include_labels: bool = Query(default=False),
    connection: sqlite3.Connection = Depends(get_db),
) -> Response:
    selected_years: list[int] | None = None
    if years:
        try:
            selected_years = sorted({int(item.strip()) for item in years.split(",") if item.strip()})
        except ValueError as error:
            raise _error("INVALID_YEARS", "年份参数必须是逗号分隔的数字") from error
    try:
        content, filename = export_package(
            connection,
            years=selected_years,
            include_answers=include_answers,
            include_labels=include_labels,
            profile_id=get_active_profile_id(connection),
        )
    except ValueError as error:
        raise _error("EXPORT_FAILED", str(error)) from error
    return Response(
        content=content,
        media_type="application/vnd.english-study-question-bank",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/schema/{version}")
def get_question_bank_schema(version: str) -> FileResponse:
    if version != "1.0":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "不支持的 ESQ Schema 版本"})
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "esq-1.0.schema.json"
    return FileResponse(schema_path, media_type="application/schema+json", filename=schema_path.name)


@router.get("/assets/{package_id}/{content_version}/{asset_id}")
def get_question_bank_asset(
    package_id: str,
    content_version: str,
    asset_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> FileResponse:
    row = connection.execute(
        """
        SELECT stored_path, media_type
        FROM question_bank_assets
        WHERE package_id = ? AND content_version = ? AND asset_id = ?
        """,
        (package_id, content_version, asset_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "题库资源不存在"})
    path = Path(row["stored_path"])
    root = QUESTION_BANK_DIR.resolve()
    if not path.is_file() or root not in path.resolve().parents:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "题库资源不存在"})
    return FileResponse(path, media_type=row["media_type"])
