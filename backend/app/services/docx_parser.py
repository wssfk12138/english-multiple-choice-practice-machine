from __future__ import annotations

import json
import copy
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree
from pypdf import PdfReader

from .passage_cleanup import repair_inline_blank_paragraph_breaks


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
PAGE_FOOTER_RE = re.compile(r"英语.*试题.*共\s*1?\s*5\s*页", re.I)
OPTION_MARK_RE = re.compile(
    # A bare ``A.``/``B)`` option marker must not be recognized inside a
    # lowercase word.  The previous ``(?<![A-Z])`` guard treated the final
    # ``e.`` in ``sense.`` and ``d.`` in ``world.`` as option markers, which
    # silently removed the final character from 2002 reading options.
    r"(?:\[|\(|（|【|(?<![A-Za-z]))\s*([A-Ha-h])\s*(?:\]|\)|）|】|[\.．、,])\s*",
)
QUESTION_NUMBER_RE = re.compile(r"^\s*([1-5]?\d)\s*[\.．、)]\s*(.+)$", re.S)
TEXT_MARK_RE = re.compile(r"^\s*Text\s*([1-4lI])\s*$", re.I)
ANSWER_SYMBOL_RE = r"(?:[A-O]|T)"
INVISIBLE_TEXT_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ue000-\uf8ff]"
)


def clean_text(value: str) -> str:
    value = INVISIBLE_TEXT_RE.sub("", value)
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = value.replace("〇", "0").replace("○", "0")
    value = value.replace("［", "[").replace("］", "]")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _convert_legacy(source: Path) -> Path:
    output_dir = Path(tempfile.mkdtemp(prefix="linjian-word-"))
    destination = output_dir / f"{source.stem}-converted.docx"
    script = Path(__file__).with_name("convert_word.ps1")
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Source",
            str(source),
            "-Destination",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return destination


def _pdf_blocks_by_coordinates(path: Path) -> list[str]:
    """Rebuild PDF text lines from word coordinates when the text layer has
    fragmented layout (common in copyable CET papers).  Falls back to a
    no-op list when pdfplumber is unavailable."""
    try:
        import pdfplumber
    except Exception:
        return []
    rebuilt: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False)
            lines: dict[int, list[tuple[float, str]]] = {}
            for word in words:
                key = round(word["top"] / 5)
                lines.setdefault(key, []).append((word["x0"], word["text"]))
            ordered = [
                " ".join(text for _, text in sorted(items))
                for _, items in sorted(lines.items())
            ]
            merged: list[str] = []
            for line in ordered:
                if (
                    merged
                    and re.search(r"(?:Part|Section)\s*$", merged[-1])
                    and re.search(r"^[ⅠIIVABC1-4]\b", line)
                ):
                    merged[-1] += " " + line
                else:
                    merged.append(line)
            rebuilt.extend(cleaned for line in merged if (cleaned := clean_text(line)))
    return rebuilt


