"""Named credentials live in one DPAPI-protected, atomically replaced sidecar."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ..security import protect_text, unprotect_text

LOCK = threading.RLock()
AUTH = {"openai-chat": "bearer", "openai-responses": "bearer", "anthropic": "anthropic",
        "google": "google", "kiro": "kiro", "command-code": "command-code"}


def base_identity(raw):
    value = raw.strip()
    if re.search(r'[\x00-\x20\x7f\\?#]', value):
        raise ValueError("Invalid API address")
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Invalid API address")
    if not re.fullmatch(r'[A-Za-z0-9.:[\]-]+', url.netloc):
        raise ValueError("Invalid API address")
    _ = url.port
    return url.scheme.lower() + "://" + url.netloc.lower() + url.path


def key_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_api_key(value):
    """Only for on-screen identification: keep four characters at each end."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return text[:4] + "****" + text[-4:]


def empty_keys():
    return {"keys": [], "selected": "", "suppressed": []}


def merge_keys(local, incoming):
    result = json.loads(json.dumps(local))
    for key in incoming:
        if key["id"] not in result["suppressed"] and not any(k["id"] == key["id"] for k in result["keys"]):
            result["keys"].append(dict(key))
    return result


def conflict(local, adapter):
    return bool(adapter not in AUTH or any(AUTH.get(p["adapter"]) != AUTH[adapter] for p in local))


def _path(connection):
    path = connection.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        raise ValueError("Credential storage requires an isolated file database")
    return Path(path).with_suffix(".model-keys.dpapi")


def load(connection):
    from .model_key_transaction import recover
    recover(connection)
    path = _path(connection)
    if not path.exists():
        return {"version": 1, "profiles": {}, "suppressed": {}}
    try:
        state = json.loads(unprotect_text(path.read_text(encoding="utf-8")))
        if state["version"] != 1 or not isinstance(state["profiles"], dict):
            raise ValueError()
        return state
    except Exception:
        raise ValueError("Credential store unavailable; no automatic reset") from None


def _write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sidecar_snapshot(connection):
    """Return exact sidecar bytes for transactional recovery."""
    path = _path(connection)
    return path, path.read_bytes() if path.exists() else None


def restore_snapshot(snapshot) -> None:
    path, payload = snapshot
    if payload is None:
        path.unlink(missing_ok=True)
    else:
        _write_bytes(path, payload)


def save(connection, state):
    path = _path(connection)
    encoded = protect_text(json.dumps(state, ensure_ascii=True)).encode("utf-8")
    _write_bytes(path, encoded)


def migrate(connection, profile):
    profile = connection.execute("SELECT * FROM ai_profiles WHERE id = ?", (profile["id"],)).fetchone()
    if profile is None:
        raise ValueError("API profile no longer exists")
    state = load(connection)
    pid = str(profile["id"])
    if pid not in state["profiles"]:
        item = empty_keys()
        legacy = unprotect_text(profile["api_key_encrypted"])
        if legacy:
            item["keys"] = [{"id": key_id(legacy), "name": "默认密钥", "value": legacy}]
            item["selected"] = key_id(legacy)
        state["profiles"][pid] = item
        save(connection, state)
    # Persist the replacement first. A crash can replay this cleanup safely.
    if profile["api_key_encrypted"]:
        connection.execute("UPDATE ai_profiles SET api_key_encrypted = NULL WHERE id = ?", (profile["id"],))
        connection.execute("UPDATE ai_settings SET api_key_encrypted = NULL WHERE api_key_encrypted = ?", (profile["api_key_encrypted"],))
        connection.commit()
    return state


def summary(connection, profile):
    with LOCK:
        item = migrate(connection, profile)["profiles"][str(profile["id"])]
        return {"keys": [{"id": k["id"], "name": k["name"], "mask": mask_api_key(k.get("value", ""))} for k in item["keys"]],
                "selected_key_id": item["selected"],
                "has_api_key": any(k["id"] == item["selected"] for k in item["keys"])}


def selected(connection, profile):
    with LOCK:
        item = migrate(connection, profile)["profiles"][str(profile["id"])]
        return next((k["value"] for k in item["keys"] if k["id"] == item["selected"]), "")


def edit(connection, profile, action, name="", value="", identity=""):
    with LOCK:
        state = migrate(connection, profile)
        item = state["profiles"][str(profile["id"])]
        if action == "add":
            value, name = value.strip(), name.strip()
            if not value or len(value) > 8192 or not name or len(name) > 100:
                raise ValueError("Invalid named credential")
            identity = key_id(value)
            if not any(k["id"] == identity for k in item["keys"]):
                if len(item["keys"]) >= 128:
                    raise ValueError("Credential capacity exceeded")
                item["keys"].append({"id": identity, "name": name, "value": value})
            item["suppressed"] = [k for k in item["suppressed"] if k != identity]
        elif action == "select":
            if identity and not any(k["id"] == identity for k in item["keys"]):
                raise ValueError("Credential no longer exists")
            item["selected"] = identity
        elif action == "rename":
            if not any(k["id"] == identity for k in item["keys"]):
                raise ValueError("Credential no longer exists")
            if not name.strip() or len(name) > 100:
                raise ValueError("Invalid credential name")
            for key in item["keys"]:
                if key["id"] == identity:
                    key["name"] = name.strip()
        elif action == "delete":
            if not re.fullmatch(r"[0-9a-f]{64}", identity):
                raise ValueError("Invalid credential identity")
            item["keys"] = [k for k in item["keys"] if k["id"] != identity]
            if identity not in item["suppressed"]:
                item["suppressed"].append(identity)
            if item["selected"] == identity:
                item["selected"] = ""
        else:
            raise ValueError("Invalid credential operation")
        save(connection, state)
        return summary(connection, profile)


def legacy_write(connection, profile, value, clear=False):
    with LOCK:
        if clear:
            current = summary(connection, profile)
            if current["selected_key_id"]:
                edit(connection, profile, "delete", identity=current["selected_key_id"])
        elif value:
            edit(connection, profile, "add", "默认密钥", value)
            edit(connection, profile, "select", identity=key_id(value.strip()))


def suppress_profile(connection, profile):
    with LOCK:
        state = migrate(connection, profile)
        item = state["profiles"].pop(str(profile["id"]))
        url = base_identity(profile["base_url"])
        state["suppressed"][url] = list(set(state["suppressed"].get(url, []) + item["suppressed"] + [k["id"] for k in item["keys"]]))
        save(connection, state)
