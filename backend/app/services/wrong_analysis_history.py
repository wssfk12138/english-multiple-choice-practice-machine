"""Read saved analyses within the active question bank and trash lifecycle."""
import json
from ..database import get_active_profile_id


def report_unit_ids(row):
    try:
        values = json.loads(row['unit_ids'] or '[]')
        if not isinstance(values, list):
            values = []
        return {int(value) for value in values} or {int(value) for value in row['scope_key'].split(',') if value.strip()}
    except (ValueError, TypeError):
        return set()


def history(connection, unit_id, offset=0, limit=50):
    active = get_active_profile_id(connection)
    unit = connection.execute('SELECT u.id AS unit_id,u.title AS unit_title,p.year FROM units u JOIN papers p ON p.id=u.paper_id WHERE u.id=? AND p.profile_id=? AND p.deleted_at IS NULL', (unit_id, active)).fetchone()
    if unit is None:
        raise LookupError('篇目不存在或已在回收站')
    allowed = {row[0] for row in connection.execute('SELECT u.id FROM units u JOIN papers p ON p.id=u.paper_id WHERE p.profile_id=? AND p.deleted_at IS NULL', (active,))}
    hidden = {row[0] for row in connection.execute('SELECT report_id FROM wrong_analysis_hidden WHERE unit_id=?', (unit_id,))}
    reports = []
    total = 0
    for row in connection.execute('SELECT id,unit_ids,scope_key FROM wrong_analysis_reports ORDER BY id DESC'):
        ids = report_unit_ids(row)
        if unit_id not in ids or not ids.issubset(allowed) or row['id'] in hidden:
            continue
        total += 1
        if total <= offset or len(reports) >= limit:
            continue
        item = dict(connection.execute("SELECT id,scope_key,unit_ids,scope_title,question_count,aggregate_data,report,model_name,created_at FROM wrong_analysis_reports WHERE id=?", (row["id"],)).fetchone())
        try:
            item['aggregate'] = json.loads(item.pop('aggregate_data') or '{}')
        except (ValueError, TypeError):
            item['aggregate'] = {}
        item.pop('input_snapshot', None)
        reports.append(item)
    return {'unit': dict(unit), 'items': reports, 'total': total, 'offset': offset, 'limit': limit}


def archive_reports(connection, unit_id, trash_id):
    for row in connection.execute('SELECT id,unit_ids,scope_key FROM wrong_analysis_reports'):
        if unit_id in report_unit_ids(row):
            connection.execute('INSERT OR IGNORE INTO wrong_analysis_hidden(report_id,unit_id,trash_id) VALUES(?,?,?)', (row['id'], unit_id, trash_id))


def purge_orphan_reports(connection):
    existing = {row[0] for row in connection.execute('SELECT id FROM units')}
    hidden = {(row[0], row[1]) for row in connection.execute('SELECT report_id,unit_id FROM wrong_analysis_hidden WHERE trash_id IS NULL')}
    for row in connection.execute('SELECT * FROM wrong_analysis_reports').fetchall():
        ids = report_unit_ids(row)
        if ids and not any(uid in existing and (row['id'], uid) not in hidden for uid in ids):
            connection.execute('DELETE FROM wrong_analysis_reports WHERE id=?', (row['id'],))
