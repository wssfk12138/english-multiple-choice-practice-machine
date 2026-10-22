"""Durable, bounded vocabulary requests. No prompt, key or response is persisted."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException

LIMIT = 4
RUN_SECONDS = 600
REQUEST_SECONDS = 240
CURRENT = ContextVar('vocabulary_request', default=None)
_worker_lock = threading.Lock()
_clocks = {}
_local_failure = False


def now():
    return int(time.time() * 1000)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@contextmanager
def atomic(db):
    if db.in_transaction:
        raise RuntimeError('Vocabulary request requires an independent transaction')
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def migrate(db):
    db.execute('SAVEPOINT vocabulary_requests_migration')
    try:
        _migrate(db)
    except BaseException:
        db.execute('ROLLBACK TO vocabulary_requests_migration')
        raise
    finally:
        db.execute('RELEASE vocabulary_requests_migration')


def _migrate(db):
    first = not db.execute("SELECT 1 FROM app_migrations WHERE migration_key='vocabulary-requests-v1'").fetchone()
    statements = [
        "CREATE TABLE IF NOT EXISTS vocabulary_request_gate(id INTEGER PRIMARY KEY CHECK(id=1),reason TEXT NOT NULL DEFAULT '',allowed_run TEXT,used INTEGER NOT NULL DEFAULT 0,started_at INTEGER NOT NULL DEFAULT 0)",
        'INSERT OR IGNORE INTO vocabulary_request_gate(id) VALUES(1)',
        "CREATE TABLE IF NOT EXISTS vocabulary_request_runs(id TEXT PRIMARY KEY,command_id TEXT UNIQUE NOT NULL,command_signature TEXT NOT NULL,task TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'running',config_hash TEXT NOT NULL,config_summary TEXT NOT NULL,source TEXT NOT NULL,used INTEGER NOT NULL DEFAULT 0,started_at INTEGER NOT NULL,finished_at INTEGER,explicit INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS vocabulary_request_members(run_id TEXT NOT NULL REFERENCES vocabulary_request_runs(id),entry_id INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'queued',PRIMARY KEY(run_id,entry_id))",
        "CREATE TABLE IF NOT EXISTS vocabulary_request_attempts(id TEXT PRIMARY KEY,run_id TEXT,task TEXT NOT NULL,state TEXT NOT NULL,ids TEXT NOT NULL,fingerprints TEXT NOT NULL DEFAULT '{}',result TEXT,category TEXT,created_at INTEGER NOT NULL,finished_at INTEGER)",
        'CREATE TABLE IF NOT EXISTS vocabulary_request_heads(task TEXT NOT NULL,entry_id INTEGER NOT NULL,attempt_id TEXT NOT NULL,PRIMARY KEY(task,entry_id))',
        'CREATE INDEX IF NOT EXISTS idx_vocabulary_request_members ON vocabulary_request_members(state,run_id)',
        'CREATE INDEX IF NOT EXISTS idx_vocabulary_request_attempts ON vocabulary_request_attempts(state)',
        "INSERT OR IGNORE INTO app_migrations(migration_key) VALUES('vocabulary-requests-v1')",
    ]
    for statement in statements:
        db.execute(statement)
    if first and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vocabulary_ai_requests'").fetchone():
        legacy = db.execute("SELECT COUNT(*),CAST(strftime('%s',MIN(at)) AS INTEGER)*1000 FROM vocabulary_ai_requests WHERE datetime(at)>=datetime('now','-10 minutes')").fetchone()
        if legacy[0]:
            db.execute("UPDATE vocabulary_request_gate SET used=?,started_at=?,reason=? WHERE id=1", (min(LIMIT,legacy[0]),legacy[1] or now(),'budget' if legacy[0]>=LIMIT else ''))



def configuration(db):
    from . import model_keys
    row = db.execute('SELECT * FROM ai_profiles ORDER BY enabled DESC,is_default DESC,id LIMIT 1').fetchone()
    value = dict(row) if row else {}
    if not value.get('enabled') or not value.get('base_url') or not value.get('default_model'):
        raise ValueError('请先配置并启用用于词汇的默认模型')
    fields = ['id', 'adapter', 'base_url', 'default_model', 'temperature', 'reasoning_effort', 'system_prompt', 'enabled', 'api_key_encrypted']
    digest = hashlib.sha256(dump([value.get(key) for key in fields]).encode())
    try:
        key_file = model_keys._path(db)
        if key_file.is_file():
            digest.update(key_file.read_bytes())
    except ValueError:
        # In-memory fixtures have no credential file. They never use real keys.
        pass
    return {'hash': digest.hexdigest(), 'summary': dump({key: value.get(key) for key in ('id', 'adapter', 'default_model', 'temperature', 'reasoning_effort')})}


def fingerprint(db, entry_id):
    entry = db.execute('SELECT * FROM vocabulary_entries WHERE id=?', (entry_id,)).fetchone()
    history = [dict(row) for row in db.execute('SELECT * FROM vocabulary_occurrences WHERE entry_id=? ORDER BY id', (entry_id,))]
    return hashlib.sha256(dump([dict(entry) if entry else None, history]).encode()).hexdigest()


def eligible(db, task, ids=None, retry=False):
    if ids == []:
        return []
    params = ids or []
    selection = ' AND v.id IN (' + ','.join('?' for _ in ids) + ')' if ids is not None else ''
    if task == 'translation':
        states = "'pending','queued','failed'" if retry else "'pending','queued'"
        query = f'SELECT v.id FROM vocabulary_entries v WHERE v.user_edited=0 AND v.translation_status IN ({states}){selection} ORDER BY v.updated_at,v.id'
    else:
        states = "'failed','queued'" if retry else "'queued'"
        query = f'SELECT v.id FROM vocabulary_entries v JOIN vocabulary_enrichment_jobs j ON j.entry_id=v.id WHERE j.state IN ({states}){selection} ORDER BY j.updated_at,v.id'
    return [row[0] for row in db.execute(query, params)]


def stop(db, reason):
    _clocks.clear()
    db.execute("UPDATE vocabulary_request_runs SET state='stopped' WHERE state='running'")
    db.execute("UPDATE vocabulary_request_gate SET reason=CASE WHEN reason='user' THEN reason ELSE ? END,allowed_run=NULL WHERE id=1", (reason,))


def recover(db):
    """Called exactly at process startup; never used by a status/GET request."""
    with atomic(db):
        db.execute("UPDATE vocabulary_request_attempts SET state='unknown',category='process_interrupted' WHERE state IN ('intent','inflight')")
        for task, query in [('translation', "SELECT id FROM vocabulary_entries WHERE translation_status='translating'"), ('enrichment', "SELECT entry_id FROM vocabulary_enrichment_jobs WHERE state='inflight'")]:
            for row in db.execute(query).fetchall():
                exists = db.execute("SELECT 1 FROM vocabulary_request_heads h JOIN vocabulary_request_attempts a ON h.attempt_id=a.id WHERE h.task=? AND h.entry_id=? AND a.state='unknown'", (task, row[0])).fetchone()
                if not exists:
                    attempt = uuid.uuid4().hex
                    db.execute("INSERT INTO vocabulary_request_attempts(id,task,state,ids,category,created_at) VALUES(?,?,'unknown',?,'legacy_inflight',?)", (attempt, task, dump([row[0]]), now()))
                    db.execute('INSERT OR REPLACE INTO vocabulary_request_heads VALUES(?,?,?)', (task, row[0], attempt))
        db.execute("UPDATE vocabulary_entries SET translation_status='failed',translation_error='上次请求结果未知，请先核查费用后重试' WHERE translation_status='translating'")
        db.execute("UPDATE vocabulary_enrichment_jobs SET state='failed',error='上次请求结果未知，请先核查费用后重试' WHERE state='inflight'")
        if db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state='unknown'").fetchone():
            stop(db, 'unknown')
            db.execute("UPDATE vocabulary_request_gate SET reason='unknown' WHERE id=1")
        elif not db.execute("SELECT 1 FROM app_migrations WHERE migration_key='vocabulary-request-pause-v1'").fetchone():
            paused = db.execute("SELECT value FROM app_settings WHERE key='vocabulary_enrichment_paused'").fetchone()
            if paused and paused[0] == '1':
                stop(db, 'user')
        db.execute("INSERT OR IGNORE INTO app_migrations(migration_key) VALUES('vocabulary-request-pause-v1')")


def create_run(db, task, ids=None, *, explicit=False, retry=False, command_id=None, source='automatic', config=None):
    if task not in {'translation', 'enrichment'}:
        raise HTTPException(400, '请明确词汇任务类型')
    ids = list(dict.fromkeys(ids)) if ids is not None else None
    if config is None:
        # Complete any legacy credential migration before reserving an attempt.
        # Rechecks inside the writer transaction only hash local state and never
        # invoke a migration or commit behind the transaction's back.
        filename = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
        if filename:
            from .ai_client import _settings_with_key
            _settings_with_key(db)
        config = configuration(db)
    command_id = command_id or uuid.uuid4().hex
    signature = dump([task, ids, source, explicit, retry])
    with atomic(db):
        prior = db.execute('SELECT id,command_signature FROM vocabulary_request_runs WHERE command_id=?', (command_id,)).fetchone()
        if prior:
            if prior['command_signature'] != signature:
                raise HTTPException(409, '该操作标识已用于其他范围')
            return prior['id']
        gate = dict(db.execute('SELECT * FROM vocabulary_request_gate WHERE id=1').fetchone())
        active = db.execute("SELECT * FROM vocabulary_request_runs WHERE state='running' ORDER BY started_at,id LIMIT 1").fetchone()
        unknown = db.execute("SELECT * FROM vocabulary_request_attempts WHERE state='unknown'").fetchall()
        bulk = explicit and retry and task == 'translation' and source == 'retry-all' and ids is None
        if active and bulk:
            if active['config_hash'] != config['hash'] or unknown:
                raise HTTPException(409, '当前配置已变化或存在未知请求，请先核查任务状态')
            members = eligible(db, task, None, True)
            receipt = uuid.uuid4().hex
            target = active['id'] if active['task'] == task else receipt
            db.execute('INSERT INTO vocabulary_request_runs(id,command_id,command_signature,task,config_hash,config_summary,source,started_at,explicit,state) VALUES(?,?,?,?,?,?,?,?,0,?)', (receipt, command_id, signature, task, config['hash'], config['summary'], source, active['started_at'], 'done' if active['task'] == task else 'running'))
        else:
            if active or db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state IN ('intent','inflight')").fetchone():
                if explicit:
                    raise HTTPException(409, '当前词汇请求尚未结束，请先暂停或等待')
                return None
            scoped_retry = explicit and retry and (ids is not None or bulk)
            if unknown and not scoped_retry:
                if explicit:
                    raise HTTPException(409, '上次请求结果未知，重试可能重复产生费用，请先核查')
                return None
            if gate['reason'] and not explicit:
                return None
            members = eligible(db, task, ids, retry)
            if not members:
                return None
            if scoped_retry:
                for attempt in unknown:
                    if attempt['task'] == task and set(json.loads(attempt['ids'])) & set(members):
                        db.execute("UPDATE vocabulary_request_attempts SET state='acknowledged',finished_at=? WHERE id=?", (now(), attempt['id']))
            if not explicit and (gate['used'] >= LIMIT or gate['started_at'] and (now() < gate['started_at'] or now() - gate['started_at'] >= RUN_SECONDS * 1000)):
                stop(db, 'budget')
                return None
            target = uuid.uuid4().hex
            db.execute('INSERT INTO vocabulary_request_runs(id,command_id,command_signature,task,config_hash,config_summary,source,started_at,explicit) VALUES(?,?,?,?,?,?,?,?,?)', (target, command_id, signature, task, config['hash'], config['summary'], source, now(), int(explicit)))
            if explicit:
                db.execute('UPDATE vocabulary_request_gate SET allowed_run=?,used=0,started_at=? WHERE id=1', (target, now()))
            else:
                db.execute('UPDATE vocabulary_request_gate SET started_at=CASE WHEN started_at=0 THEN ? ELSE started_at END WHERE id=1', (now(),))
        for entry_id in members:
            db.execute("UPDATE vocabulary_request_members SET state='superseded' WHERE entry_id=? AND state='queued' AND run_id<>? AND run_id IN (SELECT id FROM vocabulary_request_runs WHERE task=?)", (entry_id, target, task))
            db.execute("INSERT INTO vocabulary_request_members(run_id,entry_id) VALUES(?,?) ON CONFLICT(run_id,entry_id) DO UPDATE SET state='queued'", (target, entry_id))
            if task == 'translation':
                db.execute("UPDATE vocabulary_entries SET translation_status='queued',translation_error='' WHERE id=? AND user_edited=0", (entry_id,))
            else:
                db.execute("UPDATE vocabulary_enrichment_jobs SET state='queued',error='' WHERE entry_id=?", (entry_id,))
        if bulk:
            db.execute("UPDATE vocabulary_request_gate SET reason='' WHERE id=1")
        return target


def claim(db, config):
    with atomic(db):
        gate = dict(db.execute('SELECT * FROM vocabulary_request_gate WHERE id=1').fetchone())
        if db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state IN ('intent','inflight')").fetchone():
            return None
        runs = db.execute("SELECT * FROM vocabulary_request_runs WHERE state='running' AND (?='' OR id=?) ORDER BY started_at,id", (gate['reason'], gate['allowed_run'] or '')).fetchall()
        for run in runs:
            scoped = run['explicit'] and gate['allowed_run'] == run['id'] and json.loads(run['command_signature'])[4]
            if not scoped and db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state='unknown'").fetchone():
                return None
            wall, monotonic = _clocks.setdefault(run['id'], (now(), time.monotonic()))
            elapsed = max(now() - min(run['started_at'], gate['started_at']), wall - min(run['started_at'], gate['started_at']) + (time.monotonic() - monotonic) * 1000)
            if config['hash'] != run['config_hash']:
                stop(db, 'configuration')
                return None
            if now() < run['started_at'] or elapsed > (RUN_SECONDS - REQUEST_SECONDS) * 1000 or gate['used'] >= LIMIT or run['used'] >= LIMIT:
                stop(db, 'budget')
                return None
            candidates = [row[0] for row in db.execute("SELECT entry_id FROM vocabulary_request_members WHERE run_id=? AND state='queued' ORDER BY entry_id", (run['id'],))]
            members = eligible(db, run['task'], candidates)
            for entry_id in set(candidates) - set(members):
                db.execute("UPDATE vocabulary_request_members SET state='skipped' WHERE run_id=? AND entry_id=?", (run['id'], entry_id))
            if not members:
                db.execute("UPDATE vocabulary_request_runs SET state='done',finished_at=? WHERE id=?", (now(), run['id']))
                db.execute('UPDATE vocabulary_request_gate SET allowed_run=NULL WHERE allowed_run=?', (run['id'],))
                _clocks.pop(run['id'], None)
                continue
            ids = members[:8 if run['task'] == 'translation' else 1]
            attempt = uuid.uuid4().hex
            for entry_id in ids:
                db.execute("UPDATE vocabulary_request_members SET state='intent' WHERE run_id=? AND entry_id=?", (run['id'], entry_id))
                db.execute('INSERT OR REPLACE INTO vocabulary_request_heads VALUES(?,?,?)', (run['task'], entry_id, attempt))
                if run['task'] == 'translation':
                    db.execute("UPDATE vocabulary_entries SET translation_status='translating',updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_edited=0", (entry_id,))
                else:
                    db.execute("UPDATE vocabulary_enrichment_jobs SET state='inflight',error='' WHERE entry_id=?", (entry_id,))
            fingerprints = {str(entry_id): fingerprint(db, entry_id) for entry_id in ids}
            db.execute("INSERT INTO vocabulary_request_attempts(id,run_id,task,state,ids,fingerprints,created_at) VALUES(?,?,?,'intent',?,?,?)", (attempt, run['id'], run['task'], dump(ids), dump(fingerprints), now()))
            db.execute('UPDATE vocabulary_request_runs SET used=used+1 WHERE id=?', (run['id'],))
            db.execute('UPDATE vocabulary_request_gate SET used=used+1 WHERE id=1')
            return {'id': attempt, 'run_id': run['id'], 'task': run['task'], 'ids': ids, 'hash': config['hash'], 'fingerprints': fingerprints}
    return None


class RequestAbort(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('词汇请求状态或内容已变化')


class Execution:
    def __init__(self, claim):
        self.claim = claim
        self.sent = False
        self.responded = False
        self.saving = False
        self.failure = ''

    def check(self, db):
        attempt = db.execute('SELECT state FROM vocabulary_request_attempts WHERE id=?', (self.claim['id'],)).fetchone()
        if not attempt or attempt[0] not in {'intent', 'inflight', 'unknown', 'acknowledged'}:
            raise RequestAbort('superseded')
        if configuration(db)['hash'] != self.claim['hash']:
            raise RequestAbort('configuration')
        for entry_id in self.claim['ids']:
            head = db.execute('SELECT attempt_id FROM vocabulary_request_heads WHERE task=? AND entry_id=?', (self.claim['task'], entry_id)).fetchone()
            if not head or head[0] != self.claim['id'] or fingerprint(db, entry_id) != self.claim['fingerprints'][str(entry_id)]:
                raise RequestAbort('content_changed')

    def before_send(self, db):
        with atomic(db):
            self.check(db)
            run = db.execute('SELECT state FROM vocabulary_request_runs WHERE id=?', (self.claim['run_id'],)).fetchone()
            gate = db.execute('SELECT reason,allowed_run FROM vocabulary_request_gate WHERE id=1').fetchone()
            if run[0] != 'running' or gate[0] and gate[1] != self.claim['run_id']:
                raise RequestAbort('user' if gate[0] == 'user' else 'configuration')
            if not db.execute("UPDATE vocabulary_request_attempts SET state='inflight' WHERE id=? AND state='intent'", (self.claim['id'],)).rowcount:
                raise RequestAbort('superseded')
        self.sent = True

    def after_response(self, db):
        self.responded = True
        db.execute('BEGIN IMMEDIATE')
        self.check(db)
        self.saving = True


def finish(db, execution, category=''):
    attempt = execution.claim
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    active = db.execute('SELECT state FROM vocabulary_request_attempts WHERE id=?', (attempt['id'],)).fetchone()
    if not active or active[0] in {'done', 'failed'}:
        db.rollback()
        return 0
    written, invalid = [], {}
    for entry_id in attempt['ids']:
        head = db.execute('SELECT attempt_id FROM vocabulary_request_heads WHERE task=? AND entry_id=?', (attempt['task'], entry_id)).fetchone()
        if not head or head[0] != attempt['id']:
            continue
        if attempt['task'] == 'translation':
            entry = db.execute('SELECT translation_status FROM vocabulary_entries WHERE id=?', (entry_id,)).fetchone()
            success = not category and entry and entry[0] == 'ready'
        else:
            success = not category
        if success:
            written.append(entry_id)
        else:
            invalid[str(entry_id)] = category or 'validation'
            if attempt['task'] == 'translation':
                db.execute("UPDATE vocabulary_entries SET translation_status='failed',translation_error='词汇请求已停止，请查看任务状态后重试' WHERE id=? AND user_edited=0 AND translation_status='translating'", (entry_id,))
        db.execute('UPDATE vocabulary_request_members SET state=? WHERE run_id=? AND entry_id=?', ('done' if success else 'queued' if not execution.sent else 'failed', attempt['run_id'], entry_id))
        if attempt['task'] == 'enrichment':
            db.execute("UPDATE vocabulary_enrichment_jobs SET state=?,error=? WHERE entry_id=? AND state='inflight'", ('ready' if success else 'failed', '' if success else '词汇请求已停止，请先核查任务状态', entry_id))
    if invalid and not category:
        category = 'validation'
    state = 'unknown' if category == 'unknown' else 'failed' if invalid else 'done'
    if not execution.sent:
        state = 'done'
        category = 'not_sent_' + (category or 'configuration')
        for entry_id in attempt['ids']:
            if attempt['task'] == 'translation':
                db.execute("UPDATE vocabulary_entries SET translation_status='queued',translation_error='' WHERE id=? AND translation_status IN ('translating','failed') AND user_edited=0", (entry_id,))
            else:
                db.execute("UPDATE vocabulary_enrichment_jobs SET state='queued',error='' WHERE entry_id=? AND state IN ('inflight','failed')", (entry_id,))
    db.execute('UPDATE vocabulary_request_attempts SET state=?,category=?,result=?,finished_at=? WHERE id=?', (state, category, dump({'writtenIds': written, 'invalid': invalid}), now(), attempt['id']))
    if category:
        stop(db, category.removeprefix('not_sent_'))
    elif not db.execute("SELECT 1 FROM vocabulary_request_members WHERE run_id=? AND state='queued'", (attempt['run_id'],)).fetchone():
        db.execute("UPDATE vocabulary_request_runs SET state='done',finished_at=? WHERE id=?", (now(), attempt['run_id']))
        db.execute('UPDATE vocabulary_request_gate SET allowed_run=NULL WHERE allowed_run=?', (attempt['run_id'],))
    elif db.execute('SELECT used FROM vocabulary_request_gate WHERE id=1').fetchone()[0] >= LIMIT:
        stop(db, 'budget')
    db.commit()
    return len(written)


def status(db):
    gate = dict(db.execute('SELECT * FROM vocabulary_request_gate WHERE id=1').fetchone())
    unknown = [{**dict(row), 'ids': json.loads(row['ids'])} for row in db.execute("SELECT id,task,ids,created_at,category FROM vocabulary_request_attempts WHERE state='unknown' ORDER BY created_at")]
    runs = [dict(row) for row in db.execute("SELECT r.id,r.task,r.state,r.used,r.explicit,COUNT(m.entry_id) remaining FROM vocabulary_request_runs r LEFT JOIN vocabulary_request_members m ON m.run_id=r.id AND m.state='queued' WHERE r.state IN ('running','stopped') GROUP BY r.id ORDER BY r.started_at")]
    pending = {task: eligible(db, task) for task in ('translation', 'enrichment')}
    retry = {'translation': [row[0] for row in db.execute("SELECT id FROM vocabulary_entries WHERE user_edited=0 AND translation_status='failed' ORDER BY id")], 'enrichment': [row[0] for row in db.execute("SELECT entry_id FROM vocabulary_enrichment_jobs WHERE state='failed' ORDER BY entry_id")]}
    profile = db.execute("SELECT 1 FROM ai_profiles WHERE enabled=1 AND TRIM(base_url)<>'' AND TRIM(default_model)<>'' LIMIT 1").fetchone()
    waiting = not bool(profile)
    active = next((run for run in runs if run['state'] == 'running' and (not gate['reason'] or run['id'] == gate['allowed_run'])), None)
    return {'reason': 'unknown' if unknown else gate['reason'], 'used': gate['used'], 'limit': LIMIT, 'active': bool(active), 'task': active['task'] if active else '', 'unknown': unknown, 'runs': runs, 'pending': {task: len(ids) for task, ids in pending.items()}, 'pendingIds': pending, 'retryIds': retry, 'waitingConfiguration': waiting, 'transmissionActive': bool(db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state IN ('intent','inflight')").fetchone())}


def pause(db):
    with atomic(db):
        stop(db, 'user')
        db.execute("UPDATE vocabulary_request_gate SET reason='user' WHERE id=1")


def acknowledge(db, attempt_id):
    with atomic(db):
        if not db.execute("UPDATE vocabulary_request_attempts SET state='acknowledged',finished_at=? WHERE id=? AND state='unknown'", (now(), attempt_id)).rowcount:
            raise HTTPException(409, '该未知批次已处理，请刷新')
        if not db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state='unknown'").fetchone():
            db.execute("UPDATE vocabulary_request_gate SET reason=CASE WHEN reason='unknown' THEN 'user' ELSE reason END,allowed_run=NULL WHERE id=1")


def work():
    global _local_failure
    from . import vocabulary
    if _local_failure:
        return {'translated':0,'remaining':0}
    if not _worker_lock.acquire(blocking=False):
        return {'translated': 0, 'remaining': 0}
    translated = 0
    try:
        with vocabulary.connect() as db:
            while True:
                try:
                    config = configuration(db)
                except (ValueError, LookupError):
                    return {'translated': translated, 'remaining': len(eligible(db, 'translation'))}
                attempt = claim(db, config)
                if not attempt:
                    return {'translated': translated, 'remaining': len(eligible(db, 'translation'))}
                execution = Execution(attempt)
                token = CURRENT.set(execution)
                category = ''
                try:
                    if attempt['task'] == 'translation':
                        vocabulary._translate_vocabulary_batch(db, vocabulary._translation_rows(db, attempt['ids']))
                    else:
                        vocabulary.enrich_vocabulary_entry(db, attempt['ids'][0])
                    if not execution.sent:
                        raise RequestAbort('configuration')
                    written = finish(db, execution, execution.failure)
                    translated += written if attempt['task'] == 'translation' else 0
                except BaseException as error:
                    db.rollback()
                    if isinstance(error, RequestAbort):
                        category = error.reason
                    elif not execution.sent:
                        category = 'configuration'
                    elif not execution.responded:
                        category = 'unknown'
                    else:
                        category = 'save' if isinstance(error, sqlite3.Error) else 'parse' if isinstance(error, json.JSONDecodeError) else 'validation'
                    try:
                        finish(db, execution, category)
                    except BaseException:
                        _local_failure = True
                        db.rollback()
                        # The durable inflight record remains for startup recovery.
                        raise
                    if not isinstance(error, Exception):
                        raise
                    return {'translated': translated, 'remaining': len(eligible(db, 'translation'))}
                finally:
                    CURRENT.reset(token)
                if category:
                    return {'translated': translated, 'remaining': len(eligible(db, 'translation'))}
    finally:
        _worker_lock.release()


def automatic(task, ids=None):
    from .vocabulary import connect
    with connect() as db:
        try:
            create_run(db, task, ids)
        except (ValueError, LookupError):
            return {'translated': 0, 'remaining': len(eligible(db, 'translation'))}
    return work()


def commit_content(db):
    if CURRENT.get() is None:
        db.commit()


def queue_state(db):
    gate = db.execute('SELECT reason,allowed_run FROM vocabulary_request_gate WHERE id=1').fetchone()
    current = db.execute("SELECT task FROM vocabulary_request_runs WHERE state='running' AND (?='' OR id=?) ORDER BY started_at,id LIMIT 1", (gate['reason'], gate['allowed_run'] or '')).fetchone()
    unknown = db.execute("SELECT 1 FROM vocabulary_request_attempts WHERE state='unknown' LIMIT 1").fetchone()
    configured = db.execute("SELECT 1 FROM ai_profiles WHERE enabled=1 AND TRIM(base_url)<>'' AND TRIM(default_model)<>'' LIMIT 1").fetchone()
    return {'reason':'unknown' if unknown else gate['reason'], 'active':bool(current), 'task':current[0] if current else '', 'waitingConfiguration':not bool(configured), 'localFailure':_local_failure}
