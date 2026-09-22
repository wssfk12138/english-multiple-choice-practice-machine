import copy
import unittest
from backend.app.services.esq import _question_hash


class EsqNumberIdentityTests(unittest.TestCase):
    def test_json_integer_and_float_scores_share_content_identity(self):
        unit = {'passage': {'blocks': [{'type': 'paragraph', 'text': 'Original'}]}}
        question = {'questionKey': 'test.q1', 'number': 1, 'type': 'single_choice',
                    'stem': 'Choose', 'score': 1, 'options': [{'key': 'A', 'content': 'Answer'}],
                    'metadata': {'weight': [1, 0.5]}}
        answer = {'correctOption': 'A', 'score': 1}
        floating = copy.deepcopy(question)
        floating.update(number=1.0, score=1.0, metadata={'weight': [1.0, 0.5]})
        expected = _question_hash(unit, question, answer)
        self.assertEqual(expected, _question_hash(unit, floating, {'correctOption': 'A', 'score': 1.0}))
        self.assertNotEqual(expected, _question_hash(unit, floating, {'correctOption': 'A', 'score': 1.5}))
        changed = copy.deepcopy(question)
        changed['options'][0]['content'] = 'Changed answer'
        self.assertNotEqual(expected, _question_hash(unit, changed, answer))
