"""Auditable, network-free identity repair; dry-run rolls back the entire plan."""
import argparse
import json
from psycopg.types.json import Jsonb
from worker.db import connect, file_entry
from worker.venue_kinds import classify_entry
from worker.venue_identity import inputs_for
from worker.sponsors import is_sponsor
from worker.category_coherence import contexts_for, assess, log_veto

def repair(conn):
    conn.execute("set local lock_timeout='5s'")
    conn.execute("set local statement_timeout='30s'")
    conn.execute("select pg_advisory_xact_lock(hashtextextended('identity-safety-v1',0))")
    rows=conn.execute("select * from entries where content_type='place' order by id for update").fetchall()
    before=[(r['id'],r['user_id'],r['save_id'],r['note']) for r in rows]
    overrides={r['id']:r['venue_kind'] for r in rows if r['venue_kind_source']=='user'}
    custom=conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by folder_id,entry_id").fetchall()
    changed=[];flagged=[];saves={}
    for entry in rows:
        save_id=entry['save_id']
        if save_id not in saves:
            save=conn.execute('select * from saves where id=%s for update',(save_id,)).fetchone()
            signals=dict(save['raw_signals'] or {})
            signals.update(inputs_for(signals).payload())
            conn.execute('update saves set raw_signals=%s where id=%s',(Jsonb(signals),save_id))
            saves[save_id]=signals
        signals=saves[save_id]
        place=conn.execute('select * from places where id=%s',(entry['place_id'],)).fetchone() if entry['place_id'] else None
        lookup={'name':entry['title'],'category_contexts':contexts_for(signals),'venue_kind':entry['attributes'].get('venue_kind','other')}
        reason='sponsor_not_venue' if is_sponsor(entry['title'],signals['sponsor_candidates']) or is_sponsor(entry['candidate'].get('venue_name'),signals['sponsor_candidates']) else None
        if place and not reason:
            verdict=assess(place,lookup)
            reason=verdict['reason']
            if reason:log_veto(place,lookup,verdict)
        old_kind=entry['venue_kind']
        original={k:entry[k] for k in ('place_id','venue_kind','venue_kind_source','needs_review','review_reason','candidate','organization_city','attributes')}
        if reason and entry['place_id'] is None and reason in (entry.get('review_reason') or '').split(';'):
            continue
        # An idempotent audit retains the FIRST pre-repair state, not a repaired copy.
        if reason:
            reasons=list(dict.fromkeys(filter(None,[*(entry.get('review_reason') or '').split(';'),reason])))
            conn.execute("update entries set place_id=null,needs_review=true,review_reason=%s,organization_city='',updated_at=now(),embedding=null where id=%s",(';'.join(reasons),entry['id']))
            entry.update(place_id=None,needs_review=True,review_reason=';'.join(reasons),organization_city='')
            place=None
            file_entry(conn,entry,place,organization={})
            conn.execute("update saves set status='needs_review' where id=%s and status not in ('queued','processing')",(save_id,))
            conn.execute("update jobs set status='needs_review',updated_at=now() where save_id=%s and status not in ('queued','processing')",(save_id,))
            flagged.append({'id':str(entry['id']),'title':entry['title'],'reason':reason,'pin_removed':original['place_id'] is not None})
        else:
            classify_entry(conn,entry,place)
        if old_kind!=entry['venue_kind']:
            changed.append({'id':str(entry['id']),'title':entry['title'],'old':old_kind,'new':entry['venue_kind']})
        if reason or old_kind!=entry['venue_kind']:
            key='identity_safety_v1:'+str(entry['id'])
            conn.execute("update saves set diagnostics=diagnostics || %s where id=%s and not diagnostics ? %s",
                         (Jsonb({key:json.loads(json.dumps(original,default=str))}),save_id,key))
    after=conn.execute("select id,user_id,save_id,note,venue_kind from entries where content_type='place' order by id").fetchall()
    if before!=[(r['id'],r['user_id'],r['save_id'],r['note']) for r in after]:raise RuntimeError('Identity/ownership/note preservation failed')
    if any(r['venue_kind']!=overrides[r['id']] for r in after if r['id'] in overrides):raise RuntimeError('User override changed')
    if custom!=conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by folder_id,entry_id").fetchall():raise RuntimeError('Custom memberships changed')
    return {'checked':len(rows),'changed_kinds':changed,'flagged':flagged,'user_overrides_preserved':len(overrides),
            'identities_notes_owners_custom_folders_preserved':True,'provider_calls':0}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true',help='Commit the repair; default is rollback-only dry-run')
    args=parser.parse_args()
    with connect() as conn:
        result=repair(conn)
        if not args.apply:conn.rollback()
    print(json.dumps({'applied':args.apply,**result},indent=2))

if __name__=='__main__':main()
