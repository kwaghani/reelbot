"""Private, sanitized personal-library recovery snapshot across schema versions.

This is not a provider-cache backup. Coordinates, cached provider decorations,
diagnostic events and derived search vectors are intentionally excluded. Restore
is permitted only into a disposable loopback database, never over production.
"""
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.db import connect
from worker.retention_policy import safe_candidate
from worker.retention_objects import clean_imagery
from worker.storage import R2Storage

TABLES = ('users','devices','account_sessions','saves','places','entries','folders',
          'folder_items','jobs','sync_mutations','account_deletions')


def fingerprint(tables):
    fields={'users':('id','device_id'), 'devices':('id','user_id','token_hash'),
            'saves':('id','user_id','source_url'),
            'entries':('id','user_id','save_id','note','venue_kind','venue_kind_source'),
            'folders':('id','user_id','name','kind'),
            'folder_items':('folder_id','entry_id','user_id')}
    stable={name:sorted(({key:row.get(key) for key in keys} for row in tables.get(name,[])),
                       key=lambda row:json.dumps(row,sort_keys=True,default=str)) for name,keys in fields.items()}
    return hashlib.sha256(json.dumps(stable,sort_keys=True,default=str).encode()).hexdigest()


def capture(conn):
    conn.execute('set transaction isolation level repeatable read, read only')
    tables={}
    for name in TABLES:
        if conn.execute('select to_regclass(%s) name',('public.'+name,)).fetchone()['name']:
            tables[name]=conn.execute(sql.SQL('select * from {}').format(sql.Identifier(name))).fetchall()
    before=fingerprint(tables)
    for row in tables.get('places',[]):
        evidence=next((e.get('candidate') or {} for e in tables['entries'] if e.get('place_id')==row['id'] and
                       any((e.get('candidate') or {}).get(k) for k in ('venue_name','title','name'))),{})
        allowed=('id','google_place_id','provider','provider_place_id','lookup_key','last_refreshed_at',
                 'created_at','venue_kind','venue_kind_source','resolution_attribution')
        value={k:v for k,v in row.items() if k in allowed}
        value.update(extracted_name=evidence.get('venue_name') or evidence.get('title') or evidence.get('name') or '',
                     extracted_city=evidence.get('city_hint') or '',
                     extracted_neighborhood=(evidence.get('attributes') or {}).get('neighborhood') or '',
                     imagery=clean_imagery(row.get('imagery')),lat=None,lng=None,coords_fetched_at=None)
        row.clear();row.update(value)
    for name in ('saves','entries','jobs','sync_mutations'):
        for row in tables.get(name,[]):
            for key in ('raw_signals','diagnostics','cost','candidate','payload','result'):
                if key in row:row[key]=safe_candidate(row[key])
            row.pop('venue_kind_primary_type',None)
            if 'embedding' in row:row['embedding']=None
    # User-corrected titles remain exact; otherwise use pre-resolution evidence.
    for row in tables.get('entries',[]):
        if row.get('place_id') and not row.get('verified_at'):
            evidence=row.get('candidate') or {}
            row['title']=evidence.get('venue_name') or evidence.get('title') or evidence.get('name') or 'Saved place'
    if fingerprint(tables)!=before:raise RuntimeError('Personal recovery fingerprint changed')
    return {'format':'reelbot-personal-recovery-v1','created_at':datetime.now(timezone.utc).isoformat(),
            'fingerprint':before,'tables':tables}


def upload(key):
    if not key.startswith('release-recovery/') or not key.endswith('.json.gz'):
        raise ValueError('Explicit release-recovery/*.json.gz key required')
    backend=R2Storage()
    if backend.exists(key):raise RuntimeError('Recovery snapshot already exists; never overwrite it')
    with connect() as conn: payload=capture(conn)
    data=gzip.compress(json.dumps(payload,default=str).encode())
    backend.put(key,data,'application/gzip')
    if not backend.exists(key):raise RuntimeError('Recovery upload verification failed')
    return {'key':key,'fingerprint':payload['fingerprint'],'counts':{k:len(v) for k,v in payload['tables'].items()},'bytes':len(data)}


def restore(conn,payload):
    if payload['format']!='reelbot-personal-recovery-v1':raise RuntimeError('Unknown recovery format')
    # Caller creates a fresh schema first. Refuse any overwrite, including fixtures.
    if conn.execute('select count(*) n from users').fetchone()['n']:raise RuntimeError('Restore requires an empty database')
    for name in TABLES:
        rows=payload['tables'].get(name,[])
        if not rows:continue
        if name=='folders':
            pending=list(rows);rows=[];parents=set()
            while pending:
                ready=[row for row in pending if not row.get('parent_folder_id') or str(row['parent_folder_id']) in parents]
                if not ready:raise RuntimeError('Recovery has a missing or cyclic folder parent')
                rows.extend(ready);parents.update(str(row['id']) for row in ready)
                pending=[row for row in pending if str(row['id']) not in parents]
        types={r['column_name']:r['data_type'] for r in conn.execute("select column_name,data_type from information_schema.columns where table_schema='public' and table_name=%s",(name,))}
        for row in rows:
            columns=[k for k in row if k in types]
            values=[Jsonb(row[k]) if types[k] in ('json','jsonb') and row[k] is not None else row[k] for k in columns]
            conn.execute(sql.SQL('insert into {} ({}) overriding system value values ({})').format(
                sql.Identifier(name),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns)),values)
    restored={name:conn.execute(sql.SQL('select * from {}').format(sql.Identifier(name))).fetchall()
              for name in payload['tables']}
    if fingerprint(restored)!=payload['fingerprint']:raise RuntimeError('Restored personal fingerprint mismatch')
    return {'fingerprint':payload['fingerprint'],'counts':{k:len(v) for k,v in restored.items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('key');parser.add_argument('--restore-test-url')
    args=parser.parse_args()
    if not args.restore_test_url:
        print(json.dumps(upload(args.key)));return
    target=urlsplit(args.restore_test_url)
    if target.hostname not in ('localhost','127.0.0.1') or 'test' not in target.path:
        raise RuntimeError('Restore tests require a named disposable loopback database')
    backend=R2Storage()
    payload=json.loads(gzip.decompress(backend.client.get_object(Bucket=backend.bucket,Key=args.key)['Body'].read()))
    with psycopg.connect(args.restore_test_url,row_factory=dict_row) as conn:
        print(json.dumps(restore(conn,payload)))


if __name__=='__main__':main()
