"""Atomic venue-kind backfill; later bounded batches fill missing provider types once."""
import json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.registry import sync_registry
from worker.venue_kinds import backfill
VERSION='20260908090846_venue_kinds'

def run(database_url):
    with psycopg.connect(database_url,row_factory=dict_row) as conn:
        conn.execute('select pg_advisory_xact_lock(735005)')
        prior=conn.execute('select report from schema_migrations where id=%s',(VERSION,)).fetchone()
        if prior:return {**prior['report'],'already_migrated':True}
        conn.execute((Path(__file__).parent/'migrations'/f'{VERSION}.sql').read_text(),prepare=False)
        sync_registry(conn)
        report=backfill(conn)
        conn.execute('insert into schema_migrations(id,report) values(%s,%s)',(VERSION,Jsonb(report)))
        return report
