"""Provenance-based purge. Never delete a whole photos/ or thumbs/ prefix.

Plan before dropping legacy metadata. Manifest survives retries and contains no
photo payload. Ambiguous/shared non-Google references stop the purge for review.
"""
import json
import re
from pathlib import Path
from worker.db import connect
from worker import storage

HASH = re.compile(r'^[0-9a-f]{64}$')

def references(value, inherited=''):
    if isinstance(value, list):
        for part in value: yield from references(part, inherited)
    elif isinstance(value, dict):
        source = str(value.get('source') or value.get('provider') or inherited).lower()
        asset = value.get('asset')
        if isinstance(asset, str) and HASH.fullmatch(asset): yield 'thumbs/'+asset+'.jpg', source
        for key in ('object_key', 'storage_key'):
            if isinstance(value.get(key), str): yield value[key], source
        for key, part in value.items():
            yield from references(part, 'map' if key == 'map_thumbnail' else source)

def clean_imagery(value):
    if not isinstance(value, dict): return {}
    clean = dict(value)
    if clean.get('provider') == 'google' or clean.get('source') == 'google':
        clean = {k: v for k, v in clean.items() if k in ('provider','source','checked_at','retry_at','map')}
    if 'candidates' in clean: clean['candidates'] = [c for c in clean['candidates'] if c.get('source') != 'google']
    if 'map' in clean: clean['map'] = clean_imagery(clean['map'])
    return clean

def plan_database(conn):
    forbidden, keep = {}, set()
    rows = conn.execute('select * from places').fetchall()
    for row in rows:
        for key, source in references({'imagery':row.get('imagery'), 'map_thumbnail':row.get('map_thumbnail')}):
            if source in ('google','map','mapkit snapshot','maps static'): forbidden[key] = source
            elif source in ('commons','site','cover'): keep.add(key)
    for row in conn.execute('select cover_imagery from saves').fetchall():
        keep.update(key for key, _ in references(row['cover_imagery'], 'cover'))
    if keep.intersection(forbidden): raise RuntimeError('Image provenance conflict: review shared objects before deleting')
    stats={'objects_planned':len(forbidden),'database_images_deleted':0,'database_image_bytes_deleted':0}
    for key, source in forbidden.items():
        conn.execute('insert into retention_object_purge(object_key,bytes,reason) values(%s,0,%s) on conflict do nothing',(key,source))
        hashed=key.removeprefix('thumbs/').removesuffix('.jpg')
        if HASH.fullmatch(hashed):
            deleted=conn.execute('delete from image_assets where content_hash=%s returning octet_length(jpeg) bytes',(hashed,)).fetchone()
            if deleted:
                stats['database_images_deleted']+=1;stats['database_image_bytes_deleted']+=deleted['bytes']
    return stats

def run(*, apply=False):
    # Explicit R2 instance prevents a local degraded fallback claiming a cloud purge.
    backend=storage.R2Storage() if storage.settings().has_r2 else storage._backend()
    if isinstance(backend, storage.R2Storage):
        with connect() as conn:
            for page in backend.client.get_paginator('list_objects_v2').paginate(Bucket=backend.bucket):
                for obj in page.get('Contents',[]):
                    key=obj['Key']
                    reason='google' if re.fullmatch(r'photos/[^/]+/google-[^/]+',key) else 'map' if key.startswith(('map-snippets/','map-thumbnails/')) else None
                    if reason: conn.execute('insert into retention_object_purge(object_key,bytes,reason) values(%s,%s,%s) on conflict do nothing',(key,obj['Size'],reason))
    with connect() as conn: rows=conn.execute('select * from retention_object_purge where deleted_at is null order by object_key').fetchall()
    result={'planned':len(rows),'deleted_objects':0,'deleted_bytes':0,'already_absent':0,'failures':0}
    for row in rows:
        key=row['object_key']; size=0; present=False
        try:
            present=backend.exists(key)
            if present:
                size=backend.client.head_object(Bucket=backend.bucket,Key=key)['ContentLength'] if isinstance(backend,storage.R2Storage) else backend._path(key).stat().st_size
            if not apply: continue
            backend.delete(key)
            if backend.exists(key): raise RuntimeError('Object still present')
            hashed=key.removeprefix('thumbs/').removesuffix('.jpg')
            if HASH.fullmatch(hashed):
                from worker.imagery import cache_dir
                (cache_dir()/(hashed+'.jpg')).unlink(missing_ok=True)
            with connect() as conn: conn.execute('update retention_object_purge set bytes=%s,deleted_at=now(),error_type=null where object_key=%s',(size,key))
            result['deleted_objects']+=int(present);result['deleted_bytes']+=size;result['already_absent']+=int(not present)
        except Exception as exc:
            result['failures']+=1
            if apply:
                with connect() as conn: conn.execute('update retention_object_purge set error_type=%s where object_key=%s',(type(exc).__name__,key))
    if result['failures']: raise RuntimeError(json.dumps(result))
    return result

if __name__ == '__main__':
    import sys
    print(json.dumps(run(apply='--apply' in sys.argv)))
