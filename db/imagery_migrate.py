"""Add imagery storage atomically; no network/photo acquisition runs inside the migration."""
from pathlib import Path
from psycopg.types.json import Jsonb
import psycopg
VERSION='20260909202103_venue_imagery'
def run(url):
    with psycopg.connect(url) as conn:
        conn.execute('select pg_advisory_xact_lock(735006)')
        previous=conn.execute('select report from schema_migrations where id=%s',(VERSION,)).fetchone()
        if previous:return {**previous[0],'already_migrated':True}
        before=conn.execute('select id,user_id,save_id,place_id,note,venue_kind,venue_kind_source from entries order by id').fetchall()
        links=conn.execute('select folder_id,entry_id,user_id from folder_items order by folder_id,entry_id').fetchall()
        conn.execute((Path(__file__).parent/'migrations'/f'{VERSION}.sql').read_text(),prepare=False)
        assert before==conn.execute('select id,user_id,save_id,place_id,note,venue_kind,venue_kind_source from entries order by id').fetchall()
        assert links==conn.execute('select folder_id,entry_id,user_id from folder_items order by folder_id,entry_id').fetchall()
        report={'entries_preserved':len(before),'memberships_preserved':len(links),'provider_calls':0}
        conn.execute('insert into schema_migrations(id,report) values(%s,%s)',(VERSION,Jsonb(report)))
        return report
