from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch
from tests.vocabulary_execution_fixture import migrate, reply

from backend.app.database import SCHEMA
from backend.app.services.vocabulary import (
    add_vocabulary,
    clean_machine_meanings,
    clean_meaning,
    model_text,
    normalize_term,
    translate_queued_vocabulary,
    vocabulary_key,
)


class VocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        migrate(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_duplicate_add_increments_count_and_becomes_frequent(self) -> None:
        first = add_vocabulary(
            self.connection,
            {
                "term": "Claims",
                "context_sentence": "The author claims that the policy is effective.",
            },
        )
        second = add_vocabulary(
            self.connection,
            {
                "term": "claims",
                "context_sentence": "The company claims a different result.",
            },
        )
        self.assertTrue(first["is_new"])
        self.assertFalse(first["is_frequent"])
        self.assertFalse(second["is_new"])
        self.assertEqual(second["encounter_count"], 2)
        self.assertTrue(second["is_frequent"])
        occurrence_count = self.connection.execute(
            "SELECT COUNT(*) FROM vocabulary_occurrences"
        ).fetchone()[0]
        self.assertEqual(occurrence_count, 2)

    def test_words_wait_in_queue_and_translate_in_one_batch(self) -> None:
        first = add_vocabulary(
            self.connection,
            {
                "term": "claims",
                "context_sentence": "The author claims that the policy is effective.",
            },
        )
        second = add_vocabulary(
            self.connection,
            {
                "term": "evidence",
                "context_sentence": "The evidence supports the conclusion.",
            },
        )
        self.assertEqual(first["translation_status"], "pending")
        self.assertEqual(second["translation_status"], "pending")
        self.connection.execute(
            """
            UPDATE ai_profiles
            SET enabled = 1, default_model = 'test-model'
            WHERE id = (SELECT id FROM ai_profiles ORDER BY id LIMIT 1)
            """
        )
        self.connection.commit()
        response = {
            "translations": [
                {
                    "entryId": first["entry_id"],
                    "lemma": "claim",
                    "generatedExample": {"sentence": "She claims the prize.", "translation": "她领取奖品。"},
                    "morphology": {"lemma": "claim", "currentForm": "第三人称单数", "ambiguous": False, "note": "", "forms": []},
                    "phonetic": "",
                    "partOfSpeech": "v.",
                    "contextualMeaning": "声称",
                    "commonMeaning": "声称；主张",
                    "memoryHint": "",
                },
                {
                    "entryId": second["entry_id"],
                    "lemma": "evidence",
                    "generatedExample": {"sentence": "The evidence is clear.", "translation": "证据很明确。"},
                    "morphology": {"lemma": "evidence", "currentForm": "原形", "ambiguous": False, "note": "", "forms": []},
                    "phonetic": "",
                    "partOfSpeech": "n.",
                    "contextualMeaning": "证据",
                    "commonMeaning": "证据；证明",
                    "memoryHint": "",
                },
            ]
        }
        from backend.app.services.vocabulary import queue_vocabulary_translations

        queue_vocabulary_translations(
            self.connection,
            [first["entry_id"], second["entry_id"]],
        )
        with (
            patch(
                "backend.app.services.vocabulary.connect",
                return_value=self.connection,
            ),
            patch(
                "backend.app.services.vocabulary.chat_completion",
                side_effect=reply(__import__("json").dumps(response, ensure_ascii=False)),
            ) as completion,
        ):
            result = translate_queued_vocabulary()
        self.assertEqual(result["translated"], 2)
        self.assertEqual(completion.call_count, 1)
        rows = self.connection.execute(
            """
            SELECT translation_status, common_meaning
            FROM vocabulary_entries
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual([row["translation_status"] for row in rows], ["ready", "ready"])
        self.assertEqual([row["common_meaning"] for row in rows], ["声称；主张", "证据；证明"])

    def test_normalization_is_case_and_spacing_insensitive(self) -> None:
        self.assertEqual(normalize_term("  Artificial   Intelligence "), "artificial intelligence")

    def test_inflections_have_distinct_vocabulary_keys(self) -> None:
        self.assertEqual(vocabulary_key("claims"), "claims")
        self.assertEqual(vocabulary_key("claimed"), "claimed")
        self.assertEqual(vocabulary_key("claiming"), "claiming")

    def test_more_than_five_words_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            add_vocabulary(
                self.connection,
                {"term": "one two three four five six"},
            )

    def test_model_list_is_rendered_as_readable_text(self) -> None:
        self.assertEqual(model_text(["合法性", "正当性", "合理性"], 100), "合法性、正当性、合理性")

    def test_model_annotations_are_removed_from_meaning(self) -> None:
        self.assertEqual(
            clean_meaning("随机地，任意地（指面试者的选择方式）"),
            "随机地，任意地",
        )
        self.assertEqual(
            clean_meaning("当前语境释义：支持；拥护【本文政策语境】"),
            "支持；拥护",
        )
        self.assertEqual(
            clean_meaning("（因未提供原句，此处给出常见释义）目标；客观的"),
            "目标；客观的",
        )
        self.assertEqual(
            clean_meaning("波动，起伏；在句子中通常指价格或数量不稳定变化"),
            "波动，起伏",
        )

    def test_existing_machine_meanings_are_cleaned_but_user_edits_are_kept(self) -> None:
        self.connection.execute(
            """
            INSERT INTO vocabulary_entries
                (term, normalized_term, contextual_meaning, common_meaning,
                 translation_status, user_edited)
            VALUES
                ('randomly', 'randomly', '随机地（指选择方式）', '随机地【副词】', 'ready', 0),
                ('claim', 'claim', '用户保留（我的注释）', '声称', 'ready', 1)
            """
        )
        self.connection.commit()
        self.assertEqual(clean_machine_meanings(self.connection), 1)
        rows = self.connection.execute(
            "SELECT contextual_meaning, common_meaning FROM vocabulary_entries ORDER BY id"
        ).fetchall()
        self.assertEqual(tuple(rows[0]), ("随机地", "随机地"))
        self.assertEqual(tuple(rows[1]), ("用户保留（我的注释）", "声称"))

    def test_batch_translation_stores_discrimination_fields(self) -> None:
        import json

        from backend.app.services.vocabulary import (
            _serialize_entry,
            queue_vocabulary_translations,
        )

        first = add_vocabulary(
            self.connection,
            {"term": "claims", "context_sentence": "The author claims the result."},
        )
        second = add_vocabulary(
            self.connection,
            {"term": "evidence", "context_sentence": "The evidence supports it."},
        )
        self.connection.execute(
            """
            UPDATE ai_profiles
            SET enabled = 1, default_model = 'test-model'
            WHERE id = (SELECT id FROM ai_profiles ORDER BY id LIMIT 1)
            """
        )
        self.connection.commit()
        response = {
            "translations": [
                {
                    "entryId": first["entry_id"],
                    "lemma": "claim",
                    "generatedExample": {"sentence": "She claims the prize.", "translation": "她领取奖品。"},
                    "morphology": {"lemma": "claim", "currentForm": "第三人称单数", "ambiguous": False, "note": "", "forms": []},
                    "phonetic": "",
                    "partOfSpeech": "v.",
                    "contextualMeaning": "声称",
                    "commonMeaning": "声称；主张",
                    "memoryHint": "",
                    "synonyms": [
                        {"word": "assert", "note": "更强调坚定地声明"},
                        {"word": "maintain", "note": "强调持续坚持"},
                    ],
                    "antonyms": [],
                    "similarForms": [{"word": "clams", "note": "差一个字母"}],
                },
                {
                    "entryId": second["entry_id"],
                    "lemma": "evidence",
                    "generatedExample": {"sentence": "The evidence is clear.", "translation": "证据很明确。"},
                    "morphology": {"lemma": "evidence", "currentForm": "原形", "ambiguous": False, "note": "", "forms": []},
                    "phonetic": "",
                    "partOfSpeech": "n.",
                    "contextualMeaning": "证据",
                    "commonMeaning": "证据；证明",
                    "memoryHint": "",
                    "synonyms": [],
                    "antonyms": [],
                    "similarForms": [],
                },
            ]
        }
        queue_vocabulary_translations(
            self.connection,
            [first["entry_id"], second["entry_id"]],
        )
        with (
            patch(
                "backend.app.services.vocabulary.connect",
                return_value=self.connection,
            ),
            patch(
                "backend.app.services.vocabulary.chat_completion",
                side_effect=reply(json.dumps(response, ensure_ascii=False)),
            ),
        ):
            result = translate_queued_vocabulary()
        self.assertEqual(result["translated"], 2)
        first_payload = _serialize_entry(self.connection, first["entry_id"])
        second_payload = _serialize_entry(self.connection, second["entry_id"])
        self.assertEqual(
            [item["word"] for item in first_payload["synonyms"]],
            ["assert", "maintain"],
        )
        self.assertEqual(first_payload["antonyms"], [])
        self.assertEqual(first_payload["similar_forms"][0]["word"], "clams")
        self.assertEqual(second_payload["synonyms"], [])
        self.assertEqual(second_payload["antonyms"], [])
        self.assertEqual(second_payload["similar_forms"], [])

    def test_local_similar_matches_are_attached_and_prioritized(self) -> None:
        from backend.app.services.vocabulary import _serialize_entry

        self.connection.executemany(
            """
            INSERT INTO vocabulary_entries
                (term, normalized_term, translation_status,
                 synonyms, antonyms, similar_forms)
            VALUES (?, ?, 'ready', '[]', '[]', ?)
            """,
            [
                ("adapt", "adapt", "[]"),
                ("adopt", "adopt", "[]"),
                ("adept", "adept", "[]"),
                ("effect", "effect", json_dump([{"word": "affect", "note": "易混"}])),
                ("affect", "affect", "[]"),
            ],
        )
        self.connection.commit()
        adapt_id = self.connection.execute(
            "SELECT id FROM vocabulary_entries WHERE term = 'adapt'"
        ).fetchone()["id"]
        payload = _serialize_entry(self.connection, adapt_id)
        local_words = [item["word"] for item in payload["local_similar"]]
        self.assertEqual(local_words, ["adept", "adopt"])
        self.assertTrue(
            all(item["source"] == "本地匹配" for item in payload["local_similar"])
        )

    def test_discrimination_list_allows_empty_and_caps_entries(self) -> None:
        from backend.app.services.vocabulary import _discrimination_list

        self.assertEqual(_discrimination_list([]), [])
        self.assertEqual(_discrimination_list("[]"), [])
        self.assertEqual(_discrimination_list(None), [])
        self.assertEqual(
            _discrimination_list(
                [
                    {"word": "a", "note": "x"},
                    {"word": "A", "note": "duplicate"},
                    {"word": "b", "note": "y"},
                    {"word": "c", "note": "z"},
                    "not-a-dict",
                ]
            ),
            [{"word": "a", "note": "x"}, {"word": "b", "note": "y"}, {"word": "c", "note": "z"}],
        )
        self.assertEqual(_discrimination_list([{"word": "", "note": "x"}]), [])

    def test_practice_exit_queues_all_pending_words(self) -> None:
        from backend.app.services.vocabulary import queue_vocabulary_translations

        first = add_vocabulary(
            self.connection,
            {"term": "claims", "context_sentence": "The author claims it."},
        )
        second = add_vocabulary(
            self.connection,
            {"term": "evidence", "context_sentence": "Evidence supports it."},
        )
        third = add_vocabulary(
            self.connection,
            {"term": "policy", "context_sentence": "The policy changed."},
        )
        self.assertEqual(first["translation_status"], "pending")

        queued = queue_vocabulary_translations(
            self.connection,
            [first["entry_id"]],
        )
        self.assertEqual(queued, [first["entry_id"]])
        statuses = dict(
            self.connection.execute(
                "SELECT id, translation_status FROM vocabulary_entries"
            ).fetchall()
        )
        self.assertEqual(statuses[first["entry_id"]], "queued")
        self.assertEqual(statuses[second["entry_id"]], "pending")
        self.assertEqual(statuses[third["entry_id"]], "pending")

        all_queued = queue_vocabulary_translations(
            self.connection,
            [],
            include_all_pending=True,
        )
        self.assertEqual(
            set(all_queued),
            {first["entry_id"], second["entry_id"], third["entry_id"]},
        )


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
