from __future__ import annotations

import json
import sqlite3

from .resume_title import resume_title

from ..database import get_active_profile_id

VOCABULARY_PROFILE_CONDITION = """EXISTS (SELECT 1 FROM vocabulary_occurrences vo JOIN units vu ON vu.id = vo.unit_id JOIN papers vp ON vp.id = vu.paper_id WHERE vo.entry_id = vocabulary_entries.id AND vp.profile_id = ? AND vp.deleted_at IS NULL)"""


def frequent_question_ids(connection: sqlite3.Connection) -> list[int]:
    return [int(row["question_id"]) for row in connection.execute("""SELECT w.question_id FROM wrong_stats w JOIN questions q ON q.id = w.question_id JOIN units u ON u.id = q.unit_id JOIN papers p ON p.id = u.paper_id WHERE p.profile_id = ? AND p.deleted_at IS NULL AND w.wrong_count > 0 AND (w.manually_frequent = 1 OR w.wrong_count >= 3 OR (json_array_length(w.recent_results) >= 5 AND (SELECT COUNT(*) FROM json_each(w.recent_results) WHERE value = 0) >= 3))""", (get_active_profile_id(connection),)).fetchall()]


def study_todos(connection: sqlite3.Connection) -> dict:
    profile_id = get_active_profile_id(connection)
    resume = connection.execute("""SELECT s.id, s.mode,
  (SELECT json_object('year', p.year, 'paper', p.title, 'unit', u.title)
   FROM json_each(s.unit_ids) ids JOIN units u ON u.id = ids.value
   JOIN papers p ON p.id = u.paper_id ORDER BY u.sequence, u.id LIMIT 1) AS title
FROM practice_sessions s
WHERE s.status = 'active'
  AND EXISTS (SELECT 1 FROM json_each(s.unit_ids) ids JOIN units u ON u.id = ids.value
    JOIN papers p ON p.id = u.paper_id WHERE p.profile_id = ? AND p.status = 'published' AND p.deleted_at IS NULL)
  AND NOT EXISTS (SELECT 1 FROM json_each(s.unit_ids) ids LEFT JOIN units u ON u.id = ids.value
    LEFT JOIN papers p ON p.id = u.paper_id WHERE p.id IS NULL OR p.profile_id <> ? OR p.status <> 'published' OR p.deleted_at IS NOT NULL)
ORDER BY (SELECT COUNT(*) FROM practice_unit_submissions pus WHERE pus.session_id = s.id) DESC,
  (SELECT COUNT(*) FROM practice_answers pa WHERE pa.session_id = s.id AND TRIM(COALESCE(pa.user_answer, '')) <> '') DESC,
  s.started_at DESC, s.id DESC LIMIT 1""", (profile_id, profile_id)).fetchone()
    due = connection.execute("""SELECT COUNT(*) AS count FROM vocabulary_entries WHERE translation_status = 'ready' AND (next_review_at IS NULL OR datetime(next_review_at) <= CURRENT_TIMESTAMP) AND study_status <> 'mastered' AND EXISTS (SELECT 1 FROM vocabulary_occurrences vo JOIN units vu ON vu.id = vo.unit_id JOIN papers vp ON vp.id = vu.paper_id WHERE vo.entry_id = vocabulary_entries.id AND vp.profile_id = ? AND vp.deleted_at IS NULL)""", (profile_id,)).fetchone()
    return {"profile_id": profile_id, "resume_session": {**dict(resume), "title": resume_title(resume["mode"], json.loads(resume["title"] or "{}"))} if resume else None, "review_count": int(due["count"]), "frequent_count": len(frequent_question_ids(connection))}
