from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import QUESTION_BANK_DIR


MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 300 * 1024 * 1024
MAX_FILES = 1000
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_TEXT_LENGTH = 100_000
EXTERNAL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
ALLOWED_ROOTS = {"manifest.json", "papers", "answers", "labels", "assets", "LICENSE.txt", "README.md"}
ALLOWED_ASSET_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/ogg",
}
ALLOWED_ASSET_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
}


class EsqValidationError(ValueError):
    def __init__(self, details: list[dict[str, str]]) -> None:
        self.details = details
        super().__init__("ESQ 题库包校验失败")


def _error(details: list[dict[str, str]], path: str, reason: str) -> None:
    details.append({"path": path, "reason": reason})


def _key(value: Any, path: str, details: list[dict[str, str]]) -> str:
    if not isinstance(value, str) or not EXTERNAL_KEY_RE.fullmatch(value):
        _error(details, path, "必须是 3-200 位安全外部标识")
        return ""
    return value


def _text(value: Any, path: str, details: list[dict[str, str]], *, required: bool = True) -> str:
    if not isinstance(value, str):
        _error(details, path, "必须是字符串")
        return ""
    if required and not value.strip():
        _error(details, path, "不能为空")
    if len(value) > MAX_TEXT_LENGTH:
        _error(details, path, f"长度不能超过 {MAX_TEXT_LENGTH}")
    return value


def _safe_member(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"非法压缩包路径：{name}")
    normalized = str(path)
    root = normalized.split("/", 1)[0]
    if root not in ALLOWED_ROOTS:
        raise ValueError(f"压缩包包含不允许的顶层目录：{root}")
    if normalized.lower().endswith((".exe", ".dll", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".html", ".htm")):
        raise ValueError(f"压缩包包含不允许的文件：{name}")
    return normalized


def _read_member(archive: zipfile.ZipFile, name: str, *, limit: int = MAX_JSON_BYTES) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f"文件过大：{name}")
    with archive.open(info, "r") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"文件过大：{name}")
    return data


def _validate_asset_signature(media_type: str, data: bytes) -> bool:
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if media_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "audio/mpeg":
        return data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        )
    if media_type == "audio/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if media_type == "audio/wav":
        return data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if media_type == "audio/ogg":
        return data.startswith(b"OggS")
    return False


