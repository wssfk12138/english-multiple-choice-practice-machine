from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any


def read_snapshot(raw: str) -> dict:
    snapshot = json.loads(raw or '{}')
    if not isinstance(snapshot, dict):
        raise ValueError('练习内容快照格式不兼容，请升级应用后重试')
    if not snapshot:
        return {}
    if snapshot.get('version') != 1 or not isinstance(snapshot.get('units'), list):
        raise ValueError('练习内容快照格式不兼容，请升级应用后重试')
    if not isinstance(snapshot.get('revision'), str) or not snapshot['revision'] or len(snapshot['units']) > 1000:
        raise ValueError('练习内容快照格式无效')
    keys = set()
    for unit in snapshot['units']:
        if not isinstance(unit, dict) or not isinstance(unit.get('unit_key'), str) or not unit['unit_key'] or unit['unit_key'] in keys or not isinstance(unit.get('payload'), dict) or not isinstance(unit.get('questions'), list):
            raise ValueError('练习内容快照篇目无效')
        keys.add(unit['unit_key'])
        questions = set()
        for q in unit['questions']:
            if not isinstance(q, dict) or not isinstance(q.get('question_key'), str) or not q['question_key'] or q['question_key'] in questions or not isinstance(q.get('payload'), dict):
                raise ValueError('练习内容快照评分依据无效')
            score = q['payload'].get('score')
            if not isinstance(q.get('answer'), str) or not isinstance(score, (int,float)) or not 0 <= score < float('inf') or not isinstance(q['payload'].get('options'), list) or not isinstance(q['payload'].get('option_order'), list):
                raise ValueError('练习内容快照评分依据无效')
            questions.add(q['question_key'])
    return snapshot


def snapshots_equivalent(left: dict, right: dict) -> bool:
    def normalize(value, path=()):
        if isinstance(value, bool):
            # Python bool is an int subclass, but JSON booleans are not numbers.
            return ('boolean', value)
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            # Native equality already accepts 2 == 2.0 without rounding large ints.
            return value
        if isinstance(value, list):
            return [normalize(item, path + (str(index),)) for index, item in enumerate(value)]
        if isinstance(value, dict):
            result = {}
            for key in sorted(value):
                if not path and key == 'revision':
                    continue
                if path and path[-1] == 'shared_data' and key in {'content_package_id', 'content_version'}:
                    continue
                result[key] = normalize(value[key], path + (key,))
            return result
        return value
    return normalize(left) == normalize(right)


