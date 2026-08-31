from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import httpx

from ..security import unprotect_text
from .ai_adapters import adapter_for, normalize_reasoning_effort


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_CHAT_RETRY_ATTEMPTS = 3


def _public_profile(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["has_api_key"] = bool(payload.pop("api_key_encrypted", None))
    for field in ("enabled", "is_default"):
        if field in payload:
            payload[field] = bool(payload[field])
    return payload


def get_ai_profile(
    connection: sqlite3.Connection,
    profile_id: int | None = None,
) -> dict[str, Any]:
    if profile_id is None:
        row = connection.execute(
            """
            SELECT * FROM ai_profiles
            ORDER BY enabled DESC, is_default DESC, id
            LIMIT 1
            """
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM ai_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise LookupError("API 配置不存在")
    return _public_profile(row)


def _profile_with_key(
    connection: sqlite3.Connection,
    profile_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM ai_profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise LookupError("API 配置不存在")
    payload = dict(row)
    payload["api_key"] = unprotect_text(payload.pop("api_key_encrypted"))
    payload["enabled"] = bool(payload["enabled"])
    payload["is_default"] = bool(payload["is_default"])
    return payload


def ensure_ai_model_catalog(connection: sqlite3.Connection) -> int:
    """Keep configured default models usable from the local cache after restart."""
    profiles = connection.execute(
        """
        SELECT id, default_model
        FROM ai_profiles
        WHERE TRIM(default_model) <> ''
        """
    ).fetchall()
    changed = 0
    for profile in profiles:
        model_id = profile["default_model"].strip()
        current = connection.execute(
            """
            SELECT is_available
            FROM ai_profile_models
            WHERE profile_id = ? AND model_id = ?
            """,
            (profile["id"], model_id),
        ).fetchone()
        if current is None:
            connection.execute(
                """
                INSERT INTO ai_profile_models
                    (profile_id, model_id, display_name, is_visible, is_available)
                VALUES (?, ?, ?, 1, 1)
                """,
                (profile["id"], model_id, model_id),
            )
            changed += 1
        elif not current["is_available"]:
            # A configured default may be a manually entered model that is absent
            # from /models. Keep it selectable until the user changes the config.
            connection.execute(
                """
                UPDATE ai_profile_models
                SET is_available = 1, updated_at = CURRENT_TIMESTAMP
                WHERE profile_id = ? AND model_id = ?
                """,
                (profile["id"], model_id),
            )
            changed += 1
    if changed:
        connection.commit()
    return changed


def get_ai_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    profile = get_ai_profile(connection)
    return {
        "id": profile["id"],
        "name": profile["name"],
        "adapter": profile.get("adapter", "openai-chat"),
        "base_url": profile["base_url"],
        "model": profile["default_model"],
        "temperature": profile["temperature"],
        "max_tokens": profile["max_tokens"],
        "reasoning_effort": profile.get("reasoning_effort", ""),
        "system_prompt": profile["system_prompt"],
        "has_api_key": profile["has_api_key"],
    }


def _settings_with_key(connection: sqlite3.Connection) -> dict[str, Any]:
    profile = get_ai_profile(connection)
    payload = _profile_with_key(connection, profile["id"])
    payload["model"] = payload.pop("default_model")
    return payload


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _model_list_urls(base_url: str) -> list[tuple[str, str]]:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        raise ValueError("请先填写 API Base URL")
    urls: list[tuple[str, str]] = []
    if normalized.endswith("/v1"):
        urls.append(("openai", normalized + "/models"))
        urls.append(("ollama", normalized[:-3].rstrip("/") + "/api/tags"))
    else:
        urls.append(("openai", normalized + "/v1/models"))
        urls.append(("openai", normalized + "/models"))
        urls.append(("ollama", normalized + "/api/tags"))
    return list(dict.fromkeys(urls))


def _parse_model_list(payload: Any) -> list[dict[str, str]]:
    rows: list[Any]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
        rows = payload["models"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("模型列表接口返回格式不兼容")

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            model_id = row.strip()
            owned_by = ""
        elif isinstance(row, dict):
            model_id = str(
                row.get("id") or row.get("name") or row.get("model") or ""
            ).strip()
            owned_by = str(row.get("owned_by") or row.get("provider") or "").strip()
        else:
            continue
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append({"id": model_id, "owned_by": owned_by})
    if not models:
        raise ValueError("接口没有返回可用模型")
    return sorted(models, key=lambda item: item["id"].lower())


def list_available_models(
    connection: sqlite3.Connection,
    *,
    base_url: str,
    api_key: str | None = None,
    use_saved_api_key: bool = False,
    profile_id: int | None = None,
    adapter: str = "openai-chat",
) -> dict[str, Any]:
    normalized = _normalize_base_url(base_url)
    key = (api_key or "").strip()
    if use_saved_api_key:
        saved = (
            _profile_with_key(connection, profile_id)
            if profile_id is not None
            else _settings_with_key(connection)
        )
        if _normalize_base_url(saved["base_url"]) != normalized:
            raise ValueError("已保存的 API Key 只能用于原接口地址")
        key = saved["api_key"] or ""

    definition = adapter_for(adapter)
    headers = {"Accept": "application/json", **definition.headers(key)}
    endpoints = definition.model_endpoints(normalized)
    if not endpoints:
        return {"models": definition.parse_models(None), "source": definition.id, "endpoint": "static"}

    failures: list[str] = []
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for provider, url in endpoints:
            try:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                models = definition.parse_models(response.json())
                return {
                    "models": models,
                    "source": provider,
                    "endpoint": url,
                }
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
                failures.append(f"{url}: {error}")
    raise ValueError("无法获取模型列表，请检查接口地址、API Key 或手动填写模型名称")


def chat_completion(
    connection: sqlite3.Connection,
    messages: list[dict[str, str]],
    *,
    response_format: dict[str, Any] | None = None,
    profile_id: int | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    settings = (
        _profile_with_key(connection, profile_id)
        if profile_id is not None
        else _settings_with_key(connection)
    )
    selected_model = (model or settings.get("model") or settings.get("default_model") or "").strip()
    if not settings["base_url"].strip() or not selected_model:
        raise ValueError("请先填写 API 地址和模型名称")
    if not settings.get("enabled", True):
        raise ValueError("所选 API 配置当前未启用")
    definition = adapter_for(settings.get("adapter"))
    key = settings.get("api_key") or ""
    if definition.id == "kiro" and not key.startswith("ksk_"):
        raise ValueError("Kiro 需要以 ksk_ 开头的 API Key；本应用不导入 OpenCodex/Kiro CLI 的 OAuth 会话")
    url = definition.chat_url(settings["base_url"], selected_model)
    headers = {"Content-Type": "application/json", **definition.headers(key)}
    effort = normalize_reasoning_effort(
        settings.get("reasoning_effort") if reasoning_effort is None else reasoning_effort
    )
    payload = definition.serialize(
        selected_model, messages, temperature=settings["temperature"],
        max_tokens=max_tokens, response_format=response_format,
        reasoning_effort=effort if definition.supports_reasoning else "",
    )
    def post_with_retry(client: httpx.Client, request_payload: dict[str, Any]) -> httpx.Response:
        last_response: httpx.Response | None = None
        last_error: httpx.HTTPError | None = None
        for attempt in range(_CHAT_RETRY_ATTEMPTS):
            try:
                response = client.post(url, headers=headers, json=request_payload)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as error:
                last_error = error
                if attempt == _CHAT_RETRY_ATTEMPTS - 1:
                    raise ValueError("模型服务暂时不可用，请稍后重试或切换 API 配置") from error
                time.sleep(2**attempt)
                continue
            last_response = response
            if response.status_code not in _TRANSIENT_STATUS_CODES:
                return response
            if attempt < _CHAT_RETRY_ATTEMPTS - 1:
                retry_after = response.headers.get("Retry-After", "").strip()
                try:
                    delay = max(1.0, min(8.0, float(retry_after)))
                except ValueError:
                    delay = float(2**attempt)
                time.sleep(delay)
                continue
            raise ValueError(
                f"模型服务暂时不可用（HTTP {response.status_code}），"
                "请稍后重试或切换 API 配置"
            )
        if last_response is not None:
            return last_response
        raise ValueError("模型服务暂时不可用，请稍后重试或切换 API 配置") from last_error

    with httpx.Client(timeout=120) as client:
        response = post_with_retry(client, payload)
        if (
            definition.id == "openai-chat"
            and response.status_code in {400, 404, 422}
            and response_format
        ):
            # Some OpenAI-compatible local APIs do not implement
            # response_format. The prompt still requests strict JSON.
            payload.pop("response_format", None)
            response = post_with_retry(client, payload)
        response.raise_for_status()
        data: Any = response.content if definition.response_kind == "bytes" else response.text if definition.response_kind == "text" else response.json()
    return definition.parse_text(data)


def _extract_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            text = part
        elif isinstance(part, dict):
            text = part.get("text") or part.get("content") or ""
            if isinstance(text, dict):
                text = text.get("value") or ""
        else:
            text = ""
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def strip_json_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines)
    return value.strip()


def parse_json_response(value: str) -> Any:
    return json.loads(strip_json_fence(value))
