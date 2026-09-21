import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import test_personal as personal
from worker.db import connect
from worker.registry import sync_registry
from scripts.release_snapshot import capture, restore


class RecoverySnapshotTests(unittest.TestCase):
    setUpClass=classmethod(personal.PersonalTests.setUpClass.__func__)
    setUp=personal.PersonalTests.setUp
    register=personal.PersonalTests.register
    save=personal.PersonalTests.save
    venue=personal.PersonalTests.venue

    def test_sanitized_snapshot_restores_personal_data_without_coordinates(self):
        entry,_=self.venue(self.save(self.a),self.a)
        self.client.patch('/items/'+str(entry['id']),headers=self.a['headers'],json={'note':'Recovery note'})
        with connect() as conn:
            parent=conn.execute("insert into folders(user_id,name,kind) values(%s,'Parent','custom') returning id",(self.a['user'],)).fetchone()['id']
            conn.execute("insert into folders(user_id,name,kind,parent_folder_id) values(%s,'Child','custom',%s)",(self.a['user'],parent))
        with connect() as conn:payload=capture(conn)
        payload['tables']['folders'].reverse()  # Child must restore after parent regardless of storage order.
        self.assertIsNone(payload['tables']['places'][0]['lat'])
        self.assertNotIn('formatted_address',payload['tables']['places'][0])
        payload=json.loads(json.dumps(payload,default=str))
        source=os.environ['TEST_DATABASE_URL'];parts=urlsplit(source)
        self.assertIn(parts.hostname,('localhost','127.0.0.1'))
        name='reelbot_recovery_test_'+uuid4().hex[:8]
        with psycopg.connect(source,autocommit=True) as conn:
            conn.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
        try:
            with psycopg.connect(urlunsplit(parts._replace(path='/'+name)),row_factory=dict_row) as conn:
                root=Path(__file__).resolve().parents[1]
                conn.execute((root/'db/schema.sql').read_text(),prepare=False);sync_registry(conn)
                conn.execute((root/'db/migrations/20260914150000_accounts_sync.sql').read_text(),prepare=False)
                from db.retention_migrate import run
                run(conn)
                result=restore(conn,payload)
                self.assertEqual(result['counts']['entries'],1)
                self.assertEqual(conn.execute('select note from entries').fetchone()['note'],'Recovery note')
                self.assertIsNone(conn.execute('select lat from places').fetchone()['lat'])
        finally:
            with psycopg.connect(source,autocommit=True) as conn:
                conn.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))
