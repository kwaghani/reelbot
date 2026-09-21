"""Release expansion/activation rehearsal on a newly created loopback database."""
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from db.staged_migrate import apply, ACCOUNT_EXPAND, ACCOUNT_VERSION, RETENTION_VERSION
from worker.registry import sync_registry


class StagedMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = os.environ['TEST_DATABASE_URL']
        parts = urlsplit(cls.source)
        if parts.hostname not in ('localhost', '127.0.0.1') or 'test' not in parts.path:
            raise RuntimeError('Disposable loopback database required')
        cls.name = 'reelbot_staged_test_' + uuid4().hex[:8]
        cls.url = urlunsplit(parts._replace(path='/' + cls.name))
        with psycopg.connect(cls.source, autocommit=True) as conn:
            conn.execute(sql.SQL('create database {}').format(sql.Identifier(cls.name)))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.source, autocommit=True) as conn:
            conn.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(cls.name)))

    def setUp(self):
        self.conn = psycopg.connect(self.url, row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.conn.execute('drop schema public cascade;create schema public')
        self.conn.execute((Path(__file__).resolve().parents[1] / 'db/schema.sql').read_text(), prepare=False)
        sync_registry(self.conn)
        self.conn.execute("insert into schema_migrations(id,report) values('20260911150000_shared_image_cache','{}')")
        self.owner = self.conn.execute("insert into users(device_id) values('staged-test') returning id").fetchone()['id']
        self.saved = self.conn.execute("insert into saves(user_id,source_url,platform) values(%s,'https://example.com/fixture','instagram') returning id", (self.owner,)).fetchone()['id']
        self.conn.commit()

    def test_expansion_preserves_rows_and_legacy_delete_semantics(self):
        before = self.conn.execute('select * from saves').fetchall()
        apply(self.conn, 'accounts-expand')
        after = self.conn.execute('select * from saves').fetchall()
        self.assertEqual([{key: row[key] for key in before[0]} for row in after], before)
        self.assertEqual(self.conn.execute("select count(*) n from pg_trigger where not tgisinternal and tgname like 'personal_%%'").fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('select count(*) n from sync_changes').fetchone()['n'], 0)
        self.assertTrue(apply(self.conn, 'accounts-expand')['already_applied'])
        self.conn.execute('delete from saves where id=%s', (self.saved,))
        self.assertEqual(self.conn.execute('select count(*) n from saves').fetchone()['n'], 0)

    def test_activation_requires_expansion_then_seeds_without_changing_identity(self):
        with self.assertRaisesRegex(RuntimeError, 'expansion'):
            apply(self.conn, 'accounts-activate')
        apply(self.conn, 'accounts-expand')
        apply(self.conn, 'accounts-activate')
        self.assertEqual(self.conn.execute('select id from saves').fetchone()['id'], self.saved)
        self.assertGreater(self.conn.execute('select count(*) n from sync_changes').fetchone()['n'], 0)
        self.conn.execute('delete from saves where id=%s', (self.saved,))
        self.assertIsNotNone(self.conn.execute('select deleted_at from saves').fetchone()['deleted_at'])

    def test_retention_expansion_does_not_scrub_or_drop_legacy_columns(self):
        apply(self.conn, 'accounts-expand'); apply(self.conn, 'accounts-activate')
        place = self.conn.execute("insert into places(google_place_id,name,city,formatted_address,lat,lng) values('fixture','Fixture name','Fixture city','Fixture address',1,2) returning id").fetchone()['id']
        apply(self.conn, 'retention-expand')
        row = self.conn.execute('select name,city,lat,lng,coords_fetched_at from places where id=%s', (place,)).fetchone()
        self.assertEqual(row['name'], 'Fixture name'); self.assertEqual(row['lat'], 1)
        self.assertIsNone(row['coords_fetched_at'])
        self.assertIsNone(self.conn.execute("select 1 from schema_migrations where id='20260915000000_google_retention'").fetchone())

    def test_retention_activation_requires_expansion_and_only_records_the_marker(self):
        apply(self.conn, 'accounts-expand'); apply(self.conn, 'accounts-activate')
        with self.assertRaisesRegex(RuntimeError, 'expansion'):
            apply(self.conn, 'retention-activate')
        place = self.conn.execute("insert into places(google_place_id,name,city,formatted_address,lat,lng) values('fixture','Fixture name','Fixture city','Fixture address',1,2) returning id").fetchone()['id']
        apply(self.conn, 'retention-expand')
        before = self.conn.execute('select * from places where id=%s', (place,)).fetchone()
        apply(self.conn, 'retention-activate')
        after = self.conn.execute('select * from places where id=%s', (place,)).fetchone()
        self.assertEqual(after, before)
        self.assertIsNotNone(self.conn.execute('select 1 from schema_migrations where id=%s', (RETENTION_VERSION,)).fetchone())
        self.assertTrue(apply(self.conn, 'retention-activate')['already_applied'])

    def test_retention_activation_unblocks_the_new_runtime_schema_gate(self):
        gate = (Path(__file__).resolve().parents[1] / 'deploy/render/check_schema.py').read_text()
        self.assertIn(RETENTION_VERSION, gate)
        apply(self.conn, 'accounts-expand'); apply(self.conn, 'accounts-activate')
        apply(self.conn, 'retention-expand')
        self.assertIsNone(self.conn.execute('select 1 from schema_migrations where id=%s', (RETENTION_VERSION,)).fetchone())
        apply(self.conn, 'retention-activate')
        self.assertIsNotNone(self.conn.execute('select 1 from schema_migrations where id=%s', (RETENTION_VERSION,)).fetchone())

    def test_retention_activation_refuses_an_incomplete_expansion(self):
        apply(self.conn, 'accounts-expand'); apply(self.conn, 'accounts-activate')
        apply(self.conn, 'retention-expand')
        self.conn.execute('alter table places drop column coords_fetched_at')
        with self.assertRaisesRegex(RuntimeError, 'coords_fetched_at'):
            apply(self.conn, 'retention-activate')

    def test_rehearsal_rollback_leaves_no_expansion(self):
        apply(self.conn, 'accounts-expand'); self.conn.rollback()
        self.assertIsNone(self.conn.execute('select 1 from schema_migrations where id=%s', (ACCOUNT_EXPAND,)).fetchone())
        self.assertIsNone(self.conn.execute("select to_regclass('public.account_sessions') relation").fetchone()['relation'])

    def test_deploy_hook_cannot_implicitly_purge_data(self):
        hook = (Path(__file__).resolve().parents[1] / 'deploy/render/pre-deploy.sh').read_text()
        self.assertNotIn('python -m db.migrate', hook)
        self.assertIn('python -m deploy.render.check_schema', hook)
