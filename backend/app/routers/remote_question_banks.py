from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..config import UPLOAD_DIR
from ..database import get_db
from ..schemas import QuestionBankCatalogSettingsWrite, QuestionBankDownloadRequest
from ..services.esq import EsqValidationError
from ..services.question_bank_catalog import (
    QuestionBankCatalogError, download_package, fetch_catalog, find_package, validate_remote_url,
)
from .question_banks import create_question_bank_import


router = APIRouter(prefix="/remote-question-banks", tags=["remote-question-banks"])
SETTING_KEY = "question_bank_catalog_url"


def _read_url(connection) -> str:
    row = connection.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    return str(row["value"] if row else "")


@router.get("/settings")
def get_settings(connection=Depends(get_db)) -> dict[str, str]:
    return {"question_bank_catalog_url": _read_url(connection)}


@router.put("/settings")
def update_settings(payload: QuestionBankCatalogSettingsWrite, connection=Depends(get_db)) -> dict[str, str]:
    try:
        value = validate_remote_url(payload.question_bank_catalog_url) if payload.question_bank_catalog_url.strip() else ""
    except QuestionBankCatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    connection.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, value),
    )
    connection.commit()
    return {"question_bank_catalog_url": value}


@router.post("/check")
def check_catalog(connection=Depends(get_db)) -> dict:
    url = _read_url(connection)
    if not url:
        raise HTTPException(status_code=422, detail="尚未配置远程题库目录地址")
    try:
        return fetch_catalog(url)
    except QuestionBankCatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/download")
def download_question_bank(payload: QuestionBankDownloadRequest, connection=Depends(get_db)) -> dict:
    url = _read_url(connection)
    if not url:
        raise HTTPException(status_code=422, detail="尚未配置远程题库目录地址")
    stored_path: Path | None = None
    try:
        catalog = fetch_catalog(url)
        package = find_package(catalog, payload.package_id, payload.content_version)
        stored_path = download_package(package, UPLOAD_DIR)
        result = create_question_bank_import(connection, stored_path, package["fileName"])
        return {**result, "package_id": package["packageId"], "content_version": package["contentVersion"], "verified_size": package["size"], "verified_sha256": package["sha256"]}
    except HTTPException:
        raise
    except EsqValidationError as exc:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "题库包校验失败", "details": exc.details}) from exc
    except QuestionBankCatalogError as exc:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="远程题库无法建立导入草稿") from exc