def capture_session(connection: sqlite3.Connection, session_id: int, revision: str) -> None:
    """Called inside the content-upgrade transaction, BEFORE editing any unit.

    Only stable keys cross devices. Answers stay in private storage, never in
    the practice response. Existing snapshots are immutable.
    """
    from .practice import get_session
    session = connection.execute('SELECT * FROM practice_sessions WHERE id=?', (session_id,)).fetchone()
    if read_snapshot(session['content_snapshot']):
        return
    response = get_session(connection, session_id)
    saved = []
    for unit in response['units']:
        key = connection.execute('SELECT external_key FROM units WHERE id=?', (unit['id'],)).fetchone()[0]
        payload = {k: copy.deepcopy(v) for k, v in unit.items() if k not in {'id', 'paper_id', 'questions', 'submission'}}
        questions = []
        for question in unit['questions']:
            original = connection.execute('SELECT external_key,answer FROM questions WHERE id=?', (question['id'],)).fetchone()
            questions.append({'question_key': original['external_key'], 'answer': original['answer'],
                'payload': {k: copy.deepcopy(v) for k, v in question.items() if k not in {'id', 'user_answer', 'is_correct', 'answer'}}})
        saved.append({'unit_key': key, 'payload': payload, 'questions': questions})
    snapshot = {'version': 1, 'revision': revision, 'units': saved}
    connection.execute('UPDATE practice_sessions SET content_snapshot=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (json.dumps(snapshot, ensure_ascii=False), session_id))


def restore_unit(connection: sqlite3.Connection, snapshot: dict, unit_id: int) -> dict | None:
    if not snapshot:
        return None
    unit = connection.execute('SELECT external_key,paper_id FROM units WHERE id=?', (unit_id,)).fetchone()
    saved = next((item for item in snapshot['units'] if item['unit_key'] == unit['external_key']), None)
    if saved is None:
        raise ValueError('练习内容快照缺少篇目，无法安全恢复')
    payload = copy.deepcopy(saved['payload'])
    payload.update(id=unit_id, paper_id=unit['paper_id'], questions=[])
    for item in saved['questions']:
        question = connection.execute('SELECT id FROM questions WHERE unit_id=? AND external_key=?', (unit_id, item['question_key'])).fetchone()
        if question is None:
            raise ValueError('练习内容快照缺少题目映射，无法安全恢复')
        payload['questions'].append({**copy.deepcopy(item['payload']), 'id': question['id']})
    return payload


def snapshot_grade(connection: sqlite3.Connection, row: Any) -> tuple[str, float]:
    session = connection.execute('SELECT content_snapshot FROM practice_sessions WHERE id=?', (row['session_id'],)).fetchone()
    snapshot = read_snapshot(session['content_snapshot'])
    if not snapshot:
        return row['answer'], row['score']
    keys = connection.execute('SELECT q.external_key AS question_key,u.external_key AS unit_key FROM questions q JOIN units u ON u.id=q.unit_id WHERE q.id=?', (row['question_id'],)).fetchone()
    unit = next((item for item in snapshot['units'] if item['unit_key'] == keys['unit_key']), None)
    item = next((item for item in (unit or {}).get('questions', []) if item['question_key'] == keys['question_key']), None)
    if item is None:
        raise ValueError('练习内容快照缺少评分依据，已停止提交')
    return item['answer'], item['payload']['score']

def snapshot_choice_required(session) -> bool:
    snapshot = read_snapshot(session['content_snapshot'])
    return session['status'] == 'active' and bool(snapshot.get('revision')) and session['snapshot_accepted_revision'] != snapshot['revision']

def require_snapshot_choice(session) -> None:
    if snapshot_choice_required(session):
        raise ValueError('题库内容已修正，请先选择继续旧内容或用修正版重开')

def choose_snapshot(connection: sqlite3.Connection, session_id: int, choice: str) -> dict:
    from .questions import serialize_unit
    if choice not in {'continue', 'restart'}:
        raise ValueError('请选择有效的续练方式')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        session = connection.execute('SELECT * FROM practice_sessions WHERE id=?',(session_id,)).fetchone()
        if session is None or session['status'] != 'active':
            raise ValueError('练习已结束，请刷新后重试')
        snapshot = read_snapshot(session['content_snapshot'])
        if not snapshot.get('revision'):
            raise ValueError('当前练习没有待确认的内容修正')
        if choice == 'continue':
            connection.execute('UPDATE practice_sessions SET snapshot_accepted_revision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(snapshot['revision'],session_id))
            return {'id':session_id}
        new_id = connection.execute("INSERT INTO practice_sessions(mode,paper_id,unit_ids,shuffle_options,candidate_policy_version,updated_at) VALUES(?,?,?,?,1,CURRENT_TIMESTAMP)",
            (session['mode'],session['paper_id'],session['unit_ids'],session['shuffle_options'])).lastrowid
        connection.execute('UPDATE practice_sessions SET content_revisions=? WHERE id=?',(current_content_revisions(connection,json.loads(session['unit_ids'])),new_id))
        for unit_id in json.loads(session['unit_ids']):
            selected = {r[0] for r in connection.execute('SELECT a.question_id FROM practice_answers a JOIN questions q ON q.id=a.question_id WHERE a.session_id=? AND q.unit_id=?',(session_id,unit_id))}
            unit = serialize_unit(connection,unit_id,shuffle_options=bool(session['shuffle_options']),only_question_ids=selected)
            for question in unit['questions']:
                connection.execute("INSERT INTO practice_answers(session_id,question_id,user_answer,option_order,updated_at) VALUES(?,?,'',?,CURRENT_TIMESTAMP)",(new_id,question['id'],json.dumps(question['option_order'])))
        connection.execute("UPDATE practice_sessions SET status='abandoned',updated_at=CURRENT_TIMESTAMP WHERE id=?",(session_id,))
        return {'id':new_id}

def current_content_revisions(connection, unit_ids: list[int]) -> str:
    revisions = {}
    for unit_id in unit_ids:
        unit = connection.execute('SELECT external_key,shared_data FROM units WHERE id=?',(unit_id,)).fetchone()
        revision = json.loads(unit['shared_data'] or '{}').get('content_revision')
        if revision:
            revisions[unit['external_key']] = revision
    return json.dumps(revisions, ensure_ascii=False)

def validate_incoming_session_content(connection, session: dict) -> None:
    snapshot = read_snapshot(session.get('content_snapshot', '{}'))
    revisions = json.loads(session.get('content_revisions') or '{}')
    if not isinstance(revisions, dict):
        raise ValueError('练习内容版本格式无效')
    for unit_id in json.loads(session.get('unit_ids') or '[]'):
        unit = connection.execute('SELECT external_key,shared_data FROM units WHERE id=?',(unit_id,)).fetchone()
        revision = json.loads(unit['shared_data'] or '{}').get('content_revision')
        if not revision:
            continue
        saved = next((u for u in snapshot.get('units',[]) if u['unit_key']==unit['external_key']), None)
        if not saved and revisions.get(unit['external_key']) != revision:
            raise ValueError('旧设备的练习缺少修正前内容快照，请先升级来源设备后同步')
