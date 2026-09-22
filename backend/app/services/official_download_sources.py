OFFICIAL_RELEASE = (
    "https://github.com/wssfk12138/english-multiple-choice-practice-machine"
    "/releases/download/question-banks-v1.2.0/"
)
OFFICIAL_CATALOG = OFFICIAL_RELEASE + "question-bank-catalog.json"
PROXY_ORIGINS = ("https://ghfast.top/", "https://gh-proxy.com/")


def official_download_sources(url: str, enabled: bool) -> list[str]:
    import re
    if not enabled or not url.startswith(OFFICIAL_RELEASE):
        return [url]
    name = url[len(OFFICIAL_RELEASE):]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        return [url]
    return [url, *(origin + url for origin in PROXY_ORIGINS)]
