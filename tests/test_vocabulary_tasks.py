import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.database import SCHEMA
from backend.app.services import vocabulary_tasks
from backend.app.services.vocabulary import (
    _translate_queued_vocabulary_batch,
    add_vocabulary,
    queue_vocabulary_translations,
)


class VocabularyTaskPolicyTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def tearDown(self):
        self.connection.close()

    def test_quota_caps_shared_requests_per_window(self):
        self.assertEqual(vocabulary_tasks.quota_remaining(self.connection), 4)
        for task in ("translation", "enrichment", "translation", "enrichment"):
            self.assertTrue(vocabulary_tasks.consume_quota(self.connection, task))
        self.connection.commit()
        self.assertEqual(vocabulary_tasks.quota_remaining(self.connection), 0)
        self.assertFalse(vocabulary_tasks.consume_quota(self.connection, "translation"))

    def test_network_policy_matches_fastai_hosts_exactly(self):
        fastai = vocabulary_tasks.network_policy("https://www.fastaitoken.com/v1")
        self.assertTrue(fastai["fastai"])
        self.assertEqual(fastai["read"], 180)
        self.assertEqual(fastai["connect"], 15)
        lookalike = vocabulary_tasks.network_policy("https://fastaitoken.com.evil.example/v1")
        self.assertFalse(lookalike["fastai"])
        self.assertEqual(lookalike["read"], 60)
        other = vocabulary_tasks.network_policy("https://api.openai.com/v1")
        self.assertEqual(other["read"], 60)
        self.assertEqual(vocabulary_tasks.TOTAL_DEADLINE_SECONDS, 240)
        self.assertEqual(vocabulary_tasks.MAX_BATCH_WORDS, 8)

    def test_failure_reason_classification(self):
        self.assertEqual(vocabulary_tasks.failure_reason(vocabulary_tasks.VocabularyTruncatedError("x")), "truncated")
        self.assertEqual(vocabulary_tasks.failure_reason(ValueError("请先填写 API 地址和模型名称")), "configuration")
        self.assertEqual(vocabulary_tasks.failure_reason(json.JSONDecodeError("x", "doc", 0)), "parse")
        self.assertEqual(vocabulary_tasks.failure_reason(ValueError("模型没有返回该单词的有效翻译")), "validation")
        self.assertEqual(vocabulary_tasks.failure_reason(RuntimeError("boom")), "unknown")

    def _queue_one_word(self):
        entry = add_vocabulary(self.connection, {"term": "corruption", "context_sentence": "bribery scandals involved corruption at every level."})
        queue_vocabulary_translations(self.connection, [entry["entry_id"]])
        self.connection.commit()
        return entry["entry_id"]

    def test_translation_marks_truncation_with_safe_reason(self):
        entry_id = self._queue_one_word()
        error = vocabulary_tasks.VocabularyTruncatedError("词汇响应被截断或未完整生成，未自动重发")
        with (
            patch("backend.app.services.vocabulary.connect", return_value=self.connection),
            patch("backend.app.services.vocabulary._has_translation_model", return_value=True),
            patch("backend.app.services.vocabulary.chat_completion", side_effect=error) as completion,
        ):
            result = _translate_queued_vocabulary_batch()
        self.assertEqual(result["translated"], 0)
        self.assertEqual(completion.call_count, 1)
        row = self.connection.execute(
            "SELECT translation_status, translation_error FROM vocabulary_entries WHERE id=?", (entry_id,)
        ).fetchone()
        self.assertEqual(row["translation_status"], "failed")
        self.assertIn("未自动重发", row["translation_error"])

    def test_quota_exhausted_leaves_words_queued_without_failure(self):
        entry_id = self._queue_one_word()
        for _ in range(4):
            self.assertTrue(vocabulary_tasks.consume_quota(self.connection, "translation"))
        self.connection.commit()
        with (
            patch("backend.app.services.vocabulary.connect", return_value=self.connection),
            patch("backend.app.services.vocabulary._has_translation_model", return_value=True),
            patch("backend.app.services.vocabulary.chat_completion") as completion,
        ):
            result = _translate_queued_vocabulary_batch()
        self.assertEqual(result["translated"], 0)
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(completion.call_count, 0)
        row = self.connection.execute(
            "SELECT translation_status FROM vocabulary_entries WHERE id=?", (entry_id,)
        ).fetchone()
        self.assertEqual(row["translation_status"], "queued")


if __name__ == '__main__':
    unittest.main()
