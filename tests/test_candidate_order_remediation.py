from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.services.questions import serialize_unit


class CandidateOrderTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE papers(id INTEGER, year INTEGER, subject TEXT);
            CREATE TABLE units(id INTEGER, paper_id INTEGER, unit_type TEXT, subtype TEXT,
                title TEXT, sequence INTEGER, passage TEXT, shared_data TEXT);
            CREATE TABLE questions(id INTEGER, unit_id INTEGER, sequence INTEGER,
                number INTEGER, stem TEXT, question_type TEXT, score REAL, metadata TEXT, answer TEXT);
            CREATE TABLE options(id INTEGER, question_id INTEGER, stable_key TEXT,
                original_label TEXT, content TEXT, sequence INTEGER, metadata TEXT);
            INSERT INTO papers VALUES(1, 2023, 'test');
            INSERT INTO units VALUES(1, 1, 'part_b', 'paragraph_reordering', 'test', 1, '', '{}');
            INSERT INTO questions VALUES(1, 1, 1, 41, '', 'single', 2, '{}', 'key-c');
            INSERT INTO options VALUES(1, 1, 'key-a', 'A', 'first', 1, '{}');
            INSERT INTO options VALUES(2, 1, 'key-c', 'C', 'third', 2, '{}');
        """)

    def test_new_ordering_preserves_original_letters_and_identity(self):
        with patch('backend.app.services.questions.random.shuffle', side_effect=AssertionError('must not shuffle')):
            unit = serialize_unit(self.db, 1, shuffle_options=True, include_answers=True)
        question = unit['questions'][0]
        self.assertEqual([o['label'] for o in question['options']], ['A', 'C'])
        self.assertEqual(question['option_order'], ['key-a', 'key-c'])
        self.assertEqual(question['answer'], 'key-c')

    def test_existing_saved_mapping_is_retained(self):
        unit = serialize_unit(self.db, 1, shuffle_options=True, answer_orders={1: ['key-c', 'key-a']}, candidate_policy_version=0)
        self.assertEqual([(o['stable_key'], o['label']) for o in unit['questions'][0]['options']], [('key-c', 'A'), ('key-a', 'B')])

    def test_fixed_insertion_without_metadata_preserves_order(self):
        restored = serialize_unit(self.db, 1, shuffle_options=True, answer_orders={1: ['key-a', 'key-c']}, candidate_policy_version=1)
        self.assertEqual([o['label'] for o in restored['questions'][0]['options']], ['A', 'C'])
        self.db.execute("UPDATE units SET subtype='paragraph_insertion', passage='Paragraph E has been correctly placed.'")
        with patch('backend.app.services.questions.random.shuffle', side_effect=AssertionError('must not shuffle')):
            serialize_unit(self.db, 1, shuffle_options=True)

    def test_heading_matching_still_shuffles(self):
        self.db.execute("UPDATE units SET subtype='heading_matching'")
        with patch('backend.app.services.questions.random.shuffle', side_effect=lambda items: items.reverse()):
            result = serialize_unit(self.db, 1, shuffle_options=True)
        self.assertEqual(result['questions'][0]['option_order'], ['key-c', 'key-a'])

    def test_wrong_subset_and_zero_questions(self):
        result = serialize_unit(self.db, 1, shuffle_options=True, only_question_ids=set())
        self.assertEqual(result['questions'], [])
        self.assertEqual(result['max_score'], 0)


if __name__ == '__main__':
    unittest.main()
