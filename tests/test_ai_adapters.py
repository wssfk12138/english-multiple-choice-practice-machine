from __future__ import annotations

import binascii
import json
import sqlite3
import struct
import unittest

from backend.app import database
from backend.app.services.ai_adapters import (
    ADAPTER_IDS,
    KIRO_MODELS,
    adapter_for,
    normalize_adapter,
    normalize_reasoning_effort,
)


FAKE_SECRET = "contract-test-secret-value"


def _eventstream_frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    header_values = (
        (":message-type", "event"),
        (":event-type", "assistantResponseEvent"),
        (":content-type", "application/json"),
    )
    headers = b"".join(
        bytes([len(name)]) + name.encode("utf-8") + bytes([7])
        + struct.pack(">H", len(value)) + value.encode("utf-8")
        for name, value in header_values
    )
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude_crc = struct.pack(">I", binascii.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + prelude_crc + headers + body
    return message + struct.pack(">I", binascii.crc32(message) & 0xFFFFFFFF)


class AdapterContractTests(unittest.TestCase):
    def test_all_six_adapters_are_registered_and_unknown_defaults_to_chat(self) -> None:
        self.assertEqual(
            ADAPTER_IDS,
            ("openai-chat", "openai-responses", "anthropic", "google", "kiro", "command-code"),
        )
        self.assertEqual(normalize_adapter("unknown"), "openai-chat")
        for adapter_id in ADAPTER_IDS:
            self.assertEqual(adapter_for(adapter_id).id, adapter_id)

    def test_urls_headers_and_model_endpoints(self) -> None:
        cases = {
            "openai-chat": ("https://example.test/v1/chat/completions", "Bearer " + FAKE_SECRET),
            "openai-responses": ("https://example.test/v1/responses", "Bearer " + FAKE_SECRET),
            "anthropic": ("https://example.test/v1/messages", FAKE_SECRET),
            "google": ("https://example.test/models/model-a:generateContent", FAKE_SECRET),
            "kiro": ("https://example.test/", "Bearer " + FAKE_SECRET),
            "command-code": ("https://example.test/alpha/generate", "Bearer " + FAKE_SECRET),
        }
        for adapter_id, (expected_url, expected_auth) in cases.items():
            adapter = adapter_for(adapter_id)
            self.assertEqual(adapter.chat_url("https://example.test/", "model-a"), expected_url)
            headers = adapter.headers(FAKE_SECRET)
            self.assertNotIn(None, headers.values())
            self.assertIn(expected_auth, headers.values())

        self.assertEqual(adapter_for("google").model_endpoints("https://example.test")[0][1], "https://example.test/models")
        self.assertEqual(adapter_for("command-code").model_endpoints("https://example.test")[0][1], "https://example.test/provider/v1/models")
        self.assertEqual(adapter_for("kiro").model_endpoints("https://example.test"), [])

    def test_request_serialization_and_reasoning_boundaries(self) -> None:
        messages = [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Question"},
        ]
        payloads = {
            adapter_id: adapter_for(adapter_id).serialize(
                "gpt-5.6-sol", messages, temperature=0.2, max_tokens=100,
                response_format=None, reasoning_effort="high",
            )
            for adapter_id in ADAPTER_IDS
        }
        self.assertEqual(payloads["openai-chat"]["reasoning_effort"], "high")
        self.assertEqual(payloads["openai-responses"]["reasoning"], {"effort": "high"})
        self.assertNotIn("reasoning_effort", payloads["anthropic"] )
        self.assertNotIn("reasoning", payloads["google"] )
        self.assertEqual(payloads["command-code"]["params"]["reasoning_effort"], "high")
        self.assertEqual(payloads["kiro"]["additionalModelRequestFields"], {"reasoning": {"effort": "high"}})
        self.assertNotIn(FAKE_SECRET, json.dumps(payloads))

    def test_response_and_model_parsing(self) -> None:
        responses = {
            "openai-chat": ({"choices": [{"message": {"content": "chat"}}]}, "chat"),
            "openai-responses": ({"output": [{"content": [{"type": "output_text", "text": "responses"}]}]}, "responses"),
            "anthropic": ({"content": [{"type": "text", "text": "anthropic"}]}, "anthropic"),
            "google": ({"candidates": [{"content": {"parts": [{"text": "google"}]}}]}, "google"),
            "command-code": ('{"type":"text-delta","text":"command"}\n{"type":"text-delta","text":" code"}', "command code"),
            "kiro": (_eventstream_frame({"content": "kiro"}), "kiro"),
        }
        for adapter_id, (wire, expected) in responses.items():
            self.assertEqual(adapter_for(adapter_id).parse_text(wire), expected)

        models = adapter_for("google").parse_models({"models": [{"name": "models/gemini-2.5-pro", "displayName": "Gemini"}]})
        self.assertEqual(models[0]["id"], "gemini-2.5-pro")
        self.assertEqual(adapter_for("kiro").parse_models(None)[0]["id"], KIRO_MODELS[0])

    def test_kiro_crc_corruption_is_rejected_without_secret(self) -> None:
        frame = bytearray(_eventstream_frame({"content": "ok"}))
        frame[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC") as captured:
            adapter_for("kiro").parse_text(bytes(frame))
        self.assertNotIn(FAKE_SECRET, str(captured.exception))

    def test_reasoning_validation(self) -> None:
        for value in ("", "low", "medium", "high"):
            self.assertEqual(normalize_reasoning_effort(value), value)
        with self.assertRaises(ValueError):
            normalize_reasoning_effort("ultra")


class AiProfileMigrationTests(unittest.TestCase):
    def test_legacy_profiles_gain_default_adapter_and_reasoning_columns(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(database.SCHEMA)
        connection.execute("ALTER TABLE ai_profiles RENAME TO ai_profiles_current")
        connection.execute(
            """
            CREATE TABLE ai_profiles (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
                api_key_encrypted TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0, default_model TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.2, max_tokens INTEGER NOT NULL DEFAULT 1200,
                system_prompt TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("INSERT INTO ai_profiles (id, name, base_url) VALUES (7, 'legacy', 'https://example.test')")
        database._run_migrations(connection)
        row = connection.execute("SELECT adapter, reasoning_effort FROM ai_profiles WHERE id = 7").fetchone()
        self.assertEqual(dict(row), {"adapter": "openai-chat", "reasoning_effort": ""})
        database._run_migrations(connection)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM ai_profiles WHERE id = 7").fetchone()[0], 1)
        connection.close()


if __name__ == "__main__":
    unittest.main()
