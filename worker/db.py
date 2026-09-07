"""Personal persistence. All library operations require an authenticated owner."""
from __future__ import annotations
import hashlib
import os
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.reel_urls import canonical_reel_url
load_dotenv()


def connect():
    return psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=8)


def vector_literal(values):
    if len(values) != 384:
        raise ValueError('Embedding must have 384 dimensions')
    return '[' + ','.join(str(float(v)) for v in values) + ']'


def save_hash(user_id, url):
    # Globally unique index; the same source can belong privately to different people.
    return hashlib.sha256((str(user_id)+':'+canonical_reel_url(url)).encode()).hexdigest()


def enqueue(conn, user_id, url):
    url = canonical_reel_url(url)
    platform = 'instagram' if 'instagram.com' in url else 'tiktok' if 'tiktok.com' in url else 'youtube'
    row = conn.execute('''insert into saves(user_id,source_url,url_hash,platform)
        values(%s,%s,%s,%s) on conflict(url_hash) do update set source_url=excluded.source_url returning *''',
        (user_id,url,save_hash(user_id,url),platform)).fetchone()
    job = conn.execute('''insert into jobs(user_id,save_id,payload,status) values(%s,%s,%s,%s)
        on conflict(save_id) do update set save_id=excluded.save_id returning id''',
        (user_id,row['id'],Jsonb({'url':url}),row['status'])).fetchone()
    return {**row,'job_id':job['id']}


def fail_expired(conn):
    rows = conn.execute('''update saves set status='failed',error_reason='Processing timed out. You can retry.',
        retry_at=case when attempts<2 then now()+interval '15 seconds' else null end,
        attempts=greatest(attempts,1),updated_at=now(),resolved_at=now()
        where (status='processing' and started_at<=now()-interval '58 seconds')
            or (status='queued' and updated_at<=now()-interval '58 seconds') returning id''').fetchall()
    for row in rows:
        conn.execute("update jobs set status='failed',error_reason='Processing timed out. You can retry.',updated_at=now() where save_id=%s",(row['id'],))
    return len(rows)


def claim(conn):
    fail_expired(conn)
    # The first attempt includes queue wait; manual retries receive a fresh arrival timestamp.
    row = conn.execute('''update saves set status='processing',
        started_at=case when attempts=0 then updated_at else now() end,updated_at=now(),
        attempts=attempts+1,error_reason=null,retry_at=null,resolved_at=null where id=(
        select id from saves where status='queued' or (status='failed' and attempts<2 and retry_at<=now())
        order by created_at for update skip locked limit 1) returning *''').fetchone()
    if row:
        conn.execute("update jobs set status='processing',updated_at=now() where save_id=%s",(row['id'],))
    return row


ITEMS_SQL = '''select u.id,u.user_id,u.save_id,u.place_id,u.note,u.confidence,u.needs_review,u.candidate,
    u.created_at,coalesce(p.name,u.candidate->>'name','Unconfirmed venue') as name,
    coalesce(p.city,u.candidate->>'city_hint','') as city,p.formatted_address,p.lat,p.lng,p.primary_type,
    p.google_place_id,s.source_url,s.status,
    coalesce((select jsonb_agg(jsonb_build_object('id',f.id,'name',f.name,'kind',f.kind))
      from folder_items fi join folders f on f.id=fi.folder_id
      where fi.user_place_id=u.id and fi.user_id=u.user_id),'[]') as folders
    from user_places u join saves s on s.id=u.save_id left join places p on p.id=u.place_id'''


def items(conn,user_id):
    return conn.execute(ITEMS_SQL+' where u.user_id=%s order by u.created_at desc',(user_id,)).fetchall()


def file_place(conn, row, place):
    owner, item_id = row['user_id'],row['id']
    conn.execute('update user_places set embedding=null,updated_at=now() where id=%s and user_id=%s',(item_id,owner))
    # Re-resolution only replaces automatic assignments; personal organization survives retries.
    conn.execute('''delete from folder_items fi using folders f where fi.folder_id=f.id
        and fi.user_place_id=%s and fi.user_id=%s and f.kind<>'custom' ''',(item_id,owner))
    targets = [('needs_review','Needs Review','alert-circle')] if row['needs_review'] else []
    if place:
        from worker.places import category
        if place.get('city'):
            targets.append(('auto_city',place['city'],'location'))
        targets.append(('auto_category',category(place.get('primary_type')),'grid'))
    for kind,name,icon in targets:
        folder = conn.execute('''insert into folders(user_id,name,kind,icon) values(%s,%s,%s,%s)
            on conflict(user_id,kind,name) do update set name=excluded.name returning id''', (owner,name,kind,icon)).fetchone()
        conn.execute('insert into folder_items(folder_id,user_place_id,user_id) values(%s,%s,%s) on conflict do nothing',
                     (folder['id'],item_id,owner))


def store_candidate(conn,save,candidate,place,confidence,reason):
    from worker.places import lookup_key
    candidate = {**candidate, 'review_reason':reason}
    row = conn.execute('''insert into user_places(user_id,save_id,place_id,confidence,needs_review,candidate,candidate_key)
        values(%s,%s,%s,%s,%s,%s,%s) on conflict(user_id,save_id,candidate_key) do update set
        place_id=excluded.place_id,confidence=excluded.confidence,needs_review=excluded.needs_review,
        candidate=excluded.candidate,updated_at=now() returning *''',
        (save['user_id'],save['id'],place['id'] if place else None,confidence,
         not place or confidence<0.6,Jsonb(candidate),lookup_key(candidate))).fetchone()
    file_place(conn,row,place)
    return row
