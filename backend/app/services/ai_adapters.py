from __future__ import annotations

import binascii
import json
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote


AdapterId = Literal[
    "openai-chat", "openai-responses", "anthropic",
    "google", "kiro", "command-code",
]
ResponseKind = Literal["json", "text", "bytes"]
DEFAULT_ADAPTER: AdapterId = "openai-chat"
ADAPTER_IDS: tuple[AdapterId, ...] = (
    "openai-chat", "openai-responses", "anthropic",
    "google", "kiro", "command-code",
)
KIRO_MODELS = (
    "kiro-auto", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "claude-sonnet-5", "claude-opus-5", "claude-opus-4.8",
    "claude-opus-4.7", "claude-opus-4.6", "claude-opus-4.5",
    "claude-sonnet-4.6", "claude-sonnet-4.5", "claude-sonnet-4.0",
    "claude-haiku-4.5", "deepseek-3.2", "minimax-m2.5",
    "minimax-m2.1", "glm-5", "qwen3-coder-next",
)


def normalize_adapter(value: object) -> AdapterId:
    candidate = str(value or DEFAULT_ADAPTER)
    return candidate if candidate in ADAPTER_IDS else DEFAULT_ADAPTER  # type: ignore[return-value]


def normalize_reasoning_effort(value: object) -> str:
    effort = str(value or "").strip().lower()
    if effort not in {"", "low", "medium", "high"}:
        raise ValueError("推理强度只支持未设置、低、中或高")
    return effort


def _base(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("请先填写 API Base URL")
    return normalized


def _with_v1(base_url: str, suffix: str) -> str:
    base = _base(base_url)
    return base + suffix if base.endswith("/v1") else base + "/v1" + suffix


def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    systems: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            systems.append(_content_text(message.get("content")))
        else:
            rest.append(message)
    return "\n".join(filter(None, systems)), rest


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text", "")) for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def _data_image(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return None
    prefix, data = value.split(";base64,", 1)
    return prefix[5:], data


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return messages


def _openai_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts = [{"type": "input_text", "text": content}]
        else:
            parts = []
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"type": "input_text", "text": str(part.get("text", ""))})
                elif part.get("type") == "image_url":
                    parts.append({"type": "input_image", "image_url": part.get("image_url", {}).get("url", "")})
        result.append({"role": message.get("role", "user"), "content": parts})
    return result


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system, rest = _split_system(messages)
    output = []
    for message in rest:
        content = message.get("content")
        if isinstance(content, str):
            parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
        else:
            parts = []
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"type": "text", "text": str(part.get("text", ""))})
                elif part.get("type") == "image_url":
                    image = _data_image(part.get("image_url", {}).get("url"))
                    if image:
                        parts.append({"type": "image", "source": {"type": "base64", "media_type": image[0], "data": image[1]}})
        output.append({"role": message.get("role", "user"), "content": parts})
    return system, output


def _google_contents(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system, rest = _split_system(messages)
    output = []
    for message in rest:
        parts = []
        content = message.get("content")
        if isinstance(content, str):
            parts.append({"text": content})
        else:
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"text": str(part.get("text", ""))})
                elif part.get("type") == "image_url":
                    image = _data_image(part.get("image_url", {}).get("url"))
                    if image:
                        parts.append({"inlineData": {"mimeType": image[0], "data": image[1]}})
        output.append({"role": "model" if message.get("role") == "assistant" else "user", "parts": parts})
    return system, output


