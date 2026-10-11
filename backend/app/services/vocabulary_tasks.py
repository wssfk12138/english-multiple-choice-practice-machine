"""词汇 AI 任务的额度与请求策略（从 Android 平板 S1+S3 移植，桌面适配）。

额度：翻译与补缺两类任务共享本机额度——10 分钟窗口最多 4 次请求；
翻译每批最多 8 词。额度耗尽时剩余任务保持排队（不标记失败），
由下一次触发（练习退出、打开单词本、手动重试）继续。
请求：一次性发送——不自动重试、不跟随重定向、不二次回放；FastAI 精确域名
连接 15 秒 / 读取空闲 180 秒，其他供应商读取空闲 60 秒；总期限 240 秒
（看门狗强制中断）；响应上限 4 MiB。错误信息不带 URL、API Key 或供应商正文。
截断（finish_reason 命中 length/incomplete/content_filter/max_tokens）按安全
代码 vocabulary_response_truncated 停止，不当有效词汇、不降级补发。
"""
from __future__ import annotations

import httpx
import json
import sqlite3
from urllib.parse import urlparse

MAX_REQUESTS_PER_WINDOW = 4
REQUEST_WINDOW_SECONDS = 600
MAX_BATCH_WORDS = 8
TOTAL_DEADLINE_SECONDS = 240
RESPONSE_BYTES_LIMIT = 4 * 1024 * 1024
FASTAI_HOSTS = {"fastaitoken.com", "www.fastaitoken.com"}
TRUNCATED_FINISH_REASONS = {"length", "incomplete", "content_filter", "max_tokens"}

SAFE_HTTP_MESSAGE = "模型服务暂时不可用（网络或供应商错误），未自动重发；请检查网络后用重试入口继续"


class VocabularyTruncatedError(ValueError):
    """供应商未完整生成；固定安全代码，调度层不再区分原文。"""

    code = "vocabulary_response_truncated"


def ensure_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS vocabulary_ai_requests (
            at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            task TEXT NOT NULL
        )
        """
    )


def quota_remaining(connection: sqlite3.Connection) -> int:
    ensure_ledger(connection)
    used = connection.execute(
        "SELECT COUNT(*) FROM vocabulary_ai_requests WHERE datetime(at) >= datetime('now', ?)",
        (f"-{REQUEST_WINDOW_SECONDS} seconds",),
    ).fetchone()[0]
    return max(0, MAX_REQUESTS_PER_WINDOW - used)


def record_request(connection: sqlite3.Connection, task: str) -> None:
    ensure_ledger(connection)
    connection.execute("INSERT INTO vocabulary_ai_requests(task) VALUES(?)", (task,))
    connection.execute("DELETE FROM vocabulary_ai_requests WHERE datetime(at) < datetime('now', '-1 day')")


def consume_quota(connection: sqlite3.Connection, task: str) -> bool:
    """原子地检查并消耗一次额度；真实请求发出前调用（测试中 mock 掉
    请求层即不消耗额度）。返回 False 表示 10 分钟窗口额度已用完。"""
    ensure_ledger(connection)
    cursor = connection.execute(
        f"""
        INSERT INTO vocabulary_ai_requests(task)
        SELECT ? WHERE (
            SELECT COUNT(*) FROM vocabulary_ai_requests
            WHERE datetime(at) >= datetime('now', '-{REQUEST_WINDOW_SECONDS} seconds')
        ) < {MAX_REQUESTS_PER_WINDOW}
        """,
        (task,),
    )
    return cursor.rowcount == 1


def network_policy(base_url: str) -> dict:
    host = (urlparse(base_url).hostname or "").lower()
    fastai = host in FASTAI_HOSTS
    return {"connect": 15, "read": 180 if fastai else 60, "fastai": fastai}


def failure_reason(error: BaseException) -> str:
    if isinstance(error, VocabularyTruncatedError):
        return "truncated"
    text = str(error)
    if isinstance(error, httpx.HTTPError) or "模型服务暂时不可用" in text:
        return "http"
    if "请先" in text or "未启用" in text or "未配置" in text:
        return "configuration"
    if isinstance(error, json.JSONDecodeError):
        return "parse"
    if isinstance(error, ValueError):
        return "validation"
    return "unknown"
