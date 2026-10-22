import json,sqlite3,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from backend.app import database
from backend.app.routers.vocabulary import list_entries
from backend.app.services.vocabulary import _serialize_entry,review_entry
from backend.app.services.sqlite_snapshot import read_snapshot


class HookedConnection(sqlite3.Connection):
    hook=None
    def execute(self,sql,*args,**kwargs):
        query=sql.strip().lower()
        if self.hook and (query.startswith('select entry_id,count(*) from vocabulary_reviews') or query.startswith('select count(*) from vocabulary_reviews where entry_id')):
            callback=self.hook;self.hook=None;callback()
        return super().execute(sql,*args,**kwargs)


class VocabularyReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'synthetic.db'
        self.reader=sqlite3.connect(self.path,factory=HookedConnection);self.reader.row_factory=sqlite3.Row
        self.reader.execute('PRAGMA journal_mode=WAL');self.reader.executescript(database.SCHEMA);database._run_migrations(self.reader)
        self.reader.execute("INSERT INTO vocabulary_entries(id,term,normalized_term,translation_status,common_meaning,user_edited) VALUES(1,'evidence','evidence','ready','合成释义',1)");self.reader.commit()
        self.writer=sqlite3.connect(self.path);self.writer.row_factory=sqlite3.Row

    def tearDown(self):self.reader.close();self.writer.close();self.temp.cleanup()

    def concurrent_rating(self):review_entry(self.writer,1,'fluent')

    def test_queue_fields_counts_and_revision_share_one_wal_snapshot(self):
        self.reader.hook=self.concurrent_rating
        response=list_entries(status='review',limit=12,connection=self.reader)
        self.assertEqual(len(response['items']),1)
        entry=response['items'][0];revision=json.loads(entry['review_revision'])
        self.assertIsNone(entry['last_reviewed_at']);self.assertEqual(revision[-1],0)
        self.assertEqual(response['counts']['review'],1);self.assertEqual(response['revision'],'0')
        self.assertEqual(self.writer.execute('SELECT COUNT(*) FROM vocabulary_reviews').fetchone()[0],1)
        self.assertEqual(list_entries(status='review',connection=self.reader)['items'],[])
        self.assertFalse(self.reader.in_transaction)

    def test_detail_fields_and_review_count_share_one_wal_snapshot(self):
        self.reader.hook=self.concurrent_rating
        entry=_serialize_entry(self.reader,1)
        self.assertIsNone(entry['last_reviewed_at']);self.assertEqual(json.loads(entry['review_revision'])[-1],0)
        fresh=_serialize_entry(self.reader,1)
        self.assertIsNotNone(fresh['last_reviewed_at']);self.assertEqual(json.loads(fresh['review_revision'])[-1],1)

    def test_nested_snapshot_failure_preserves_the_outer_write(self):
        self.reader.execute("UPDATE vocabulary_entries SET note='synthetic outer edit' WHERE id=1")
        with self.assertRaises(RuntimeError):
            with read_snapshot(self.reader):raise RuntimeError('synthetic read failure')
        self.assertTrue(self.reader.in_transaction)
        self.assertEqual(self.reader.execute('SELECT note FROM vocabulary_entries').fetchone()[0],'synthetic outer edit')
        self.reader.rollback()

    def test_ack_is_atomic_lightweight_and_command_retry_does_not_rate_twice(self):
        from backend.app.services import vocabulary as service
        revision=service._serialize_entry(self.reader,1)['review_revision']
        with patch.object(service,'local_similar_matches',side_effect=AssertionError('ack must not match the whole vocabulary')):
            ack=review_entry(self.reader,1,'fluent',expected_revision=revision,command_id='synthetic-command',response_projection='ack')
            retry=review_entry(self.reader,1,'fluent',expected_revision=revision,command_id='synthetic-command',response_projection='ack')
        self.assertEqual(set(ack),{'id','study_status','review_revision'})
        self.assertEqual(ack,retry);self.assertEqual(ack['study_status'],'mastered')
        self.assertEqual(json.loads(ack['review_revision'])[-1],1)
        self.assertFalse(self.reader.in_transaction)
        # Default clients retain the complete response, including after retry.
        full=review_entry(self.reader,1,'fluent',command_id='synthetic-command')
        self.assertEqual(full['term'],'evidence');self.assertIn('occurrences',full)
        self.assertEqual(full['review_revision'],ack['review_revision'])
        self.assertEqual(self.reader.execute('SELECT COUNT(*) FROM vocabulary_reviews').fetchone()[0],1)

    def test_projection_contract_and_limited_query_do_not_read_all_rows_first(self):
        from backend.app.routers import vocabulary as routes
        statements=[];self.reader.set_trace_callback(statements.append)
        with patch.object(routes,'local_similar_matches',side_effect=AssertionError('explicit projections skip matching')):
            summary=list_entries(limit=1,projection='summary',connection=self.reader)['items'][0]
            queue=list_entries(limit=1,projection='queue',connection=self.reader)['items'][0]
            review=list_entries(limit=1,projection='review',connection=self.reader)['items'][0]
        self.assertEqual(summary['term'],queue['term'])
        self.assertEqual(review['review_detail'],summary)
        self.assertEqual(queue['review_revision'],summary['review_revision'])
        self.assertNotIn('common_meaning',queue)
        row_reads=[s for s in statements if s.lstrip().startswith(('SELECT * FROM (SELECT *,','SELECT *,\n'))]
        self.assertEqual(len(row_reads),3)
        self.assertTrue(all('LIMIT 1 OFFSET 0' in s for s in row_reads))

    def test_similarity_cpu_runs_after_releasing_plain_read_snapshot(self):
        from backend.app.services import vocabulary as service
        from backend.app.routers import vocabulary as routes
        def match(*args,**kwargs):
            self.assertFalse(self.reader.in_transaction)
            self.assertIsNotNone(kwargs.get('pool'))
            return {}
        with patch.object(service,'local_similar_matches',side_effect=match):
            service._serialize_entry(self.reader,1)
        with patch.object(routes,'local_similar_matches',side_effect=match):
            list_entries(limit=1,connection=self.reader)


if __name__=='__main__':unittest.main()
