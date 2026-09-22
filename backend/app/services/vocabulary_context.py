"""Conservative encounter projection and validated model enrichment."""
import json
import re
from datetime import datetime, timezone


def safe_object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def valid_source_sentence(occurrence):
    if occurrence.get("source_kind") in {"question", "fragment"}:
        return False
    sentence = str(occurrence.get("context_sentence") or "").strip()
    term = str(occurrence.get("surface_form") or "").strip().lower().replace("’", "'")
    words = re.findall(r"[a-z]+(?:['-][a-z]+)*", sentence.lower().replace("’", "'"))
    return bool(term and len(sentence) <= 1500 and len(words) >= 3
                and re.search(r"[.!?][\"'”’)]*$", sentence)
                and not re.match(r"(?:directions|section|part [ab])\b", sentence, re.I)
                and " " + term + " " in " " + " ".join(words) + " ")


def occurrence_key(value):
    return str(value.get("sync_id") or json.dumps([value.get("created_at") or "", value.get("context_sentence") or "", value.get("year") or "", value.get("unit_title") or ""], ensure_ascii=False, separators=(",", ":")))


def encounter_time(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError):
        return 0


def project_vocabulary(entry, history):
    valid = [item for item in history if valid_source_sentence(item)]
    latest = max(valid, key=lambda item: (encounter_time(item.get("created_at")), occurrence_key(item)), default=None)
    key = occurrence_key(latest) if latest else ""
    return {**entry, "occurrences": [latest] if latest else [], "latest_sentence": latest.get("context_sentence", "") if latest else "", "context_key": key,
            "morphology": safe_object(entry.get("morphology")), "generated_example": safe_object(entry.get("generated_example")),
            "contextual_meaning": entry.get("contextual_meaning", "") if key and entry.get("contextual_occurrence_key") == key else ""}


def vocabulary_enhancements(value, has_source):
    def text(item, limit):
        return item.strip()[:limit] if isinstance(item, str) else ""
    source = safe_object(value.get("morphology"))
    forms = source.get("forms") if isinstance(source.get("forms"), list) else []
    forms = [{"label": text(item.get("label"), 40), "word": text(item.get("word"), 100)} for item in forms if isinstance(item, dict)]
    morphology = {"lemma": text(source.get("lemma") or value.get("lemma"), 120), "currentForm": text(source.get("currentForm"), 80), "ambiguous": source.get("ambiguous") is True, "note": text(source.get("note"), 200), "forms": [item for item in forms if item["label"] and item["word"]][:10]}
    example = safe_object(value.get("generatedExample") or value.get("generated_example"))
    return morphology, {} if has_source else {"sentence": text(example.get("sentence"), 500), "translation": text(example.get("translation"), 500)}

def valid_vocabulary_enhancements(value, term, has_source):
    morphology, example = vocabulary_enhancements(value, has_source)
    if not morphology['lemma'] or not morphology['currentForm'] or (morphology['ambiguous'] and not morphology['note']):
        return False
    if has_source:
        return isinstance(value.get('contextualMeaning'), str) and bool(value['contextualMeaning'].strip())
    return bool(example.get('translation') and valid_source_sentence({'surface_form': term, 'context_sentence': example.get('sentence')}))


def vocabulary_write_guard(entry):
    fields = ['term', 'user_edited', 'common_meaning', 'contextual_meaning', 'morphology', 'generated_example', 'contextual_occurrence_key']
    return ' AND '.join(field + ' IS ?' for field in fields), [entry.get(field) for field in fields]

