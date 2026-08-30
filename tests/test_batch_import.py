from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.schemas import ModelAssistRequest
from tools.batch_import import _month_token, _set_number, discover_batch


class BatchImportDiscoveryTests(unittest.TestCase):
    def test_month_token_handles_dot_and_chinese_month_formats(self) -> None:
        self.assertEqual(_month_token(Path("2025.12四级第1套.pdf")), "12")
        self.assertEqual(_month_token(Path("2024年12月六级第1套.pdf")), "12")
        self.assertEqual(_month_token(Path("2025-06六级第1套.pdf")), "06")

    def test_set_number_handles_parenthesized_sets_but_not_combined_set_names(self) -> None:
        self.assertEqual(_set_number(Path("2025.12四级第1套.pdf")), 1)
        self.assertEqual(_set_number(Path("2025.12四级（第二套）.pdf")), 2)
        self.assertEqual(_set_number(Path("2025.12四级(3).pdf")), 3)
        self.assertIsNone(_set_number(Path("2025.12四级真题全3套.pdf")))

    def test_pairs_answer_and_audio_by_year_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "2023-06-第一套"
            folder.mkdir()
            question = folder / "2023年6月四级第一套.docx"
            answer = folder / "2023年6月四级第一套答案.pdf"
            audio = folder / "2023年6月四级第一套听力.mp3"
            question.write_bytes(b"question")
            answer.write_bytes(b"answer")
            audio.write_bytes(b"audio")

            items = discover_batch(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(Path(items[0].question_path), question)
            self.assertEqual(tuple(Path(path) for path in items[0].answer_paths), (answer,))
            self.assertEqual(tuple(Path(path) for path in items[0].audio_paths), (audio,))

    def test_discovers_supported_non_mp3_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "2026-06-第一套"
            folder.mkdir()
            question = folder / "2026年6月六级第一套.docx"
            question.write_bytes(b"question")
            audio = folder / "2026年6月六级第一套听力.m4a"
            audio.write_bytes(b"audio")

            items = discover_batch(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(tuple(Path(path) for path in items[0].audio_paths), (audio,))

    def test_does_not_guess_unmarked_answer_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = root / "2010年考研英语二真题.doc"
            unrelated = root / "2010年考研英语二参考资料.pdf"
            question.write_bytes(b"question")
            unrelated.write_bytes(b"reference")

            items = discover_batch(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].answer_paths, ())

    def test_does_not_cross_pair_cet4_and_cet6(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cet4 = root / "四级历年真题资料" / "2024年06月四级"
            cet6 = root / "六级历年真题资料" / "2024年06月六级"
            cet4.mkdir(parents=True)
            cet6.mkdir(parents=True)
            cet4_question = cet4 / "2024年6月四级第1套.docx"
            cet6_question = cet6 / "2024年6月六级第1套.docx"
            cet4_answer = cet4 / "2024年6月四级第1套解析.pdf"
            cet6_answer = cet6 / "2024年6月六级第1套解析.pdf"
            for path in (cet4_question, cet6_question, cet4_answer, cet6_answer):
                path.write_bytes(path.name.encode("utf-8"))

            items = discover_batch(root)
            by_question = {Path(item.question_path): item for item in items}

            self.assertEqual(
                tuple(Path(path) for path in by_question[cet4_question].answer_paths),
                (cet4_answer,),
            )
            self.assertEqual(
                tuple(Path(path) for path in by_question[cet6_question].answer_paths),
                (cet6_answer,),
            )

    def test_filename_level_wins_over_mixed_parent_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "六级历年真题资料" / "2024年06月六级"
            folder.mkdir(parents=True)
            question = folder / "2024年6月六级第1套.docx"
            cet4_answer = folder / "2024年6月四级第1套解析.pdf"
            cet6_answer = folder / "2024年6月六级第1套解析.pdf"
            question.write_bytes(b"question")
            cet4_answer.write_bytes(b"cet4")
            cet6_answer.write_bytes(b"cet6")

            items = discover_batch(root)

            self.assertEqual(
                tuple(Path(path) for path in items[0].answer_paths),
                (cet6_answer,),
            )

    def test_recognizes_chinese_set_numbers_and_ignores_listening_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "2024年06月四级"
            folder.mkdir()
            question = folder / "2024年6月四级第 一 套.docx"
            answer = folder / "2024年6月四级解析第一套.pdf"
            transcript = folder / "2024年6月第1套听力原文译文.pdf"
            for path in (question, answer, transcript):
                path.write_bytes(path.name.encode("utf-8"))

            items = discover_batch(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(tuple(Path(path) for path in items[0].answer_paths), (answer,))

    def test_ignores_question_named_with_opposite_level_in_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "六级历年真题资料"
            folder = source / "2020年07月六级"
            folder.mkdir(parents=True)
            wrong = folder / "2020年07月四级真题全1套.docx"
            right = folder / "2020年07月六级真题全1套.pdf"
            wrong.write_bytes(b"wrong")
            right.write_bytes(b"right")

            items = discover_batch(source)

            self.assertEqual([Path(item.question_path) for item in items], [right])

    def test_all_sets_word_pairs_multiple_answer_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question_folder = root / "2017年06月四级" / "2017.06四级真题word"
            answer_folder = root / "2017年06月四级" / "2017.06四级解析PDF"
            question_folder.mkdir(parents=True)
            answer_folder.mkdir(parents=True)
            question = question_folder / "2017年06月四级真题全3套.docx"
            question.write_bytes(b"question")
            answers = []
            for set_number in (1, 2, 3):
                answer = answer_folder / f"2017.06英语四级解析第{set_number}套.pdf"
                answer.write_bytes(f"answer-{set_number}".encode())
                answers.append(answer)

            items = discover_batch(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(
                {Path(path) for path in items[0].answer_paths},
                set(answers),
            )

    def test_model_assist_output_budget_is_provider_managed(self) -> None:
        request = ModelAssistRequest(max_tokens=12000)
        self.assertEqual(request.max_tokens, 12000)
        self.assertEqual(ModelAssistRequest(max_tokens=100000).max_tokens, 100000)

    def test_prefers_individual_pdfs_over_combined_three_set_word(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "2018年12月四级"
            folder.mkdir()
            (folder / "2018年12月四级真题全3套.docx").write_bytes(b"combined")
            for set_number in (1, 2, 3):
                (folder / f"2018.12四级真题第{set_number}套.pdf").write_bytes(
                    f"pdf-{set_number}".encode()
                )
                (folder / f"2018.12英语四级解析第{set_number}套.pdf").write_bytes(
                    f"answer-{set_number}".encode()
                )

            items = discover_batch(root)

            self.assertEqual(len(items), 3)
            self.assertEqual(
                {Path(item.question_path).stem for item in items},
                {
                    "2018.12四级真题第1套",
                    "2018.12四级真题第2套",
                    "2018.12四级真题第3套",
                },
            )
            self.assertTrue(all(len(item.answer_paths) == 1 for item in items))


if __name__ == "__main__":
    unittest.main()
