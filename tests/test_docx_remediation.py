import unittest
from backend.app.services.docx_parser import _ensure_numbered_blanks, _labeled_question_groups, _unlabeled_question_groups, _parse_part_b
class RemediationParserTests(unittest.TestCase):
    def test_percent_decimal_and_word_suffix_are_not_blanks(self):
        text='sharing about 1% of genes and 1.5 units. x1 word 1 useful'
        self.assertEqual(_ensure_numbered_blanks(text, range(1,2)), 'sharing about 1% of genes and 1.5 units. x1 word 1 ______ useful')
    def test_american_continues_stem_instead_of_becoming_option_a(self):
        _, questions=_labeled_question_groups(['24. Several', 'American cities are mentioned to show that', '[A] a policy works', '[B] another policy works', '[C] no policy works', '[D] all policies fail'],21)
        self.assertEqual(questions[0]['stem'], 'Several American cities are mentioned to show that')
        self.assertEqual([o['key'] for o in questions[0]['options']],list('ABCD'))
    def test_punctuation_continuation_does_not_shift_bare_options(self):
        blocks=['Passage body.']
        for n in range(5): blocks += ['The author suggests that', '.', 'first choice', 'second choice', 'third choice', 'fourth choice']
        passage,questions=_unlabeled_question_groups(blocks,21)
        self.assertEqual(passage,'Passage body.')
        self.assertEqual(len(questions),5)
        self.assertTrue(all(q['stem']=='The author suggests that.' for q in questions))
        self.assertTrue(all(q['options'][-1]['content']=='fourth choice' for q in questions))
    def test_missing_heading_labels_do_not_consume_body(self):
        blocks=['Part B','Directions:','For questions 41-45 choose subheadings from list A.', *['Body paragraph '+str(n) for n in range(12)], 'Part C']
        unit=_parse_part_b(blocks,{})
        self.assertEqual(unit['shared_data']['candidates'],{})
        self.assertIn('Body paragraph 11',unit['passage'])
    def test_candidate_continuation_stops_at_numbered_body(self):
        blocks=['Part B','Directions:','For questions 41-45 choose subheadings from list A.', '[A] First heading', '[B] Second', 'heading continued', *['['+c+'] Heading '+c for c in 'CDEFG'],'41. Body paragraph','Section III Translation']
        unit=_parse_part_b(blocks,{})
        self.assertEqual(unit['shared_data']['candidates']['B'],'Second heading continued')
        self.assertIn('Body paragraph',unit['passage'])
        self.assertNotIn('Section III',unit['passage'])
