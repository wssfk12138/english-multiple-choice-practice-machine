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
            "morphology": vocabulary_enhancements(entry, bool(latest))[0], "generated_example": safe_object(entry.get("generated_example")),
            "contextual_meaning": entry.get("contextual_meaning", "") if key and entry.get("contextual_occurrence_key") == key else ""}


MORPHOLOGY_LABELS = {'base': '原形', 'base form': '原形', 'infinitive': '原形', 'infinitive form': '原形', 'lemma': '原形', 'present': '现在式', 'present tense': '现在式', 'present form': '现在式', 'past': '过去式', 'past tense': '过去式', 'past form': '过去式', 'past participle': '过去分词', 'present participle': '现在分词', 'gerund': '动名词', 'third person singular': '第三人称单数', 'third person singular present': '第三人称单数', 'plural': '复数', 'singular': '单数', 'singular form': '单数', 'plural form': '复数', 'comparative': '比较级', 'superlative': '最高级', 'uncountable': '不可数', 'uncountable noun': '不可数', 'mass noun': '不可数', '3rd person singular': '第三人称单数', 'fixed phrase': '固定短语'}


def morphology_label(value):
    label = value.strip()[:40] if isinstance(value, str) else ''
    if not label:
        return ''
    if label in MORPHOLOGY_LABELS.values() or label == '词形待确认':
        return label
    return MORPHOLOGY_LABELS.get(re.sub(r'[\s_-]+', ' ', label.lower()).strip(), '词形待确认')


def vocabulary_enhancements(value, has_source):
    def text(item, limit):
        return item.strip()[:limit] if isinstance(item, str) else ''
    source = safe_object(value.get('morphology'))
    forms = source.get('forms') if isinstance(source.get('forms'), list) else []
    forms = [{'label': morphology_label(item.get('label')), 'rawLabel': text(item.get('rawLabel') or item.get('label'), 100), 'word': text(item.get('word'), 100)} for item in forms if isinstance(item, dict)]
    morphology = {'lemma': text(source.get('lemma') or value.get('lemma'), 120), 'currentForm': morphology_label(source.get('currentForm')), 'rawCurrentForm': text(source.get('rawCurrentForm') or source.get('currentForm'), 100), 'ambiguous': source.get('ambiguous') is True, 'note': text(source.get('note'), 200), 'forms': [item for item in forms if item['label'] and item['word']][:10]}
    example = safe_object(value.get('generatedExample') or value.get('generated_example'))
    return morphology, {'sentence': text(example.get('sentence'), 500), 'translation': text(example.get('translation'), 500)}


def valid_generated_example(value, term):
    if not isinstance(value.get('sentence'), str) or not isinstance(value.get('translation'), str):
        return False
    sentence = value['sentence'].strip()
    if not sentence or len(sentence) > 500 or not value['translation'].strip() or len(value['translation']) > 500 or not re.search(r'''[.!?]["'”’)]*$''', sentence) or re.match(r'(?:directions|section|part [ab])\b', sentence, re.I):
        return False
    words = re.findall(r"[a-z]+(?:['-][a-z]+)*", sentence.lower().replace('’', "'"))
    target = str(term or '').strip().lower().replace('’', "'")
    return bool(target) and ' ' + target + ' ' in ' ' + ' '.join(words) + ' '


def valid_vocabulary_enhancements(value, term, has_source):
    raw = safe_object(value.get('morphology'))
    if any(not isinstance(raw.get(key), str) for key in ('lemma', 'currentForm', 'note')) or not raw['lemma'].strip() or not raw['currentForm'].strip() or not isinstance(raw.get('ambiguous'), bool) or not isinstance(raw.get('forms'), list):
        return False
    if any(not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ('label', 'word')) for item in raw['forms']):
        return False
    if morphology_label(raw['currentForm']) == '词形待确认' and (not raw['ambiguous'] or not raw['note'].strip()):
        return False
    if raw['ambiguous'] and not raw['note'].strip():
        return False
    return valid_generated_example(safe_object(value.get('generatedExample') or value.get('generated_example')), term)


def vocabulary_write_guard(entry):
    fields = ['term', 'user_edited', 'lemma', 'phonetic', 'part_of_speech', 'common_meaning', 'memory_hint', 'synonyms', 'antonyms', 'similar_forms', 'contextual_meaning', 'morphology', 'generated_example', 'contextual_occurrence_key']
    fields += [key for key in ('sync_id', 'daily_study_identity') if key in entry]
    return ' AND '.join(field + ' IS ?' for field in fields), [entry.get(field) for field in fields]
