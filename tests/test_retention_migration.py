"""Synthetic legacy-content purge and real pg_dump/restore round trip."""
import gzip
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from uuid import uuid4
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.registry import sync_registry
from db.retention_migrate import run

class RetentionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=os.environ['TEST_DATABASE_URL'];parts=urlsplit(source)
        if parts.hostname not in ('localhost','127.0.0.1') or 'test' not in parts.path:raise RuntimeError('Disposable local test only')
        suffix=uuid4().hex[:8]
        cls.names=['reelbot_retention_fixture_test_'+suffix,'reelbot_retention_restore_test_'+suffix]
        cls.source=source;cls.urls=[urlunsplit(parts._replace(path='/'+n)) for n in cls.names]
        with psycopg.connect(source,autocommit=True) as conn:
            for name in cls.names:conn.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.source,autocommit=True) as conn:
            for name in cls.names:conn.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))
    def connect(self):return psycopg.connect(self.urls[0],row_factory=dict_row)
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        with self.connect() as conn:
            conn.execute('drop schema public cascade;create schema public')
            conn.execute((root/'db/schema.sql').read_text(),prepare=False);sync_registry(conn)
            conn.execute((root/'db/migrations/20260914150000_accounts_sync.sql').read_text(),prepare=False)
            self.owner=conn.execute("insert into users(device_id) values('retention-fixture') returning id").fetchone()['id']
            saved=conn.execute("insert into saves(user_id,source_url,platform) values(%s,'https://instagram.com/reel/fixture/','instagram') returning id",(self.owner,)).fetchone()['id']
            self.entry_ids=[]
            for index in range(2):
                place=conn.execute("insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,details,map_thumbnail) values(%s,'Google only name','Google only address',38,-122,'restaurant',%s,%s) returning id",('fixture-'+str(index),Jsonb({'rating':4.2,'websiteUri':'https://google-response.example'}),Jsonb({'owner':{'asset':'c'*64,'source':'MapKit snapshot'}} if index==0 else {}))).fetchone()['id']
                candidate={'venue_name':'Independent caption name','city_hint':'Caption city','attributes':{'neighborhood':'Caption district'},'place_candidates':[{'place':{'id':'opaque','displayName':{'text':'Google only candidate'}}}]} if index==0 else {}
                row=conn.execute("""insert into entries(user_id,save_id,place_id,title,note,summary,content_type,attributes,candidate,candidate_key,confidence)
                    values(%s,%s,%s,'Google only name','Keep my note','Keep my summary','place',%s,%s,%s,.8) returning id""",(self.owner,saved,place,Jsonb({'venue_kind':'restaurant'}),Jsonb(candidate),str(index))).fetchone()
                self.entry_ids.append(row['id'])
            self.folder=conn.execute("insert into folders(user_id,name,kind) values(%s,'My custom folder','custom') returning id",(self.owner,)).fetchone()['id']
            conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s)',(self.folder,self.entry_ids[0],self.owner))
            conn.execute('insert into image_assets(content_hash,jpeg) values(%s,%s)',('c'*64,b'fixture image bytes'))
            conn.execute('update saves set raw_signals=%s,diagnostics=%s where id=%s',(Jsonb({'caption':'Keep reel caption','candidates':[candidate]}),Jsonb({'places_queries':[{'results':[{'displayName':{'text':'Google only diagnostic'}}]}]}),saved))

    def test_purge_is_counted_preserves_personal_data_and_reseeds_full_sync(self):
        with self.connect() as conn:report=run(conn)
        self.assertEqual(report['places'],2);self.assertEqual(report['needs_reextraction'],1)
        self.assertEqual(report['unknown_coordinate_times'],2)
        self.assertEqual(report['database_images_deleted'],1);self.assertEqual(report['database_image_bytes_deleted'],19)
        with self.connect() as conn:
            self.assertEqual(run(conn),{'already_applied':True})
            rows=conn.execute('select * from entries order by candidate_key').fetchall()
            self.assertEqual(rows[0]['title'],'Independent caption name');self.assertEqual(rows[1]['title'],'Saved place')
            self.assertTrue(all(r['note']=='Keep my note' and r['summary']=='Keep my summary' and r['user_id']==self.owner for r in rows))
            self.assertEqual(conn.execute('select count(*) n from folder_items where folder_id=%s',(self.folder,)).fetchone()['n'],1)
            entities={r['entity'] for r in conn.execute('select entity from sync_changes').fetchall()}
            self.assertTrue({'saves','entries','folders','folder_items'}<=entities)
            for table in ('places','entries','saves','sync_changes'):
                self.assertNotIn('Google only',str(conn.execute(sql.SQL('select to_jsonb(t) as value from {} t').format(sql.Identifier(table))).fetchall()))
        from worker.coordinate_sweep import run as sweep
        with patch('worker.coordinate_sweep.connect',self.connect):self.assertEqual(sweep()['places_nulled'],2)

    def test_real_backup_round_trip_excludes_coordinates_but_restores_ownership(self):
        from worker import retention_backup
        with self.connect() as conn:
            run(conn)
            conn.execute('update places set lat=38,lng=-122,coords_fetched_at=now()')
        original_popen=subprocess.Popen
        def local_dump(command,**kwargs):
            return original_popen(['docker','exec','reelbot-audit-db','pg_dump','-U','postgres','-d',self.names[0],*command[1:]],**kwargs)
        with tempfile.TemporaryDirectory(prefix='reelbot-retention-test-') as directory:
            path=Path(directory)/'safe.sql.gz'
            with patch('worker.retention_backup.connect',self.connect),patch('worker.retention_backup.settings',return_value=SimpleNamespace(database_url=self.urls[0])),patch('worker.retention_backup.subprocess.Popen',side_effect=local_dump):retention_backup.create(path)
            restored=subprocess.run(['docker','exec','-i','reelbot-audit-db','psql','-U','postgres','-d',self.names[1],'-v','ON_ERROR_STOP=1'],input=gzip.decompress(path.read_bytes()),capture_output=True)
            self.assertEqual(restored.returncode,0,restored.stderr.decode()[-1000:])
        with psycopg.connect(self.urls[1],row_factory=dict_row) as conn:
            self.assertEqual(conn.execute('select count(*) n from entries where user_id=%s and note=%s',(self.owner,'Keep my note')).fetchone()['n'],2)
            self.assertEqual(conn.execute('select count(*) n from places where lat is not null or lng is not null or coords_fetched_at is not null').fetchone()['n'],0)
