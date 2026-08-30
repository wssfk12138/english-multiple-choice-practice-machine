from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import DATA_DIR


CURRENT_VERSION_NAME = os.environ.get("ENGLISH_PRACTICE_VERSION", "0.1.0")
try:
    CURRENT_VERSION_CODE = int(os.environ.get("ENGLISH_PRACTICE_VERSION_CODE", "1"))
except ValueError:
    CURRENT_VERSION_CODE = 1

OFFICIAL_MANIFEST_URL = (
    "https://github.com/wssfk12138/english-multiple-choice-practice-machine"
    "/releases/latest/download/windows-update.json"
)
ALLOWED_UPDATE_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
UPDATE_DIR = DATA_DIR / "updates"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
MAX_RELEASE_NOTES_LENGTH = 6000
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
PACKAGE_TYPES = frozenset({"exe", "msix", "zip"})


class UpdateError(ValueError):
    pass


def _validate_update_url(raw: Any, *, redirect: bool = False) -> str:
    value = str(raw or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UpdateError("更新地址必须使用 HTTPS")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError("更新地址包含不允许的连接信息")
    if parsed.hostname.lower() not in ALLOWED_UPDATE_HOSTS:
        raise UpdateError("更新地址不属于允许的 GitHub 发布源")
    if parsed.fragment or (parsed.query and not redirect):
        raise UpdateError("更新地址不能包含查询参数或片段")
    return value


class _RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_update_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, *, timeout: float):
    _validate_update_url(url)
    opener = urllib.request.build_opener(_RestrictedRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream, application/json",
            "User-Agent": "EnglishPracticeMachine-Updater/2",
        },
    )
    return opener.open(request, timeout=timeout)


def _strict_keys(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    missing = required - value.keys()
    unknown = value.keys() - allowed
    if missing:
        raise UpdateError(f"{label}缺少字段：{', '.join(sorted(missing))}")
    if unknown:
        raise UpdateError(f"{label}包含未知字段：{', '.join(sorted(unknown))}")


def validate_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UpdateError("更新清单必须是 JSON 对象")
    _strict_keys(raw, {"schemaVersion", "windows"}, {"schemaVersion", "windows"}, "更新清单")
    if raw["schemaVersion"] != 1:
        raise UpdateError("更新清单 schemaVersion 不受支持")

    platform = raw["windows"]
    if not isinstance(platform, dict):
        raise UpdateError("更新清单 windows 字段无效")
    required = {
        "versionName",
        "versionCode",
        "packageUrl",
        "packageSize",
        "packageSha256",
        "packageType",
    }
    _strict_keys(platform, required | {"releaseNotes"}, required, "Windows 更新信息")

    version_name = platform["versionName"]
    version_code = platform["versionCode"]
    package_size = platform["packageSize"]
    package_sha = platform["packageSha256"]
    package_type = platform["packageType"]
    release_notes = platform.get("releaseNotes", "")
    if not isinstance(version_name, str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", version_name
    ):
        raise UpdateError("更新清单 versionName 无效")
    if isinstance(version_code, bool) or not isinstance(version_code, int) or version_code < 1:
        raise UpdateError("更新清单 versionCode 无效")
    package_url = _validate_update_url(platform["packageUrl"])
    if isinstance(package_size, bool) or not isinstance(package_size, int):
        raise UpdateError("更新清单 packageSize 无效")
    if package_size < 1 or package_size > MAX_PACKAGE_BYTES:
        raise UpdateError("更新包大小超出允许范围")
    if not isinstance(package_sha, str) or not SHA256_RE.fullmatch(package_sha):
        raise UpdateError("更新清单 SHA-256 无效")
    if package_type not in PACKAGE_TYPES:
        raise UpdateError("更新包类型不受支持")
    if not isinstance(release_notes, str) or len(release_notes) > MAX_RELEASE_NOTES_LENGTH:
        raise UpdateError("更新说明格式或长度无效")

    return {
        "versionName": version_name,
        "versionCode": version_code,
        "packageUrl": package_url,
        "packageSize": package_size,
        "packageSha256": package_sha.lower(),
        "packageType": package_type,
        "releaseNotes": release_notes,
    }


def fetch_official_manifest() -> dict[str, Any]:
    try:
        with _open_url(OFFICIAL_MANIFEST_URL, timeout=20) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, UpdateError) as exc:
        raise UpdateError("无法获取官方更新清单") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("官方更新清单超过大小上限")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("官方更新清单不是有效 JSON") from exc
    return validate_manifest(payload)


