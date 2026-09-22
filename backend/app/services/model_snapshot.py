"""Explicit source-authoritative model snapshot; ordinary key exchange stays add-only."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .model_key_sync import Profile, FIELDS, Credential
from . import model_keys as keys
from .model_key_transaction import atomic_update


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(max_length=512)
    owned_by: str = Field(max_length=512)
    provider: str = Field(max_length=512)
    is_visible: bool
    is_available: bool


class SnapshotProfile(Profile):
    # Complete snapshot fields are REQUIRED; old partial packets must not reset data.
    default_model: str = Field(max_length=512)
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(ge=0, le=1000000)
    reasoning_effort: str = Field(max_length=32)
    system_prompt: str = Field(max_length=100000)
    keys: list[Credential] = Field(max_length=128, repr=False)
    selected: str = Field(max_length=64)
    enabled: bool
    is_default: bool
    models: list[Model] = Field(max_length=4096)


class SnapshotExchange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=1, max_length=512, repr=False)
    operation: Literal["authoritative_push"]
    snapshot_version: Literal[1]
    profiles: list[SnapshotProfile] = Field(min_length=1, max_length=256, repr=False)


def receive(connection, incoming):
    if not 1 <= len(incoming) <= 256:
        raise ValueError("Invalid profile count")
    incoming = [SnapshotProfile.model_validate(p).model_dump() for p in incoming]
    names = set()
    for profile in incoming:
        keys.base_identity(profile["base_url"])
        if not profile["name"].strip() or profile["name"] in names:
            raise ValueError("Ambiguous profile name")
        names.add(profile["name"])
        if profile["adapter"] not in keys.AUTH:
            raise ValueError("Unsupported authentication")
        ids = [k["id"] for k in profile["keys"]]
        if any(k["id"] != keys.key_id(k["value"]) for k in profile["keys"]):
            raise ValueError("Invalid credential identity")
        if len(ids) != len(set(ids)) or (profile["selected"] and profile["selected"] not in ids):
            raise ValueError("Invalid credential selection")
        models = [m["model_id"] for m in profile["models"]]
        if len(models) != len(set(models)):
            raise ValueError("Duplicate model identity")
    if sum(p["is_default"] for p in incoming) > 1:
        raise ValueError("Multiple default profiles")

    def update(state):
        for name in names:
            if connection.execute("SELECT COUNT(*) FROM ai_profiles WHERE name=?", (name,)).fetchone()[0] > 1:
                raise ValueError("Ambiguous local profile name")
        # Preserve Windows-only profiles, but the source owns the global default.
        connection.execute("UPDATE ai_profiles SET is_default=0 WHERE is_default<>0")
        for remote in incoming:
            local = connection.execute("SELECT * FROM ai_profiles WHERE name=?", (remote["name"],)).fetchone()
            columns = (*FIELDS, "enabled", "is_default")
            values = tuple(remote[f] for f in columns)
            if local:
                pid = local["id"]
                connection.execute("UPDATE ai_profiles SET " + ",".join(f"{f}=?" for f in columns)
                                   + ", api_key_encrypted=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values, pid))
                if local["api_key_encrypted"]:
                    connection.execute("UPDATE ai_settings SET api_key_encrypted=NULL WHERE api_key_encrypted=?", (local["api_key_encrypted"],))
            else:
                pid = connection.execute("INSERT INTO ai_profiles (" + ",".join(columns) + ") VALUES ("
                                         + ",".join("?" for _ in columns) + ")", values).lastrowid
            state["profiles"][str(pid)] = {"keys": remote["keys"], "selected": remote["selected"], "suppressed": []}
            state["suppressed"].pop(keys.base_identity(remote["base_url"]), None)
            connection.execute("DELETE FROM ai_profile_models WHERE profile_id=?", (pid,))
            model_fields = ("model_id", "display_name", "owned_by", "provider", "is_visible", "is_available")
            connection.executemany("INSERT INTO ai_profile_models(profile_id," + ",".join(model_fields)
                                   + ") VALUES (?,?,?,?,?,?,?)",
                                   [(pid, *(m[f] for f in model_fields)) for m in remote["models"]])
        return len(incoming)

    return atomic_update(connection, update)
