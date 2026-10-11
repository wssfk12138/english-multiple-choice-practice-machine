import json
import sqlite3
import unittest
from fastapi import HTTPException

from backend.app import database
from backend.app.routers import vocabulary as vocabulary_router
from backend.app.services import daily_study, vocabulary as vocabulary_service


class DailyStudyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        self.db.execute(
            "INSERT INTO vocabulary_entries(term,normalized_term,common_meaning,contextual_meaning,translation_status,encounter_count,last_seen_at) VALUES"
            "('corruption','corruption','腐败','the corruption of…','ready',3,'2026-10-10 00:00:00'),"
            "('abet','abet','教唆','','pending',1,'2026-09-01 00:00:00'),"
            "('zealous','zealous','热心的','','ready',1,'2026-10-11 00:00:00')"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_catalog_orders_by_recency_and_frequency_with_identity(self):
        result = daily_study.daily_study_catalog(self.db)
        self.assertEqual([item['term'] for item in result['items']], ['corruption', 'zealous', 'abet'])
        self.assertEqual(result['items'][0]['identity'], 'nt:corruption')
        self.assertEqual(json.loads(result['scope_key'])[0], 'daily-study')
        self.assertTrue(result['revision'].startswith('v2:'))

    def test_catalog_filters_status_and_search(self):
        review = daily_study.daily_study_catalog(self.db, status='review')
        self.assertEqual([item['term'] for item in review['items']], ['corruption', 'zealous'])
        pending = daily_study.daily_study_catalog(self.db, status='pending')
        self.assertEqual([item['term'] for item in pending['items']], ['abet'])
        found = daily_study.daily_study_catalog(self.db, search='corr')
        self.assertEqual([item['term'] for item in found['items']], ['corruption'])
        self.assertNotEqual(found['scope_key'], review['scope_key'])

    def test_scope_current_requires_profile_occurrence(self):
        profile = database.get_active_profile_id(self.db)
        paper = self.db.execute(
            "INSERT INTO papers(profile_id,external_key,year,title,status) VALUES(?,'p',2016,'Exam','published')",
            (profile,),
        ).lastrowid
        unit = self.db.execute(
            "INSERT INTO units(paper_id,external_key,unit_type,title,sequence) VALUES(?,'u','reading','Text',1)",
            (paper,),
        ).lastrowid
        entry_id = self.db.execute("SELECT id FROM vocabulary_entries WHERE term='corruption'").fetchone()[0]
        self.db.execute(
            "INSERT INTO vocabulary_occurrences(entry_id,unit_id,surface_form,context_sentence) VALUES(?,?, 'corruption','corrupt practices')",
            (entry_id, unit),
        )
        self.db.commit()
        scoped = daily_study.daily_study_catalog(self.db, scope='current')
        self.assertEqual([item['term'] for item in scoped['items']], ['corruption'])
        self.assertEqual(json.loads(scoped['scope_key'])[2], ['profile', profile])
        metadata = daily_study.daily_study_metadata(self.db, 'all', '', 'current')
        catalog = daily_study.daily_study_catalog(self.db, scope='current')
        self.assertEqual(metadata, {key: catalog[key] for key in ('scope_key', 'revision')})

    def test_detail_exposes_daily_study_identity(self):
        entry_id = self.db.execute("SELECT id FROM vocabulary_entries WHERE term='corruption'").fetchone()[0]
        payload = vocabulary_service._serialize_entry(self.db, entry_id)
        self.assertEqual(payload['daily_study_identity'], 'nt:corruption')

    def test_router_routes_resolve_before_entry_param(self):
        catalog = vocabulary_router.daily_study_catalog(status='all', search='', scope='all', connection=self.db)
        self.assertIn('items', catalog)
        status = vocabulary_router.daily_study_status(status='all', search='', scope='all', connection=self.db)
        self.assertEqual(set(status), {'scope_key', 'revision'})
        with self.assertRaises(HTTPException) as raised:
            vocabulary_router.read_entry(99999, connection=self.db)
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == '__main__':
    unittest.main()
