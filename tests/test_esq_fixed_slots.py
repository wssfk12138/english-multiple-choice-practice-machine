import copy
import io
import json
import sqlite3
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from backend.app import database
from backend.app.services import esq

class FixedSlotsExchangeTests(unittest.TestCase):
    def test_fixed_slots_survive_database_and_export_with_explicit_empty(self):
        source=Path(__file__).resolve().parents[1]/'examples/demo-bank.esq'
        original=esq.load_esq_package(source)
        for spelling, slots in [('fixed_slots',[{'type':'fixed','label':'B'},{'type':'question','number':1}]),('fixedSlots',[])]:
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.executescript(database.SCHEMA);database._run_migrations(db)
            profile=database.get_active_profile_id(db)
            package=copy.deepcopy(original);unit=package['papers'][0]['units'][0]
            unit[spelling]=slots
            with patch.object(esq,'_extract_assets',return_value={}):
                esq.publish_package(db,package,source,{},import_ai_labels=False,profile_id=profile)
            shared=json.loads(db.execute('SELECT shared_data FROM units').fetchone()[0])
            self.assertEqual(shared['fixed_slots'],slots)
            data,_=esq.export_package(db,profile_id=profile)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                manifest=json.loads(archive.read('manifest.json'))
                paper=json.loads(archive.read(manifest['papers'][0]['path']))
                self.assertEqual(paper['units'][0]['fixed_slots'],slots)
            db.close()

    def test_invalid_fixed_chain_rejected(self):
        paper={'paperKey':'test.paper','year':2025,'units':[{'unitKey':'test.unit','type':'part_b','passage':{'blocks':[]},'candidates':[{'key':'A','content':'Text'}],'questions':[{'questionKey':'test.q','number':41,'stem':'Choose','options':[{'key':'A','content':'Text'}]}],'fixed_slots':[{'type':'fixed','label':'Z'}]}]}
        errors=[]
        esq._validate_paper(paper,{'paperKey':'test.paper'},{'answers':{'test.q':{'correctOption':'A'}}},None,{},errors)
        self.assertTrue(any(item['path'].endswith('.fixed_slots') for item in errors),errors)
