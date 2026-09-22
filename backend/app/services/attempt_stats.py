"""Merge graded attempts without counting answer edits or replay twice."""

import json

FIELDS = ('attempt_count', 'wrong_count', 'recent_results', 'consecutive_correct',
          'last_wrong_at', 'last_attempt_at')


def ledger(row):
    encoded = row.get('attempt_ledger')
    if encoded:
        value = json.loads(encoded)
        if not isinstance(value, dict) or value.get('v') != 1:
            raise ValueError('Invalid attempt ledger')
        base, attempts = value.get('base'), value.get('attempts')
        if not isinstance(base, dict) or not isinstance(attempts, dict):
            raise ValueError('Invalid attempt ledger')
        if set(base) != set(FIELDS):
            raise ValueError('Invalid attempt baseline fields')
        for key, attempt in attempts.items():
            if (not key or len(key) > 256 or not isinstance(attempt, list) or len(attempt) != 2
                    or not isinstance(attempt[0], str) or len(attempt[0]) > 64
                    or type(attempt[1]) is not bool):
                raise ValueError('Invalid graded attempt')
    else:
        base = {field: row.get(field) for field in FIELDS}
        base['recent_results'] = json.loads(base.get('recent_results') or '[]')
        attempts = {}
    for name in ('attempt_count', 'wrong_count', 'consecutive_correct'):
        if not encoded and base.get(name) is None:
            base[name] = 0
        if type(base[name]) is not int or not 0 <= base[name] <= 9007199254740991:
            raise ValueError('Invalid legacy attempt count')
    if base['wrong_count'] > base['attempt_count'] or base['consecutive_correct'] > base['attempt_count']:
        raise ValueError('Invalid legacy attempt totals')
    recent = base.get('recent_results')
    if not isinstance(recent, list) or len(recent) > 10 or any(type(item) is not bool for item in recent):
        raise ValueError('Invalid recent attempt results')
    base['recent_results'] = recent
    for field in ('last_wrong_at', 'last_attempt_at'):
        if base.get(field) is not None and (not isinstance(base[field], str) or len(base[field]) > 64):
            raise ValueError('Invalid attempt timestamp')
        base.setdefault(field, None)
    return {'v': 1, 'base': base, 'attempts': attempts}


def base_key(base):
    return (base['attempt_count'], base['wrong_count'], base['last_attempt_at'] or '',
            base['consecutive_correct'], base['last_wrong_at'] or '',
            ''.join('1' if item else '0' for item in base['recent_results']))


def aggregate(left, right=None, attempt=None):
    value = ledger(left)
    if right is not None:
        other = ledger(right)
        if base_key(other['base']) > base_key(value['base']):
            value['base'] = other['base']
        for key, item in other['attempts'].items():
            if key not in value['attempts'] or tuple(item) > tuple(value['attempts'][key]):
                value['attempts'][key] = item
    if attempt is not None:
        key, timestamp, correct = attempt
        value['attempts'][key] = [timestamp, correct]
    base = value['base']
    if base['attempt_count'] + len(value['attempts']) > 9007199254740991:
        raise ValueError('Attempt count exceeds safe integer range')
    result = dict(base)
    recent = list(base['recent_results'])
    for _, (timestamp, correct) in sorted(value['attempts'].items(), key=lambda item: (item[1][0], item[0])):
        result['attempt_count'] += 1
        result['wrong_count'] += int(not correct)
        result['consecutive_correct'] = result['consecutive_correct'] + 1 if correct else 0
        if not correct:
            result['last_wrong_at'] = timestamp
        result['last_attempt_at'] = timestamp
        recent.append(correct)
    result['recent_results'] = json.dumps(recent[-10:], separators=(',', ':'))
    result['attempt_ledger'] = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return result
