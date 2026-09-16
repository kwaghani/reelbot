"""Consistent restoreable backup with no coordinate leases or provider caches."""
import gzip
import subprocess
import tempfile
from datetime import date, timedelta
import re
from pathlib import Path
from psycopg import sql
from config import settings, psycopg_database_url
from worker.db import connect
from worker.storage import R2Storage

def create(path):
    # pg_dump sections and our Class-A-only COPY use the same MVCC snapshot.
    with connect() as conn, gzip.open(path,'wb',compresslevel=9) as output:
        conn.execute('set transaction isolation level repeatable read, read only')
        if not conn.execute("select 1 from schema_migrations where id='20260915000000_google_retention'").fetchone():
            raise RuntimeError('Retention migration required before making a new backup')
        snapshot=conn.execute('select pg_export_snapshot() s').fetchone()['s']
        base=['pg_dump','--no-owner','--no-privileges','--snapshot='+snapshot]
        # Credentials go in the process environment, never the argument list.
        import os
        env={**os.environ,'PGDATABASE':psycopg_database_url(settings().database_url)}
        def dump(section):
            command=base+['--section='+section]
            if section=='data': command+=['--exclude-table-data=public.places','--exclude-table-data=public.city_bias_cache','--exclude-table-data=public.natural_geocode_cache']
            with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env) as process:
                while block:=process.stdout.read(1024*1024): output.write(block)
                if process.wait(): raise RuntimeError('Backup failed; database credentials and provider payloads are not logged')
        dump('pre-data');dump('data')
        columns=[r['column_name'] for r in conn.execute("select column_name from information_schema.columns where table_schema='public' and table_name='places' order by ordinal_position").fetchall()]
        names=sql.SQL(',').join(map(sql.Identifier,columns))
        expressions=sql.SQL(',').join(sql.SQL('NULL') if name in ('lat','lng','coords_fetched_at') else sql.Identifier(name) for name in columns)
        output.write((sql.SQL('COPY public.places ({}) FROM stdin;\n').format(names).as_string(conn)).encode())
        with conn.cursor().copy(sql.SQL('COPY (SELECT {} FROM public.places) TO STDOUT').format(expressions)) as copy:
            for block in copy: output.write(block)
        output.write(b'\\.\n')
        dump('post-data')

def main():
    key=f'backups/{date.today():%Y-%m-%d}.sql.gz'
    backend=R2Storage()  # Do not silently treat a local fallback as a backup.
    with tempfile.TemporaryDirectory(prefix='reelbot-retention-backup-') as directory:
        path=Path(directory)/'backup.sql.gz';create(path)
        backend.put(key,path.read_bytes(),'application/gzip')
        if not backend.exists(key): raise RuntimeError('Backup verification failed')
    print('Retention-safe backup uploaded; coordinates excluded.')
    # Preserve the existing 30-day backup lifecycle, but delete only validated
    # date-addressed backup keys after a new upload is verified.
    for page in backend.client.get_paginator('list_objects_v2').paginate(Bucket=backend.bucket,Prefix='backups/'):
        for obj in page.get('Contents',[]):
            match=re.fullmatch(r'backups/(\d{4}-\d{2}-\d{2})\.sql\.gz',obj['Key'])
            if match and date.fromisoformat(match[1]) < date.today()-timedelta(days=30): backend.delete(obj['Key'])

if __name__=='__main__': main()
