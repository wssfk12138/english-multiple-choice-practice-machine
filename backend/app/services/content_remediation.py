"""Exact, content-only upgrades. Manifests contain no learning data."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

UNIT_FIELDS = ('unit_type', 'subtype', 'title', 'sequence', 'passage')
QUESTION_FIELDS = ('number', 'stem', 'question_type', 'answer', 'score', 'sequence')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get('version') != 1 or not re.fullmatch(r'[a-zA-Z0-9._-]{1,80}', str(manifest.get('revision', ''))):
        raise ValueError('题库修正清单格式无效')
    if not isinstance(manifest.get('units'), list) or len(manifest['units']) > 1000:
        raise ValueError('题库修正清单篇目无效')
    keys = set()
    for change in manifest['units']:
        key = change.get('unitKey')
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError('题库修正清单含重复或无效篇目')
        keys.add(key)
        reviewed = change.get('reviewedBefore', [])
        if not isinstance(reviewed, list) or len(reviewed) > 10:
            raise ValueError('题库修正历史基线无效')
        identities = []
        for unit in [change.get('before', {}), change.get('after', {}), *reviewed]:
            required = {*UNIT_FIELDS, 'shared.content_blocks', 'shared.directions', 'shared.candidates', 'questions'}
            if not required <= unit.keys() or set(unit) - required - {'shared.fixed_slots'}:
                raise ValueError('题库修正清单字段无效')
            questions = unit['questions']
            if not isinstance(questions, list) or not questions:
                raise ValueError('题库修正清单题目无效')
            ids = {}
            for q in questions:
                if not {*QUESTION_FIELDS, 'key', 'metadata', 'options'} == q.keys() or q['key'] in ids:
                    raise ValueError('题库修正清单题目身份无效')
                labels = [o['key'] for o in q['options']]
                if not labels or len(set(labels)) != len(labels) or q['answer'] not in labels:
                    raise ValueError('题库修正清单选项或答案无效')
                ids[q['key']] = set(labels)
            identities.append(ids)
        if any(ids != identities[0] for ids in identities[1:]):
            raise ValueError('题库修正不得增删题目或选项身份')
        hashes = change.get('questionHashes', {})
        if hashes.keys() != identities[1].keys() or any(not re.fullmatch(r'[0-9a-f]{64}', h) for h in hashes.values()):
            raise ValueError('题库修正清单题目指纹无效')


def read_unit(connection, unit):
    shared = json.loads(unit['shared_data'] or '{}')
    result = {key: unit[key] for key in UNIT_FIELDS}
    result['subtype'] = result['subtype'] or ''
    for key, default in [('content_blocks', []), ('directions', ''), ('candidates', {})]:
        result['shared.' + key] = shared.get(key, default)
    if 'fixed_slots' in shared:
        result['shared.fixed_slots'] = shared['fixed_slots']
    result['questions'] = []
    for question in connection.execute('SELECT * FROM questions WHERE unit_id=? ORDER BY sequence,id', (unit['id'],)):
        q = {key: question[key] for key in QUESTION_FIELDS}
        q.update(key=question['external_key'], metadata=json.loads(question['metadata'] or '{}'), options=[])
        for option in connection.execute('SELECT * FROM options WHERE question_id=? ORDER BY sequence,id', (question['id'],)):
            q['options'].append({'key': option['stable_key'], 'original_label': option['original_label'],
                'content': option['content'], 'sequence': option['sequence'], 'metadata': json.loads(option['metadata'] or '{}')})
        result['questions'].append(q)
    return result


def conflicts(actual, before, after):
    found = []
    for key in sorted(set(before) | set(after) | set(actual)):
        value = actual.get(key)
        if value in (before.get(key), after.get(key)):
            continue
        # Legacy Windows imports retained the exact plaintext but no blocks.
        if key == 'shared.content_blocks' and value == [] and actual['passage'] in (before['passage'], after['passage']):
            continue
        found.append(key)
    return found


def preflight(connection, manifest):
    validate_manifest(manifest)
    records = []
    keys = set()
    for change in manifest['units']:
        if change['unitKey'] in keys:
            raise ValueError('题库修正清单含重复篇目')
        keys.add(change['unitKey'])
        for unit in connection.execute('SELECT u.* FROM units u JOIN papers p ON p.id=u.paper_id WHERE u.external_key=? AND p.deleted_at IS NULL', (change['unitKey'],)):
            actual = read_unit(connection, unit)
            baselines = [change['before'], *change.get('reviewedBefore', [])]
            errors = [] if any(not conflicts(actual, baseline, change['after']) for baseline in baselines) else conflicts(actual, change['before'], change['after'])
            revision = json.loads(unit['shared_data'] or '{}').get('content_revision')
            records.append({'unitId': unit['id'], 'unitKey': change['unitKey'], 'actual': actual,
                'conflicts': errors, 'change': change, 'contentChanged': actual != change['after'],
                'needed': actual != change['after'] or revision != manifest['revision']})
    return records


def apply(connection, manifest):
    """Caller owns transaction/backup. Preflight all units before the first write."""
    from .practice_snapshots import capture_session
    if not connection.in_transaction:
        raise ValueError('题库修正必须在显式事务中执行')
    records = preflight(connection, manifest)
    bad = [r for r in records if r['conflicts']]
    if bad:
        raise ValueError('题库内容存在未审查差异，修正已停止：' + ', '.join(r['unitKey'] for r in bad))
    pending = [r for r in records if r['needed']]
    session_ids = set()
    for record in pending:
        if not record['contentChanged']:
            continue
        uid = record['unitId']
        for session in connection.execute('SELECT id,unit_ids FROM practice_sessions'):
            if uid in json.loads(session['unit_ids'] or '[]'):
                session_ids.add(session['id'])
        session_ids.update(r[0] for r in connection.execute('SELECT DISTINCT session_id FROM practice_answers WHERE question_id IN (SELECT id FROM questions WHERE unit_id=?)', (uid,)))
    for session_id in sorted(session_ids):
        capture_session(connection, session_id, manifest['revision'])
    connection.execute('CREATE TABLE IF NOT EXISTS content_remediation_receipts (unit_id INTEGER, revision TEXT, before_hash TEXT NOT NULL, after_hash TEXT NOT NULL, historical_wrong_stats TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(unit_id,revision))')
    for record in pending:
        uid, target = record['unitId'], record['change']['after']
        receipt = connection.execute('SELECT after_hash FROM content_remediation_receipts WHERE unit_id=? AND revision=?', (uid, manifest['revision'])).fetchone()
        if receipt and receipt['after_hash'] != digest(target):
            raise ValueError('同一题库修订号的内容指纹发生变化')
        unit = connection.execute('SELECT * FROM units WHERE id=?', (uid,)).fetchone()
        shared = json.loads(unit['shared_data'] or '{}')
        for key, value in target.items():
            if key.startswith('shared.'):
                shared[key[7:]] = value
        if record['contentChanged']:
            shared['label_review_versions'] = {r['external_key']: r['label_version'] for r in connection.execute('SELECT q.external_key,l.label_version FROM question_ai_labels l JOIN questions q ON q.id=l.question_id WHERE q.unit_id=?', (uid,))}
        shared['content_revision'] = manifest['revision']
        connection.execute('UPDATE units SET unit_type=?,subtype=?,title=?,sequence=?,passage=?,shared_data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (*[target[k] for k in UNIT_FIELDS], json.dumps(shared, ensure_ascii=False), uid))
        historical = [dict(r) for r in connection.execute('SELECT w.* FROM wrong_stats w JOIN questions q ON q.id=w.question_id WHERE q.unit_id=?', (uid,))]
        for q in target['questions']:
            qid = connection.execute('SELECT id FROM questions WHERE unit_id=? AND external_key=?', (uid, q['key'])).fetchone()[0]
            connection.execute('UPDATE questions SET number=?,stem=?,question_type=?,answer=?,score=?,sequence=?,metadata=?,content_hash=? WHERE id=?',
                (*[q[k] for k in QUESTION_FIELDS], json.dumps(q['metadata'], ensure_ascii=False), record['change']['questionHashes'][q['key']], qid))
            for option in q['options']:
                connection.execute('UPDATE options SET original_label=?,content=?,sequence=?,metadata=? WHERE question_id=? AND stable_key=?',
                    (option['original_label'], option['content'], option['sequence'], json.dumps(option['metadata'], ensure_ascii=False), qid, option['key']))
        connection.execute('INSERT INTO content_remediation_receipts(unit_id,revision,before_hash,after_hash,historical_wrong_stats) VALUES(?,?,?,?,?) ON CONFLICT(unit_id,revision) DO NOTHING',
            (uid, manifest['revision'], digest(record['actual']), digest(target), json.dumps(historical, ensure_ascii=False)))
        if read_unit(connection, connection.execute('SELECT * FROM units WHERE id=?', (uid,)).fetchone()) != target:
            raise ValueError('题库修正写入校验失败')
    return {'revision': manifest['revision'], 'units': len(pending), 'sessions': len(session_ids)}


def load_manifest(path: Path):
    manifest = json.loads(path.read_text(encoding='utf-8'))
    validate_manifest(manifest)
    return manifest


def apply_with_backup(connection, manifest, backup_path: Path):
    """Create an exclusive SQLite online backup, then recheck under a write lock."""
    if connection.in_transaction:
        raise ValueError('备份前存在未提交事务')
    records = preflight(connection, manifest)
    if any(r['conflicts'] for r in records):
        raise ValueError('题库内容存在未审查差异，修正已停止')
    if not any(r['needed'] for r in records):
        return {'revision': manifest['revision'], 'units': 0, 'sessions': 0}
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with backup_path.open('xb'):
        pass
    with sqlite3.connect(backup_path) as backup:
        connection.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('题库修正备份校验失败')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        return apply(connection, manifest)


def bundled_manifest():
    from ..config import FRONTEND_DIST
    path = FRONTEND_DIST / 'content-remediation.json'
    return load_manifest(path) if path.exists() else None


def ensure_content_remediation(connection):
    from ..config import DATA_DIR
    from uuid import uuid4
    manifest = bundled_manifest()
    if manifest:
        return apply_with_backup(connection, manifest, DATA_DIR / 'content-backups' / (manifest['revision'] + '-' + uuid4().hex + '.db'))
    return {'units': 0, 'sessions': 0}


def validate_remediated_import(paper, manifest=None):
    from .esq import _question_hash
    manifest = manifest if manifest is not None else bundled_manifest()
    if not manifest:
        return
    changes = {c['unitKey']: c for c in manifest['units']}
    for unit in paper['units']:
        change = changes.get(unit['unitKey'])
        if not change:
            continue
        target = change['after']
        valid = (unit.get('passage', {}).get('blocks', []) == target['shared.content_blocks']
            and unit.get('instructions', '') == target['shared.directions']
            and {o['key']: o['content'] for o in unit.get('candidates', [])} == target['shared.candidates']
            and unit.get('fixed_slots', unit.get('fixedSlots')) == target.get('shared.fixed_slots')
            and [q['questionKey'] for q in unit['questions']] == [q['key'] for q in target['questions']])
        if not valid or any(_question_hash(unit, q, paper['answers'][q['questionKey']]) != change['questionHashes'][q['questionKey']] for q in unit['questions']):
            raise ValueError('此题库包含已修正的旧内容，请使用新版题库包；现有学习记录未修改')
