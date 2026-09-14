"""Repair administrative anchors in place, keeping entry ownership, notes and history."""
from pathlib import Path
from psycopg.types.json import Jsonb
from worker.registry import sync_registry,attribute_fields
from worker.venue_identity import administrative_type,administrative_name,infer_kind
from worker.places import resolution_guard
VERSION='20260910000049_venue_identity'

def repair(conn):
    records=[]
    before=conn.execute('select id,user_id,save_id,note from entries order by id').fetchall()
    custom=conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    sync_registry(conn)
    for entry in conn.execute('select * from entries order by id').fetchall():
        place=conn.execute('select * from places where id=%s',(entry['place_id'],)).fetchone() if entry['place_id'] else None
        reason=resolution_guard(place,{'venue_kind':entry['venue_kind']}) if place else None
        if reason:
            conn.execute('insert into venue_identity_repairs(entry_id,previous_place_id,previous_candidate,reason) values(%s,%s,%s,%s) on conflict do nothing',
                (entry['id'],entry['place_id'],Jsonb(entry['candidate']),reason))
            conn.execute("""update entries set place_id=null,needs_review=true,verified_at=null,
                confidence=least(confidence,0.3),review_reason=%s,embedding=null,updated_at=now() where id=%s""",(reason+';unresolved_place',entry['id']))
            entry.update(place_id=None,needs_review=True,verified_at=None);place=None
            records.append({'entry_id':str(entry['id']),'title':entry['title'],'reason':reason})
        if entry['content_type']=='place':
            from worker.venue_kinds import classify_entry
            classify_entry(conn,entry,place)
    assert before==conn.execute('select id,user_id,save_id,note from entries order by id').fetchall()
    assert custom==conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    return {'entries_preserved':len(before),'custom_memberships_preserved':len(custom),'flagged_entries':records,'flagged_count':len(records)}

def run(conn):
    conn.execute('select pg_advisory_xact_lock(735007)')
    conn.execute((Path(__file__).parent/'migrations'/f'{VERSION}.sql').read_text(),prepare=False)
    report=repair(conn)
    conn.execute('insert into schema_migrations(id,report) values(%s,%s) on conflict(id) do update set report=excluded.report',(VERSION,Jsonb(report)))
    return report
