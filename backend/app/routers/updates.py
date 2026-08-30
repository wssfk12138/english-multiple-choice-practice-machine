from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas import UpdateOpenRequest
from ..services.updates import (
    UpdateError,
    check_for_update,
    download_official_package,
    open_verified_package,
    update_status,
)


router = APIRouter(prefix="/updates", tags=["updates"])


def _http_error(exc: UpdateError, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/status")
def get_update_status() -> dict[str, object]:
    return update_status()


@router.post("/check")
def check_update() -> dict[str, object]:
    try:
        return check_for_update()
    except UpdateError as exc:
        raise _http_error(exc, 502) from exc


@router.post("/download")
async def download_update(request: Request) -> dict[str, object]:
    if await request.body():
        raise HTTPException(status_code=422, detail="下载更新不接受客户端 URL 或清单")
    try:
        return download_official_package()
    except UpdateError as exc:
        raise _http_error(exc, 502) from exc


@router.post("/open")
def open_update(payload: UpdateOpenRequest) -> dict[str, str]:
    try:
        return open_verified_package(payload.file_name)
    except UpdateError as exc:
        raise _http_error(exc, 400) from exc
