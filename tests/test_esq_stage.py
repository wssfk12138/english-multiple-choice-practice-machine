import asyncio
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from backend.app import database
from backend.app.services import esq_stage


class EsqStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / 'synthetic.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(database.SCHEMA)
        database._run_migrations(self.db)
        self.db.commit()
        self.archive = Path(__file__).resolve().parents[1] / 'examples/demo-bank.esq'

    def tearDown(self):
        self.db.close();self.temporary.cleanup()

    def upload(self, data=None):
        return asyncio.run(esq_stage.upload(self.db,self.root,UploadFile(io.BytesIO(self.archive.read_bytes() if data is None else data),filename='synthetic.esq')))

    def test_complete_upload_survives_restart_and_resume_is_idempotent(self):
        stage = self.upload()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_jobs').fetchone()[0],0)
        self.db.close();self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row
        database._run_migrations(self.db);self.db.commit()
        self.assertEqual(esq_stage.pending(self.db,self.root)[0]['stage_id'],stage)
        result=esq_stage.resume(self.db,self.root,stage)
        self.assertEqual(result['id'],esq_stage.resume(self.db,self.root,stage)['id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_jobs').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM papers').fetchone()[0],0,'unapproved draft cannot publish papers')
        self.assertEqual(esq_stage.pending(self.db,self.root),[])
        with self.assertRaises(HTTPException):esq_stage.discard(self.db,self.root,stage)

    def test_partial_upload_is_explicitly_incomplete(self):
        stage='a'*32;directory=esq_stage.stage_path(self.root,stage);directory.mkdir(parents=True)
        (directory/'upload.part').write_bytes(b'partial')
        self.db.execute("INSERT INTO esq_import_stages(id,filename,profile_id) VALUES(?,'synthetic.esq',1)",(stage,));self.db.commit()
        task=esq_stage.pending(self.db,self.root)[0]
        self.assertFalse(task['complete']);self.assertEqual(task['phase'],'incomplete')
        with self.assertRaises(HTTPException) as caught:esq_stage.resume(self.db,self.root,stage)
        self.assertEqual(caught.exception.status_code,409)

    def test_complete_marker_before_metadata_commit_can_resume(self):
        stage=self.upload()
        self.db.execute("UPDATE esq_import_stages SET state='receiving',archive_hash='',archive_bytes=0 WHERE id=?",(stage,));self.db.commit()
        self.assertTrue(esq_stage.pending(self.db,self.root)[0]['complete'])
        self.assertIn('id',esq_stage.resume(self.db,self.root,stage))

    def test_bad_archive_and_hash_mismatch_preserve_source_without_half_draft(self):
        stage=self.upload(b'invalid archive')
        with self.assertRaises(HTTPException):esq_stage.resume(self.db,self.root,stage)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_jobs').fetchone()[0],0)
        self.assertTrue((esq_stage.stage_path(self.root,stage)/'archive.esq').exists())
        valid=self.upload()
        (esq_stage.stage_path(self.root,valid)/'archive.esq').write_bytes(b'changed')
        with self.assertRaises(HTTPException) as caught:esq_stage.resume(self.db,self.root,valid)
        self.assertEqual(caught.exception.status_code,409)

    def test_job_and_stage_mapping_commit_atomically(self):
        stage=self.upload()
        self.db.execute("CREATE TEMP TRIGGER reject_ready BEFORE UPDATE ON esq_import_stages WHEN NEW.state='ready' BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END");self.db.commit()
        with self.assertRaises(HTTPException):esq_stage.resume(self.db,self.root,stage)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_jobs').fetchone()[0],0)
        self.assertIsNone(self.db.execute('SELECT job_id FROM esq_import_stages').fetchone()[0])

    def test_discard_only_deletes_selected_unpublished_stage_and_paths_are_validated(self):
        one,two=self.upload(),self.upload()
        self.assertEqual(esq_stage.discard(self.db,self.root,one),{'discarded':True})
        self.assertFalse(esq_stage.stage_path(self.root,one).exists())
        self.assertTrue(esq_stage.stage_path(self.root,two).exists())
        for value in ('../escape','A'*32,'x'*32,''):
            with self.assertRaises(HTTPException):esq_stage.stage_path(self.root,value)


if __name__=='__main__':unittest.main()
