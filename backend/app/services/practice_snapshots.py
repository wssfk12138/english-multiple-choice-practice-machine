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

def _normalized(value: Any) -> Any:
    if isinstance(value, bool):
        # Python bool is an int subclass, but JSON booleans are not numbers.
        return ('boolean', value)
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        # Native equality already accepts 2 == 2.0 without rounding large ints.
        return value
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(value[key]) for key in sorted(value)}
    return value

def _question_material(payload: dict) -> Any:
    """Scoring material that must match before an old selection can be carried.

    Display labels and option order are runtime-generated; options are aligned
    by stable_key, so only key/content/metadata participate. option_order is
    excluded on purpose — a carried row keeps the order the user originally saw.
    """
    options = sorted(
        (
            {
                'key': option.get('stable_key'),
                'content': option.get('content') or '',
                'metadata': _normalized(option.get('metadata') or {}),
            }
            for option in payload.get('options') or []
            if isinstance(option, dict)
        ),
        key=lambda option: option['key'] or '',
    )
    return _normalized({
        'number': payload.get('number'),
        'stem': payload.get('stem') or '',
        'question_type': payload.get('question_type') or '',
        'score': payload.get('score'),
        'metadata': _normalized(payload.get('metadata') or {}),
        'options': options,
    })

def _snapshot_question_unchanged(saved: dict, current: dict, current_answer: Any) -> bool:
    return _question_material(saved.get('payload') or {}) == _question_material(current) \
        and saved.get('answer') == current_answer

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
        # 转移口径（仅 paper 模式）：已提交篇目整体原样带入——成绩是历史事实，
        # 不因迁移重判；未提交篇目逐题比对快照材料与当前内容，未修正的题保留
        # 旧选择（判分键一致，结果不变），被修正的题重置给用户重做。
        transfer = session['mode'] == 'paper'
        submitted_units = {row['unit_id'] for row in connection.execute(
            'SELECT unit_id FROM practice_unit_submissions WHERE session_id=?', (session_id,))} if transfer else set()
        carried = 0
        reset = 0
        for unit_id in json.loads(session['unit_ids']):
            old_answers = connection.execute(
                'SELECT a.question_id, a.user_answer, a.option_order, a.is_correct, q.external_key, q.answer AS current_answer'
                ' FROM practice_answers a JOIN questions q ON q.id=a.question_id WHERE a.session_id=? AND q.unit_id=?',
                (session_id, unit_id)).fetchall()
            old_by_question = {row['question_id']: row for row in old_answers}
            unit = serialize_unit(connection, unit_id, shuffle_options=bool(session['shuffle_options']), only_question_ids=set(old_by_question))
            unit_key = connection.execute('SELECT external_key FROM units WHERE id=?', (unit_id,)).fetchone()[0]
            saved_unit = next((item for item in snapshot['units'] if item['unit_key'] == unit_key), None)
            carry_whole_unit = unit_id in submitted_units
            for question in unit['questions']:
                old = old_by_question.get(question['id'])
                has_answer = bool(old and str(old['user_answer'] or '').strip())
                saved = next((item for item in (saved_unit or {}).get('questions', [])
                              if item['question_key'] == old['external_key']), None) if old else None
                if transfer and old and (carry_whole_unit or (has_answer and saved and _snapshot_question_unchanged(saved, question, old['current_answer']))):
                    connection.execute(
                        "INSERT INTO practice_answers(session_id,question_id,user_answer,option_order,is_correct,updated_at)"
                        " VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                        (new_id, question['id'], old['user_answer'] or '', old['option_order'] or '[]',
                         old['is_correct'] if carry_whole_unit else None))
                    carried += 1
                else:
                    connection.execute(
                        "INSERT INTO practice_answers(session_id,question_id,user_answer,option_order,updated_at) VALUES(?,?,'',?,CURRENT_TIMESTAMP)",
                        (new_id, question['id'], json.dumps(question['option_order'])))
                    # 只有原本作答过、因修正被清掉的题才计入“需重做”；建会话时就存在的空行不算。
                    if has_answer:
                        reset += 1
        carried_submissions = 0
        for submission in connection.execute(
                'SELECT unit_id, submitted_at, score, max_score FROM practice_unit_submissions WHERE session_id=?',
                (session_id,)).fetchall():
            connection.execute(
                'INSERT INTO practice_unit_submissions(session_id,unit_id,submitted_at,score,max_score) VALUES(?,?,?,?,?)',
                (new_id, submission['unit_id'], submission['submitted_at'], submission['score'], submission['max_score']))
            carried_submissions += 1
        connection.execute("UPDATE practice_sessions SET status='abandoned',updated_at=CURRENT_TIMESTAMP WHERE id=?",(session_id,))
        return {'id':new_id, 'carried':carried, 'reset':reset, 'carried_submissions':carried_submissions}

def historical_retry_snapshot(connection, round_id: int, selected: dict[int, set[int]]) -> dict:
    source = connection.execute('SELECT s.content_snapshot FROM wrong_retry_rounds r JOIN practice_sessions s ON s.id=r.session_id WHERE r.id=? AND r.deleted_at IS NULL', (round_id,)).fetchone()
    if source is None:
        raise ValueError('错题记录的原练习不存在，无法安全恢复')
    snapshot = read_snapshot(source['content_snapshot'])
    if not snapshot.get('revision'):
        return {}
    units = []
    for unit_id, ids in selected.items():
        restored = restore_unit(connection, snapshot, unit_id)
        key = connection.execute('SELECT external_key FROM units WHERE id=?', (unit_id,)).fetchone()[0]
        saved = copy.deepcopy(next(item for item in snapshot['units'] if item['unit_key'] == key))
        saved['questions'] = [item for item, question in zip(saved['questions'], restored['questions']) if question['id'] in ids]
        if len(saved['questions']) != len(ids):
            raise ValueError('历史记录的题目已缺失，无法安全恢复')
        units.append(saved)
    return {**snapshot, 'units': units}


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
