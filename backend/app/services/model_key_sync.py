"""Add-only credential exchange. Inventories are private to authenticated TLS peers."""
from __future__ import annotations

from . import model_keys as keys
from pydantic import BaseModel, ConfigDict, Field


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=8192, repr=False)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    adapter: str = Field(max_length=32)
    base_url: str = Field(min_length=1, max_length=2048)
    default_model: str = Field(default="", max_length=512)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=0, ge=0, le=1000000)
    reasoning_effort: str = Field(default="", max_length=32)
    system_prompt: str = Field(default="", max_length=100000)
    keys: list[Credential] = Field(default_factory=list, max_length=128, repr=False)
    selected: str = Field(default="", max_length=64)


class Exchange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=1, max_length=512, repr=False)
    operation: str = Field(pattern=r"^(pull|push)$")
    known: dict[str, list[str]] = Field(default_factory=dict, max_length=256)
    profiles: list[Profile] = Field(default_factory=list, max_length=256, repr=False)


def validate_inventory(known):
    for url, ids in known.items():
        if len(url) > 2048 or keys.base_identity(url) != url or len(ids) > 4096:
            raise ValueError("Invalid inventory")
        if any(not isinstance(identity, str) or not keys.re.fullmatch(r'[0-9a-f]{64}', identity) for identity in ids):
            raise ValueError("Invalid inventory")

FIELDS = ("name", "adapter", "base_url", "default_model", "temperature", "max_tokens", "reasoning_effort", "system_prompt")


def profiles(connection):
    return connection.execute("SELECT * FROM ai_profiles ORDER BY id").fetchall()


def inventory(connection):
    result = {}
    for profile in profiles(connection):
        state = keys.migrate(connection, profile)
        item = state["profiles"][str(profile["id"])]
        url = keys.base_identity(profile["base_url"])
        available = set(item["suppressed"] + [k["id"] for k in item["keys"]])
        result[url] = sorted(available.intersection(result[url]) if url in result else available)
    state = keys.load(connection)
    for url, suppressed in state["suppressed"].items():
        result[url] = sorted(set(result.get(url, []) + suppressed))
    return result


def export(connection, known):
    result = []
    for profile in profiles(connection):
        state = keys.migrate(connection, profile)
        item = state["profiles"][str(profile["id"])]
        identity = keys.base_identity(profile["base_url"])
        result.append({**{field: profile[field] for field in FIELDS},
                       "keys": [k for k in item["keys"] if k["id"] not in known.get(identity, [])],
                       "selected": item["selected"]})
    return result


def receive(connection, incoming):
    # Schema validation is completed for the whole batch before any write.
    incoming = [Profile.model_validate(profile).model_dump() for profile in incoming]
    for profile in incoming:
        keys.base_identity(profile["base_url"])
        if profile["adapter"] not in keys.AUTH:
            raise ValueError("Unsupported authentication")
        if any(k["id"] != keys.key_id(k["value"]) for k in profile["keys"]):
            raise ValueError("Invalid credential identity")
    conflicts = []
    for remote in incoming:
        identity = keys.base_identity(remote["base_url"])
        current = [p for p in profiles(connection) if keys.base_identity(p["base_url"]) == identity]
        peers = [p for p in incoming if keys.base_identity(p["base_url"]) == identity]
        if keys.conflict([*current, *peers], remote["adapter"]):
            conflicts.append({"profile_ids": [p["id"] for p in current], "reason": "authentication_incompatible"})
            continue
        state = keys.load(connection)
        if not current and identity in state["suppressed"]:
            continue
        if not current:
            name = remote["name"]
            suffix = 1
            while connection.execute("SELECT 1 FROM ai_profiles WHERE name = ?", (name,)).fetchone():
                suffix += 1
                name = remote["name"] + " (" + str(suffix) + ")"
            connection.execute(
                "INSERT INTO ai_profiles (name, adapter, base_url, default_model, temperature, max_tokens, reasoning_effort, system_prompt, is_default) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (name, *[remote[f] for f in FIELDS[1:]]))
            current = [connection.execute("SELECT * FROM ai_profiles WHERE name = ?", (name,)).fetchone()]
            if remote["default_model"]:
                connection.execute("INSERT OR IGNORE INTO ai_profile_models(profile_id, model_id, display_name) VALUES (?, ?, ?)",
                                   (current[0]["id"], remote["default_model"], remote["default_model"]))
            connection.commit()
        for profile in current:
            state = keys.migrate(connection, profile)
            item = state["profiles"][str(profile["id"])]
            item["suppressed"] = list(set(item["suppressed"] + state["suppressed"].get(identity, [])))
            merged = keys.merge_keys(item, remote["keys"])
            if len(merged["keys"]) > 128:
                raise ValueError("Credential capacity exceeded")
            state["profiles"][str(profile["id"])] = merged
            keys.save(connection, state)
    return conflicts
