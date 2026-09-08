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
    from worker.url_resolve import video_identity
    original=url.strip(); canonical=canonical_reel_url(url)
    platform,identifier=video_identity(canonical)
    if not identifier:
        cached=conn.execute("select canonical_url,platform_video_id from source_url_cache where source_url=%s and fetched_at>now()-interval '30 days'",(canonical,)).fetchone()
        if cached:canonical,identifier=cached['canonical_url'],cached['platform_video_id']
    source_hash=save_hash(user_id,original)
    lock=f'{user_id}:{platform}:{identifier}' if identifier else 'source:'+source_hash
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',(lock,))
    row=conn.execute('''select * from saves where user_id=%s and (source_hash=%s or
        (platform=%s and platform_video_id=%s)) order by created_at limit 1''',(user_id,source_hash,platform,identifier)).fetchone()
    if not row:
        row=conn.execute('''insert into saves(user_id,source_url,source_hash,url_hash,platform,canonical_url,platform_video_id)
            values(%s,%s,%s,%s,%s,%s,%s) returning *''',
            (user_id,original,source_hash,save_hash(user_id,canonical) if identifier else None,platform,canonical if identifier else None,identifier)).fetchone()
    conn.execute('insert into save_source_urls(save_id,user_id,source_url) values(%s,%s,%s) on conflict do nothing',(row['id'],user_id,original))
    job = conn.execute('''insert into jobs(user_id,save_id,payload,status) values(%s,%s,%s,%s)
        on conflict(save_id) do update set save_id=excluded.save_id returning id''',
        (user_id,row['id'],Jsonb({'url':original}),row['status'])).fetchone()
    return {**row,'job_id':job['id']}


def fail_expired(conn):
    rows = conn.execute('''update saves set status='failed',error_reason='Processing timed out. You can retry.',
        retry_at=case when attempts<2 then now()+interval '15 seconds' else null end,
        attempts=greatest(attempts,1),updated_at=now(),resolved_at=now()
        where status='processing' and started_at<=now()-interval '150 seconds' returning id''').fetchall()
    for row in rows:
        conn.execute("update jobs set status='failed',error_reason='Processing timed out. You can retry.',updated_at=now() where save_id=%s",(row['id'],))
    return len(rows)


def claim(conn):
    fail_expired(conn)
    # Waiting in a durable queue is not a failed fetch. Only claimed work has a lease.
    row = conn.execute('''update saves set status='processing',
        started_at=now(),updated_at=now(),
        attempts=attempts+1,error_reason=null,retry_at=null,resolved_at=null where id=(
        select id from saves where status='queued' or (status='failed' and attempts<2 and retry_at<=now())
        or (status='fetch_blocked' and retry_at<=now())
        order by created_at for update skip locked limit 1) returning *''').fetchone()
    if row:
        conn.execute("update jobs set status='processing',updated_at=now() where save_id=%s",(row['id'],))
    return row


ITEMS_SQL = '''select e.*,e.title as name,coalesce(p.city,e.candidate->>'city_hint','') as city,
    p.name as place_name,p.formatted_address,p.lat,p.lng,p.primary_type,p.google_place_id,
    s.source_url,s.canonical_url,s.platform_video_id,s.status,s.raw_signals->>'thumbnail' as thumbnail,
    coalesce((select jsonb_agg(jsonb_build_object('id',f.id,'name',f.name,'kind',f.kind,
        'parent_folder_id',f.parent_folder_id,'content_type',f.content_type,'facet_key',f.facet_key,'facet_value',f.facet_value))
      from folder_items fi join folders f on f.id=fi.folder_id
      where fi.entry_id=e.id and fi.user_id=e.user_id),'[]') as folders
    from entries e join saves s on s.id=e.save_id left join places p on p.id=e.place_id'''


def items(conn,user_id):
    rows=conn.execute(ITEMS_SQL+' where e.user_id=%s order by e.created_at desc,e.id',(user_id,)).fetchall()
    for row in rows: row.pop('embedding',None)
    return rows


def prune_auto_folders(conn,owner):
    conn.execute('''delete from folders f where f.user_id=%s and f.kind='auto_facet'
        and not exists(select 1 from folder_items fi where fi.folder_id=f.id)''',(owner,))
    conn.execute('''delete from folders f where f.user_id=%s and f.kind='auto_type'
        and not exists(select 1 from folder_items fi where fi.folder_id=f.id)
        and not exists(select 1 from folders c where c.parent_folder_id=f.id)''',(owner,))


