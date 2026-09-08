"""Canonical identity and lossless consolidation of a person's duplicate saves."""
from psycopg.types.json import Jsonb
from worker.db import save_hash

def bind_identity(conn, saved, resolved):
    key=f"{saved['user_id']}:{resolved['platform']}:{resolved['platform_video_id']}"
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
    other=conn.execute('''select * from saves where user_id=%s and platform=%s
        and platform_video_id=%s and id<>%s order by created_at limit 1 for update''',
        (saved['user_id'],resolved['platform'],resolved['platform_video_id'],saved['id'])).fetchone()
    if other:
        consolidate(conn,saved,other)
        return other,True
    row=conn.execute('''update saves set canonical_url=%s,platform=%s,platform_video_id=%s,url_hash=%s,
        diagnostics=diagnostics || %s,updated_at=now() where id=%s returning *''',
        (resolved['canonical_url'],resolved['platform'],resolved['platform_video_id'],save_hash(saved['user_id'],resolved['canonical_url']),
         Jsonb({'resolution':resolved}),saved['id'])).fetchone()
    return row,False

def consolidate(conn, source, destination):
    """Retain manual notes, verified fields, memberships and all original URL aliases."""
    owner=source['user_id']; mappings=[]
    if owner!=destination['user_id']:raise ValueError('Cannot combine different personal owners')
    for row in conn.execute('select * from entries where save_id=%s for update',(source['id'],)).fetchall():
        existing=conn.execute('select * from entries where user_id=%s and save_id=%s and candidate_key=%s for update',
                              (owner,destination['id'],row['candidate_key'])).fetchone()
        if not existing:
            conn.execute('update entries set save_id=%s where id=%s',(destination['id'],row['id']));continue
        notes=list(dict.fromkeys(v for v in [existing['note'],row['note']] if v))
        conn.execute('update entries set note=%s,updated_at=now(),embedding=null where id=%s',('\n\n'.join(notes),existing['id']))
        if row['verified_at'] and not existing['verified_at']:
            conn.execute('''update entries set title=%s,summary=%s,attributes=%s,place_id=%s,
                needs_review=%s,review_reason=%s,verified_at=%s where id=%s''',
                (row['title'],row['summary'],Jsonb(row['attributes']),row['place_id'],row['needs_review'],row['review_reason'],row['verified_at'],existing['id']))
        conn.execute('''insert into folder_items(folder_id,entry_id,user_id)
            select folder_id,%s,user_id from folder_items where entry_id=%s on conflict do nothing''',(existing['id'],row['id']))
        mappings.append({'from':str(row['id']),'to':str(existing['id']),'original_attributes':row['attributes']})
        conn.execute('delete from entries where id=%s',(row['id'],))
    conn.execute('update save_source_urls set save_id=%s where save_id=%s',(destination['id'],source['id']))
    conn.execute('''update saves set diagnostics=jsonb_set(diagnostics,'{merged_sources}',
        coalesce(diagnostics->'merged_sources','[]') || %s),updated_at=now() where id=%s''',
        (Jsonb([{'save_id':str(source['id']),'source_url':source['source_url'],'signals':source['raw_signals'],'cost':source['cost'],'entries':mappings}]),destination['id']))
    combined=dict(destination['cost'])
    for key,value in source['cost'].items():
        if type(value) in (int,float):combined[key]=combined.get(key,0)+value
        elif isinstance(value,list):combined[key]=combined.get(key,[])+value
    conn.execute('update saves set cost=%s where id=%s',(Jsonb(combined),destination['id']))
    conn.execute('delete from saves where id=%s',(source['id'],))
    from worker.db import prune_auto_folders
    prune_auto_folders(conn,owner)
