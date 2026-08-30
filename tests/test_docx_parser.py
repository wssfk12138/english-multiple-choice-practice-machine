from __future__ import annotations

import unittest

from lxml import etree

from backend.app.services.docx_parser import (
    NS,
    _cet_answer_key_is_reliable,
    _extract_answers_from_text,
    _detect_subject,
    _ensure_numbered_blanks,
    _extract_ooxml_text,
    _has_objective_part_b,
    _cet_choice_groups,
    _parse_cet_paragraph_matching,
    _parse_part_b,
    _parse_cet_units,
    _remove_duplicate_cloze_number_noise,
    apply_answers_to_draft,
    clean_text,
    extract_answer_key,
    validate_draft,
)
from backend.app.services.passage_cleanup import repair_inline_blank_paragraph_breaks


class OoxmlBlankExtractionTests(unittest.TestCase):
    def test_cet_parser_recovers_all_objective_sections(self) -> None:
        blocks = [
            "Part II Listening Comprehension",
            "Section A",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(1, 8)],
            "Section B",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(8, 16)],
            "Section C",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(16, 26)],
            "Part III Reading Comprehension",
            "Section A",
            "Passage with 26 ______ through 35 ______.",
            "A) a B) b C) c D) d E) e F) f G) g H) h I) i J) j K) k L) l M) m N) n O) o",
            "Section B",
            *[f"[{letter}] Paragraph {letter}." for letter in "ABCDEFGHIJK"],
            *[f"{n}. Statement {n}." for n in range(36, 46)],
            "Section C",
            "Passage One",
            "Questions 46 to 50 are based on the following passage.",
            "Passage one body.",
            *[f"{n}. Stem {n}?" for n in range(46, 51) for _ in [0]],
        ]
        for number in range(46, 51):
            index = blocks.index(f"{number}. Stem {number}?") + 1
            blocks[index:index] = ["A) a", "B) b", "C) c", "D) d"]
        blocks.extend([
            "Passage Two",
            "Questions 51 to 55 are based on the following passage.",
            "Passage two body.",
        ])
        for number in range(51, 56):
            blocks.extend([
                f"{number}. Stem {number}?", "A) a", "B) b", "C) c", "D) d"
            ])
        blocks.append("Part IV Translation")

        from backend.app.services.exam_templates import template_units

        units = _parse_cet_units(blocks, {}, template_units("cet4"))
        self.assertEqual(sum(len(unit["questions"]) for unit in units), 55)
        self.assertTrue(all(len(question["options"]) == 4 for unit in units[:3] for question in unit["questions"]))
        self.assertEqual(len(units[3]["shared_data"]["word_bank"]), 15)
        self.assertEqual(len(units[4]["shared_data"]["paragraphs"]), 11)
        self.assertTrue(all(question["stem"] for unit in units[-2:] for question in unit["questions"]))
        self.assertTrue(all(len(question["options"]) == 4 for unit in units[-2:] for question in unit["questions"]))

    def test_sparse_or_out_of_range_cet_answers_are_rejected(self) -> None:
        self.assertFalse(_cet_answer_key_is_reliable({1: "H", 2: "E", 18: "O"}))
        reliable = {number: "A" for number in range(1, 56)}
        reliable.update({number: "O" for number in range(26, 46)})
        self.assertTrue(_cet_answer_key_is_reliable(reliable))

    def test_paragraph_matching_recovers_a_unique_missing_final_label(self) -> None:
        blocks = [
            "Section B",
            *[f"[{letter}] Paragraph {letter}." for letter in "ABCDEFGHIJK"],
            "This separate final paragraph has enough text to be a real candidate "
            "and its missing label is confirmed by the supplied answer key.",
            *[f"{number}. Statement {number}." for number in range(36, 46)],
            "Section C",
        ]
        unit = _parse_cet_paragraph_matching(
            blocks,
            {38: "L"},
            0,
            len(blocks) - 1,
            {
                "subtype": "paragraph_matching",
                "title": "paragraph_matching",
                "seq": 1,
                "numbers": range(36, 46),
            },
        )
        self.assertEqual(
            list(unit["shared_data"]["paragraphs"]),
            list("ABCDEFGHIJKL"),
        )
        self.assertEqual(
            unit["shared_data"]["paragraphs"]["K"],
            "Paragraph K.",
        )

    def test_private_word_control_character_is_removed(self) -> None:
        self.assertEqual(clean_text("Text 3\ue004"), "Text 3")

    def test_underlined_question_number_becomes_visible_blank(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:t>The court cannot </w:t></w:r>
              <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve"> 1 </w:t></w:r>
              <w:r><w:t> its legitimacy.</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(
            clean_text(_extract_ooxml_text(paragraph)),
            "The court cannot 1 ______ its legitimacy.",
        )

    def test_underlined_word_is_not_converted_to_a_blank(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>Directions</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(clean_text(_extract_ooxml_text(paragraph)), "Directions")

    def test_explicit_underscores_are_preserved(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:t>(42) _________</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(
            clean_text(_extract_ooxml_text(paragraph)),
            "(42) _________",
        )

    def test_sequential_bare_numbers_become_blanks(self) -> None:
        passage = (
            "The site dates to 3500 B.C. It is 1 prone to earthquakes, "
            "which caused it to 2 sink. The rise 3 covered the city."
        )
        self.assertEqual(
            _ensure_numbered_blanks(passage, range(1, 4)),
            "The site dates to 3500 B.C. It is 1 ______ prone to earthquakes, "
            "which caused it to 2 ______ sink. The rise 3 ______ covered the city.",
        )

    def test_part_b_parenthesized_positions_are_normalized(self) -> None:
        self.assertEqual(
            _ensure_numbered_blanks(
                "First paragraph. (41) Second paragraph. (42) _________ Third.",
                range(41, 43),
            ),
            "First paragraph. 41 ______ Second paragraph. 42 ______ Third.",
        )

    def test_missing_number_at_broken_text_frame_is_recovered(self) -> None:
        passage = (
            "shifting 6 and climate change eroded a barrier that\n\n"
            "Pavlopetri. A survey was\n\n"
            "data to analyze sea levels 9 British researchers returned."
        )
        repaired = _ensure_numbered_blanks(passage, range(6, 10))
        self.assertIn("barrier that 7 ______ Pavlopetri", repaired)
        self.assertIn("survey was 8 ______ data", repaired)

    def test_duplicate_early_cloze_number_noise_is_removed(self) -> None:
        passage = (
            "Our lives. 1 ______ 9 AI also has the potential hazard of "
            "2 ______ changing experiences. Later they 9 ______ their preferences."
        )
        self.assertEqual(
            _remove_duplicate_cloze_number_noise(passage),
            "Our lives. 1 ______ AI also has the potential hazard of "
            "2 ______ changing experiences. Later they 9 ______ their preferences.",
        )

    def test_inline_blank_does_not_start_a_false_paragraph(self) -> None:
        passage = (
            "At first glance this might seem like a strength that\n\n"
            "1 ______ the ability to make judgments.\n\n"
            "A genuine new paragraph starts here."
        )
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(passage),
            "At first glance this might seem like a strength that "
            "1 ______ the ability to make judgments.\n\n"
            "A genuine new paragraph starts here.",
        )

    def test_sentence_ending_before_blank_keeps_paragraph_break(self) -> None:
        passage = "Here are some tips:\n\n41 ______ First tip."
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(passage),
            "Here are some tips: 41 ______ First tip.",
        )

        completed = "This is a complete sentence.\n\n41 ______ New paragraph."
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(completed),
            completed,
        )

    def test_question_body_is_not_mistaken_for_answer_key(self) -> None:
        blocks = [
            "Mark your answers on ANSWER SHEET 1.",
            "26. How should the author respond?",
            "[A] Carefully. [B] Directly.",
        ]
        self.assertEqual(extract_answer_key(blocks), {})

    def test_grouped_answer_ranges_are_supported(self) -> None:
        self.assertEqual(
            _extract_answers_from_text("1-5: ADCBB\n6-10: ADDCB"),
            {
                1: "A", 2: "D", 3: "C", 4: "B", 5: "B",
                6: "A", 7: "D", 8: "D", 9: "C", 10: "B",
            },
        )

    def test_cet_answer_ranges_support_questions_46_to_55_and_a_to_o(self) -> None:
        answers = _extract_answers_from_text(
            "26-35: I L B N G E O A D C\n46-50: B C A D C\n51-55: B C D A B"
        )
        self.assertEqual([answers[n] for n in range(26, 36)], list("ILBNGEOADC"))
        self.assertEqual([answers[n] for n in range(46, 56)], list("BCADCBCDAB"))

    def test_english_two_embedded_answer_layout_is_supported(self) -> None:
        blocks = [
            "2010年英语二参考真题答案",
            "1.D 2.C 3.B 4.A 5.A",
            "Text 121~25D A B C CText 226~30A C B D B",
            "Part B",
            "41.F 42.T 43.F 44.T 45.F",
        ]
        answers = extract_answer_key(blocks)
        self.assertEqual([answers[n] for n in range(21, 26)], list("DABCC"))
        self.assertEqual([answers[n] for n in range(41, 46)], list("FTFTF"))

    def test_english_two_subject_is_detected_from_header(self) -> None:
        self.assertEqual(
            _detect_subject(
                "2010年考研英语二真题.doc",
                ["2010 年全国硕士研究生招生考试", "英语（二）", "（科目代码：204）"],
            ),
            "英语二",
        )

    def test_true_false_part_b_is_parsed_as_objective_questions(self) -> None:
        blocks = [
            "Section II Reading Comprehension",
            "Part B",
            "Directions:",
            "Read the following text and decide whether each of the statements is true or false. Choose T if the statement is true or F if the statement is not true.",
            "Article paragraph one.",
            "Article paragraph two.",
            "Statement one.",
            "Statement two.",
            "Statement three.",
            "Statement four.",
            "Statement five.",
            "Section IIITranslation",
        ]
        self.assertTrue(_has_objective_part_b(blocks))
        unit = _parse_part_b(
            blocks,
            {41: "F", 42: "T", 43: "F", 44: "T", 45: "F"},
        )
        self.assertEqual(unit["subtype"], "true_false")
        self.assertEqual(unit["passage"], "Article paragraph one.\n\nArticle paragraph two.")
        self.assertEqual([question["stem"] for question in unit["questions"]], [
            "Statement one.",
            "Statement two.",
            "Statement three.",
            "Statement four.",
            "Statement five.",
        ])
        self.assertEqual(
            [option["key"] for option in unit["questions"][0]["options"]],
            ["T", "F"],
        )

    def test_translation_part_b_is_not_treated_as_objective(self) -> None:
        blocks = [
            "Part B",
            "Read the following text carefully and then translate the underlined segments into Chinese.",
        ]
        self.assertFalse(_has_objective_part_b(blocks))

    def test_validation_uses_questions_present_in_the_draft(self) -> None:
        def unit(unit_type: str, title: str, sequence: int, numbers: range) -> dict:
            return {
                "unit_type": unit_type,
                "subtype": "cloze" if unit_type == "cloze" else "reading_a",
                "title": title,
                "sequence": sequence,
                "passage": "Passage",
                "shared_data": {},
                "questions": [
                    {
                        "number": number,
                        "stem": "",
                        "options": [
                            {"key": key, "content": key}
                            for key in ("A", "B", "C", "D")
                        ],
                        "answer": "A",
                        "score": 0.5 if number <= 20 else 2.0,
                    }
                    for number in numbers
                ],
            }

        draft = {
            "answers": {str(number): "A" for number in range(1, 41)},
            "answer_status": {"status": "confirmed"},
            "answers_confirmed": True,
            "units": [
                unit("cloze", "完型填空", 1, range(1, 21)),
                unit("reading", "阅读 Text 1", 2, range(21, 26)),
                unit("reading", "阅读 Text 2", 3, range(26, 31)),
                unit("reading", "阅读 Text 3", 4, range(31, 36)),
                unit("reading", "阅读 Text 4", 5, range(36, 41)),
            ],
        }
        apply_answers_to_draft(draft)
        self.assertEqual(validate_draft(draft), [])

    def test_cet6_validation_matches_listening_by_subtype(self) -> None:
        def unit(unit_type: str, subtype: str, numbers: range) -> dict:
            return {
                "unit_type": unit_type,
                "subtype": subtype,
                "title": subtype,
                "sequence": 1,
                "passage": "Passage",
                "shared_data": {},
                "questions": [
                    {
                        "number": number,
                        "stem": f"Question {number}",
                        "options": [
                            {"key": key, "content": key}
                            for key in ("A", "B", "C", "D")
                        ],
                        "answer": "A",
                        "score": 1.0,
                    }
                    for number in numbers
                ],
            }

        draft = {
            "exam_type": "cet6",
            "set_number": 1,
            "answers": {str(number): "A" for number in range(1, 56)},
            "answer_status": {"status": "confirmed"},
            "answers_confirmed": True,
            "units": [
                unit("listening", "long_conversation", range(1, 9)),
                unit("listening", "passage", range(9, 16)),
                unit("listening", "lecture", range(16, 26)),
                unit("word_bank", "word_bank", range(26, 36)),
                unit("paragraph_matching", "paragraph_matching", range(36, 46)),
                unit("reading", "reading_a", range(46, 51)),
                unit("reading", "reading_a", range(51, 56)),
            ],
        }
        self.assertEqual(validate_draft(draft), [])

    def test_cet_third_set_does_not_require_listening_units(self) -> None:
        # 第三套试卷卷面常标注听力与其它套相同、不再重复给出，
        # 校验时应允许 set_number=3 的 CET 草稿缺少 listening 单元。
        def unit(unit_type: str, subtype: str, numbers: range) -> dict:
            return {
                "unit_type": unit_type,
                "subtype": subtype,
                "title": subtype,
                "sequence": 1,
                "passage": "Passage",
                "shared_data": {},
                "questions": [
                    {
                        "number": number,
                        "stem": f"Question {number}",
                        "options": [
                            {"key": key, "content": key}
                            for key in ("A", "B", "C", "D")
                        ],
                        "answer": "A",
                        "score": 1.0,
                    }
                    for number in numbers
                ],
            }

        draft = {
            "exam_type": "cet6",
            "set_number": 3,
            "answers": {str(number): "A" for number in range(26, 56)},
            "answer_status": {"status": "confirmed"},
            "answers_confirmed": True,
            "units": [
                unit("word_bank", "word_bank", range(26, 36)),
                unit("paragraph_matching", "paragraph_matching", range(36, 46)),
                unit("reading", "reading_a", range(46, 51)),
                unit("reading", "reading_a", range(51, 56)),
            ],
        }
        self.assertEqual(validate_draft(draft), [])

    def test_cet_reading_section_a_above_reading_title_is_found(self) -> None:
        # 部分试卷版式把 "Section A" 放在 "Reading Comprehension" 标题之上，
        # 修复前 _cet_section_indexes 从 reading 之后才开始找 Section A 导致漏检。
        blocks = [
            "Part II Listening Comprehension",
            "Section A",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(1, 8)],
            "Section B",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(8, 16)],
            "Section C",
            *[f"{n}. A) a{n} B) b{n} C) c{n} D) d{n}" for n in range(16, 26)],
            "Section A",
            "Reading Comprehension",
            "(40 minutes)",
            "Passage with 26 ______ through 35 ______.",
            "A) a B) b C) c D) d E) e F) f G) g H) h I) i J) j K) k L) l M) m N) n O) o",
            "Section B",
            *[f"[{letter}] Paragraph {letter}." for letter in "ABCDEFGHIJK"],
            *[f"{n}. Statement {n}." for n in range(36, 46)],
            "Section C",
            "Passage One",
            "Questions 46 to 50 are based on the following passage.",
            "Passage one body.",
            *[f"{n}. Stem {n}?" for n in range(46, 51)],
            "Passage Two",
            "Questions 51 to 55 are based on the following passage.",
            "Passage two body.",
            *[f"{n}. Stem {n}?" for n in range(51, 56)],
            "Part IV Translation",
        ]
        for number in range(46, 51):
            index = blocks.index(f"{number}. Stem {number}?") + 1
            blocks[index:index] = ["A) a", "B) b", "C) c", "D) d"]
        for number in range(51, 56):
            index = blocks.index(f"{number}. Stem {number}?") + 1
            blocks[index:index] = ["A) a", "B) b", "C) c", "D) d"]

        from backend.app.services.exam_templates import template_units

        units = _parse_cet_units(blocks, {}, template_units("cet6"))
        self.assertEqual(sum(len(unit["questions"]) for unit in units), 55)
        self.assertEqual(len(units), 7)

    def test_cet_mangled_questions_header_still_starts_option_groups(self) -> None:
        # PDF 文字层把 "Questions 1 to 4" 渲染成 "Questions! to 4"，
        # 修复前头部正则 ^Questions?\s+\d+ 匹配失败导致前 4 题选项被跳过。
        blocks = [
            "Directions: In this section, you will hear two long conversations.",
            "Questions! to 4 are based on the conversation you have just heard.",
            "1. A) a1 C) c1",
            "B) b1 D) d1",
            "2. A) a2 C) c2",
            "B) b2 D) d2",
            "3. A) a3 C) c3",
            "B) b3 D) d3",
            "4. A) a4 C) c4",
            "B) b4 D) d4",
            "Questions 5 to 8 are based on the conversation you have just heard.",
            "5. A) a5 C) c5",
            "B) b5 D) d5",
            "6. A) a6 C) c6",
            "B) b6 D) d6",
            "7. A) a7 C) c7",
            "B) b7 D) d7",
            "8. A) a8 C) c8",
            "B) b8 D) d8",
        ]
        choices = _cet_choice_groups(blocks, 8)
        self.assertEqual(len(choices), 8)
        self.assertTrue(
            all([option["key"] for option in group] == list("ABCD") for group in choices)
        )

    def test_cet_parser_does_not_crash_when_listening_sections_are_incomplete(self) -> None:
        from backend.app.services.exam_templates import template_units

        blocks = [
            "Part II Listening Comprehension",
            "Section A",
            "1. A) a B) b C) c D) d",
            "Section B",
            "Part III Reading Comprehension",
        ]
        self.assertEqual(_parse_cet_units(blocks, {}, template_units("cet6")), [])


if __name__ == "__main__":
    unittest.main()