def _read_json(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(_read_member(archive, name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"JSON 文件无效：{name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根节点必须是对象：{name}")
    return payload


def _normalize_blocks(raw: Any, path: str, details: list[dict[str, str]]) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("paragraphs"), list):
        raw = [
            {
                "blockKey": item.get("paragraphKey", f"p{index}"),
                "type": "paragraph",
                "text": item.get("text", ""),
            }
            for index, item in enumerate(raw["paragraphs"], 1)
            if isinstance(item, dict)
        ]
    if not isinstance(raw, list):
        _error(details, path, "必须是 blocks 数组")
        return []
    blocks: list[dict[str, Any]] = []
    allowed = {"paragraph", "quote", "image", "table", "audio", "separator"}
    for index, item in enumerate(raw):
        current_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            _error(details, current_path, "内容块必须是对象")
            continue
        block_type = item.get("type")
        if block_type not in allowed:
            _error(details, f"{current_path}.type", "不支持的内容块类型")
            continue
        block = dict(item)
        block["blockKey"] = _key(item.get("blockKey"), f"{current_path}.blockKey", details)
        if block_type in {"paragraph", "quote"}:
            block["text"] = _text(item.get("text", ""), f"{current_path}.text", details)
        elif block_type == "image":
            block["assetId"] = _key(item.get("assetId"), f"{current_path}.assetId", details)
            block["alt"] = _text(item.get("alt", ""), f"{current_path}.alt", details)
        elif block_type == "audio":
            block["assetId"] = _key(item.get("assetId"), f"{current_path}.assetId", details)
            if "transcript" in item:
                block["transcript"] = _text(item.get("transcript", ""), f"{current_path}.transcript", details, required=False)
        elif block_type == "table":
            rows = item.get("rows")
            if not isinstance(rows, list) or not rows:
                _error(details, f"{current_path}.rows", "表格至少需要一行")
            else:
                normalized_rows: list[list[str]] = []
                for row_index, row in enumerate(rows):
                    if not isinstance(row, list) or not row:
                        _error(details, f"{current_path}.rows[{row_index}]", "表格行必须是非空数组")
                        continue
                    normalized_rows.append(
                        [
                            _text(cell, f"{current_path}.rows[{row_index}][{cell_index}]", details)
                            for cell_index, cell in enumerate(row)
                        ]
                    )
                block["rows"] = normalized_rows
        blocks.append(block)
    return blocks


def flatten_blocks(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type in {"paragraph", "quote"}:
            parts.append(str(block.get("text", "")))
        elif block_type == "table":
            rows = block.get("rows", [])
            parts.append("\n".join("\t".join(str(cell) for cell in row) for row in rows))
        elif block_type == "image":
            parts.append(f"[图片：{block.get('alt', '题库图片')}]")
        elif block_type == "audio":
            transcript = block.get("transcript", "")
            parts.append(f"[音频]{(': ' + transcript) if transcript else ''}")
        elif block_type == "separator":
            parts.append("---")
    return "\n\n".join(part for part in parts if part).strip()


def _db_passage(blocks: list[dict[str, Any]]) -> str:
    text = flatten_blocks(blocks)
    return re.sub(r"\{\{blank:(\d+)\}\}", r"\1 ______", text)


def _question_hash(
    unit: dict[str, Any],
    question: dict[str, Any],
    answer: dict[str, Any],
) -> str:
    canonical = {
        "passage": unit.get("passage", {}),
        "question": {
            key: question.get(key)
            for key in ("questionKey", "number", "type", "stem", "stemBlocks", "options", "score", "metadata")
        },
        "answer": answer,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_asset_index(
    archive: zipfile.ZipFile,
    raw: dict[str, Any],
    details: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    assets = raw.get("assets", [])
    if not isinstance(assets, list):
        _error(details, "assets/index.json.assets", "必须是数组")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(assets):
        path = f"assets/index.json.assets[{index}]"
        if not isinstance(item, dict):
            _error(details, path, "资源记录必须是对象")
            continue
        asset_id = _key(item.get("assetId"), f"{path}.assetId", details)
        asset_path = item.get("path")
        if not isinstance(asset_path, str):
            _error(details, f"{path}.path", "必须是字符串")
            continue
        try:
            asset_path = _safe_member(asset_path)
        except ValueError as error:
            _error(details, f"{path}.path", str(error))
            continue
        if Path(asset_path).suffix.lower() not in ALLOWED_ASSET_EXTENSIONS:
            _error(details, f"{path}.path", "资源扩展名不受支持")
        media_type = item.get("mediaType")
        if media_type not in ALLOWED_ASSET_TYPES:
            _error(details, f"{path}.mediaType", "不支持的媒体类型")
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            _error(details, f"{path}.sha256", "必须是 64 位 SHA-256")
        if asset_id in result:
            _error(details, f"{path}.assetId", "资源 ID 重复")
            continue
        try:
            data = _read_member(archive, asset_path, limit=50 * 1024 * 1024)
        except (KeyError, ValueError) as error:
            _error(details, f"{path}.path", str(error))
            continue
        if sha256 and hashlib.sha256(data).hexdigest().lower() != str(sha256).lower():
            _error(details, f"{path}.sha256", "文件校验和不匹配")
        if media_type in ALLOWED_ASSET_TYPES and not _validate_asset_signature(media_type, data):
            _error(details, f"{path}.mediaType", "文件头与声明的媒体类型不匹配")
        result[asset_id] = {
            **item,
            "assetId": asset_id,
            "path": asset_path,
            "mediaType": media_type,
            "bytes": len(data),
        }
    return result


def _validate_paper(
    paper: dict[str, Any],
    reference: dict[str, Any],
    answers: dict[str, Any],
    labels: dict[str, Any] | None,
    assets: dict[str, dict[str, Any]],
    details: list[dict[str, str]],
) -> dict[str, Any]:
    paper_key = _key(paper.get("paperKey"), "paper.paperKey", details)
    if paper_key != reference.get("paperKey"):
        _error(details, "paper.paperKey", "必须与 manifest.papers[].paperKey 一致")
    year = paper.get("year")
    if not isinstance(year, int) or not 1900 <= year <= 2200:
        _error(details, "paper.year", "必须是 1900-2200 的整数")
    units = paper.get("units")
    if not isinstance(units, list) or not units:
        _error(details, "paper.units", "至少需要一个练习单元")
        units = []
    normalized_units: list[dict[str, Any]] = []
    unit_keys: set[str] = set()
    question_keys: set[str] = set()
    answer_map = answers.get("answers", {}) if isinstance(answers, dict) else {}
    if not isinstance(answer_map, dict):
        _error(details, f"answers[{year}].answers", "必须是对象")
        answer_map = {}
    for unit_index, raw_unit in enumerate(units):
        path = f"papers/{year}.units[{unit_index}]"
        if not isinstance(raw_unit, dict):
            _error(details, path, "单元必须是对象")
            continue
        unit = dict(raw_unit)
        unit_key = _key(unit.get("unitKey"), f"{path}.unitKey", details)
        if unit_key in unit_keys:
            _error(details, f"{path}.unitKey", "单元 ID 重复")
        unit_keys.add(unit_key)
        unit_type = unit.get("type")
        if unit_type not in {"cloze", "reading", "part_b", "listening", "word_bank", "paragraph_matching"}:
            _error(details, f"{path}.type", "不支持该题型")
        passage = unit.get("passage")
        if not isinstance(passage, dict):
            _error(details, f"{path}.passage", "必须是对象")
            passage = {"blocks": []}
        blocks = _normalize_blocks(passage.get("blocks", passage.get("paragraphs")), f"{path}.passage.blocks", details)
        unit["passage"] = {**passage, "blocks": blocks}
        unit["passageText"] = _db_passage(blocks)
        unit["contentBlocks"] = blocks
        for block in blocks:
            if block.get("type") in {"image", "audio"} and block.get("assetId") not in assets:
                _error(details, f"{path}.passage.blocks", f"引用了不存在的资源 {block.get('assetId')}")
        candidates = unit.get("candidates", [])
        if candidates and not isinstance(candidates, list):
            _error(details, f"{path}.candidates", "必须是数组")
            candidates = []
        candidate_keys: set[str] = set()
        normalized_candidates: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                _error(details, f"{path}.candidates[{candidate_index}]", "候选项必须是对象")
                continue
            key = candidate.get("key")
            content = _text(candidate.get("content", ""), f"{path}.candidates[{candidate_index}].content", details)
            if not isinstance(key, str) or not re.fullmatch(r"[A-Z]", key):
                _error(details, f"{path}.candidates[{candidate_index}].key", "必须是单个大写字母")
                continue
            if key in candidate_keys:
                _error(details, f"{path}.candidates[{candidate_index}].key", "候选项键重复")
            candidate_keys.add(key)
            normalized_candidates.append({**candidate, "key": key, "content": content})
        unit["candidates"] = normalized_candidates
        questions = unit.get("questions", [])
        if not isinstance(questions, list) or not questions:
            _error(details, f"{path}.questions", "至少需要一道题")
            questions = []
        normalized_questions: list[dict[str, Any]] = []
        for question_index, raw_question in enumerate(questions):
            question_path = f"{path}.questions[{question_index}]"
            if not isinstance(raw_question, dict):
                _error(details, question_path, "题目必须是对象")
                continue
            question = dict(raw_question)
            question_key = _key(question.get("questionKey"), f"{question_path}.questionKey", details)
            if question_key in question_keys:
                _error(details, f"{question_path}.questionKey", "题目 ID 重复")
            question_keys.add(question_key)
            _text(question.get("stem", ""), f"{question_path}.stem", details, required=False)
            if question.get("stemBlocks") is not None:
                question["stemBlocks"] = _normalize_blocks(
                    question.get("stemBlocks"),
                    f"{question_path}.stemBlocks",
                    details,
                )
                for block in question["stemBlocks"]:
                    if block.get("type") in {"image", "audio"} and block.get("assetId") not in assets:
                        _error(details, f"{question_path}.stemBlocks", f"引用了不存在的资源 {block.get('assetId')}")
            if question.get("type") != "single_choice":
                _error(details, f"{question_path}.type", "v1 仅支持 single_choice")
            score = question.get("score")
            if not isinstance(score, (int, float)) or score < 0:
                _error(details, f"{question_path}.score", "分值必须是非负数字")
            options = question.get("options")
            if not options and normalized_candidates:
                options = normalized_candidates
            if not isinstance(options, list) or not options:
                _error(details, f"{question_path}.options", "必须包含选项或使用单元候选项")
                options = []
            option_keys: set[str] = set()
            normalized_options: list[dict[str, Any]] = []
            for option_index, raw_option in enumerate(options):
                option_path = f"{question_path}.options[{option_index}]"
                if not isinstance(raw_option, dict):
                    _error(details, option_path, "选项必须是对象")
                    continue
                option_key = raw_option.get("key")
                if not isinstance(option_key, str) or not re.fullmatch(r"[A-Z]", option_key):
                    _error(details, f"{option_path}.key", "必须是单个大写字母")
                    continue
                if option_key in option_keys:
                    _error(details, f"{option_path}.key", "选项键重复")
                option_keys.add(option_key)
                normalized_options.append(
                    {
                        **raw_option,
                        "key": option_key,
                        "content": _text(raw_option.get("content", ""), f"{option_path}.content", details),
                    }
                )
                if raw_option.get("contentBlocks") is not None:
                    normalized_options[-1]["contentBlocks"] = _normalize_blocks(
                        raw_option.get("contentBlocks"),
                        f"{option_path}.contentBlocks",
                        details,
                    )
                    for block in normalized_options[-1]["contentBlocks"]:
                        if block.get("type") in {"image", "audio"} and block.get("assetId") not in assets:
                            _error(details, f"{option_path}.contentBlocks", f"引用了不存在的资源 {block.get('assetId')}")
            answer = answer_map.get(question_key)
            if not isinstance(answer, dict):
                _error(details, f"answers.{question_key}", "缺少标准答案")
                answer = {}
            correct = answer.get("correctOption")
            if correct not in option_keys:
                _error(details, f"answers.{question_key}.correctOption", "答案必须对应题目选项")
            question["options"] = normalized_options
            question["answerData"] = answer
            question["contentHash"] = _question_hash(unit, question, answer)
            normalized_questions.append(question)
            if labels and isinstance(labels.get("labels"), dict) and question_key in labels["labels"]:
                label = labels["labels"][question_key]
                if isinstance(label, dict):
                    label["questionContentHash"] = label.get("questionContentHash", "")
        unit["questions"] = normalized_questions
        normalized_units.append(unit)
    return {
        **paper,
        "paperKey": paper_key,
        "year": year,
        "units": normalized_units,
        "answers": answer_map,
        "labels": labels or {},
    }


def load_esq_package(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise EsqValidationError([{"path": "file", "reason": "ESQ 文件超过 100 MiB"}])
    if not zipfile.is_zipfile(path):
        raise EsqValidationError([{"path": "file", "reason": "不是有效的 ESQ/ZIP 文件"}])
    details: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                _error(details, "archive", "文件数量超过 1000")
            total_size = 0
            member_names: set[str] = set()
            for info in infos:
                try:
                    name = _safe_member(info.filename)
                except ValueError as error:
                    _error(details, f"archive.{info.filename}", str(error))
                    continue
                if name in member_names:
                    _error(details, f"archive.{name}", "压缩包路径重复")
                member_names.add(name)
                total_size += info.file_size
                if info.flag_bits & 0x1:
                    _error(details, f"archive.{name}", "不允许加密 ZIP")
                if info.compress_size and info.file_size / info.compress_size > 100:
                    _error(details, f"archive.{name}", "压缩比过高")
            if total_size > MAX_UNPACKED_BYTES:
                _error(details, "archive", "解压后总大小超过 300 MiB")
            if "manifest.json" not in member_names:
                _error(details, "manifest.json", "缺少 manifest.json")
            if details:
                raise EsqValidationError(details)
            manifest = _read_json(archive, "manifest.json")
            _validate_manifest(manifest, details)
            assets: dict[str, dict[str, Any]] = {}
            if "assets/index.json" in member_names:
                assets = _validate_asset_index(archive, _read_json(archive, "assets/index.json"), details)
            package_papers: list[dict[str, Any]] = []
            seen_paper_keys: set[str] = set()
            for reference_index, reference in enumerate(manifest.get("papers", [])):
                ref_path = f"manifest.papers[{reference_index}]"
                if not isinstance(reference, dict):
                    _error(details, ref_path, "必须是对象")
                    continue
                paper_key = reference.get("paperKey")
                paper_path = reference.get("path")
                answer_path = reference.get("answerPath")
                if paper_key in seen_paper_keys:
                    _error(details, f"{ref_path}.paperKey", "试卷 ID 重复")
                    continue
                seen_paper_keys.add(paper_key)
                if paper_path not in member_names:
                    _error(details, f"{ref_path}.path", "题目文件不存在")
                    continue
                if answer_path not in member_names:
                    _error(details, f"{ref_path}.answerPath", "答案文件不存在")
                    continue
                paper = _read_json(archive, paper_path)
                answers = _read_json(archive, answer_path)
                labels = _read_json(archive, reference["labelPath"]) if reference.get("labelPath") in member_names else None
                normalized = _validate_paper(paper, reference, answers, labels, assets, details)
                package_papers.append(normalized)
            if details:
                raise EsqValidationError(details)
            return {
                "manifest": manifest,
                "papers": package_papers,
                "assets": assets,
                "source_path": str(path),
            }
    except EsqValidationError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise EsqValidationError([{"path": "file", "reason": f"读取 ESQ 失败：{error}"}]) from error


def _validate_manifest(manifest: dict[str, Any], details: list[dict[str, str]]) -> None:
    if manifest.get("format") != "esq":
        _error(details, "manifest.format", "必须为 esq")
    schema_version = manifest.get("schemaVersion", "")
    if schema_version not in ("1.0", "1.1"):
        _error(details, "manifest.schemaVersion", "仅支持 ESQ 1.0 / 1.1")
    _key(manifest.get("packageId"), "manifest.packageId", details)
    if not isinstance(manifest.get("contentVersion"), str) or not SEMVER_RE.fullmatch(manifest["contentVersion"]):
        _error(details, "manifest.contentVersion", "必须是语义化版本号")
    _text(manifest.get("title", ""), "manifest.title", details)
    _text(manifest.get("subject", ""), "manifest.subject", details)
    _text(manifest.get("publisher", ""), "manifest.publisher", details)
    license_data = manifest.get("license")
    if not isinstance(license_data, dict):
        _error(details, "manifest.license", "必须是对象")
    elif not isinstance(license_data.get("notice"), str) or not license_data["notice"].strip():
        _error(details, "manifest.license.notice", "必须填写使用声明")
    source = manifest.get("source")
    if not isinstance(source, dict):
        _error(details, "manifest.source", "必须是对象")
    elif not isinstance(source.get("description"), str) or not source["description"].strip():
        _error(details, "manifest.source.description", "必须填写来源说明")
    papers = manifest.get("papers")
    if not isinstance(papers, list) or not papers:
        _error(details, "manifest.papers", "至少包含一套试卷")
    if schema_version == "1.1":
        for index, paper_entry in enumerate(papers):
            path = f"manifest.papers[{index}]"
            if not isinstance(paper_entry, dict):
                continue
            exam_type = paper_entry.get("examType")
            if isinstance(exam_type, str) and exam_type not in {"", "cet4", "cet6", "postgraduate_english1", "postgraduate_english2"}:
                _error(details, f"{path}.examType", "不支持的考试类型")
            exam_month = paper_entry.get("examMonth")
            if exam_month is not None and not isinstance(exam_month, int):
                _error(details, f"{path}.examMonth", "必须是整数")
            set_number = paper_entry.get("setNumber")
            if set_number is not None and not isinstance(set_number, int):
                _error(details, f"{path}.setNumber", "必须是整数")
            listening_tracks = paper_entry.get("listeningTracks")
            if listening_tracks is not None and not isinstance(listening_tracks, list):
                _error(details, f"{path}.listeningTracks", "必须是数组")


def build_preview(
    connection: Any,
    package: dict[str, Any],
    *,
    profile_id: int = 1,
) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    totals = {"papers": 0, "units": 0, "questions": 0, "assets": len(package.get("assets", {}))}
    for paper in package["papers"]:
        totals["papers"] += 1
        totals["units"] += len(paper["units"])
        totals["questions"] += sum(len(unit["questions"]) for unit in paper["units"])
        existing = connection.execute(
            """
            SELECT id, year, title, external_key, content_version
            FROM papers
            WHERE profile_id = ? AND deleted_at IS NULL AND external_key = ?
            LIMIT 1
            """,
            (profile_id, paper["paperKey"]),
        ).fetchone()
        conflicts.append(
            {
                "paperKey": paper["paperKey"],
                "year": paper["year"],
                "title": paper.get("title", ""),
                "existing": dict(existing) if existing else None,
                "defaultAction": "replace_with_imported" if existing else "import",
            }
        )
    return {
        "packageId": package["manifest"]["packageId"],
        "contentVersion": package["manifest"]["contentVersion"],
        "title": package["manifest"]["title"],
        "publisher": package["manifest"]["publisher"],
        "hasAnswers": True,
        "hasAiLabels": any(bool(paper.get("labels")) for paper in package["papers"]),
        "totals": totals,
        "conflicts": conflicts,
    }


def _paper_external_key(paper: dict[str, Any]) -> str:
    return paper["paperKey"]


def _unit_external_key(paper: dict[str, Any], unit: dict[str, Any]) -> str:
    return unit["unitKey"]


def _question_external_key(question: dict[str, Any]) -> str:
    return question["questionKey"]


def _safe_package_dir(package_id: str, content_version: str) -> Path:
    safe_package = re.sub(r"[^A-Za-z0-9._-]+", "_", package_id)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "_", content_version)
    return QUESTION_BANK_DIR / safe_package / safe_version


def _extract_assets(
    package: dict[str, Any],
    archive_path: Path,
) -> list[dict[str, Any]]:
    assets = package.get("assets", {})
    if not assets:
        return []
    target_root = _safe_package_dir(package["manifest"]["packageId"], package["manifest"]["contentVersion"])
    target_root.mkdir(parents=True, exist_ok=True)
    stored: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as archive:
        for asset_id, asset in assets.items():
            relative = PurePosixPath(asset["path"])
            destination = (target_root / Path(*relative.parts)).resolve()
            if target_root.resolve() not in destination.parents:
                raise ValueError(f"资源路径越界：{asset['path']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(asset["path"], "r") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            stored.append(
                {
                    **asset,
                    "assetId": asset_id,
                    "storedPath": str(destination),
                }
            )
    return stored


def _label_rows(
    connection: Any,
    question_id: int,
    label: dict[str, Any],
) -> tuple[bool, str]:
    existing = connection.execute(
        "SELECT locked, user_edited FROM question_ai_labels WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if existing and (existing["locked"] or existing["user_edited"]):
        return False, "本地标签已锁定或人工编辑"
    if not label.get("questionContentHash"):
        return False, "标签缺少题目内容哈希"
    locked = int(label.get("reviewStatus") == "locked")
    connection.execute(
        """
        INSERT INTO question_ai_labels
            (question_id, primary_skill, secondary_skills, trap_types,
             attention_points, vocabulary_demand, context_dependency,
             grammar_dependency, confidence, locked, user_edited,
             model_name, label_version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(question_id) DO UPDATE SET
            primary_skill = excluded.primary_skill,
            secondary_skills = excluded.secondary_skills,
            trap_types = excluded.trap_types,
            attention_points = excluded.attention_points,
            vocabulary_demand = excluded.vocabulary_demand,
            context_dependency = excluded.context_dependency,
            grammar_dependency = excluded.grammar_dependency,
            confidence = excluded.confidence,
            locked = excluded.locked,
            model_name = excluded.model_name,
            label_version = question_ai_labels.label_version + 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            question_id,
            str(label.get("primarySkill", "")),
            json.dumps(label.get("secondarySkills", []), ensure_ascii=False),
            json.dumps(label.get("trapTypes", []), ensure_ascii=False),
            json.dumps(label.get("attentionPoints", []), ensure_ascii=False),
            label.get("vocabularyDemand", "medium"),
            label.get("contextDependency", "medium"),
            label.get("grammarDependency", "medium"),
            float(label.get("confidence", 0) or 0),
            locked,
            str(label.get("source", "esq")),
        ),
    )
    return True, "已导入"


def publish_package(
    connection: Any,
    package: dict[str, Any],
    archive_path: Path,
    resolutions: dict[str, str],
    *,
    import_ai_labels: bool = True,
    profile_id: int = 1,
) -> dict[str, Any]:
    extracted_assets = _extract_assets(package, archive_path)
    manifest = package["manifest"]
    package_id = manifest["packageId"]
    content_version = manifest["contentVersion"]
    paper_results: list[dict[str, Any]] = []
    imported_questions = 0
    skipped_papers = 0
    label_imported = 0
    label_skipped = 0
    for paper in package["papers"]:
        paper_key = _paper_external_key(paper)
        existing = connection.execute(
            """
            SELECT * FROM papers
            WHERE profile_id = ? AND deleted_at IS NULL AND external_key = ?
            LIMIT 1
            """,
            (profile_id, paper_key),
        ).fetchone()
        action = resolutions.get(paper_key, "replace_with_imported" if existing else "import")
        if existing and action == "keep_existing":
            skipped_papers += 1
            paper_results.append(
                {
                    "paperKey": paper_key,
                    "paperId": int(existing["id"]),
                    "action": action,
                    "questionCount": 0,
                }
            )
            continue
        if action not in {"import", "replace_with_imported"}:
            raise ValueError(f"{paper_key} 的冲突处理动作无效：{action}")
        if existing:
            paper_id = existing["id"]
            connection.execute(
                """
                UPDATE papers
                SET profile_id = ?, subject = ?, title = ?, source_file = ?,
                    status = 'published', external_key = ?, package_id = ?,
                    content_version = ?, source_metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    profile_id,
                    paper.get("subject", manifest.get("subject", "")),
                    paper["title"],
                    manifest.get("source", {}).get("description", ""),
                    paper_key,
                    package_id,
                    content_version,
                    json.dumps(manifest.get("source", {}), ensure_ascii=False),
                    paper_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO papers
                    (profile_id, year, subject, title, source_file, status, external_key,
                     package_id, content_version, source_metadata)
                VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    paper["year"],
                    paper.get("subject", manifest.get("subject", "")),
                    paper["title"],
                    manifest.get("source", {}).get("description", ""),
                    paper_key,
                    package_id,
                    content_version,
                    json.dumps(manifest.get("source", {}), ensure_ascii=False),
                ),
            )
            paper_id = cursor.lastrowid
        question_id_by_key: dict[str, int] = {}
        question_hash_by_key: dict[str, str] = {}
        imported_unit_keys: set[str] = set()
        imported_question_keys: set[str] = set()
        for unit in paper["units"]:
            unit_key = _unit_external_key(paper, unit)
            imported_unit_keys.add(unit_key)
            unit_row = connection.execute(
                "SELECT * FROM units WHERE paper_id = ? AND (external_key = ? OR sequence = ?)",
                (paper_id, unit_key, unit["sequence"]),
            ).fetchone()
            shared_data = {
                "directions": unit.get("instructions", ""),
                "candidates": {item["key"]: item["content"] for item in unit.get("candidates", [])},
                "content_blocks": unit.get("contentBlocks", []),
                "content_package_id": package_id,
                "content_version": content_version,
            }
            if unit_row:
                unit_id = unit_row["id"]
                connection.execute(
                    """
                    UPDATE units
                    SET unit_type = ?, subtype = ?, title = ?, sequence = ?,
                        passage = ?, shared_data = ?, external_key = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        unit["type"],
                        unit.get("subtype"),
                        unit["title"],
                        unit["sequence"],
                        unit["passageText"],
                        json.dumps(shared_data, ensure_ascii=False),
                        unit_key,
                        unit_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage,
                         shared_data, external_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        unit["type"],
                        unit.get("subtype"),
                        unit["title"],
                        unit["sequence"],
                        unit["passageText"],
                        json.dumps(shared_data, ensure_ascii=False),
                        unit_key,
                    ),
                )
                unit_id = cursor.lastrowid
            for question_sequence, question in enumerate(unit["questions"], 1):
                question_key = _question_external_key(question)
                imported_question_keys.add(question_key)
                q_row = connection.execute(
                    "SELECT * FROM questions WHERE unit_id = ? AND (external_key = ? OR number = ?)",
                    (unit_id, question_key, question["number"]),
                ).fetchone()
                stem_blocks = question.get("stemBlocks", [])
                metadata = dict(question.get("metadata", {}))
                if stem_blocks:
                    metadata["content_blocks"] = stem_blocks
                answer_data = question["answerData"]
                answer = answer_data["correctOption"]
                if q_row:
                    question_id = q_row["id"]
                    connection.execute(
                        """
                        UPDATE questions
                        SET number = ?, stem = ?, question_type = ?, answer = ?,
                            score = ?, sequence = ?, metadata = ?,
                            external_key = ?, content_hash = ?
                        WHERE id = ?
                        """,
                        (
                            question["number"],
                            question.get("stem", ""),
                            question["type"],
                            answer,
                            answer_data.get("score", question.get("score", 0)),
                            question_sequence,
                            json.dumps(metadata, ensure_ascii=False),
                            question_key,
                            question["contentHash"],
                            question_id,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO questions
                            (unit_id, number, stem, question_type, answer, score,
                             sequence, metadata, external_key, content_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            unit_id,
                            question["number"],
                            question.get("stem", ""),
                            question["type"],
                            answer,
                            answer_data.get("score", question.get("score", 0)),
                            question_sequence,
                            json.dumps(metadata, ensure_ascii=False),
                            question_key,
                            question["contentHash"],
                        ),
                    )
                    question_id = cursor.lastrowid
                question_id_by_key[question_key] = question_id
                question_hash_by_key[question_key] = question["contentHash"]
                imported_option_keys = [option["key"] for option in question["options"]]
                for option_sequence, option in enumerate(question["options"], 1):
                    option_row = connection.execute(
                        "SELECT id FROM options WHERE question_id = ? AND stable_key = ?",
                        (question_id, option["key"]),
                    ).fetchone()
                    option_metadata = {}
                    if option.get("contentBlocks"):
                        option_metadata["content_blocks"] = option["contentBlocks"]
                    option_payload = (
                        json.dumps(option_metadata, ensure_ascii=False),
                        question_id,
                        option["key"],
                        option["key"],
                        option["content"],
                        option_sequence,
                    )
                    if option_row:
                        connection.execute(
                            """
                            UPDATE options
                            SET original_label = ?, content = ?, sequence = ?, metadata = ?
                            WHERE id = ?
                            """,
                            (
                                option["key"],
                                option["content"],
                                option_sequence,
                                option_payload[0],
                                option_row["id"],
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO options
                                (question_id, stable_key, original_label, content, sequence, metadata)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                question_id,
                                option["key"],
                                option["key"],
                                option["content"],
                                option_sequence,
                                option_payload[0],
                            ),
                        )
                if imported_option_keys:
                    connection.execute(
                        f"""
                        DELETE FROM options
                        WHERE question_id = ?
                          AND stable_key NOT IN ({','.join('?' for _ in imported_option_keys)})
                        """,
                        (question_id, *imported_option_keys),
                    )
        if import_ai_labels:
            labels = paper.get("labels", {}).get("labels", {})
            if isinstance(labels, dict):
                for question_key, label in labels.items():
                    question_id = question_id_by_key.get(question_key)
                    if (
                        not question_id
                        or not isinstance(label, dict)
                        or label.get("questionContentHash") != question_hash_by_key.get(question_key)
                    ):
                        label_skipped += 1
                        continue
                    imported, _ = _label_rows(connection, question_id, label)
                    if imported:
                        label_imported += 1
                    else:
                        label_skipped += 1
        revision_summary = {
            "paperKey": paper_key,
            "action": action,
            "questionCount": len(imported_question_keys),
            "preservedExistingPaper": bool(existing),
        }
        connection.execute(
            """
            INSERT INTO question_bank_revisions
                (package_id, content_version, paper_external_key, action, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                package_id,
                content_version,
                paper_key,
                action,
                json.dumps(revision_summary, ensure_ascii=False),
            ),
        )
        paper_results.append(
            {
                "paperKey": paper_key,
                "paperId": int(paper_id),
                "action": action,
                "questionCount": len(imported_question_keys),
                "preservedExistingPaper": bool(existing),
            }
        )
    for asset in extracted_assets:
        connection.execute(
            """
            INSERT INTO question_bank_assets
                (package_id, content_version, asset_id, stored_path, media_type, sha256, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, content_version, asset_id) DO UPDATE SET
                stored_path = excluded.stored_path,
                media_type = excluded.media_type,
                sha256 = excluded.sha256,
                metadata = excluded.metadata
            """,
            (
                package_id,
                content_version,
                asset["assetId"],
                asset["storedPath"],
                asset["mediaType"],
                asset["sha256"],
                json.dumps(asset, ensure_ascii=False),
            ),
        )
    connection.execute(
        """
        INSERT INTO question_bank_packages
            (package_id, content_version, title, publisher, manifest_data, source_file, status)
        VALUES (?, ?, ?, ?, ?, ?, 'published')
        ON CONFLICT(package_id, content_version) DO UPDATE SET
            title = excluded.title,
            publisher = excluded.publisher,
            manifest_data = excluded.manifest_data,
            source_file = excluded.source_file,
            status = 'published',
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            package_id,
            content_version,
            manifest["title"],
            manifest["publisher"],
            json.dumps(manifest, ensure_ascii=False),
            str(package["source_path"]),
        ),
    )
    return {
        "packageId": package_id,
        "contentVersion": content_version,
        "papers": paper_results,
        "skippedPapers": skipped_papers,
        "labelsImported": label_imported,
        "labelsSkipped": label_skipped,
        "assetsImported": len(extracted_assets),
    }


def _blocks_from_unit(row: Any) -> list[dict[str, Any]]:
    shared = {}
    try:
        shared = json.loads(row["shared_data"] or "{}")
    except json.JSONDecodeError:
        pass
    blocks = shared.get("content_blocks")
    if isinstance(blocks, list) and blocks:
        return blocks
    paragraphs = str(row["passage"] or "").split("\n\n")
    return [
        {"blockKey": f"p{index}", "type": "paragraph", "text": text}
        for index, text in enumerate(paragraphs, 1)
        if text
    ]


def _remap_asset_blocks(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_remap_asset_blocks(item, mapping) for item in value]
    if isinstance(value, dict):
        result = {key: _remap_asset_blocks(item, mapping) for key, item in value.items()}
        if "assetId" in result and result["assetId"] in mapping:
            result["assetId"] = mapping[result["assetId"]]
        return result
    return value


def export_package(
    connection: Any,
    *,
    years: list[int] | None = None,
    include_answers: bool = True,
    include_labels: bool = False,
    profile_id: int | None = None,
) -> tuple[bytes, str]:
    query = "SELECT * FROM papers WHERE status = 'published' AND deleted_at IS NULL"
    params: list[Any] = []
    if profile_id is not None:
        query += " AND profile_id = ?"
        params.append(profile_id)
    if years:
        query += f" AND year IN ({','.join('?' for _ in years)})"
        params.extend(years)
    papers = connection.execute(query + " ORDER BY year", params).fetchall()
    if not papers:
        raise ValueError("没有可导出的正式题库")
    package_suffix = "-".join(str(row["year"]) for row in papers)
    package_id = f"local.english-practice.export.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    manifest_papers: list[dict[str, Any]] = []
    paper_files: dict[str, dict[str, Any]] = {}
    answer_files: dict[str, dict[str, Any]] = {}
    label_files: dict[str, dict[str, Any]] = {}
    asset_payloads: dict[str, tuple[dict[str, Any], bytes]] = {}

    def register_assets(value: Any, package_id_hint: str, version_hint: str, paper_key: str, mapping: dict[str, str]) -> None:
        if isinstance(value, list):
            for item in value:
                register_assets(item, package_id_hint, version_hint, paper_key, mapping)
            return
        if not isinstance(value, dict):
            return
        asset_id = value.get("assetId")
        if isinstance(asset_id, str) and asset_id not in mapping:
            asset_row = connection.execute(
                """
                SELECT asset_id, stored_path, media_type, sha256, metadata
                FROM question_bank_assets
                WHERE asset_id = ?
                  AND (? = '' OR package_id = ?)
                  AND (? = '' OR content_version = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (asset_id, package_id_hint, package_id_hint, version_hint, version_hint),
            ).fetchone()
            if asset_row and Path(asset_row["stored_path"]).is_file():
                new_id = f"{paper_key}.{asset_id}"
                if len(new_id) > 190:
                    new_id = f"{paper_key[:120]}.asset.{hashlib.sha256(new_id.encode()).hexdigest()[:24]}"
                mapping[asset_id] = new_id
                original_path = Path(asset_row["stored_path"])
                extension = original_path.suffix.lower() or ".bin"
                file_token = hashlib.sha256(new_id.encode()).hexdigest()[:24]
                asset_path = f"assets/{'audio' if asset_row['media_type'].startswith('audio/') else 'images'}/{file_token}{extension}"
                asset_metadata = json.loads(asset_row["metadata"] or "{}")
                asset_data = original_path.read_bytes()
                asset_payloads[new_id] = (
                    {
                        **asset_metadata,
                        "assetId": new_id,
                        "path": asset_path,
                        "mediaType": asset_row["media_type"],
                        "sha256": asset_row["sha256"],
                        "size": len(asset_data),
                    },
                    asset_data,
                )
        for child in value.values():
            register_assets(child, package_id_hint, version_hint, paper_key, mapping)

    for paper_row in papers:
        paper_key = paper_row["external_key"] or f"local.english-practice.{paper_row['year']}"
        paper_data = {
            "paperKey": paper_key,
            "year": paper_row["year"],
            "title": paper_row["title"],
            "subject": paper_row["subject"],
            "examType": paper_row["exam_type"] if "exam_type" in paper_row.keys() else "",
            "examMonth": paper_row["exam_month"] if "exam_month" in paper_row.keys() else 0,
            "setNumber": paper_row["set_number"] if "set_number" in paper_row.keys() else 1,
            "units": [],
        }
        answers: dict[str, Any] = {}
        labels: dict[str, Any] = {}
        paper_asset_mapping: dict[str, str] = {}
        units = connection.execute(
            "SELECT * FROM units WHERE paper_id = ? ORDER BY sequence",
            (paper_row["id"],),
        ).fetchall()
        for unit_row in units:
            unit_key = unit_row["external_key"] or f"{paper_key}.unit{unit_row['sequence']}"
            unit_data = {
                "unitKey": unit_key,
                "type": unit_row["unit_type"],
                "subtype": unit_row["subtype"],
                "title": unit_row["title"],
                "sequence": unit_row["sequence"],
                "passage": {"blocks": _blocks_from_unit(unit_row)},
                "questions": [],
            }
            try:
                shared = json.loads(unit_row["shared_data"] or "{}")
            except json.JSONDecodeError:
                shared = {}
            if shared.get("directions"):
                unit_data["instructions"] = shared["directions"]
            candidates = shared.get("candidates")
            if isinstance(candidates, dict):
                unit_data["candidates"] = [
                    {"key": key, "content": value}
                    for key, value in sorted(candidates.items())
                ]
            question_rows = connection.execute(
                "SELECT * FROM questions WHERE unit_id = ? ORDER BY sequence",
                (unit_row["id"],),
            ).fetchall()
            for question_row in question_rows:
                question_key = question_row["external_key"] or f"{paper_key}.q{question_row['number']:02d}"
                try:
                    metadata = json.loads(question_row["metadata"] or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                question_data = {
                    "questionKey": question_key,
                    "number": question_row["number"],
                    "type": question_row["question_type"],
                    "stem": question_row["stem"],
                    "score": question_row["score"],
                    "options": [],
                }
                if metadata.get("content_blocks"):
                    question_data["stemBlocks"] = metadata["content_blocks"]
                option_rows = connection.execute(
                    "SELECT * FROM options WHERE question_id = ? ORDER BY sequence",
                    (question_row["id"],),
                ).fetchall()
                for option_row in option_rows:
                    option_data = {
                        "key": option_row["stable_key"],
                        "content": option_row["content"],
                    }
                    try:
                        option_metadata = json.loads(option_row["metadata"] or "{}")
                    except (KeyError, json.JSONDecodeError):
                        option_metadata = {}
                    if option_metadata.get("content_blocks"):
                        option_data["contentBlocks"] = option_metadata["content_blocks"]
                    question_data["options"].append(option_data)
                unit_data["questions"].append(question_data)
                if include_answers:
                    answers[question_key] = {
                        "correctOption": question_row["answer"],
                        "score": question_row["score"],
                    }
                if include_labels:
                    label_row = connection.execute(
                        "SELECT * FROM question_ai_labels WHERE question_id = ?",
                        (question_row["id"],),
                    ).fetchone()
                    if label_row:
                        labels[question_key] = {
                            "questionContentHash": question_row["content_hash"] or "",
                            "primarySkill": label_row["primary_skill"],
                            "secondarySkills": json.loads(label_row["secondary_skills"] or "[]"),
                            "trapTypes": json.loads(label_row["trap_types"] or "[]"),
                            "attentionPoints": json.loads(label_row["attention_points"] or "[]"),
                            "vocabularyDemand": label_row["vocabulary_demand"],
                            "contextDependency": label_row["context_dependency"],
                            "grammarDependency": label_row["grammar_dependency"],
                            "confidence": label_row["confidence"],
                            "source": label_row["model_name"] or "local",
                            "reviewStatus": "locked" if label_row["locked"] else "unreviewed",
                        }
            register_assets(
                unit_data,
                shared.get("content_package_id", ""),
                shared.get("content_version", ""),
                paper_key,
                paper_asset_mapping,
            )
            unit_data = _remap_asset_blocks(unit_data, paper_asset_mapping)
            paper_data["units"].append(unit_data)
        paper_path = f"papers/{paper_row['year']}.json"
        answer_path = f"answers/{paper_row['year']}.json"
        reference = {
            "paperKey": paper_key,
            "year": paper_row["year"],
            "path": paper_path,
            "answerPath": answer_path,
        }
        paper_exam_type_ref = paper_row["exam_type"] if "exam_type" in paper_row.keys() else ""
        paper_exam_month_ref = paper_row["exam_month"] if "exam_month" in paper_row.keys() else 0
        has_cet_meta = bool(paper_exam_type_ref or paper_exam_month_ref)
        if has_cet_meta:
            reference["examType"] = paper_exam_type_ref
            reference["examMonth"] = paper_exam_month_ref
            reference["setNumber"] = paper_row["set_number"] if "set_number" in paper_row.keys() else 1
            session_group = paper_row["session_group_key"] if "session_group_key" in paper_row.keys() else ""
            if session_group:
                reference["sessionGroupKey"] = session_group
        if include_labels and labels:
            label_path = f"labels/{paper_row['year']}.json"
            reference["labelPath"] = label_path
            label_files[label_path] = {
                "paperKey": paper_key,
                "labelVersion": "1.0",
                "labels": labels,
            }
        manifest_papers.append(reference)
        paper_files[paper_path] = paper_data
        answer_files[answer_path] = {"paperKey": paper_key, "answers": answers}
    manifest = {
        "format": "esq",
        "schemaVersion": "1.1" if any(p.get("examType") for p in manifest_papers) else "1.0",
        "packageId": package_id,
        "contentVersion": "1.0.0",
        "title": f"英语刷题机导出题库 {package_suffix}",
        "subject": "考研英语（一）",
        "language": "en",
        "locale": "zh-CN",
        "publisher": "英语刷题机用户",
        "license": {
            "spdx": "NOASSERTION",
            "notice": "导出内容请根据原始来源和个人使用范围传播。",
        },
        "source": {
            "type": "local_export",
            "description": "从英语刷题机本地题库导出",
        },
        "papers": manifest_papers,
        "features": {
            "hasAnswers": include_answers,
            "hasAiLabels": bool(label_files),
            "hasAssets": bool(asset_payloads),
        },
        "generator": {"name": "英语刷题机", "version": "0.1.0"},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, payload in paper_files.items():
            archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2))
        if include_answers:
            for name, payload in answer_files.items():
                archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2))
        for name, payload in label_files.items():
            archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2))
        if asset_payloads:
            archive.writestr(
                "assets/index.json",
                json.dumps(
                    {"assets": [metadata for metadata, _ in asset_payloads.values()]},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for metadata, data in asset_payloads.values():
                archive.writestr(metadata["path"], data)
        archive.writestr(
            "LICENSE.txt",
            "请根据题库原始来源确认传播范围。英语刷题机只负责数据交换格式。\n",
        )
    return buffer.getvalue(), f"english-practice-{package_suffix}.esq"
