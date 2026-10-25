from __future__ import annotations

import sqlite3
import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from ..database import get_db
from ..schemas import LanSyncHandshake, LanSyncPullRequest, LanSyncPushRequest
from ..services import sync as sync_service
from ..services import sync_permissions as permissions
from pydantic import BaseModel, Field
from ..services import model_key_sync, model_keys, model_snapshot
from ..services import lan_question_banks as banks
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/lan-sync", tags=["lan-sync-management"])
public_router = APIRouter(prefix="/lan-sync", tags=["lan-sync"])


class BankSharing(BaseModel):
    enabled: bool
    profile_ids: list[int] = Field(default_factory=list, max_length=100)


class BankAccess(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    sha256: str = Field(default='', max_length=64)


@router.get('/question-banks/sharing')
def bank_sharing(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    with banks.LOCK:
        return {**banks.state(), 'choices': banks.choices(connection)}


@router.put('/question-banks/sharing')
def update_bank_sharing(payload: BankSharing, connection: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        return banks.publish(connection, payload.enabled, payload.profile_ids)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Question bank publication failed; previous snapshot is unchanged') from None


def _authorize_banks(connection, token, request):
    if _transport(request) != 'tls-v2':
        raise HTTPException(403, 'Question bank sharing requires authenticated TLS')
    permissions.authorize(connection, token, 'question_bank', 'tls-v2')


@public_router.post('/question-banks/catalog')
def shared_bank_catalog(payload: BankAccess, request: Request, response: Response,
                        connection: sqlite3.Connection = Depends(get_db)) -> dict:
    response.headers['Cache-Control'] = 'no-store'
    with sync_service._tokens_lock, banks.LOCK:
        _authorize_banks(connection, payload.token, request)
        return {**banks.catalog(), 'hostId': request.app.state.lan_tls_host_id}


@public_router.post('/question-banks/download')
def shared_bank_download(payload: BankAccess, request: Request,
                         connection: sqlite3.Connection = Depends(get_db)):
    with sync_service._tokens_lock, banks.LOCK:
        _authorize_banks(connection, payload.token, request)
        path, item = banks.package(payload.sha256)
        if not path.is_file() or path.stat().st_size != item['size']:
            raise HTTPException(409, 'Shared package unavailable; republish the snapshot')
    # FastAPI 0.116 closes yield dependencies before streaming. Own each read connection.
    # https://github.com/fastapi/fastapi/blob/0.116.1/docs/en/docs/advanced/custom-response.md
    def chunks():
        from .. import database
        with path.open('rb') as source:
            while True:
                current = database.connect()
                try:
                    with sync_service._tokens_lock, banks.LOCK:
                        _authorize_banks(current, payload.token, request)
                        banks.package(payload.sha256)
                        chunk = source.read(64 * 1024)
                finally:
                    current.close()
                if not chunk:
                    break
                yield chunk
    return StreamingResponse(chunks(), media_type='application/zip',
                             headers={'Cache-Control': 'no-store', 'Content-Length': str(item['size']),
                                      'X-Content-Type-Options': 'nosniff'})


@public_router.post("/model-keys")
async def exchange_model_keys(request: Request, response: Response,
                              connection: sqlite3.Connection = Depends(get_db)) -> dict:
    if _transport(request) != "tls-v2":
        raise HTTPException(403, "Model keys require authenticated TLS")
    response.headers["Cache-Control"] = "no-store"
    try:
        raw = await request.body()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError()
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("operation") == "authoritative_push":
            payload = model_snapshot.SnapshotExchange.model_validate(data)
        else:
            payload = model_key_sync.Exchange.model_validate(data)
            model_key_sync.validate_inventory(payload.known)
    except Exception:
        raise HTTPException(422, "Invalid model key request") from None
    with sync_service._tokens_lock, model_keys.LOCK:
        permissions.authorize(connection, payload.token, "models", "tls-v2")
        try:
            if payload.operation == "pull":
                return {"version": 1, "known": model_key_sync.inventory(connection),
                        "profiles": model_key_sync.export(connection, payload.known)}
            if payload.operation == "authoritative_push":
                applied = model_snapshot.receive(connection, [p.model_dump() for p in payload.profiles])
                return {"version": 1, "snapshot_version": 1, "applied_profiles": applied}
            conflicts = model_key_sync.receive(connection, [p.model_dump() for p in payload.profiles])
            return {"version": 1, "conflicts": conflicts}
        except Exception:
            raise HTTPException(422, "Model key exchange failed; retry after checking secure storage") from None


class CategoryPreferences(BaseModel):
    categories: list[str] = Field(max_length=5)


@router.get("/devices/{device_id}/permissions")
def device_permissions(device_id: str = Path(min_length=1, max_length=128),
                       connection: sqlite3.Connection = Depends(get_db)) -> dict:
    with sync_service._tokens_lock:
        host = permissions.preferences(connection, device_id)
        selected = next((sync_service._token_categories.get(token) or []
                         for token, device in reversed(list(sync_service._token_devices.items()))
                         if device == device_id and sync_service.verify_token(token)), [])
        return {"categories": host, "effective_categories": [c for c in selected if c in host]}


@router.put("/devices/{device_id}/permissions")
def update_device_permissions(payload: CategoryPreferences,
                              device_id: str = Path(min_length=1, max_length=128),
                              connection: sqlite3.Connection = Depends(get_db)) -> dict:
    return permissions.set_preferences(connection, device_id, payload.categories)


@router.get("/status")
def status(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    return _management_status(connection)


@router.post("/passcode")
def create_passcode(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    passcode = sync_service.reset_passcode(connection)
    from ..lan_sync_host import LAN_SYNC_PORT, lan_sync_host_running, start_lan_sync_host

    if not lan_sync_host_running() and not start_lan_sync_host():
        sync_service.disable_sync(connection)
        raise HTTPException(
            503,
            f"局域网同步端口 {LAN_SYNC_PORT} 无法监听，请关闭占用该端口的程序后重试",
        )
    if not lan_sync_host_running():
        sync_service.disable_sync(connection)
        raise HTTPException(503, "局域网同步主机未能启动，请稍后重试")
    return {**_management_status(connection), "passcode": passcode}


@router.post("/pairing")
def create_tls_pairing(response: Response, connection: sqlite3.Connection = Depends(get_db),
                       replace_device_id: str = Query(default="", max_length=128)) -> dict:
    from ..lan_tls_host import LAN_TLS_PORT, start_tls_host, tls_status
    from urllib.parse import urlsplit
    if not sync_service.sync_enabled(connection):
        raise HTTPException(409, "请先开启局域网同步")
    if not start_tls_host():
        raise HTTPException(503, f"安全同步无法启动，请检查 {LAN_TLS_PORT} 端口和本机身份；不会回退 HTTP")
    state = tls_status()
    urls = sync_service.sync_status(connection)["host_urls"]
    if not state["tls_identity"] or not urls:
        raise HTTPException(503, "安全同步尚未就绪或未找到局域网地址")
    try:
        invitation = sync_service.create_pairing_invitation(connection, replace_device_id)
    except ValueError:
        raise HTTPException(409, "旧绑定已变化，请刷新设备列表")
    response.headers["Cache-Control"] = "no-store"
    return {
        "type": "english-practice-lan-sync", "version": 2,
        "host": f"https://{urlsplit(urls[0]).hostname}:{LAN_TLS_PORT}",
        **state["tls_identity"], **invitation,
    }


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


@router.get("/devices")
def devices(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    return sync_service.paired_devices(connection, limit, offset)


class DeviceName(BaseModel):
    display_name: str = Field(max_length=80, pattern=r"^[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]*$")


@router.put("/devices/{device_id}/name")
def rename_device(payload: DeviceName, device_id: str = Path(min_length=1, max_length=128),
                  connection: sqlite3.Connection = Depends(get_db)) -> dict:
    # Host-local management only; LAN clients cannot rename other devices.
    with sync_service._tokens_lock:
        sync_service._ensure_paired_devices_table(connection)
        cursor = connection.execute("UPDATE sync_paired_devices SET display_name = ? WHERE device_id = ?",
                                    (payload.display_name.strip(), device_id))
        if not cursor.rowcount:
            raise HTTPException(404, "设备已解除配对，请刷新列表")
        connection.commit()
    return {"display_name": payload.display_name.strip()}


@router.delete("/devices/{device_id}")
def revoke_device(
    device_id: str = Path(min_length=1, max_length=128),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"revoked": sync_service.revoke_device(connection, device_id)}


def _management_status(connection: sqlite3.Connection) -> dict:
    from ..lan_sync_host import lan_sync_host_running
    from ..lan_tls_host import tls_status

    result = sync_service.sync_status(connection)
    listening = lan_sync_host_running()
    return {
        **result,
        **tls_status(),
        "listening": listening,
        "host_urls": result["host_urls"] if listening else [],
    }


@public_router.post("/handshake")
def handshake(
    payload: LanSyncHandshake,
    request: Request,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    host_id = getattr(request.app.state, "lan_tls_host_id", "")
    if payload.categories is not None:
        permissions.categories(payload.categories)
    transport = _transport(request)
    if transport == "tls-v2":
        if payload.host_id != host_id:
            raise HTTPException(401, "同步主机身份不匹配")
    elif payload.host_id or payload.pairing_code:
        raise HTTPException(401, "安全配对只允许 HTTPS")
    result = sync_service.authenticate_device(
        connection, payload.passcode, payload.pairing_token, payload.profile_names, payload.device_id,
        transport=transport, pairing_code=payload.pairing_code, device_model=payload.device_model.strip(),
    )
    if not result:
        raise HTTPException(401, "同步口令不正确")
    token, pairing_token = result
    return {"token": token, "pairing_token": pairing_token, "tables": sorted(sync_service.SYNC_TABLES),
            **permissions.negotiate(connection, token, payload.categories),
            **({"host_id": host_id, "version": 2, "model_keys_version": 1, "model_snapshot_version": 1} if transport == "tls-v2" else {})}


@public_router.post("/pull")
def pull(
    payload: LanSyncPullRequest,
    request: Request,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    with sync_service._tokens_lock:
        profiles = permissions.authorize(connection, payload.token, payload.category, _transport(request))
        if payload.category == "models":
            raise HTTPException(409, "Model sync requires the named-key TLS protocol")
        snapshot = permissions.snapshot(connection, payload.category, payload.tables, profiles,
                                        payload.cursor, payload.tombstone_cursor)
        sync_service.touch_last_sync(connection)
        return snapshot


@public_router.post("/push")
def push(
    payload: LanSyncPushRequest,
    request: Request,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        with sync_service._tokens_lock:
            profiles = permissions.authorize(connection, payload.token, payload.category, _transport(request))
            if payload.category == "question_bank":
                raise HTTPException(403, "Question banks only flow from Windows to Android")
            permissions.validate_batch(payload.category, payload.changes, payload.tombstones)
            if payload.category == "models":
                raise HTTPException(409, "Model sync requires the named-key TLS protocol")
            changes = payload.changes
            if payload.category == "wrong":
                changes = {"practice_sessions": changes.get("practice_sessions", []),
                           **{k: v for k, v in changes.items() if k != "practice_sessions"}}
            applied = sync_service.merge(connection, changes, payload.tombstones, profiles, category=payload.category)
    except ValueError as exc:
        raise HTTPException(422, f"同步数据无法合并：{exc}") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(422, "同步数据违反本机数据库约束") from exc
    sync_service.touch_last_sync(connection)
    return {"applied": applied}


def _transport(request: Request) -> str:
    if getattr(request.app.state, "lan_tls_host_id", ""):
        if request.url.scheme != "https":
            raise HTTPException(401, "安全同步只允许 HTTPS")
        return "tls-v2"
    return "legacy"


def _require_profiles(payload: LanSyncPullRequest | LanSyncPushRequest, request: Request) -> list[str]:
    token = getattr(payload, "token", None)
    profiles = sync_service.session_profiles(token, _transport(request)) if isinstance(token, str) else None
    if profiles is None:
        raise HTTPException(401, "同步会话已过期，请重新握手")
    return profiles
