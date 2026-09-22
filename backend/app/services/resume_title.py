from __future__ import annotations

import re


def resume_title(mode: str, identity: dict) -> str:
    year = str(identity.get("year") or "").strip()
    pattern = re.compile(r"(?<![0-9])" + year + r"(?![0-9])(?:\s*年)?") if re.fullmatch(r"\d{4}", year) else None

    def clean(value):
        value = str(value or "").strip()
        return (pattern.sub("", value) if pattern else value).strip(" \t\r\n·—:：-")

    paper, unit = clean(identity.get("paper")), clean(identity.get("unit"))
    parts = [year + " 年" if year else "", paper]
    if mode != "paper" and unit and unit != paper:
        parts.append(unit)
    return " · ".join(part for part in parts if part) or "继续练习"