def check_for_update() -> dict[str, Any]:
    manifest = fetch_official_manifest()
    return {
        "available": manifest["versionCode"] > CURRENT_VERSION_CODE,
        "currentVersion": CURRENT_VERSION_NAME,
        "currentVersionCode": CURRENT_VERSION_CODE,
        "latest": {
            "versionName": manifest["versionName"],
            "versionCode": manifest["versionCode"],
            "packageSize": manifest["packageSize"],
            "packageType": manifest["packageType"],
            "releaseNotes": manifest["releaseNotes"],
        },
    }


def _safe_package_name(manifest: dict[str, Any]) -> str:
    url_name = Path(urllib.parse.urlsplit(manifest["packageUrl"]).path).name
    fallback = f"english-practice-{manifest['versionName']}.{manifest['packageType']}"
    candidate = url_name if FILE_NAME_RE.fullmatch(url_name or "") else fallback
    if Path(candidate).suffix.lower() != f".{manifest['packageType']}":
        raise UpdateError("更新包文件扩展名与清单不一致")
    return candidate


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                raise UpdateError("更新包超过大小上限")
            digest.update(chunk)
    return size, digest.hexdigest()


def _receipt_path(file_name: str) -> Path:
    return UPDATE_DIR / f"{file_name}.receipt.json"


def download_official_package() -> dict[str, Any]:
    manifest = fetch_official_manifest()
    if manifest["versionCode"] <= CURRENT_VERSION_CODE:
        raise UpdateError("当前已经是最新版本")
    file_name = _safe_package_name(manifest)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / file_name
    temporary = UPDATE_DIR / f"{file_name}.part"
    receipt = _receipt_path(file_name)
    try:
        digest = hashlib.sha256()
        total = 0
        with _open_url(manifest["packageUrl"], timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > manifest["packageSize"] or total > MAX_PACKAGE_BYTES:
                    raise UpdateError("更新包超过清单声明的大小")
                digest.update(chunk)
                output.write(chunk)
        if total != manifest["packageSize"]:
            raise UpdateError("更新包大小校验失败")
        if digest.hexdigest() != manifest["packageSha256"]:
            raise UpdateError("更新包 SHA-256 校验失败")
        temporary.replace(target)
        receipt_payload = {
            "schemaVersion": 1,
            "fileName": file_name,
            "versionName": manifest["versionName"],
            "versionCode": manifest["versionCode"],
            "packageType": manifest["packageType"],
            "packageSize": total,
            "packageSha256": digest.hexdigest(),
            "verifiedAt": datetime.now(timezone.utc).isoformat(),
        }
        receipt_tmp = receipt.with_suffix(receipt.suffix + ".part")
        receipt_tmp.write_text(json.dumps(receipt_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt_tmp.replace(receipt)
        return receipt_payload
    except (urllib.error.URLError, TimeoutError, OSError, UpdateError) as exc:
        temporary.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("更新包下载失败") from exc


def _load_receipt(file_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(_receipt_path(file_name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("更新包缺少有效的校验收据") from exc
    required = {
        "schemaVersion",
        "fileName",
        "versionName",
        "versionCode",
        "packageType",
        "packageSize",
        "packageSha256",
        "verifiedAt",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise UpdateError("更新包校验收据格式无效")
    return payload


def open_verified_package(file_name: str) -> dict[str, str]:
    if not FILE_NAME_RE.fullmatch(file_name) or Path(file_name).name != file_name:
        raise UpdateError("更新包文件名无效")
    target = UPDATE_DIR / file_name
    if not target.is_file():
        raise UpdateError("更新包不存在")
    receipt = _load_receipt(file_name)
    if receipt["schemaVersion"] != 1 or receipt["fileName"] != file_name:
        raise UpdateError("更新包校验收据不匹配")
    if receipt["packageType"] not in PACKAGE_TYPES or Path(file_name).suffix.lower() != f".{receipt['packageType']}":
        raise UpdateError("更新包类型不匹配")
    if isinstance(receipt["packageSize"], bool) or not isinstance(receipt["packageSize"], int):
        raise UpdateError("更新包校验收据大小无效")
    if not isinstance(receipt["packageSha256"], str) or not SHA256_RE.fullmatch(receipt["packageSha256"]):
        raise UpdateError("更新包校验收据哈希无效")
    actual_size, actual_sha = _hash_file(target)
    if actual_size != receipt["packageSize"] or actual_sha != receipt["packageSha256"]:
        raise UpdateError("更新包在下载后发生变化，已拒绝打开")
    if not hasattr(os, "startfile"):
        raise UpdateError("当前平台不支持打开 Windows 更新包")
    os.startfile(str(target))  # type: ignore[attr-defined]
    return {"fileName": file_name, "status": "opened"}


def update_status() -> dict[str, Any]:
    return {
        "currentVersion": CURRENT_VERSION_NAME,
        "currentVersionCode": CURRENT_VERSION_CODE,
        "source": "GitHub 官方发布源",
    }
