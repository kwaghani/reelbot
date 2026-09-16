"""Restore a private sanitized snapshot into a newly created disposable database.

Never changes the source database or overwrites an existing test database.
The generated test database is removed after verification, including on failure.
"""
import argparse
import gzip
import json
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from config import settings, psycopg_database_url
from worker.registry import sync_registry
from worker.storage import R2Storage
from scripts.release_snapshot import restore


def run(key):
    if not key.startswith('release-recovery/') or not key.endswith('.json.gz'):
        raise ValueError('Explicit release recovery snapshot required')
    backend=R2Storage()
    payload=json.loads(gzip.decompress(backend.client.get_object(Bucket=backend.bucket,Key=key)['Body'].read()))
    source=psycopg_database_url(settings().database_url)
    name='reelbot_release32_restore_test_'+uuid4().hex[:12]
    root=Path(__file__).resolve().parents[1]
    with psycopg.connect(source,autocommit=True) as admin:
        admin.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    try:
        with psycopg.connect(make_conninfo(source,dbname=name),row_factory=dict_row) as conn:
            conn.execute("set statement_timeout='60s'")
            conn.execute((root/'db/schema.sql').read_text(),prepare=False)
            sync_registry(conn)
            conn.execute((root/'db/migrations/20260914150000_accounts_sync.sql').read_text(),prepare=False)
            from db.retention_migrate import run as retention
            retention(conn)  # Empty disposable database only; no source writes.
            result=restore(conn,payload)
            result['coordinate_rows']=conn.execute('select count(*) n from places where lat is not null or lng is not null').fetchone()['n']
            if result['coordinate_rows']:raise RuntimeError('Recovery contained cached coordinates')
        return {**result,'restored':True,'test_database_removed':True}
    finally:
        with psycopg.connect(source,autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('key')
    print(json.dumps(run(parser.parse_args().key)))
