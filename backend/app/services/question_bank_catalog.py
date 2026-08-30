from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .esq import MAX_PACKAGE_BYTES


MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CATALOG_PACKAGES = 500
PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


class QuestionBankCatalogError(ValueError):
    pass


def _assert_public_host(hostname: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise QuestionBankCatalogError("远程题库地址无法解析") from exc
    if not addresses:
        raise QuestionBankCatalogError("远程题库地址无法解析")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise QuestionBankCatalogError("远程题库地址不能指向本机、局域网或保留网络")


def validate_remote_url(raw: Any, *, resolve_host: bool = False) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 2048:
        raise QuestionBankCatalogError("远程题库地址无效")
    try:
        parsed = urllib.parse.urlsplit(raw.strip())
        port = parsed.port
    except ValueError as exc:
        raise QuestionBankCatalogError("远程题库地址无效") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise QuestionBankCatalogError("远程题库地址必须使用不含凭据的 HTTPS URL")
    if port not in (None, 443):
        raise QuestionBankCatalogError("远程题库地址只允许 HTTPS 标准端口")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise QuestionBankCatalogError("远程题库地址不能指向本机或局域网")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise QuestionBankCatalogError("远程题库地址不能指向本机、局域网或保留网络")
    if resolve_host:
        _assert_public_host(hostname)
    return urllib.parse.urlunsplit(parsed)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_remote_url(newurl, resolve_host=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_remote(request: urllib.request.Request, timeout: float):
    validate_remote_url(request.full_url, resolve_host=True)
    return urllib.request.build_opener(_SafeRedirectHandler()).open(request, timeout=timeout)


def validate_catalog(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("catalogVersion") != 1:
        raise QuestionBankCatalogError("题库目录 catalogVersion 不受支持")
    packages = raw.get("packages")
    if not isinstance(packages, list) or len(packages) > MAX_CATALOG_PACKAGES:
        raise QuestionBankCatalogError("题库目录 packages 无效或超过 500 项")
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, package in enumerate(packages):
        label = f"题库目录第 {index + 1} 项"
        if not isinstance(package, dict):
            raise QuestionBankCatalogError(f"{label}无效")
        package_id = package.get("packageId")
        version = package.get("contentVersion")
        title = package.get("title")
        file_name = package.get("fileName")
        digest = package.get("sha256")
        size = package.get("size")
        license_notice = package.get("license")
        years = package.get("years", [])
        if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id):
            raise QuestionBankCatalogError(f"{label} packageId 无效")
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            raise QuestionBankCatalogError(f"{label} contentVersion 无效")
        identity = (package_id, version)
        if identity in identities:
            raise QuestionBankCatalogError("题库目录包含重复的 packageId + contentVersion")
        identities.add(identity)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise QuestionBankCatalogError(f"{label} title 无效")
        if (not isinstance(file_name, str) or not file_name or file_name != file_name.strip()
                or PurePosixPath(file_name).name != file_name
                or PureWindowsPath(file_name).name != file_name
                or not file_name.lower().endswith(".esq")):
            raise QuestionBankCatalogError(f"{label} fileName 无效")
        download_url = validate_remote_url(package.get("downloadUrl"))
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise QuestionBankCatalogError(f"{label} SHA-256 无效")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1 or size > MAX_PACKAGE_BYTES:
            raise QuestionBankCatalogError(f"{label} size 无效")
        if not isinstance(license_notice, str) or not license_notice.strip() or len(license_notice) > 2000:
            raise QuestionBankCatalogError(f"{label} license 无效")
        if not isinstance(years, list) or len(years) > 100 or any(
            not isinstance(year, int) or isinstance(year, bool) or year < 1900 or year > 2200 for year in years
        ):
            raise QuestionBankCatalogError(f"{label} years 无效")
        normalized.append({
            "packageId": package_id, "contentVersion": version, "title": title.strip(),
            "fileName": file_name, "downloadUrl": download_url, "sha256": digest.lower(),
            "size": size, "license": license_notice.strip(), "years": sorted(set(years)),
        })
    updated_at = raw.get("updatedAt")
    if updated_at is not None and not isinstance(updated_at, str):
        raise QuestionBankCatalogError("题库目录 updatedAt 无效")
    return {"catalogVersion": 1, "updatedAt": updated_at or "", "packages": normalized}


def fetch_catalog(url: str, *, timeout: float = 30) -> dict[str, Any]:
    source_url = validate_remote_url(url)
    request = urllib.request.Request(source_url, headers={"Accept": "application/json", "User-Agent": "EnglishPracticeMachine-QuestionBanks/1"})
    try:
        with _open_remote(request, timeout) as response:
            validate_remote_url(response.geturl(), resolve_host=True)
            raw = response.read(MAX_CATALOG_BYTES + 1)
    except QuestionBankCatalogError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QuestionBankCatalogError(f"题库目录请求失败：{type(exc).__name__}") from exc
    if len(raw) > MAX_CATALOG_BYTES:
        raise QuestionBankCatalogError("题库目录超过 2 MiB 大小上限")
    try:
        catalog = validate_catalog(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionBankCatalogError("题库目录不是有效 JSON") from exc
    return catalog


def find_package(catalog: dict[str, Any], package_id: str, content_version: str) -> dict[str, Any]:
    for package in catalog["packages"]:
        if package["packageId"] == package_id and package["contentVersion"] == content_version:
            return package
    raise QuestionBankCatalogError("所选题库已不在当前远程目录中，请刷新后重试")


def download_package(package: dict[str, Any], target_dir: Path, *, timeout: float = 60) -> Path:
    request = urllib.request.Request(package["downloadUrl"], headers={"Accept": "application/vnd.english-study-question-bank, application/zip", "User-Agent": "EnglishPracticeMachine-QuestionBanks/1"})
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}.esq"
    temporary = target.with_suffix(".esq.part")
    digest = hashlib.sha256()
    total = 0
    try:
        with _open_remote(request, timeout) as response, temporary.open("wb") as output:
            validate_remote_url(response.geturl(), resolve_host=True)
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES or total > package["size"]:
                    raise QuestionBankCatalogError("远程题库超过目录声明大小或 ESQ 大小上限")
                digest.update(chunk)
                output.write(chunk)
        if total != package["size"]:
            raise QuestionBankCatalogError("远程题库大小校验失败")
        if digest.hexdigest().lower() != package["sha256"]:
            raise QuestionBankCatalogError("远程题库 SHA-256 校验失败")
        temporary.replace(target)
        return target
    except QuestionBankCatalogError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QuestionBankCatalogError(f"远程题库下载失败：{type(exc).__name__}") from exc
    finally:
        temporary.unlink(missing_ok=True)