def file_entry(conn,row,place=None,data=None):
    from worker.registry import registry
    data=data or registry(); spec=data[row['content_type']]
    owner,identifier=row['user_id'],row['id']
    conn.execute('update entries set embedding=null,updated_at=now() where id=%s and user_id=%s',(identifier,owner))
    conn.execute('''delete from folder_items fi using folders f where fi.folder_id=f.id
        and fi.entry_id=%s and fi.user_id=%s and f.kind<>'custom' ''',(identifier,owner))
    parent=conn.execute('''insert into folders(user_id,name,kind,content_type,icon)
        values(%s,%s,'auto_type',%s,%s)
        on conflict(user_id,kind,content_type,parent_folder_id,name) do update set icon=excluded.icon returning id''',
        (owner,spec.get('plural_label',spec['label']),row['content_type'],spec['icon'])).fetchone()['id']
    conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s) on conflict do nothing',(parent,identifier,owner))
    facet=spec.get('primary_facet')
    value=(place or {}).get('city') or row.get('candidate',{}).get('city_hint') if facet=='city' else row['attributes'].get(facet or 'topic')
    values=value if isinstance(value,list) else [value]
    for value in values or [None]:
        display=str(value).strip().replace('_',' ').title() if value else 'Unsorted'
        folder=conn.execute('''insert into folders(user_id,name,kind,content_type,facet_key,facet_value,parent_folder_id,icon)
            values(%s,%s,'auto_facet',%s,%s,%s,%s,%s)
            on conflict(user_id,kind,content_type,parent_folder_id,name) do update set facet_value=excluded.facet_value returning id''',
            (owner,display[:100],row['content_type'],facet or 'topic',str(value) if value else None,parent,spec['icon'])).fetchone()['id']
        conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s) on conflict do nothing',(folder,identifier,owner))
    prune_auto_folders(conn,owner)


def entry_key(candidate):
    from worker.places import normalized
    return hashlib.sha256((candidate['content_type']+'|'+normalized(candidate['title'])+'|'+normalized(candidate.get('city_hint'))).encode()).hexdigest()


def store_candidate(conn,save,candidate,place,confidence,reason=None,*,entry_id=None):
    from worker.registry import sync_registry,validate_attributes
    data=sync_registry(conn)
    attrs,reasons=validate_attributes(candidate['content_type'],candidate['attributes'],data=data)
    reasons.extend(candidate.get('review_reasons',[]))
    if reason: reasons.append(reason)
    if data[candidate['content_type']]['geo']=='required' and not place and reason!='ambiguous_place': reasons.append('unresolved_place')
    if confidence<.6: reasons.append('low_confidence')
    review_reason=';'.join(dict.fromkeys(reasons)) or None
    key=entry_key(candidate)
    # A punctuation/spacing correction must retain the same personal entry and its note.
    from worker.places import normalized
    compact=lambda value:normalized(value).replace(' ','')
    matches=[e for e in conn.execute('select candidate_key,title,candidate from entries where user_id=%s and save_id=%s and content_type=%s',
        (save['user_id'],save['id'],candidate['content_type'])).fetchall()
        if compact(candidate['title']) in {compact(value) for value in (e['title'],e['candidate'].get('title'),e['candidate'].get('name'),e['candidate'].get('venue_name')) if value}
        and normalized(e['candidate'].get('city_hint'))==normalized(candidate.get('city_hint'))]
    if len(matches)==1:key=matches[0]['candidate_key']
    row=conn.execute('''insert into entries(id,user_id,save_id,place_id,content_type,title,summary,attributes,
        confidence,needs_review,review_reason,candidate,candidate_key)
        values(coalesce(%s::uuid,gen_random_uuid()),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(user_id,save_id,candidate_key) do update set
        place_id=case when entries.verified_at is not null then entries.place_id else excluded.place_id end,
        title=case when entries.verified_at is not null then entries.title else excluded.title end,
        summary=case when entries.verified_at is not null then entries.summary else excluded.summary end,
        attributes=case when entries.verified_at is not null then entries.attributes else excluded.attributes end,
        confidence=excluded.confidence,
        needs_review=case when entries.verified_at is not null then false else excluded.needs_review end,
        review_reason=case when entries.verified_at is not null then null else excluded.review_reason end,
        candidate=excluded.candidate,updated_at=now() returning *''',
        (entry_id,save['user_id'],save['id'],place['id'] if place else None,candidate['content_type'],candidate['title'],
         candidate['summary'],Jsonb(attrs),confidence,bool(review_reason),review_reason,Jsonb(candidate),key)).fetchone()
    effective_place=conn.execute('select * from places where id=%s',(row['place_id'],)).fetchone() if row['place_id'] else None
    file_entry(conn,row,effective_place,data)
    return row