@dataclass(frozen=True)
class Adapter:
    id: AdapterId
    response_kind: ResponseKind = "json"

    @property
    def supports_reasoning(self) -> bool:
        return self.id in {"openai-chat", "openai-responses", "kiro", "command-code"}

    def chat_url(self, base_url: str, model: str) -> str:
        if self.id == "openai-chat": return _with_v1(base_url, "/chat/completions")
        if self.id == "openai-responses": return _with_v1(base_url, "/responses")
        if self.id == "anthropic": return _with_v1(base_url, "/messages")
        if self.id == "google": return _base(base_url) + "/models/" + quote(model, safe="") + ":generateContent"
        if self.id == "command-code": return _base(base_url) + "/alpha/generate"
        return _base(base_url) + "/"

    def headers(self, api_key: str) -> dict[str, str]:
        if self.id in {"openai-chat", "openai-responses"}:
            return {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if self.id == "anthropic":
            return {**({"x-api-key": api_key} if api_key else {}), "anthropic-version": "2023-06-01"}
        if self.id == "google": return {"x-goog-api-key": api_key} if api_key else {}
        if self.id == "command-code":
            return {**({"Authorization": f"Bearer {api_key}"} if api_key else {}), "x-command-code-version": "0.52.1"}
        return {
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            "Accept": "*/*", "Content-Type": "application/x-amz-json-1.0",
            "x-amz-target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
            "x-amzn-codewhisperer-optout": "true", "amz-sdk-invocation-id": str(uuid.uuid4()),
            **({"tokentype": "API_KEY"} if api_key.startswith("ksk_") else {}),
        }

    def serialize(self, model: str, messages: list[dict[str, Any]], *, temperature: float, max_tokens: int | None, response_format: dict[str, Any] | None, reasoning_effort: str) -> dict[str, Any]:
        if self.id == "openai-chat":
            return {"model": model, "messages": _openai_messages(messages), "temperature": temperature, "stream": False, **({"response_format": response_format} if response_format else {}), **({"reasoning_effort": reasoning_effort} if reasoning_effort else {})}
        if self.id == "openai-responses":
            return {"model": model, "input": _openai_responses_input(messages), "temperature": temperature, **({"max_output_tokens": max_tokens} if max_tokens else {}), **({"text": {"format": response_format}} if response_format else {}), **({"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {})}
        if self.id == "anthropic":
            system, rest = _anthropic_messages(messages)
            return {"model": model, "messages": rest, "max_tokens": max_tokens or 64000, "temperature": temperature, **({"system": system} if system else {})}
        if self.id == "google":
            system, contents = _google_contents(messages)
            return {"contents": contents, "generationConfig": {"temperature": temperature, **({"maxOutputTokens": max_tokens} if max_tokens else {})}, **({"systemInstruction": {"parts": [{"text": system}]}} if system else {})}
        if self.id == "command-code":
            system, rest = _split_system(messages)
            converted = []
            for message in rest:
                content = message.get("content")
                if isinstance(content, list):
                    content = [{"type": "text", "text": p.get("text", "")} if p.get("type") == "text" else {"type": "image", "image": p.get("image_url", {}).get("url", "")} for p in content if isinstance(p, dict)]
                converted.append({"role": message.get("role"), "content": content})
            return {"model": model, "params": {"messages": converted, "tools": [], "system": system, "max_tokens": max_tokens or 64000, "stream": True, "temperature": temperature, **({"reasoning_effort": reasoning_effort} if reasoning_effort else {})}}
        system, rest = _split_system(messages)
        turns = [{"role": m.get("role"), "content": _content_text(m.get("content")), "raw": m.get("content")} for m in rest]
        current_index = max((i for i, turn in enumerate(turns) if turn["role"] == "user"), default=-1)
        if current_index < 0: raise ValueError("Kiro 请求必须包含用户消息")
        def wire(turn: dict[str, Any]) -> dict[str, Any]:
            images = []
            for part in turn["raw"] if isinstance(turn["raw"], list) else []:
                image = _data_image(part.get("image_url", {}).get("url")) if isinstance(part, dict) and part.get("type") == "image_url" else None
                if image: images.append({"format": image[0].split("/")[-1], "source": {"bytes": image[1]}})
            return {"content": turn["content"], "modelId": model, "origin": "KIRO_CLI", **({"images": images} if images else {})}
        current = wire(turns[current_index])
        if system: current["content"] = system + "\n\n" + current["content"]
        history = [{"assistantResponseMessage": {"content": t["content"]}} if t["role"] == "assistant" else {"userInputMessage": wire(t)} for t in turns[:current_index]]
        payload: dict[str, Any] = {"conversationState": {"chatTriggerType": "MANUAL", "agentContinuationId": str(uuid.uuid4()), "agentTaskType": "vibe", "conversationId": str(uuid.uuid4()), "currentMessage": {"userInputMessage": current}, **({"history": history} if history else {})}}
        if reasoning_effort and model in {"gpt-5.6-sol", "claude-opus-5"}:
            field = "reasoning" if model == "gpt-5.6-sol" else "output_config"
            payload["additionalModelRequestFields"] = {field: {"effort": reasoning_effort}}
        return payload

    def model_endpoints(self, base_url: str) -> list[tuple[str, str]]:
        base = _base(base_url)
        if self.id == "kiro": return []
        if self.id == "google": return [("google", base + "/models")]
        if self.id == "command-code": return [("command-code", base + "/provider/v1/models")]
        if self.id == "anthropic": return [("anthropic", _with_v1(base, "/models"))]
        urls = [(self.id, (base if base.endswith("/v1") else base + "/v1") + "/models")]
        if self.id == "openai-chat": urls.append(("ollama", (base[:-3] if base.endswith("/v1") else base) + "/api/tags"))
        return urls

    def parse_models(self, payload: Any) -> list[dict[str, str]]:
        if self.id == "kiro": return [{"id": item, "owned_by": "kiro"} for item in KIRO_MODELS]
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) and isinstance(payload, dict): rows = payload.get("models")
        if not isinstance(rows, list): raise ValueError("模型列表接口返回格式不兼容")
        found = {}
        for row in rows:
            if isinstance(row, str): model_id, owner = row.strip(), ""
            elif isinstance(row, dict):
                model_id = str(row.get("id") or row.get("name") or row.get("model") or "").strip()
                if self.id == "google" and model_id.startswith("models/"): model_id = model_id[7:]
                owner = str(row.get("owned_by") or row.get("provider") or (row.get("details") or {}).get("family") or self.id).strip()
            else: continue
            if model_id: found[model_id] = {"id": model_id, "owned_by": owner}
        if not found: raise ValueError("接口没有返回可用模型")
        return sorted(found.values(), key=lambda item: item["id"].lower())

    def parse_text(self, data: Any) -> str:
        pieces: list[str] = []
        if self.id == "openai-chat":
            try: pieces.append(_content_text(data["choices"][0]["message"].get("content")))
            except (KeyError, IndexError, TypeError): pass
        elif self.id == "openai-responses":
            if isinstance(data, dict) and isinstance(data.get("output_text"), str): pieces.append(data["output_text"])
            for output in data.get("output", []) if isinstance(data, dict) else []:
                for item in output.get("content", []) if isinstance(output, dict) else []:
                    if isinstance(item, dict) and isinstance(item.get("text"), str): pieces.append(item["text"])
        elif self.id == "anthropic":
            pieces.extend(str(item.get("text", "")) for item in data.get("content", []) if isinstance(item, dict) and item.get("type") == "text")
        elif self.id == "google":
            for candidate in data.get("candidates", []) if isinstance(data, dict) else []:
                pieces.extend(str(item.get("text", "")) for item in candidate.get("content", {}).get("parts", []) if isinstance(item, dict))
        elif self.id == "command-code":
            for line in str(data).splitlines():
                try: event = json.loads(line)
                except json.JSONDecodeError: continue
                if event.get("type") in {"text-delta", "reasoning-delta"} and isinstance(event.get("text"), str): pieces.append(event["text"])
                if event.get("type") == "error": raise ValueError("Command Code 上游返回协议错误")
        else:
            pieces.append(_parse_kiro_eventstream(data))
        result = "".join(pieces).strip()
        if not result: raise ValueError("模型没有返回可显示的正文")
        return result


def _parse_kiro_eventstream(data: Any) -> str:
    if not isinstance(data, bytes): raise ValueError("Kiro 响应不是 event-stream 二进制数据")
    offset, pieces = 0, []
    while offset < len(data):
        if len(data) - offset < 16: raise ValueError("Kiro event-stream 帧不完整")
        total, header_length, prelude_crc = struct.unpack_from(">III", data, offset)
        if total < 16 or offset + total > len(data) or header_length > total - 16: raise ValueError("Kiro event-stream 帧长度无效")
        frame = data[offset:offset + total]
        if binascii.crc32(frame[:8]) & 0xffffffff != prelude_crc: raise ValueError("Kiro event-stream prelude CRC 校验失败")
        if binascii.crc32(frame[:-4]) & 0xffffffff != struct.unpack_from(">I", frame, total - 4)[0]: raise ValueError("Kiro event-stream message CRC 校验失败")
        headers, cursor = {}, 12
        while cursor < 12 + header_length:
            size = frame[cursor]; cursor += 1
            name = frame[cursor:cursor + size].decode(); cursor += size
            if frame[cursor] != 7: raise ValueError("Kiro event-stream 包含不受支持的头类型")
            cursor += 1; value_size = struct.unpack_from(">H", frame, cursor)[0]; cursor += 2
            headers[name] = frame[cursor:cursor + value_size].decode(); cursor += value_size
        if headers.get(":message-type") in {"exception", "error"}: raise ValueError("Kiro 上游返回协议错误")
        if headers.get(":event-type") == "assistantResponseEvent": pieces.append(str(json.loads(frame[12 + header_length:-4]).get("content", "")))
        offset += total
    return "".join(pieces)


ADAPTERS = {adapter_id: Adapter(adapter_id, "text" if adapter_id == "command-code" else "bytes" if adapter_id == "kiro" else "json") for adapter_id in ADAPTER_IDS}


def adapter_for(value: object) -> Adapter:
    return ADAPTERS[normalize_adapter(value)]