def detect_format(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "text_pdf"
    signature = path.read_bytes()[:8]
    if signature == bytes.fromhex("D0CF11E0A1B11AE1"):
        return "legacy_doc"
    with zipfile.ZipFile(path) as archive:
        content_types = archive.read("[Content_Types].xml").decode(
            "utf-8", errors="replace"
        )
    if "macroEnabled" in content_types:
        return "macro_ooxml"
    return "docx"


def _extract_ooxml_text(element: etree._Element) -> str:
    """Read visible OOXML text while preserving formatted exam blanks.

    Some source papers draw a cloze/Part B blank by underlining only the
    question-number run. Reading ``w:t`` nodes alone keeps the number but loses
    the visual line, so normalize that run to a portable ``N ______`` marker.
    """
    parts: list[str] = []
    for run in element.xpath(".//w:r", namespaces=NS):
        text = "".join(run.xpath(".//w:t/text()", namespaces=NS))
        if not text:
            continue
        underline = run.xpath("./w:rPr/w:u/@w:val", namespaces=NS)
        is_underlined = bool(underline) and underline[0].lower() not in {
            "none",
            "0",
            "false",
        }
        stripped = text.strip()
        if is_underlined and re.fullmatch(r"(?:[1-9]|[1-4]\d)", stripped):
            parts.append(f" {stripped} ______ ")
        else:
            parts.append(text)
    return "".join(parts)


def _ensure_numbered_blanks(passage: str, numbers: range) -> str:
    """Normalize sequential exam placeholders to ``N ______``.

    A few Word exports preserve only some underline formatting. The question
    numbers themselves remain in reading order, which lets us safely repair the
    remaining placeholders without treating years or measurements as blanks.
    """
    expected = list(numbers)
    cursor = 0
    for number in numbers:
        pattern = re.compile(
            rf"(?<![\d_])(?:\(\s*)?{number}(?:\s*\))?(?:\s*_{2,})?(?![\d_])"
        )
        match = pattern.search(passage, cursor)
        if not match:
            continue
        end = match.end()
        trailing_blank = re.match(r"\s*_{2,}", passage[end:])
        if trailing_blank:
            end += trailing_blank.end()
        replacement = f"{number} ______"
        passage = passage[: match.start()] + replacement + passage[end:]
        cursor = match.start() + len(replacement)

    present = {
        int(number)
        for number in re.findall(r"(?<!\d)([1-4]?\d)\s+_{2,}", passage)
    }
    missing = [number for number in expected if number not in present]
    for number in missing:
        previous = max((value for value in present if value < number), default=None)
        following = min((value for value in present if value > number), default=None)
        if previous is None or following is None:
            continue
        previous_match = re.search(
            rf"(?<!\d){previous}\s+_{{2,}}",
            passage,
        )
        following_match = re.search(
            rf"(?<!\d){following}\s+_{{2,}}",
            passage,
            re.S,
        )
        if not previous_match or not following_match:
            continue
        gap_start = previous_match.end()
        gap_end = following_match.start()
        gap = passage[gap_start:gap_end]
        # Broken text frames occasionally drop the placeholder at a hard line
        # boundary, leaving fragments such as "barrier that\n\nPavlopetri".
        boundary = re.search(r"(?<=[A-Za-z])\n\n(?=[A-Za-z])", gap)
        if not boundary:
            continue
        insertion = gap_start + boundary.start()
        replacement = f" {number} ______ "
        passage = passage[:insertion] + replacement + passage[insertion + 2 :]
        present.add(number)
    return passage


def _remove_duplicate_cloze_number_noise(passage: str) -> str:
    """Remove a stray early copy of a question number when its real blank exists.

    The 2026 source contains ``1 [blank] 9 AI also...`` and then the genuine
    ninth placeholder later in the article. Restrict cleanup to a bare number
    immediately following another blank and followed by a capitalized word.
    """
    for number in range(1, 21):
        marked = re.search(rf"(?<!\d){number}\s+_{{2,}}", passage)
        if not marked:
            continue
        noise = re.search(
            rf"((?:[1-9]|1\d|20)\s+_{{2,}})\s+{number}(?=\s+[A-Z])",
            passage[: marked.start()],
        )
        if noise:
            start = noise.start() + len(noise.group(1))
            passage = passage[:start] + passage[noise.end() :]
    return passage


def extract_blocks(path: Path) -> tuple[list[str], str, Path | None]:
    detected = detect_format(path)
    converted: Path | None = None
    parse_path = path
    if detected == "text_pdf":
        reader = PdfReader(str(path))
        blocks: list[str] = []
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                page_text = page.extract_text() or ""
            blocks.extend(
                cleaned
                for line in page_text.splitlines()
                if (cleaned := clean_text(line))
            )
        joined = "\n".join(blocks)
        if (
            re.search(r"Reading\s+Comprehension", joined) is None
            and re.search(r"Comprehension\s*\(\s*\d+\s*minutes", joined) is not None
        ):
            # Some copyable CET PDFs split the reading header into separate
            # layout fragments (e.g. ``Comprehension (40 minutes)`` and
            # ``Pa rt III Reading``), which breaks section detection.  Rebuild
            # line order from word coordinates so the parser sees the same
            # headers a human would.
            rebuilt = _pdf_blocks_by_coordinates(path)
            if rebuilt and re.search(r"Reading\s+Comprehension", "\n".join(rebuilt)):
                blocks = rebuilt
        if len(re.sub(r"\s+", "", "\n".join(blocks))) < 100:
            raise ValueError("PDF 未检测到可靠文字层，请改用 Word 或先进行 OCR")
        return blocks, detected, None
    if detected == "legacy_doc":
        converted = _convert_legacy(path)
        parse_path = converted

    with zipfile.ZipFile(parse_path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    blocks: list[str] = []
    for child in root.xpath("./w:body/*", namespaces=NS):
        text = _extract_ooxml_text(child)
        text = clean_text(text)
        if text:
            blocks.append(text)
    return blocks, detected, converted


def create_docx_block_fragment(
    source: Path,
    destination: Path,
    *,
    start_block: int,
    end_block: int,
) -> None:
    """Copy an OOXML document while retaining only a visible block range.

    ``start_block`` and ``end_block`` use the same zero-based, inclusive
    indexes returned by :func:`extract_blocks`.  Section properties are kept
    so Word and the local parser can still open the generated fragment.
    """
    detected = detect_format(source)
    converted: Path | None = None
    parse_path = source
    if detected == "legacy_doc":
        converted = _convert_legacy(source)
        parse_path = converted
    if detected == "text_pdf":
        raise ValueError("PDF 多套拆分暂不支持生成 Word 片段")
    if start_block < 0 or end_block < start_block:
        raise ValueError("试卷拆分边界无效")
    try:
        with zipfile.ZipFile(parse_path) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
            body = root.find("w:body", namespaces=NS)
            if body is None:
                raise ValueError("Word 文档缺少正文")
            visible_index = -1
            retained: list[etree._Element] = []
            for child in list(body):
                if child.tag == f"{{{NS['w']}}}sectPr":
                    retained.append(copy.deepcopy(child))
                    continue
                text = clean_text(_extract_ooxml_text(child))
                if text and not PAGE_FOOTER_RE.search(text):
                    visible_index += 1
                    if start_block <= visible_index <= end_block:
                        retained.append(copy.deepcopy(child))
            if not retained or all(
                child.tag == f"{{{NS['w']}}}sectPr" for child in retained
            ):
                raise ValueError("拆分边界没有包含可见正文")
            for child in list(body):
                body.remove(child)
            for child in retained:
                body.append(child)
            document_xml = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
                for item in archive.infolist():
                    data = (
                        document_xml
                        if item.filename == "word/document.xml"
                        else archive.read(item.filename)
                    )
                    output.writestr(item, data)
    finally:
        if converted:
            shutil.rmtree(converted.parent, ignore_errors=True)


def _find_index(blocks: list[str], pattern: str, start: int = 0, end: int | None = None) -> int:
    rx = re.compile(pattern, re.I)
    stop = end if end is not None else len(blocks)
    for index in range(start, stop):
        if rx.search(blocks[index]):
            return index
    return -1


def _is_noise(text: str) -> bool:
    return bool(
        PAGE_FOOTER_RE.search(text)
        or re.match(r"^(Directions:|Part [ABC]|Section [ⅠⅡⅢIVU ]+)", text, re.I)
        or "ANSWER SHEET" in text
    )


def _split_option_text(text: str) -> list[tuple[str, str]]:
    normalized = clean_text(text)
    # OCR frequently turns "[A]" into "A." or joins all options into one paragraph.
    matches = list(OPTION_MARK_RE.finditer(normalized))
    if len(matches) < 2:
        return []
    options: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        label = match.group(1).upper()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = clean_text(normalized[start:end])
        if content:
            options.append((label, content))
    return options


def _extract_flat_option_groups(text: str, expected_groups: int) -> list[list[dict[str, str]]]:
    normalized = clean_text(text)
    matches = list(OPTION_MARK_RE.finditer(normalized))
    if len(matches) < expected_groups * 4:
        return []
    flat: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        label = match.group(1).upper()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = clean_text(normalized[start:end])
        # Joined Word paragraphs often leave the next question number after option D.
        content = re.sub(r"\s*(?:[1-9]|1\d|20)\s*[\.．、]\s*$", "", content)
        flat.append((label, content))
    groups: list[list[dict[str, str]]] = []
    for offset in range(0, expected_groups * 4, 4):
        chunk = flat[offset:offset + 4]
        if [label for label, _ in chunk] != ["A", "B", "C", "D"]:
            return []
        groups.append(
            [{"key": label, "content": content} for label, content in chunk]
        )
    return groups


def _options_from_segment(text: str) -> list[dict[str, str]]:
    return [
        {"key": label, "content": content}
        for label, content in _split_option_text(text)
    ]


def _parse_compact_cloze_options(text: str) -> list[list[dict[str, str]]]:
    """Parse Word exports that flatten an option table into one paragraph.

    Some older files serialize the first table row by columns (A1, A2, B1,
    B2...), while modern files serialize all 20 rows as `1. A...B...`.
    Question-number anchors let us safely recover both layouts.
    """
    normalized = clean_text(text)
    starts = list(
        re.finditer(r"(?<!\d)([1-9]|1\d|20)\s*[\.．、]\s*", normalized)
    )
    by_number: dict[int, list[dict[str, str]]] = {}
    for index, start in enumerate(starts):
        number = int(start.group(1))
        if not 1 <= number <= 20:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
        options = _options_from_segment(normalized[start.end() : end])
        if len(options) == 4 and [option["key"] for option in options] == [
            "A",
            "B",
            "C",
            "D",
        ]:
            by_number[number] = options

    run_start = next(
        (
            number
            for number in range(1, 21)
            if all(candidate in by_number for candidate in range(number, 21))
        ),
        21,
    )
    if run_start > 1:
        anchor = next(
            (
                match.start()
                for match in starts
                if int(match.group(1)) == run_start
            ),
            len(normalized),
        )
        prefix = normalized[:anchor]
        prefix_options = _split_option_text(prefix)
        missing_count = run_start - 1
        if len(prefix_options) == missing_count * 4:
            labels = [label for label, _ in prefix_options]
            expected_column_major = (
                ["A"] * missing_count
                + ["B"] * missing_count
                + ["C"] * missing_count
                + ["D"] * missing_count
            )
            if labels == expected_column_major:
                for question_index in range(missing_count):
                    by_number[question_index + 1] = [
                        {
                            "key": chr(ord("A") + column),
                            "content": prefix_options[column * missing_count + question_index][1],
                        }
                        for column in range(4)
                    ]
            else:
                for question_index in range(missing_count):
                    offset = question_index * 4
                    chunk = prefix_options[offset : offset + 4]
                    if [label for label, _ in chunk] == ["A", "B", "C", "D"]:
                        by_number[question_index + 1] = [
                            {"key": label, "content": content}
                            for label, content in chunk
                        ]

    if len(by_number) == 20:
        return [by_number[number] for number in range(1, 21)]

    # Older clean exports contain exactly 80 row-major markers and no reliable
    # question-number anchors.
    flat = _extract_flat_option_groups(normalized, 20)
    return flat if len(flat) == 20 else []


def _parse_numbered_options(text: str) -> list[dict[str, Any]]:
    normalized = clean_text(text)
    starts = list(re.finditer(r"(?<!\d)([1-9]|1\d|20)\s*[\.．、]\s*", normalized))
    if len(starts) < 5:
        return []
    result = []
    for index, start in enumerate(starts):
        number = int(start.group(1))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
        section = normalized[start.end():end]
        options = _split_option_text(section)
        if len(options) == 4:
            result.append(
                {
                    "number": number,
                    "stem": "",
                    "options": [{"key": label, "content": content} for label, content in options],
                }
            )
    return result


def _extract_answers_from_text(text: str) -> dict[int, str]:
    answers: dict[int, str] = {}
    for number, letter in re.findall(
        rf"(?<!\d)([1-5]?\d)\s*[\.．、:：]\s*({ANSWER_SYMBOL_RE})(?=\s|$|\d|[.,，。])",
        text,
        re.I,
    ):
        numeric = int(number)
        if 1 <= numeric <= 55:
            answers[numeric] = letter.upper()

    range_pattern = re.compile(
        r"(?:Text\s*[1-4]\s*|(?<![A-Za-z0-9]))"
        r"([1-5]?\d)\s*[-~～至–—]\s*([1-5]?\d)\s*[:：]?\s*"
        r"(.*?)"
        r"(?=Text\s*[1-4]\s*[1-5]?\d\s*[-~～至–—]\s*[1-5]?\d"
        r"|(?<![A-Za-z0-9])[1-5]?\d\s*[-~～至–—]\s*[1-5]?\d"
        r"|Part\s+[BC]|Section\s+[ⅠⅡⅢIV1234]|$)",
        re.I | re.S,
    )
    for match in range_pattern.finditer(text):
        first, last = int(match.group(1)), int(match.group(2))
        letters = re.findall(ANSWER_SYMBOL_RE, match.group(3).upper())
        expected = last - first + 1
        if expected > 0 and len(letters) == expected:
            for offset, letter in enumerate(letters):
                answers[first + offset] = letter

    compact = re.sub(r"\s+", "", text).upper()
    for start, end, letters in re.findall(
        rf"(?<!\d)([1-5]?\d)[-~～至–—]([1-5]?\d)[:：]?((?:{ANSWER_SYMBOL_RE}){{2,20}})",
        compact,
    ):
        first, last = int(start), int(end)
        expected = last - first + 1
        if expected > 0 and len(letters) == expected:
            for offset, letter in enumerate(letters[:expected]):
                answers[first + offset] = letter

    return answers


def extract_answer_key(
    blocks: list[str],
    *,
    require_heading: bool = True,
) -> dict[int, str]:
    text = "\n".join(blocks)
    heading_matches = list(
        re.finditer(
            r"答案速查|参考(?:真题)?答案|标准答案|answer\s*key|^\s*answers?\s*[:：]?\s*$",
            text,
            re.I | re.M,
        )
    )
    if require_heading:
        if not heading_matches:
            return {}
        text = text[heading_matches[-1].start() :]
    return _extract_answers_from_text(text)


def extract_pdf_answer_key(path: Path) -> dict[int, str]:
    reader = PdfReader(str(path))
    layout_pages: list[str] = []
    plain_pages: list[str] = []
    for page in reader.pages:
        plain_pages.append(page.extract_text() or "")
        try:
            layout_pages.append(page.extract_text(extraction_mode="layout") or "")
        except Exception:
            layout_pages.append(plain_pages[-1])
    layout_text = "\n".join(layout_pages)
    answers = _extract_answers_from_text(layout_text)
    if answers:
        return answers
    return _extract_answers_from_text("\n".join(plain_pages))


def _cet_answer_key_is_reliable(answers: dict[int, str]) -> bool:
    """Reject sparse/garbled PDF text that merely resembles CET answers."""
    if len(answers) < 25:
        return False
    for number, answer in answers.items():
        allowed = "ABCDEFGHIJKLMNOPQR" if 26 <= number <= 45 else "ABCD"
        if answer not in allowed:
            return False
    return True


def extract_answer_attachment(
    path: Path, *, exam_type: str = ""
) -> tuple[dict[int, str], dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        has_text_layer = len(re.sub(r"\s+", "", extracted_text)) >= 20
        if not has_text_layer:
            return {}, {
                "status": "manual_required",
                "message": "答案 PDF 未检测到可靠文字层，请人工录入答案",
            }
        answers = extract_pdf_answer_key(path)
        if not answers or (
            exam_type in {"cet4", "cet6"}
            and not _cet_answer_key_is_reliable(answers)
        ):
            return {}, {
                "status": "manual_required",
                "message": "答案 PDF 文字层乱码或答案覆盖不足，请通过 OCR/人工校对录入",
            }
        return answers, {
            "status": "parsed",
            "message": f"已从文本型 PDF 识别 {len(answers)} 道答案，请发布前核对",
        }

    blocks, _, converted = extract_blocks(path)
    try:
        answers = extract_answer_key(blocks, require_heading=False)
    finally:
        if converted:
            shutil.rmtree(converted.parent, ignore_errors=True)
    if not answers or (
        exam_type in {"cet4", "cet6"}
        and not _cet_answer_key_is_reliable(answers)
    ):
        return {}, {
            "status": "manual_required",
            "message": "答案 Word 未识别出可靠的客观题答案，请人工录入",
        }
    return answers, {
        "status": "parsed",
        "message": f"已从答案 Word 识别 {len(answers)} 道答案，请发布前核对",
    }


def find_companion_answer_pdf(path: Path, year: int | None) -> Path | None:
    if year is None:
        return None
    matches = sorted(
        candidate
        for candidate in path.parent.iterdir()
        if candidate.suffix.lower() == ".pdf"
        and str(year) in candidate.name
        and ("答案" in candidate.name or "answer" in candidate.name.lower())
    )
    return matches[0] if matches else None


def _parse_cloze(blocks: list[str], answers: dict[int, str]) -> dict[str, Any]:
    start = _find_index(blocks, r"Section\s*[ⅠI1]\s*Use\s+of\s+English")
    reading = _find_index(blocks, r"Reading\s+Comprehension", max(start, 0))
    section = blocks[start + 1:reading] if start >= 0 and reading > start else []
    content_blocks = [
        text
        for text in section
        if not _is_noise(text)
        and not re.search(r"Choose the best word", text, re.I)
    ]
    option_candidates = sorted(
        content_blocks,
        key=lambda text: len(OPTION_MARK_RE.findall(text)),
        reverse=True,
    )
    option_block = option_candidates[0] if option_candidates else ""
    flat_groups = _parse_compact_cloze_options(option_block)
    if flat_groups:
        option_block_indices = {
            index
            for index, text in enumerate(content_blocks)
            if text == option_block
        }
        final_questions = []
        for number, options in enumerate(flat_groups, 1):
            final_questions.append(
                {
                    "number": number,
                    "stem": "",
                    "options": options,
                    "answer": answers.get(number, ""),
                    "score": 0.5,
                }
            )
        return {
            "unit_type": "cloze",
            "subtype": "cloze",
            "title": "完型填空",
            "sequence": 1,
            "passage": "\n\n".join(
                text
                for index, text in enumerate(content_blocks)
                if index not in option_block_indices
            ),
            "shared_data": {},
            "questions": final_questions,
        }

    standalone_options = [
        _options_from_segment(text)
        for text in content_blocks
        if len(_options_from_segment(text)) == 4
    ]
    if len(standalone_options) == 20:
        option_texts = {
            text
            for text in content_blocks
            if len(_options_from_segment(text)) == 4
        }
        return {
            "unit_type": "cloze",
            "subtype": "cloze",
            "title": "完型填空",
            "sequence": 1,
            "passage": "\n\n".join(
                text for text in content_blocks if text not in option_texts
            ),
            "shared_data": {},
            "questions": [
                {
                    "number": number,
                    "stem": "",
                    "options": standalone_options[number - 1],
                    "answer": answers.get(number, ""),
                    "score": 0.5,
                }
                for number in range(1, 21)
            ],
        }

    questions: list[dict[str, Any]] = []
    option_block_indices = set()
    for index, text in enumerate(content_blocks):
        parsed = _parse_numbered_options(text)
        if parsed:
            questions.extend(parsed)
            option_block_indices.add(index)
            continue
        match = QUESTION_NUMBER_RE.match(text)
        if match and 1 <= int(match.group(1)) <= 20:
            options = _split_option_text(text)
            if len(options) == 4:
                questions.append(
                    {
                        "number": int(match.group(1)),
                        "stem": "",
                        "options": [
                            {"key": label, "content": content}
                            for label, content in options
                        ],
                    }
                )
                option_block_indices.add(index)

    deduped = {question["number"]: question for question in questions}
    passage = "\n\n".join(
        text for index, text in enumerate(content_blocks) if index not in option_block_indices
    )
    final_questions = []
    for number in range(1, 21):
        question = deduped.get(
            number,
            {"number": number, "stem": "", "options": []},
        )
        question["answer"] = answers.get(number, "")
        question["score"] = 0.5
        final_questions.append(question)
    return {
        "unit_type": "cloze",
        "subtype": "cloze",
        "title": "完型填空",
        "sequence": 1,
        "passage": passage,
        "shared_data": {},
        "questions": final_questions,
    }


def _labeled_question_groups(segment: list[str], first_number: int) -> tuple[str, list[dict]]:
    passage_parts = []
    questions: list[dict] = []
    current: dict[str, Any] | None = None
    question_started = False
    expected = set(range(first_number, first_number + 5))

    for text in segment:
        if _is_noise(text):
            continue
        match = QUESTION_NUMBER_RE.match(text)
        if match and int(match.group(1)) in expected:
            question_started = True
            if current:
                questions.append(current)
            current = {
                "number": int(match.group(1)),
                "stem": clean_text(match.group(2)),
                "options": [],
            }
            inline = _split_option_text(current["stem"])
            if inline:
                current["stem"] = clean_text(
                    current["stem"][: OPTION_MARK_RE.search(current["stem"]).start()]
                )
                current["options"] = [
                    {"key": label, "content": content} for label, content in inline
                ]
            continue

        options = _split_option_text(text)
        if current and options:
            current["options"].extend(
                {"key": label, "content": content} for label, content in options
            )
        elif current and re.match(r"^\s*\[?[A-D]\]?", text, re.I):
            label_match = re.match(
                r"^\s*(?:\[|\(|（|【)?([A-D])(?:\]|\)|）|】|[\.．、])?\s*(.*)$",
                text,
                re.I | re.S,
            )
            if label_match:
                current["options"].append(
                    {
                        "key": label_match.group(1).upper(),
                        "content": clean_text(label_match.group(2)),
                    }
                )
        elif current and len(current["options"]) < 4 and question_started:
            current["options"].append(
                {
                    "key": chr(ord("A") + len(current["options"])),
                    "content": text,
                }
            )
        elif not question_started:
            passage_parts.append(text)
    if current:
        questions.append(current)
    return "\n\n".join(passage_parts), questions


def _unlabeled_question_groups(
    segment: list[str], first_number: int
) -> tuple[str, list[dict]]:
    clean = [text for text in segment if not _is_noise(text)]
    # Modern OCR exports produce exactly five stems followed by four bare options each.
    if len(clean) < 25:
        return "\n\n".join(clean), []
    tail = clean[-25:]
    questions = []
    for index in range(5):
        offset = index * 5
        stem = tail[offset]
        options = tail[offset + 1:offset + 5]
        questions.append(
            {
                "number": first_number + index,
                "stem": stem,
                "options": [
                    {"key": chr(ord("A") + option_index), "content": content}
                    for option_index, content in enumerate(options)
                ],
            }
        )
    return "\n\n".join(clean[:-25]), questions


def _parse_reading(blocks: list[str], answers: dict[int, str]) -> list[dict[str, Any]]:
    markers = [
        (
            index,
            1 if match.group(1).lower() in {"l", "i"} else int(match.group(1)),
        )
        for index, text in enumerate(blocks)
        if (match := TEXT_MARK_RE.match(text))
    ]
    part_b = _find_index(blocks, r"^\s*Part\s*B\s*$")
    units = []
    for marker_index, text_number in markers[:4]:
        following = [
            index for index, _ in markers if index > marker_index
        ]
        end = min(following) if following else (part_b if part_b > marker_index else len(blocks))
        segment = blocks[marker_index + 1:end]
        first_number = 21 + (text_number - 1) * 5
        passage, questions = _labeled_question_groups(segment, first_number)
        if len(questions) != 5 or any(len(question["options"]) != 4 for question in questions):
            passage, questions = _unlabeled_question_groups(segment, first_number)
        for question in questions:
            question["answer"] = answers.get(question["number"], "")
            question["score"] = 2.0
        units.append(
            {
                "unit_type": "reading",
                "subtype": "reading_a",
                "title": f"阅读 Text {text_number}",
                "sequence": 1 + text_number,
                "passage": passage,
                "shared_data": {},
                "questions": questions,
            }
        )
    return units


CET_CHOICE_MARK_RE = re.compile(
    r"(?<![A-Za-z])([A-D])\s*(?:\)|\]|）|[.．、])\s*", re.I
)
CET_BANK_MARK_RE = re.compile(r"([A-O0])\s*(?:\)|\]|）|[.．、])\s*", re.I)


def _cet_choice_groups(blocks: list[str], expected_count: int) -> list[list[dict[str, str]]]:
    """Recover sequential A-D groups from CET Word exports.

    Several source files lose the first question number in a listening group,
    and occasionally lose one option label.  Word two-column exports also put
    ``A) ... C) ...`` on one line with ``B) ... D) ...`` on the next.  Label
    slots are filled per line (overwriting the same slot) and a group is
    emitted once A-D are all present, so neither interleaved columns nor a
    missing label in one line can shift the question boundaries.
    """
    groups: list[list[dict[str, str]]] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if all(label in current for label in "ABCD"):
            groups.append(
                [{"key": label, "content": current[label]} for label in "ABCD"]
            )
        current = {}

    section_blocks = [clean_text(raw) for raw in blocks]
    has_questions = any(
        re.search(r"^Questions?\b", text, re.I)
        for text in section_blocks
        if text
    )
    started = not has_questions
    for text in section_blocks:
        if not text or re.match(r"^\s*Directions?:", text, re.I):
            continue
        if re.search(r"^Questions?\b", text, re.I):
            if not started:
                started = True
            continue
        if not started:
            continue
        text = re.sub(r"^\s*\d+\s*[\.、．)]\s*", "", text)
        matches = list(CET_CHOICE_MARK_RE.finditer(text))
        if not matches:
            continue
        prefix = clean_text(text[: matches[0].start()])
        if prefix and current:
            expected_label = next(
                (label for label in "ABCD" if label not in current), ""
            )
            if expected_label and expected_label < matches[0].group(1).upper():
                current[expected_label] = prefix
        for index, match in enumerate(matches):
            label = match.group(1).upper()
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = clean_text(text[start:end])
            if label == "A" and "A" in current:
                flush()
            current[label] = content
    flush()
    return groups[:expected_count]

def _cet_section_indexes(blocks: list[str]) -> tuple[int, int, int, int, int]:
    listening = _find_index(blocks, r"Listening\s+Comprehension")
    reading = _find_index(blocks, r"Reading\s+Comprehension")
    # 部分试卷版式把 "Section A" 放在 "Reading Comprehension" 标题行之上
    # （例如 Section A / Reading Comprehension / (40 minutes) 三行），
    # 因此从 reading 标记前几行开始搜索，避免漏掉 Section A。
    search_start = max(0, reading - 3)
    section_a = _find_index(blocks, r"^\s*Section\s*A\s*(?:[（(][^）)]*[）)])?\s*$", search_start)
    section_b = _find_index(blocks, r"^\s*Section\s*B\s*(?:[（(][^）)]*[）)])?\s*$", max(section_a + 1, search_start))
    section_c = _find_index(blocks, r"^\s*Section\s*C\s*(?:[（(][^）)]*[）)])?\s*$", max(section_b + 1, search_start))
    return listening, reading, section_a, section_b, section_c


def _cet_listening_section_positions(blocks: list[str], listening: int, reading: int) -> list[int]:
    """Locate listening Section A/B/C anchors strictly within [listening, reading).

    Some CET papers mangle listening section titles: Section A becomes a bare
    'A' plus a QR-code noise line, and Section C is split as a bare 'C'
    followed by 'Section'. Searching the whole block list from the listening
    marker would leap into the reading word-bank 'Section A' and corrupt the
    listening option groups, so we confine the search to the listening span
    and tolerate split/noisy titles.
    """
    BS = chr(92)
    re_a = r"^" + BS + "s*Section" + BS + "s*A" + BS + "b"
    re_a_alone = r"^" + BS + "s*A" + BS + "s*$"
    re_b = r"^" + BS + "s*Section" + BS + "s*B" + BS + "b"
    re_c = r"^" + BS + "s*Section" + BS + "s*C" + BS + "b"
    re_c_alone = r"^" + BS + "s*C" + BS + "s*$"
    re_sec = r"^" + BS + "s*Section" + BS + "b"
    re_sec_abc = r"^" + BS + "s*Section" + BS + "s*[ABC]" + BS + "b"
    lower_end = max(0, listening + 1)
    upper_end = max(lower_end, reading) if reading > 0 else len(blocks)
    anchors = []
    for index in range(lower_end, upper_end):
        text = clean_text(blocks[index])
        if text and (re.match(re_a, text, re.I) or re.match(re_a_alone, text, re.I)):
            anchors.append(("A", index))
            break
    for index in range(lower_end, upper_end):
        text = clean_text(blocks[index])
        if text and re.match(re_b, text, re.I):
            anchors.append(("B", index))
            break
    for index in range(lower_end, upper_end):
        text = clean_text(blocks[index])
        if not text:
            continue
        if re.match(re_c, text, re.I):
            anchors.append(("C", index))
            break
        if re.match(re_c_alone, text, re.I):
            ahead = index + 1
            while ahead < upper_end:
                ahead_text = clean_text(blocks[ahead])
                if re.match(re_sec, ahead_text, re.I):
                    anchors.append(("C", index))
                    break
                if re.match(re_sec_abc, ahead_text, re.I):
                    break
                if ahead_text:
                    anchors.append(("C", index))
                    break
                ahead += 1
            if anchors and anchors[-1][0] == "C":
                break
    positions = [index for _, index in anchors]
    positions.sort()
    return positions


def _parse_cet_listening(
    blocks: list[str], answers: dict[int, str], exam_units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    listening, reading, _, _, _ = _cet_section_indexes(blocks)
    if listening < 0 or reading <= listening:
        return []
    section_starts = _cet_listening_section_positions(blocks, listening, reading)
    template_units = [unit for unit in exam_units if unit["type"] == "listening"]
    if len(section_starts) != len(template_units):
        return []
    units: list[dict[str, Any]] = []
    for index, template in enumerate(template_units):
        start = section_starts[index]
        end = section_starts[index + 1] if index + 1 < len(section_starts) else reading
        numbers = list(template["numbers"])
        choices = _cet_choice_groups(blocks[start + 1 : end], len(numbers)) if start >= 0 else []
        units.append(
            {
                "unit_type": "listening",
                "subtype": template["subtype"],
                "title": template["title"],
                "sequence": template["seq"],
                "passage": "",
                "shared_data": {},
                "questions": [
                    {
                        "number": number,
                        "stem": "",
                        "options": choices[offset] if offset < len(choices) else [],
                        "answer": answers.get(number, ""),
                        "score": 1.0,
                    }
                    for offset, number in enumerate(numbers)
                ],
            }
        )
    return units


def _parse_cet_word_bank(
    blocks: list[str], answers: dict[int, str], start: int, end: int, template: dict[str, Any]
) -> dict[str, Any]:
    section = [clean_text(text) for text in blocks[start + 1 : end] if clean_text(text)]
    # 词库可能在单个段落（老 PDF/其它格式）或多个连续段落（Word 导出常为
    # "A) word	I) word" 一行两个词）。跨段落聚合，但只接受"纯词库行"：
    # 段落里每个标签对应的 value 都必须是一个短词（不含空格或长度 <= 18），
    # 从而排除 Directions / 正文等含完整句子的段落。
    bank: dict[str, str] = {}
    bank_indices: set[int] = set()
    candidates: list[tuple[int, list[tuple[str, str]]]] = []
    for index, text in enumerate(section):
        matches = list(CET_BANK_MARK_RE.finditer(text))
        if not matches:
            continue
        entries: list[tuple[str, str]] = []
        is_bank_line = True
        for match_index, match in enumerate(matches):
            label = match.group(1).upper()
            if label == "0":
                label = "O"
            content_start = match.end()
            content_end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(text)
            value = clean_text(text[content_start:content_end])
            if not value or len(value) > 18 or " " in value:
                is_bank_line = False
                break
            entries.append((label, value))
        if is_bank_line and entries:
            candidates.append((index, entries))
    # 尝试聚合：累计已见标签，直到覆盖 A-O 全集。
    if candidates:
        for threshold_start in range(len(candidates)):
            agg: dict[str, str] = {}
            idxs: set[int] = set()
            for j in range(threshold_start, len(candidates)):
                ci, entries = candidates[j]
                for label, value in entries:
                    if label in "ABCDEFGHIJKLMNO" and label not in agg:
                        agg[label] = value
                    idxs.add(ci)
                if len(agg) >= 12 and set(agg) >= set("ABCDEFGHIJKLMNO"):
                    break
            if len(agg) >= 12 and set(agg) >= set("ABCDEFGHIJKLMNO"):
                bank = agg
                bank_indices = idxs
                break

    passage_parts = [
        text
        for index, text in enumerate(section)
        if index not in bank_indices
        and not re.search(r"^Directions", text, re.I)
        and not _is_noise(text)
    ]
    options = [{"key": key, "content": bank.get(key, "")} for key in "ABCDEFGHIJKLMNO"]
    return {
        "unit_type": "word_bank",
        "subtype": template["subtype"],
        "title": template["title"],
        "sequence": template["seq"],
        "passage": "\n\n".join(passage_parts),
        "shared_data": {"word_bank": bank},
        "questions": [
            {
                "number": number,
                "stem": "",
                "options": copy.deepcopy(options),
                "answer": answers.get(number, ""),
                "score": 0.5,
            }
            for number in template["numbers"]
        ],
    }


def _parse_cet_paragraph_matching(
    blocks: list[str], answers: dict[int, str], start: int, end: int, template: dict[str, Any]
) -> dict[str, Any]:
    candidates: dict[str, str] = {}
    statements: dict[int, str] = {}
    current_label = ""
    section = blocks[start + 1 : end]
    # Some PDF text layers embed the next paragraph label at the tail of the
    # previous paragraph (``...15.8%. D ) The region's economy...``).  Split
    # such blocks so each label starts its own candidate paragraph.
    inline_label = re.compile(
        r"(?<![A-Za-z0-9\[\]])([A-R0])\s*(?:\)|\uff09)(?=\s*[A-Z])",
    )
    expanded: list[str] = []
    for raw in section:
        text = clean_text(raw)
        parts = inline_label.split(text)
        if len(parts) == 1:
            expanded.append(raw)
            continue
        leading = parts[0]
        if leading.strip():
            expanded.append(leading)
        for i in range(1, len(parts), 2):
            label = parts[i].upper()
            if label == "0":
                label = "O"
            body = parts[i + 1] if i + 1 < len(parts) else ""
            if body.strip():
                expanded.append(f"[{label}] {body.strip()}")
    section = expanded
    referenced_labels = {
        str(answers.get(number, "")).strip().upper()
        for number in range(36, 46)
        if re.fullmatch(r"[A-P]", str(answers.get(number, "")).strip(), re.I)
    }
    for index, raw in enumerate(section):
        text = clean_text(raw)
        # CET Word exports use both ``[A] paragraph`` and ``A) paragraph``
        # forms.  The latter is common in the three-in-one source papers and
        # may also be concatenated with the first word (``B)Today``).
        candidate = re.match(
            r"^\s*(?:\[([A-R0])\]|\(?([A-R0])(?:\)|[)）\.．、]))\s*(.*)$",
            text,
            re.I | re.S,
        )
        statement = QUESTION_NUMBER_RE.match(text)
        if candidate:
            current_label = (candidate.group(1) or candidate.group(2)).upper()
            if current_label == "0":
                current_label = "O"
            candidates[current_label] = clean_text(candidate.group(3))
        elif statement and 36 <= int(statement.group(1)) <= 45:
            current_label = ""
            statements[int(statement.group(1))] = clean_text(statement.group(2))
        elif current_label and text and not re.search(r"^Directions", text, re.I):
            missing_labels = referenced_labels - candidates.keys()
            next_text = next(
                (clean_text(value) for value in section[index + 1 :] if clean_text(value)),
                "",
            )
            next_statement = QUESTION_NUMBER_RE.match(next_text)
            expected_label = chr(ord(current_label) + 1) if current_label < "R" else ""
            # Some CET Word exports drop the final paragraph label while keeping
            # its body as a separate paragraph. Recover it only when the answer
            # key references exactly that next label and statements start next.
            if (
                missing_labels == {expected_label}
                and next_statement
                and 36 <= int(next_statement.group(1)) <= 45
                and len(text) >= 80
            ):
                current_label = expected_label
                candidates[current_label] = text
            else:
                candidates[current_label] = clean_text(candidates[current_label] + " " + text)
        elif (
            text
            and not re.search(r"^(?:Directions|Section|Passage)\b", text, re.I)
            and 45 not in statements
            and all(number in statements for number in range(36, 45))
        ):
            # A few Word exports lose the final question number while keeping
            # its statement as a standalone block immediately before Section C.
            # Only recover it after 36-44 are present, so ordinary paragraph
            # continuations cannot be mistaken for question 45.
            statements[45] = text
    options = [{"key": key, "content": value} for key, value in sorted(candidates.items())]
    return {
        "unit_type": "paragraph_matching",
        "subtype": template["subtype"],
        "title": template["title"],
        "sequence": template["seq"],
        "passage": "\n\n".join(f"[{key}] {value}" for key, value in sorted(candidates.items())),
        "shared_data": {"paragraphs": candidates},
        "questions": [
            {
                "number": number,
                "stem": statements.get(number, ""),
                "options": copy.deepcopy(options),
                "answer": answers.get(number, ""),
                "score": 1.0,
            }
            for number in template["numbers"]
        ],
    }


def _parse_cet_reading_passage(
    blocks: list[str], answers: dict[int, str], start: int, end: int, template: dict[str, Any]
) -> dict[str, Any]:
    expected = list(template["numbers"])
    passage_parts: list[str] = []
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    question_started = False

    def add_choices(question: dict[str, Any], text: str) -> None:
        matches = list(CET_CHOICE_MARK_RE.finditer(text))
        existing = {option["key"]: option for option in question["options"]}
        for index, match in enumerate(matches):
            label = match.group(1).upper()
            content_start = match.end()
            content_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = clean_text(text[content_start:content_end])
            if content:
                existing[label] = {"key": label, "content": content}
        question["options"] = [existing[key] for key in "ABCD" if key in existing]

    for raw in blocks[start + 1 : end]:
        text = clean_text(raw)
        if not text or re.search(r"^Questions?\b", text, re.I):
            continue
        numbered = QUESTION_NUMBER_RE.match(text)
        if numbered and int(numbered.group(1)) in expected:
            if current:
                questions.append(current)
            body = clean_text(numbered.group(2))
            first_choice = CET_CHOICE_MARK_RE.search(body)
            current = {
                "number": int(numbered.group(1)),
                "stem": clean_text(body[: first_choice.start()]) if first_choice else body,
                "options": [],
            }
            if first_choice:
                add_choices(current, body[first_choice.start() :])
            question_started = True
            continue
        if current and len(current["options"]) == 4 and not CET_CHOICE_MARK_RE.search(text):
            questions.append(current)
            used = {question["number"] for question in questions}
            next_number = next((number for number in expected if number not in used), None)
            current = (
                {"number": next_number, "stem": text, "options": []}
                if next_number is not None
                else None
            )
            continue
        if current:
            add_choices(current, text)
        elif not question_started and not re.search(r"^Directions", text, re.I):
            passage_parts.append(text)
    if current:
        questions.append(current)
    by_number = {question["number"]: question for question in questions}
    return {
        "unit_type": "reading",
        "subtype": template["subtype"],
        "title": template["title"],
        "sequence": template["seq"],
        "passage": "\n\n".join(passage_parts),
        "shared_data": {},
        "questions": [
            {
                **by_number.get(number, {"number": number, "stem": "", "options": []}),
                "answer": answers.get(number, ""),
                "score": 2.0,
            }
            for number in expected
        ],
    }


def _parse_cet_units(
    blocks: list[str], answers: dict[int, str], exam_units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    listening, reading, section_a, section_b, section_c = _cet_section_indexes(blocks)
    units = _parse_cet_listening(blocks, answers, exam_units)
    if reading < 0 or min(section_a, section_b, section_c) < 0:
        return units
    template_by_type = {unit["type"]: unit for unit in exam_units if unit["type"] != "listening"}
    units.append(_parse_cet_word_bank(blocks, answers, section_a, section_b, template_by_type["word_bank"]))
    units.append(_parse_cet_paragraph_matching(blocks, answers, section_b, section_c, template_by_type["paragraph_matching"]))
    passage_starts = [
        index
        for index in range(section_c + 1, len(blocks))
        if re.match(r"^\s*Passage\s+(?:One|Two|1|2)\s*$", blocks[index], re.I)
    ]
    translation = _find_index(
        blocks,
        r"^\s*(?:Part\s*IV\s*)?Translation(?:\s*\(\d+\s*minutes?\))?\s*$",
        section_c + 1,
    )
    reading_templates = [unit for unit in exam_units if unit["type"] == "reading"]
    for index, template in enumerate(reading_templates):
        start = passage_starts[index] if index < len(passage_starts) else section_c
        end = passage_starts[index + 1] if index + 1 < len(passage_starts) else (translation if translation > start else len(blocks))
        units.append(_parse_cet_reading_passage(blocks, answers, start, end, template))
    return units


def _part_b_subtype(direction: str) -> str:
    low = direction.lower()
    if "true or false" in low or ("choose t" in low and "choose" in low):
        return "true_false"
    if "wrong order" in low or "reorganize" in low:
        return "paragraph_reordering"
    if "paragraphs from the list" in low:
        return "paragraph_insertion"
    if "subheading" in low:
        return "heading_matching"
    if "people" in low or "person" in low or "comments" in low or "name" in low:
        return "opinion_matching"
    return "sentence_insertion"


def _part_b_candidate_count(direction: str, subtype: str) -> int:
    range_match = re.search(r"list\s+A\s*[-–—]\s*([GH])", direction, re.I)
    if range_match:
        return ord(range_match.group(1).upper()) - ord("A") + 1
    return 8 if subtype == "paragraph_reordering" else 7


def _reading_part_b_bounds(blocks: list[str]) -> tuple[int, int]:
    reading = _find_index(
        blocks,
        r"Section\s*(?:II|Ⅱ|2)\s*Reading\s+Comprehension",
    )
    if reading < 0:
        reading = _find_index(blocks, r"Reading\s+Comprehension")
    part_b = _find_index(blocks, r"^\s*Part\s*B\s*$", max(reading, 0))
    section_three = _find_index(
        blocks,
        r"^\s*Section\s*(?:III|Ⅲ|3)\s*(?:Translation)?\s*$",
        max(part_b + 1, 0),
    )
    if section_three < 0:
        section_three = _find_index(
            blocks,
            r"^\s*Section\s*(?:III|Ⅲ|3)\s*Translation\s*$",
            max(part_b + 1, 0),
        )
    return part_b, section_three


def _parse_true_false_part_b(
    blocks: list[str],
    answers: dict[int, str],
    part_b: int,
    section_end: int,
) -> dict[str, Any]:
    section = blocks[part_b + 1 : section_end]
    direction_parts: list[str] = []
    content_start = 0
    for index, text in enumerate(section):
        if index == 0 and re.match(r"^\s*Directions:\s*$", text, re.I):
            direction_parts.append(text)
            content_start = index + 1
            continue
        if direction_parts and re.search(
            r"true\s+or\s+false|choose\s+T|ANSWER\s+SHEET|questions?",
            text,
            re.I,
        ):
            direction_parts.append(text)
            content_start = index + 1
            continue
        break

    content = [
        text
        for text in section[content_start:]
        if not _is_noise(text)
    ]
    statements = content[-5:] if len(content) >= 5 else []
    passage = content[:-5] if len(content) >= 5 else content
    direction = clean_text(" ".join(direction_parts))
    options = [
        {"key": "T", "content": "True"},
        {"key": "F", "content": "False"},
    ]
    return {
        "unit_type": "part_b",
        "subtype": "true_false",
        "title": "阅读 Part B（判断题）",
        "sequence": 6,
        "passage": "\n\n".join(passage),
        "shared_data": {
            "directions": direction,
            "candidates": {},
        },
        "questions": [
            {
                "number": number,
                "stem": statements[index] if index < len(statements) else "",
                "options": [dict(option) for option in options],
                "answer": answers.get(number, ""),
                "score": 2.0,
            }
            for index, number in enumerate(range(41, 46))
        ],
    }


def _parse_part_b(blocks: list[str], answers: dict[int, str]) -> dict[str, Any]:
    part_b, section_end = _reading_part_b_bounds(blocks)
    if part_b >= 0 and section_end > part_b:
        context = " ".join(blocks[part_b : min(section_end, part_b + 4)])
        if _part_b_subtype(context) == "true_false":
            return _parse_true_false_part_b(
                blocks,
                answers,
                part_b,
                section_end,
            )

    direction_index = next(
        (
            index
            for index, text in enumerate(blocks)
            if re.search(r"(?:for\s+)?questions?.*?41.*?45", text, re.I)
            and (
                re.search(r"list\s+A", text, re.I)
                or re.search(r"list\s+A", " ".join(blocks[index : index + 2]), re.I)
                or "numbered" in text.lower()
            )
        ),
        -1,
    )
    if direction_index < 0:
        direction_index = next(
            (
                index
                for index, text in enumerate(blocks)
                if re.search(r"numbered\s+(?:name|person|paragraph)", text, re.I)
                and re.search(r"list\s+A", text, re.I)
            ),
            -1,
        )
    reading_part_b = direction_index
    part_c_candidates = [
        index
        for index in range(max(reading_part_b, 0) + 1, len(blocks))
        if re.match(r"^\s*Part\s*C(?:\s+Directions:)?\s*$", blocks[index], re.I)
        or re.search(r"→\s*Part\s*C\s*$", blocks[index], re.I)
    ]
    part_c = part_c_candidates[0] if part_c_candidates else -1
    section = (
        blocks[reading_part_b:part_c]
        if reading_part_b >= 0 and part_c > reading_part_b
        else []
    )
    direction_parts: list[str] = []
    for text in section[:3]:
        if not direction_parts or re.search(
            r"list\s+A|numbered|extra choices|fit in|coherent text|ANSWER SHEET",
            text,
            re.I,
        ):
            direction_parts.append(text)
        else:
            break
    direction = clean_text(" ".join(direction_parts))
    candidate_map: dict[str, str] = {}
    material: list[str] = []
    for text in section[len(direction_parts) :]:
        if _is_noise(text) or text == direction:
            continue
        match = re.match(r"^\s*\[([A-O])\]\s*(.*)$", text, re.I | re.S)
        if match:
            candidate_map[match.group(1).upper()] = clean_text(match.group(2))
        else:
            material.append(text)

    subtype = _part_b_subtype(direction)
    if not candidate_map:
        usable = [
            text
            for text in material
            if not PAGE_FOOTER_RE.search(text)
            and not re.search(r"(?:41\.){2}|→|ANSWER SHEET", text, re.I)
        ]
        candidate_count = _part_b_candidate_count(direction, subtype)
        if len(usable) >= candidate_count:
            inferred = usable[-candidate_count:]
            candidate_map = {
                chr(ord("A") + index): text for index, text in enumerate(inferred)
            }
            material = material[: len(material) - candidate_count]

    questions = []
    for number in range(41, 46):
        questions.append(
            {
                "number": number,
                "stem": f"位置 {number}",
                "options": [
                    {"key": key, "content": value}
                    for key, value in sorted(candidate_map.items())
                ],
                "answer": answers.get(number, ""),
                "score": 2.0,
            }
        )
    return {
        "unit_type": "part_b",
        "subtype": subtype,
        "title": "阅读 Part B",
        "sequence": 6,
        "passage": "\n\n".join(material),
        "shared_data": {
            "directions": direction,
            "candidates": candidate_map,
        },
        "questions": questions,
    }


def _has_objective_part_b(blocks: list[str]) -> bool:
    for index, text in enumerate(blocks):
        if not re.match(r"^\s*Part\s*B\s*$", text, re.I):
            continue
        context = " ".join(blocks[index : index + 5]).lower()
        if re.search(r"translate\s+the\s+underlined|translation", context):
            return False
        if re.search(
            r"questions?.*?41.*?45|list\s+a|extra\s+choices|wrong\s+order|"
            r"reorganize|subheading|numbered\s+(?:name|person|paragraph)|"
            r"true\s+or\s+false|choose\s+t\s+if",
            context,
            re.I,
        ):
            return True
    return any(
        re.search(r"(?:for\s+)?questions?.*?41.*?45", text, re.I)
        and (
            re.search(r"list\s+A", text, re.I)
            or "numbered" in text.lower()
        )
        for text in blocks
    )


def _detect_subject(source_name: str, blocks: list[str]) -> str:
    header = " ".join([source_name, *blocks[:12]])
    if (
        re.search(r"英语\s*[\(（]?\s*二\s*[\)）]?", header)
        or re.search(r"科目代码\s*[：:]?\s*204\b", header)
    ):
        return "英语二"
    return "英语一"


def objective_question_numbers(draft: dict[str, Any]) -> list[int]:
    return sorted(
        {
            int(question["number"])
            for unit in draft.get("units", [])
            for question in unit.get("questions", [])
        }
    )


def apply_answers_to_draft(draft: dict[str, Any]) -> None:
    answers = draft.setdefault("answers", {})
    for unit in draft.get("units", []):
        for question in unit.get("questions", []):
            number = str(question.get("number", ""))
            answer = str(answers.get(number, "") or "").strip().upper()
            answers[number] = answer
            question["answer"] = answer


def validate_draft(draft: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    apply_answers_to_draft(draft)
    answers = draft["answers"]
    from .exam_templates import template_units
    expected_numbers = objective_question_numbers(draft)
    missing_answers = [
        number for number in expected_numbers if not answers.get(str(number))
    ]
    if missing_answers:
        warnings.append(f"缺少标准答案：{missing_answers}")
    if (
        draft.get("answer_status", {}).get("status") == "parsed"
        and not draft.get("answers_confirmed", False)
    ):
        warnings.append("自动识别的标准答案尚未人工确认")
    units = draft["units"]
    exam_type = draft.get("exam_type", "postgraduate_english1")
    expected_template = template_units(exam_type)
    if expected_template:
        template_by_type: dict[str, dict[str, Any]] = {}
        for t in expected_template:
            key = t["type"]
            template_by_type[key] = t
        actual_by_type: dict[str, list[dict[str, Any]]] = {}
        for u in units:
            actual_by_type.setdefault(u["unit_type"], []).append(u)
        for template in expected_template:
            matches = [
                u
                for u in units
                if u["unit_type"] == template["type"]
                and (
                    not template.get("subtype")
                    or u.get("subtype") == template["subtype"]
                )
            ]
            if not matches:
                if template["type"] == "part_b":
                    continue
                if (
                    template["type"] == "listening"
                    and int(draft.get("set_number") or 1) == 3
                ):
                    continue
                warnings.append(f"缺少{template['title']}")
                continue
            for unit in matches:
                expected_q_count = len(list(template["numbers"]))
                if len(unit["questions"]) != expected_q_count:
                    warnings.append(f"{unit['title']} 题目数 {len(unit['questions'])} 应为 {expected_q_count} 题")
                if unit["unit_type"] not in ("listening", "word_bank", "paragraph_matching"):
                    for question in unit["questions"]:
                        if len(question.get("options", [])) != 4:
                            warnings.append(f"第{question['number']}题选项数量不是4")
    else:
        cloze = next((unit for unit in units if unit["unit_type"] == "cloze"), None)
        if not cloze or len(cloze["questions"]) != 20:
            warnings.append("完型填空未识别为20题")
    part_b_units = [unit for unit in units if unit["unit_type"] == "part_b"]
    if part_b_units:
        part_b = part_b_units[0]
        if len(part_b["questions"]) != 5:
            warnings.append("Part B 未识别为5题")
        elif (
            part_b.get("subtype") != "true_false"
            and not 7 <= len(part_b.get("shared_data", {}).get("candidates", {})) <= 8
        ):
            warnings.append(
                "Part B 候选项数量异常："
                f"{len(part_b.get('shared_data', {}).get('candidates', {}))}"
            )
        elif (
            part_b.get("subtype") != "true_false"
            and any(
                question.get("answer")
                and question["answer"]
                not in part_b.get("shared_data", {}).get("candidates", {})
                for question in part_b["questions"]
            )
        ):
            warnings.append("Part B 标准答案未能对应候选项")
    for unit in units:
        for question in unit["questions"]:
            if not question.get("answer"):
                warnings.append(f"第{question['number']}题没有答案")
            elif question["answer"] not in {
                option.get("key")
                for option in question.get("options", [])
            }:
                warnings.append(f"第{question['number']}题答案未对应现有选项")
    return list(dict.fromkeys(warnings))


def _parse_template_known(
    blocks: list[str],
    answer_key: dict[int, str],
    unit_type: str,
    unit_subtype: str,
    unit_title: str,
    unit_seq: int,
    unit_numbers: list[int],
) -> dict[str, Any]:
    """Use existing cloze/reading/part_b parsers for template-guided units."""
    if unit_type == "cloze":
        unit = _parse_cloze(blocks, answer_key)
        unit["subtype"] = unit_subtype
        unit["title"] = unit_title
        unit["sequence"] = unit_seq
        return unit
    if unit_type == "reading":
        readings = _parse_reading(blocks, answer_key)
        idx = unit_seq - 2
        if 0 <= idx < len(readings):
            unit = readings[idx]
            unit["title"] = unit_title
            unit["sequence"] = unit_seq
            return unit
        return _create_template_shell(unit_type, unit_subtype, unit_title, unit_seq, unit_numbers, answer_key)
    if unit_type == "part_b":
        if _has_objective_part_b(blocks):
            unit = _parse_part_b(blocks, answer_key)
            unit["title"] = unit_title
            unit["sequence"] = unit_seq
            return unit
        return _create_template_shell(unit_type, unit_subtype, unit_title, unit_seq, unit_numbers, answer_key)
    return _create_template_shell(unit_type, unit_subtype, unit_title, unit_seq, unit_numbers, answer_key)


def _create_template_shell(
    unit_type: str,
    unit_subtype: str,
    unit_title: str,
    unit_seq: int,
    unit_numbers: list[int],
    answer_key: dict[int, str],
) -> dict[str, Any]:
    """Create a unit shell for exam types that don't have specialized parsers (listening, word_bank, etc)."""
    options: list[dict[str, str]] = [
        {"key": "A", "content": ""},
        {"key": "B", "content": ""},
        {"key": "C", "content": ""},
        {"key": "D", "content": ""},
    ]
    questions = [
        {
            "number": num,
            "stem": "",
            "answer": answer_key.get(num, ""),
            "score": 1.0,
            "question_type": "single_choice",
            "options": options,
        }
        for num in unit_numbers
    ]
    return {
        "unit_type": unit_type,
        "subtype": unit_subtype,
        "title": unit_title,
        "sequence": unit_seq,
        "passage": "",
        "shared_data": {},
        "questions": questions,
    }


def parse_exam(
    path: Path,
    answer_path: Path | None = None,
    *,
    source_name: str | None = None,
    answer_name: str | None = None,
    exam_type: str = "",
    audio_paths: list[Path] | None = None,
    reviewed_answers: dict[int, str] | None = None,
) -> dict[str, Any]:
    blocks, detected_format, converted = extract_blocks(path)
    try:
        logical_source_name = source_name or path.name
        year_match = re.search(r"(20\d{2})", logical_source_name)
        if not year_match:
            year_match = re.search(r"(20\d{2})", " ".join(blocks[:10]))
        year = int(year_match.group(1)) if year_match else None
        subject = _detect_subject(logical_source_name, blocks)
        from .exam_templates import detect_exam_type, template_units, template_subject_default, template_answer_numbers
        detected_exam_type = exam_type or detect_exam_type(" ".join(blocks[:15]), logical_source_name)
        exam_month = 0
        set_number = 1
        month_match = re.search(r"(\d{1,2})\s*月", logical_source_name)
        if not month_match:
            month_match = re.search(r"(?:20\d{2}|^|[._-])\s*(\d{1,2})\s*(?:月|四级|六级)", logical_source_name)
        if month_match:
            exam_month = int(month_match.group(1))
        set_match = re.search(r"第\s*([一二三1-3])\s*套", logical_source_name)
        if set_match:
            chinese = {"一": 1, "二": 2, "三": 3}
            set_number = chinese.get(set_match.group(1)) or int(set_match.group(1))
        if detected_exam_type and detected_exam_type != "postgraduate_english1":
            subject = template_subject_default(detected_exam_type) or subject
        exam_units = template_units(detected_exam_type)
        if not exam_units:
            exam_units = template_units("postgraduate_english1")
        answer_key = extract_answer_key(blocks)
        answer_sources = {
            str(number): "试卷 Word 内置答案" for number in answer_key
        }
        answer_status = {
            "status": "parsed" if answer_key else "missing",
            "message": (
                f"已从试卷 Word 识别 {len(answer_key)} 道答案"
                if answer_key
                else "试卷 Word 未检测到标准答案"
            ),
        }
        answer_source = "试卷 Word 内置答案" if answer_key else "未提供"
        attachment_used = False
        companion = answer_path
        if companion:
            attachment_answers, attachment_status = extract_answer_attachment(
                companion, exam_type=detected_exam_type
            )
            answer_key.update(attachment_answers)
            for number in attachment_answers:
                answer_sources[str(number)] = answer_name or companion.name
            answer_status = attachment_status
            answer_source = answer_name or companion.name
            attachment_used = True
        # A Word export may contain a sparse answer column (for example only
        # every fifth answer).  Do not treat that as proof that a companion
        # answer file is unnecessary; merge a reliable same-folder PDF for
        # any still-missing questions when one is available.
        if not companion:
            legacy_companion = find_companion_answer_pdf(path, year)
            if legacy_companion:
                attachment_answers, attachment_status = extract_answer_attachment(
                    legacy_companion, exam_type=detected_exam_type
                )
                if attachment_answers:
                    for number, answer in attachment_answers.items():
                        answer_key[number] = answer
                        answer_sources[str(number)] = legacy_companion.name
                    answer_status = attachment_status
                    answer_source = legacy_companion.name
                    attachment_used = True
        if reviewed_answers:
            reviewed_source = answer_name or "人工核验答案"
            for number, answer in reviewed_answers.items():
                answer_key[int(number)] = str(answer).strip().upper()
                answer_sources[str(number)] = reviewed_source
            answer_status = {
                "status": "confirmed",
                "message": f"已确认 {len(reviewed_answers)} 道答案",
            }
            answer_source = reviewed_source
        units: list[dict[str, Any]] = []
        if detected_exam_type in ("postgraduate_english1", "postgraduate_english2"):
            units = [_parse_cloze(blocks, answer_key)]
            units.extend(_parse_reading(blocks, answer_key))
            if _has_objective_part_b(blocks):
                units.append(_parse_part_b(blocks, answer_key))
        elif detected_exam_type in ("cet4", "cet6"):
            units = _parse_cet_units(blocks, answer_key, exam_units)
        else:
            for template_unit in exam_units:
                unit_type = template_unit["type"]
                unit_subtype = template_unit["subtype"]
                unit_title = template_unit["title"]
                unit_seq = template_unit["seq"]
                unit_numbers = list(template_unit["numbers"])
                if unit_type in ("cloze", "reading", "part_b"):
                    unit = _parse_template_known(blocks, answer_key, unit_type, unit_subtype, unit_title, unit_seq, unit_numbers)
                else:
                    unit = _create_template_shell(unit_type, unit_subtype, unit_title, unit_seq, unit_numbers, answer_key)
                units.append(unit)
        expected_numbers = {
            question["number"]
            for unit in units
            for question in unit.get("questions", [])
        }
        answer_key = {
            number: answer
            for number, answer in answer_key.items()
            if number in expected_numbers
        }
        answer_sources = {
            number: source
            for number, source in answer_sources.items()
            if int(number) in expected_numbers
        }
        for unit in units:
            for question in unit.get("questions", []):
                question["answer"] = answer_key.get(question["number"], "")
        for unit in units:
            if unit["unit_type"] in ("cloze", "word_bank"):
                unit["passage"] = _ensure_numbered_blanks(
                    unit.get("passage", ""),
                    range(
                        min(q["number"] for q in unit.get("questions", [{"number":1}])),
                        max(q["number"] for q in unit.get("questions", [{"number":20}])) + 1,
                    ),
                )
                unit["passage"] = _remove_duplicate_cloze_number_noise(
                    unit["passage"]
                )
                unit["passage"] = repair_inline_blank_paragraph_breaks(
                    unit["passage"]
                )
            elif unit["unit_type"] == "part_b":
                unit["passage"] = _ensure_numbered_blanks(
                    unit.get("passage", ""),
                    range(
                        min(q["number"] for q in unit.get("questions", [{"number":41}])),
                        max(q["number"] for q in unit.get("questions", [{"number":45}])) + 1,
                    ),
                )
                unit["passage"] = repair_inline_blank_paragraph_breaks(
                    unit["passage"]
                )
        draft = {
            "year": year,
            "subject": subject,
            "exam_type": detected_exam_type,
            "exam_month": exam_month,
            "set_number": set_number,
            "title": (
                f"{year}年{subject}真题"
                if year
                else Path(logical_source_name).stem
            ),
            "detected_format": detected_format,
            "source_file": logical_source_name,
            "answer_source": answer_source,
            "answer_status": answer_status,
            "answers_confirmed": not attachment_used,
            "answer_sources": answer_sources,
            "answers": {str(key): value for key, value in answer_key.items()},
            "units": units,
        }
        apply_answers_to_draft(draft)
        draft["warnings"] = validate_draft(draft)
        return draft
    finally:
        if converted:
            shutil.rmtree(converted.parent, ignore_errors=True)


def discover_exam_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".doc", ".docx"}
            and re.search(r"20\d{2}", path.name)
        ),
        key=lambda path: int(re.search(r"20\d{2}", path.name).group()),
    )


def import_exam_folder(
    connection: Any,
    folder: Path,
    *,
    publish_valid: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in discover_exam_files(folder):
        draft = parse_exam(path)
        warnings = validate_draft(draft)
        existing = connection.execute(
            "SELECT id FROM import_jobs WHERE filename = ? ORDER BY id DESC LIMIT 1",
            (path.name,),
        ).fetchone()
        if existing:
            job_id = existing["id"]
            connection.execute(
                """
                UPDATE import_jobs
                SET detected_year = ?, detected_format = ?, status = 'draft',
                    draft_data = ?, warnings = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    draft.get("year"),
                    draft.get("detected_format"),
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                    job_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO import_jobs
                    (filename, stored_path, detected_year, detected_format,
                     status, draft_data, warnings)
                VALUES (?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    path.name,
                    str(path),
                    draft.get("year"),
                    draft.get("detected_format"),
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )
            job_id = cursor.lastrowid

        paper_id = None
        status = "draft"
        if publish_valid and not warnings:
            paper_id = publish_draft(connection, draft, path.name)
            status = "published"
            connection.execute(
                """
                UPDATE import_jobs
                SET status = 'published', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
        results.append(
            {
                "year": draft.get("year"),
                "filename": path.name,
                "job_id": job_id,
                "paper_id": paper_id,
                "status": status,
                "warnings": warnings,
                "answer_source": draft.get("answer_source"),
            }
        )
    connection.commit()
    return results


def publish_draft(
    connection: Any,
    draft: dict[str, Any],
    source_file: str,
    *,
    profile_id: int = 1,
    audio_paths: list[Path] | None = None,
    audio_names: list[str] | None = None,
    commit: bool = True,
) -> int:
    year = draft.get("year")
    if not year:
        raise ValueError("试卷年份不能为空")
    subject = draft.get("subject", "英语一")
    external_key = str(
        draft.get("paper_key")
        or f"document:{year}:{subject}:{draft['title']}"
    ).strip()
    paper = connection.execute(
        """
        SELECT id FROM papers
        WHERE profile_id = ? AND deleted_at IS NULL
          AND (external_key = ? OR (external_key IS NULL AND year = ? AND title = ?))
        ORDER BY id LIMIT 1
        """,
        (profile_id, external_key, year, draft["title"]),
    ).fetchone()
    if paper:
        paper_id = int(paper["id"])
        connection.execute(
            """
            UPDATE papers
            SET year = ?, subject = ?, title = ?, source_file = ?,
                status = 'published', external_key = ?,
                exam_type = ?, exam_month = ?, set_number = ?, session_group_key = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (year, subject, draft["title"], source_file, external_key,
             draft.get("exam_type", ""), draft.get("exam_month", 0),
             draft.get("set_number", 1), draft.get("session_group_key", ""),
             paper_id),
        )
    else:
        cursor = connection.execute(
            """
            INSERT INTO papers
                (profile_id, year, subject, title, source_file, status, external_key,
                 exam_type, exam_month, set_number, session_group_key)
            VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?)
            """,
            (profile_id, year, subject, draft["title"], source_file, external_key,
             draft.get("exam_type", ""), draft.get("exam_month", 0),
             draft.get("set_number", 1), draft.get("session_group_key", "")),
        )
        paper_id = int(cursor.lastrowid)
    connection.execute("DELETE FROM units WHERE paper_id = ?", (paper_id,))
    for unit in draft["units"]:
        unit_cursor = connection.execute(
            """
            INSERT INTO units
                (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                unit["unit_type"],
                unit.get("subtype"),
                unit["title"],
                unit["sequence"],
                unit.get("passage", ""),
                json.dumps(unit.get("shared_data", {}), ensure_ascii=False),
            ),
        )
        unit_id = unit_cursor.lastrowid
        for sequence, question in enumerate(unit["questions"], 1):
            question_cursor = connection.execute(
                """
                INSERT INTO questions
                    (unit_id, number, stem, question_type, answer, score, sequence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unit_id,
                    question["number"],
                    question.get("stem", ""),
                    "single_choice",
                    question["answer"],
                    question["score"],
                    sequence,
                    json.dumps(question.get("metadata", {}), ensure_ascii=False),
                ),
            )
            question_id = question_cursor.lastrowid
            for option_sequence, option in enumerate(question.get("options", []), 1):
                connection.execute(
                    """
                    INSERT INTO options
                        (question_id, stable_key, original_label, content, sequence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        question_id,
                        option["key"],
                        option["key"],
                        option["content"],
                        option_sequence,
                    ),
                )
    if audio_paths:
        from .listening import attach_listening_assets

        attach_listening_assets(
            connection,
            paper_id,
            audio_paths,
            audio_names,
        )
    if commit:
        connection.commit()
    return paper_id
