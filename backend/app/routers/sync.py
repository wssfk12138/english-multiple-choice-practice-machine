from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import LanSyncHandshake, LanSyncPullRequest, LanSyncPushRequest
from ..services import sync as sync_service

router = APIRouter(prefix="/lan-sync", tags=["lan-sync-management"])
public_router = APIRouter(prefix="/lan-sync", tags=["lan-sync"])


@router.get("/status")
def status(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    return _management_status(connection)


@router.post("/passcode")
def create_passcode(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    passcode = sync_service.reset_passcode(connection)
    from ..lan_sync_host import lan_sync_host_running, start_lan_sync_host

    if not lan_sync_host_running() and not start_lan_sync_host():
        sync_service.disable_sync(connection)
        raise HTTPException(
            503,
            "局域网同步端口 8766 无法监听，请关闭占用该端口的程序后重试",
        )
    if not lan_sync_host_running():
        sync_service.disable_sync(connection)
        raise HTTPException(503, "局域网同步主机未能启动，请稍后重试")
    return {**_management_status(connection), "passcode": passcode}


@router.post("/disable")
def disable(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    sync_service.disable_sync(connection)
    from ..lan_sync_host import stop_lan_sync_host
    stop_lan_sync_host()
    return _management_status(connection)


@router.post("/devices/revoke")
def revoke_devices(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    revoked = sync_service.revoke_all_devices(connection)
    return {"revoked": revoked}


def _management_status(connection: sqlite3.Connection) -> dict:
    from ..lan_sync_host import lan_sync_host_running

    result = sync_service.sync_status(connection)
    listening = lan_sync_host_running()
    return {
        **result,
        "listening": listening,
        "host_urls": result["host_urls"] if listening else [],
    }


@public_router.post("/handshake")
def handshake(
    payload: LanSyncHandshake,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    token = sync_service.issue_token(
        connection, payload.passcode, payload.profile_names, payload.device_id
    )
    if not token:
        raise HTTPException(401, "同步口令不正确")
    return {"token": token, "tables": sorted(sync_service.SYNC_TABLES)}


@public_router.post("/pull")
def pull(
    payload: LanSyncPullRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    token = _require_token(payload)
    snapshot = sync_service.snapshot(
        connection,
        payload.tables,
        sync_service.token_profiles(token),
        payload.cursor,
        payload.tombstone_cursor,
    )
    sync_service.touch_last_sync(connection)
    return snapshot


@public_router.post("/push")
def push(
    payload: LanSyncPushRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    token = _require_token(payload)
    try:
        applied = sync_service.merge(
            connection,
            payload.changes,
            payload.tombstones,
            sync_service.token_profiles(token),
        )
    except ValueError as exc:
        raise HTTPException(422, f"同步数据无法合并：{exc}") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(422, "同步数据违反本机数据库约束") from exc
    sync_service.touch_last_sync(connection)
    return {"applied": applied}


def _require_token(payload: LanSyncPullRequest | LanSyncPushRequest) -> str:
    token = getattr(payload, "token", None)
    if not isinstance(token, str) or not sync_service.verify_token(token):
        raise HTTPException(401, "同步会话已过期，请重新握手")
    return token
