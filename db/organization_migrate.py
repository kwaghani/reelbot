"""Apply folder-locality metadata and preserve every private entry and custom link."""
import argparse
import json
import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.registry import sync_registry
from worker.geography import migrate_folders

VERSION = '20260908074706_organization_geography'


def run(database_url):
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10) as conn:
        conn.execute('select pg_advisory_xact_lock(735004)')
        previous = conn.execute('select report from schema_migrations where id=%s', (VERSION,)).fetchone()
        if previous: return {**previous['report'], 'already_migrated': True}
        conn.execute((Path(__file__).parent / 'migrations' / (VERSION + '.sql')).read_text(), prepare=False)
        sync_registry(conn)
        report = migrate_folders(conn)
        conn.execute('insert into schema_migrations(id,report) values(%s,%s)', (VERSION, Jsonb(report)))
        return {**report, 'already_migrated': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-env', default='DATABASE_URL')
    args = parser.parse_args()
    print(json.dumps(run(os.environ[args.database_env]), indent=2))
