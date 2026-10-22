"""User-controlled maintenance over the existing durable enrichment queue."""
from .vocabulary_context import safe_object, project_vocabulary

PAUSE_KEY = 'vocabulary_enrichment_paused'

def enrichment_paused(connection):
    row = connection.execute('SELECT value FROM app_settings WHERE key=?', (PAUSE_KEY,)).fetchone()
    return bool(row and row[0] == '1')

def enrichment_status(connection):
    counts = {key: 0 for key in ('queued', 'inflight', 'ready', 'failed')}
    for row in connection.execute('SELECT state,COUNT(*) AS count FROM vocabulary_enrichment_jobs GROUP BY state'):
        if row['state'] in counts:
            counts[row['state']] = row['count']
    profile = connection.execute('SELECT enabled,base_url,default_model FROM ai_profiles ORDER BY enabled DESC,is_default DESC,id LIMIT 1').fetchone()
    configured = bool(profile and profile['enabled'] and profile['base_url'] and profile['default_model'])
    return {**counts, 'paused': enrichment_paused(connection), 'waiting_configuration': not configured}

def update_enrichment_run(connection, action):
    from . import vocabulary_requests as requests
    retry_ids = [row[0] for row in connection.execute("SELECT entry_id FROM vocabulary_enrichment_jobs WHERE state='failed'")] if action == 'retry' else None
    if action not in {'start', 'pause', 'resume', 'retry', 'inventory'}:
        raise ValueError('无效的补缺操作')
    if action in {'pause', 'resume', 'retry'}:
        connection.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (PAUSE_KEY, '1' if action == 'pause' else '0'))
    if action in {'start', 'inventory'}:
        # A ready job can legitimately have empty optional fields. Never requeue it
        # just because a model found no suitable antonym or variant.
        rows = connection.execute("SELECT e.* FROM vocabulary_entries e LEFT JOIN vocabulary_enrichment_jobs j ON j.entry_id=e.id WHERE j.entry_id IS NULL AND e.translation_status='ready'").fetchall()
        for row in rows:
            occurrences = [dict(item) for item in connection.execute('SELECT * FROM vocabulary_occurrences WHERE entry_id=?', (row['id'],))]
            projection = project_vocabulary(dict(row), occurrences)
            missing = not safe_object(row['morphology']).get('lemma')
            if projection['latest_sentence']:
                missing = missing or (not row['user_edited'] and not projection['contextual_meaning'])
            else:
                missing = missing or not safe_object(row['generated_example']).get('sentence')
            if missing:
                connection.execute('INSERT OR IGNORE INTO vocabulary_enrichment_jobs(entry_id) VALUES(?)', (row['id'],))
    if action == 'retry':
        connection.execute("UPDATE vocabulary_enrichment_jobs SET state='queued',error='',updated_at=CURRENT_TIMESTAMP WHERE state='failed'")
    connection.commit()
    if action == 'pause':
        requests.pause(connection)
    elif action != 'inventory':
        try:
            requests.create_run(connection, 'enrichment', retry_ids, explicit=action in {'retry','resume'}, retry=action == 'retry', source=action)
        except (ValueError, LookupError):
            pass  # The existing status reports missing configuration; no send.
    return enrichment_status(connection)
