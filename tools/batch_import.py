from __future__ import annotations

"""Resumable batch importer for Word exam papers.

The tool deliberately talks to the local FastAPI application instead of
opening the SQLite database directly.  This keeps the same validation,
question-bank profile and model-assist behavior as the desktop UI.

Typical usage (PowerShell):

    ./.venv/Scripts/python.exe tools/batch_import.py `
      --source "path/to/exam-papers" `
      --profile-id 1 `
      --ai-profile-id 2 `
      --model deepseek-v4-flash `
      --correct-structure `
      --workers 2

The state file is intentionally local and ignored by Git.  Re-running the
same command resumes unfinished items and skips files whose content hash has
not changed.
"""

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx


QUESTION_EXTENSIONS = {".doc", ".docx", ".pdf"}
ANSWER_EXTENSIONS = {".doc", ".docx", ".pdf"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg"}
AUDIO_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
ANSWER_WORDS = (
    "答案",
    "参考答案",
    "answer",
    "answers",
    "key",
    "keys",
    "解析",
    "answerkey",
)
IGNORED_WORDS = ANSWER_WORDS + ("听力", "listening", "audio", "音频")
DEFAULT_BASE_URL = "http://127.0.0.1:8765/api"
DEFAULT_STATE = ".codex-local/batch-import-state.json"
DEFAULT_MAX_TOKENS = 0


@dataclass(frozen=True)
class BatchItem:
    question_path: str
    answer_paths: tuple[str, ...]
    audio_paths: tuple[str, ...]
    key: str
    question_sha256: str
    answer_sha256: str


def _normalized_name(path: Path) -> str:
    value = path.stem.casefold()
    value = re.sub(r"[\s_\-.（）()【】\[\]{}]+", "", value)
    for word in IGNORED_WORDS:
        value = value.replace(word.casefold(), "")
    return value


def _year_tokens(path: Path) -> set[str]:
    return set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", path.stem))


def _month_token(path: Path) -> str:
    """Return the exam month without mistaking ``.12`` for month 1.

    The source tree mixes ``2025.12``, ``2025年12月`` and ``2025-12``.
    Matching the complete year/month pair (and putting 10-12 first) avoids
    the old regex accepting only the leading ``1`` from ``12``.
    """
    match = re.search(
        r"(?<!\d)(?:19|20)\d{2}\s*(?:年|[.\-_])\s*(1[0-2]|0?[1-9])\s*月?",
        path.stem,
    )
    return match.group(1).zfill(2) if match else ""


def _set_number(path: Path) -> int | None:
    stem = path.stem
    match = re.search(r"第\s*([一二三1-3])\s*套", stem)
    if match:
        value = match.group(1)
        return {"一": 1, "二": 2, "三": 3}.get(
            value, int(value) if value.isdigit() else None
        )
    # Some answer attachments use ``（一）``/``(2)`` instead of ``第2套``.
    match = re.search(r"[（(]\s*([一二三1-3])\s*[）)]", stem)
    if not match:
        match = re.search(r"(?:解析|答案)\s*([1-3])(?:\D|$)", stem)
    if not match:
        return None
    value = match.group(1)
    return {"一": 1, "二": 2, "三": 3}.get(
        value, int(value) if value.isdigit() else None
    )


def _exam_level(path: Path) -> str:
    filename = path.stem.casefold()
    filename_cet4 = "四级" in filename or "cet4" in filename
    filename_cet6 = "六级" in filename or "cet6" in filename
    if filename_cet4 != filename_cet6:
        return "cet4" if filename_cet4 else "cet6"
    value = "/".join(part.casefold() for part in path.parts)
    has_cet4 = "四级" in value or "cet4" in value
    has_cet6 = "六级" in value or "cet6" in value
    if has_cet4 == has_cet6:
        return ""
    return "cet4" if has_cet4 else "cet6"


def _same_exam_level(left: Path, right: Path) -> bool:
    left_level = _exam_level(left)
    right_level = _exam_level(right)
    return not left_level or not right_level or left_level == right_level


def _same_exam_period(left: Path, right: Path) -> bool:
    if not (_year_tokens(left) & _year_tokens(right)):
        return False
    left_month = _month_token(left)
    right_month = _month_token(right)
    return not left_month or not right_month or left_month == right_month


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_answer_file(path: Path) -> bool:
    lowered = path.stem.casefold()
    return any(word.casefold() in lowered for word in ANSWER_WORDS)


def _is_non_question_audio_text(path: Path) -> bool:
    """Exclude listening transcripts/translations mistakenly stored as PDFs."""
    lowered = path.stem.casefold()
    return any(
        marker in lowered
        for marker in ("听力原文", "原文译文", "听力材料", "listeningtranscript")
    )


def _score_answer(question: Path, answer: Path) -> int:
    score = 0
    q_name = _normalized_name(question)
    a_name = _normalized_name(answer)
    if q_name and q_name == a_name:
        score += 100
    elif q_name and (q_name in a_name or a_name in q_name):
        score += 60
    if question.parent == answer.parent:
        score += 25
    if _year_tokens(question) & _year_tokens(answer):
        score += 25
    if _is_answer_file(answer):
        score += 15
    if answer.suffix.casefold() == ".pdf":
        score += 3
    return score


def discover_batch(source: Path) -> list[BatchItem]:
    """Discover question files and pair the best matching answer attachment."""
    if not source.exists() or not source.is_dir():
        raise ValueError(f"题库目录不存在：{source}")
    files = [path for path in source.rglob("*") if path.is_file()]
    source_level = _exam_level(source)
    question_candidates = [
        path for path in files
        if path.suffix.casefold() in QUESTION_EXTENSIONS
        and not _is_answer_file(path)
        and not _is_non_question_audio_text(path)
        and not (
            source_level
            and _exam_level(path)
            and _exam_level(path) != source_level
        )
    ]
    answers = [path for path in files if path.suffix.casefold() in ANSWER_EXTENSIONS and _is_answer_file(path)]
    audios = [path for path in files if path.suffix.casefold() in AUDIO_EXTENSIONS]
    by_paper_key: dict[tuple[tuple[str, ...], str, int | None], list[Path]] = {}
    for path in question_candidates:
        paper_key = (
            _exam_level(path),
            tuple(sorted(_year_tokens(path))),
            _month_token(path),
            _set_number(path),
        )
        by_paper_key.setdefault(paper_key, []).append(path)
    questions: list[Path] = []
    for paths in by_paper_key.values():
        preferred = sorted(
            paths,
            key=lambda path: (
                path.suffix.casefold() == ".pdf",
                len(path.name),
                str(path).casefold(),
            ),
        )[0]
        questions.append(preferred)
    individual_periods = {
        (_exam_level(path), tuple(sorted(_year_tokens(path))), _month_token(path))
        for path in questions
        if _set_number(path) is not None
    }
    questions = [
        path
        for path in questions
        if _set_number(path) is not None
        or (_exam_level(path), tuple(sorted(_year_tokens(path))), _month_token(path))
        not in individual_periods
    ]
    result: list[BatchItem] = []
    for question in sorted(questions, key=lambda item: str(item).casefold()):
        ranked = sorted(
            (
                (_score_answer(question, answer), answer)
                for answer in answers
            ),
            key=lambda item: (item[0], str(item[1]).casefold()),
            reverse=True,
        )
        question_set = _set_number(question)
        matched_answers = [
            answer
            for score, answer in ranked
            if score >= 40
            and _same_exam_level(question, answer)
            and _same_exam_period(question, answer)
            and (
                question_set is None
                or _set_number(answer) is None
                or _set_number(answer) == question_set
            )
        ]
        if question_set is not None:
            exact_set_answers = [
                answer for answer in matched_answers if _set_number(answer) == question_set
            ]
            if exact_set_answers:
                matched_answers = exact_set_answers
        same_folder_audio = [
            audio for audio in audios
            if _same_exam_level(question, audio)
            and _same_exam_period(question, audio)
            and (
                question_set is None
                or _set_number(audio) is None
                or _set_number(audio) == question_set
            )
        ]
        question_hash = _sha256(question)
        answer_hash = hashlib.sha256(
            "".join(_sha256(answer) for answer in matched_answers).encode("ascii")
        ).hexdigest()
        relative = question.relative_to(source).as_posix()
        result.append(
            BatchItem(
                question_path=str(question),
                answer_paths=tuple(str(answer) for answer in matched_answers),
                audio_paths=tuple(str(audio) for audio in sorted(same_folder_audio)),
                key=relative,
                question_sha256=question_hash,
                answer_sha256=answer_hash,
            )
        )
    return result


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "source": "", "items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "source": "", "items": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        return {"version": 1, "source": "", "items": {}}
    return payload


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as temporary:
        json.dump(state, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _post_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code not in {429, 500, 502, 503, 504}:
                return response
            if attempt == retries:
                return response
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = max(1.0, min(30.0, float(retry_after)))
            except ValueError:
                delay = min(30.0, 2.0 ** attempt)
            time.sleep(delay)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as error:
            last_error = error
            if attempt == retries:
                raise
            time.sleep(min(30.0, 2.0 ** attempt))
    raise RuntimeError("请求失败") from last_error


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"服务端返回了非 JSON 响应（HTTP {response.status_code}）") from error
    if response.status_code >= 400:
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    if not isinstance(payload, dict):
        raise RuntimeError("服务端返回格式不是 JSON 对象")
    return payload


def _upload_item(
    item: BatchItem,
    *,
    client: httpx.Client,
    base_url: str,
    profile_id: int,
    ai_profile_id: int | None,
    model: str,
    correct_structure: bool,
    max_tokens: int,
    retries: int,
    publish_clean: bool,
    force_publish: bool,
    label_after_publish: bool,
) -> dict[str, Any]:
    question = Path(item.question_path)
    files: list[tuple[str, tuple[str, tuple[str, bytes], str]]] = []
    with question.open("rb") as source:
        files.append(("file", (question.name, source.read(), "application/octet-stream")))
    for answer_name in item.answer_paths:
        answer = Path(answer_name)
        with answer.open("rb") as source:
            files.append(("answer_files", (answer.name, source.read(), "application/octet-stream")))
    for audio_name in item.audio_paths:
        audio = Path(audio_name)
        with audio.open("rb") as source:
            content_type = AUDIO_CONTENT_TYPES.get(
                audio.suffix.casefold(),
                "application/octet-stream",
            )
            files.append(("audio_files", (audio.name, source.read(), content_type)))
    data = {
        "profile_id": str(profile_id),
        "use_model_assist": "true",
        "defer_model_assist": "true",
        "model_assist_correct_structure": "true" if correct_structure else "false",
    }
    uploaded = _response_json(
        _post_with_retry(
            client,
            "POST",
            f"{base_url}/imports",
            retries=retries,
            data=data,
            files=files,
        )
    )
    job_id = int(uploaded["id"])
    assist = _response_json(
        _post_with_retry(
            client,
            "POST",
            f"{base_url}/imports/{job_id}/model-assist",
            retries=retries,
            json={
                "profile_id": ai_profile_id,
                "model": model,
                "correct_structure": correct_structure,
                "max_tokens": max_tokens,
            },
        )
    )
    draft = assist.get("draft") or {}
    model_assist = assist.get("model_assist") or draft.get("model_assist") or {}
    result: dict[str, Any] = {
        "job_id": job_id,
        "draft_status": "draft",
        "model_status": model_assist.get("status", "unknown"),
        "answered": model_assist.get("answer_total", 0),
        "warnings": len(assist.get("warnings") or draft.get("warnings") or []),
        "answer_file_count": len(item.answer_paths),
        "audio_count": len(item.audio_paths),
    }
    if publish_clean:
        warnings = assist.get("warnings") or draft.get("warnings") or []
        if warnings and not force_publish:
            result["publish_skipped"] = "存在校验警告"
        else:
            published = _response_json(
                _post_with_retry(
                    client,
                    "POST",
                    f"{base_url}/imports/{job_id}/publish",
                    retries=retries,
                    params={"force": "true" if force_publish else "false"},
                )
            )
            result.update(
                {
                    "draft_status": "published",
                    "paper_id": published.get("paper_id"),
                    "question_count": published.get("question_count", 0),
                }
            )
            if label_after_publish and published.get("paper_id"):
                result["labeling"] = _label_published_paper(
                    client,
                    base_url=base_url,
                    paper_id=int(published["paper_id"]),
                    ai_profile_id=ai_profile_id,
                    model=model,
                    max_tokens=max_tokens,
                    retries=retries,
                )
    return result


def _label_published_paper(
    client: httpx.Client,
    *,
    base_url: str,
    paper_id: int,
    ai_profile_id: int | None,
    model: str,
    max_tokens: int,
    retries: int,
) -> dict[str, Any]:
    """Label every eligible non-listening unit in one published paper."""
    run_id = f"batch-{paper_id}-{int(time.time())}"
    processed = 0
    while True:
        payload = _response_json(
            _post_with_retry(
                client,
                "POST",
                f"{base_url}/ai/question-labels/next",
                retries=retries,
                json={
                    "year": None,
                    "paper_ids": [paper_id],
                    "overwrite_unlocked": False,
                    "run_id": run_id,
                    "profile_id": ai_profile_id,
                    "model": model,
                    "max_tokens": max_tokens,
                },
            )
        )
        run_id = str(payload.get("run_id") or run_id)
        processed += int(payload.get("processed") or 0)
        if payload.get("done"):
            return {
                "status": "completed",
                "processed": processed,
                "total": int(payload.get("total") or 0),
                "labeled": int(payload.get("labeled") or 0),
                "listening_excluded": True,
            }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="英语刷题机可恢复批量导入工具")
    parser.add_argument(
        "--source",
        required=True,
        help="包含 Word/PDF 试卷、答案附件和可选 MP3/M4A/WAV/OGG 的目录",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="本地服务 API 地址")
    parser.add_argument("--profile-id", type=int, required=True, help="目标题库配置 ID")
    parser.add_argument("--ai-profile-id", type=int, default=None, help="模型配置 ID；不填使用默认启用配置")
    parser.add_argument("--model", default="", help="模型 ID，例如 deepseek-v4-flash")
    parser.add_argument("--workers", type=int, default=2, help="并发试卷数，默认 2")
    parser.add_argument("--retries", type=int, default=4, help="单个请求的重试次数")
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--state", default=DEFAULT_STATE, help="断点续跑状态文件")
    parser.add_argument("--correct-structure", action="store_true", help="允许模型修正题干和选项归属")
    parser.add_argument("--publish-clean", action="store_true", help="模型校对后自动发布无警告草稿")
    parser.add_argument("--force-publish", action="store_true", help="即使有警告也发布（不推荐）")
    parser.add_argument("--skip-labeling", action="store_true", help="发布后不启动题目智能标注")
    parser.add_argument("--dry-run", action="store_true", help="只扫描配对，不调用服务")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 篇，0 表示全部")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    source = Path(args.source).expanduser().resolve()
    state_path = Path(args.state).expanduser()
    items = discover_batch(source)
    if args.limit > 0:
        items = items[: args.limit]
    print(f"发现 {len(items)} 篇试卷；答案已配对 {sum(1 for item in items if item.answer_paths)} 篇")
    for item in items:
        answer = (
            "、".join(Path(path).name for path in item.answer_paths)
            if item.answer_paths
            else "未配对"
        )
        print(f"- {item.key} <= {answer}，音频 {len(item.audio_paths)} 个")
    if args.dry_run:
        return 0
    if args.workers < 1 or args.workers > 8:
        raise SystemExit("--workers 必须在 1-8 之间")
    state = _load_state(state_path)
    state["version"] = 1
    state["source"] = str(source)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    lock = threading.Lock()
    completed = 0

    def handle(item: BatchItem) -> tuple[str, dict[str, Any]]:
        nonlocal completed
        previous = state["items"].get(item.key)
        fingerprint = f"{item.question_sha256}:{item.answer_sha256}"
        if previous and previous.get("fingerprint") == fingerprint and previous.get("status") == "completed":
            return item.key, {"status": "skipped", "reason": "内容未变化，已完成"}
        state_entry: dict[str, Any] = {
            "fingerprint": fingerprint,
            "question_file": item.question_path,
            "answer_files": list(item.answer_paths),
            "status": "running",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with lock:
            state["items"][item.key] = state_entry
            _save_state(state_path, state)
        try:
            with httpx.Client(timeout=httpx.Timeout(240.0, connect=30.0)) as client:
                result = _upload_item(
                    item,
                    client=client,
                    base_url=args.base_url.rstrip("/"),
                    profile_id=args.profile_id,
                    ai_profile_id=args.ai_profile_id,
                    model=args.model,
                    correct_structure=args.correct_structure,
                    max_tokens=args.max_output_tokens,
                    retries=args.retries,
                    publish_clean=args.publish_clean,
                    force_publish=args.force_publish,
                    label_after_publish=not args.skip_labeling,
                )
            result["status"] = "completed"
            state_entry.update(result, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            with lock:
                state["items"][item.key] = state_entry
                _save_state(state_path, state)
            return item.key, result
        except Exception as error:
            result = {"status": "failed", "error": str(error)[:500]}
            state_entry.update(result, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            with lock:
                state["items"][item.key] = state_entry
                _save_state(state_path, state)
            return item.key, result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(handle, item) for item in items]
        for future in as_completed(futures):
            key, result = future.result()
            completed += 1
            print(f"[{completed}/{len(items)}] {key}: {result.get('status')} {result.get('model_status', '')}")
    failed = sum(1 for item in state["items"].values() if item.get("status") == "failed")
    skipped = sum(1 for item in state["items"].values() if item.get("status") == "skipped")
    print(f"批量导入完成：失败 {failed}，跳过 {skipped}。状态文件：{state_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
