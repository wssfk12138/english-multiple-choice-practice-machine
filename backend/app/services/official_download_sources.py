import re


OFFICIAL_RELEASE = (
    "https://github.com/wssfk12138/english-multiple-choice-practice-machine"
    "/releases/download/question-banks-v1.2.0/"
)
OFFICIAL_CATALOG = OFFICIAL_RELEASE + "question-bank-catalog.json"
# The catalog lives in the application repo; its ESQ assets live in this separate official release.
OFFICIAL_BANK_RELEASE = "https://github.com/wssfk12138/english-question-banks/releases/download/question-banks-2026-09-20/"
PROXY_ORIGINS = ("https://ghfast.top/", "https://gh-proxy.com/")


def is_official_download_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    value = url
    for origin in PROXY_ORIGINS:
        if value.startswith(origin):
            value = value[len(origin):]
            break
    return any(value.startswith(prefix) and bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value[len(prefix):])
    ) for prefix in (OFFICIAL_RELEASE, OFFICIAL_BANK_RELEASE))


def official_download_sources(url: str, enabled: bool) -> list[str]:
    if not enabled or not is_official_download_url(url) or url.startswith(PROXY_ORIGINS):
        return [url]
    return [url, *(origin + url for origin in PROXY_ORIGINS)]
