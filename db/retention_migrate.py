"""Forward-only Google-content purge; dry inventory first, counts but no payload backup.

Run with all older writers stopped. The previous program cannot run after this
migration. Retained notes, source evidence, and custom memberships are verified.
"""
import json
from pathlib import Path
from psycopg.types.json import Jsonb
from worker.retention_policy import safe_candidate

VERSION = '20260915000000_google_retention'
DROP_PLACE = ('name','city','formatted_address','primary_type','details','details_refreshed_at',
              'resolution_types','organization_geography','organization_checked_at','venue_primary_checked_at','map_thumbnail')


def run(conn):
    if conn.execute('select 1 from schema_migrations where id=%s',(VERSION,)).fetchone(): return {'already_applied': True}
    conn.execute('select pg_advisory_xact_lock(735015)')
    before = conn.execute('select id,user_id,save_id,note from entries order by id').fetchall()
    members = conn.execute("select fi.folder_id,fi.entry_id,fi.user_id,fi.deleted_at from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    conn.execute((Path(__file__).parent/'migrations'/f'{VERSION}.sql').read_text(),prepare=False)
    report = {'places':0,'needs_reextraction':0,'unknown_coordinate_times':0,'class_c_place_rows':0,'entries_scrubbed':0,'diagnostics_scrubbed':0}
    from worker.retention_objects import plan_database, clean_imagery
    report.update(plan_database(conn))
    places = conn.execute('select * from places').fetchall()
    for place in places:
        report['places'] += 1
        if any(place.get(k) for k in DROP_PLACE): report['class_c_place_rows'] += 1
        entries = conn.execute('select * from entries where place_id=%s order by created_at,id',(place['id'],)).fetchall()
        # A candidate is captured BEFORE resolution. Never use p.name as evidence.
        evidence = next((e['candidate'] for e in entries if any(e['candidate'].get(k) for k in ('venue_name','title','name'))), {})
        if not evidence:
            # Recover only unambiguous retained extraction, never a Google alias.
            for entry in entries:
                signals=conn.execute('select raw_signals from saves where id=%s',(entry['save_id'],)).fetchone()['raw_signals'] or {}
                candidates=signals.get('candidates') or signals.get('extracted_candidates') or []
                if isinstance(candidates,list) and len(candidates)==1 and isinstance(candidates[0],dict):
                    evidence=candidates[0];break
        name = evidence.get('venue_name') or evidence.get('title') or evidence.get('name') or ''
        city = evidence.get('city_hint') or ''
        neighborhood = (evidence.get('attributes') or {}).get('neighborhood') or ''
        missing = not bool(name)
        report['needs_reextraction'] += int(missing)
        from worker.venue_kinds import provider_kind
        # A private owner's override must never become the global place kind.
        kind,_,source = provider_kind({'primary_type':place.get('primary_type'),'types':place.get('resolution_types') or []})
        if kind=='other':
            kind=next((e['venue_kind'] for e in entries if e.get('venue_kind') and e.get('venue_kind_source')!='user'),'other')
            source='historical_inference' if kind!='other' else 'unknown'
        if place.get('lat') is not None or place.get('lng') is not None: report['unknown_coordinate_times'] += 1
        conn.execute("""update places set extracted_name=%s,extracted_city=%s,extracted_neighborhood=%s,
            venue_kind=%s,venue_kind_source=%s,needs_reextraction=%s,
            coords_fetched_at=case when lat is not null or lng is not null then now()-interval '30 days' else null end where id=%s""",
            (name,city,neighborhood,kind,source,missing,place['id']))
        conn.execute('update places set imagery=%s where id=%s',(Jsonb(clean_imagery(place.get('imagery'))),place['id']))
        for entry in entries:
            candidate = entry['candidate'] or {}
            own = candidate.get('venue_name') or candidate.get('title') or candidate.get('name')
            # Title is extraction-owned in store_candidate; never replace a user's
            # corrected title. Unknown historic records get a neutral placeholder.
            title = entry['title'] if entry.get('verified_at') else own or 'Saved place'
            attrs = dict(entry['attributes'])
            extracted_attrs = candidate.get('attributes') or {}
            if 'neighborhood' in extracted_attrs: attrs['neighborhood'] = extracted_attrs['neighborhood']
            elif not entry.get('verified_at'): attrs.pop('neighborhood',None)
            conn.execute("""update entries set title=%s,organization_city=%s,attributes=%s,candidate=%s,
                venue_kind_primary_type=null,embedding=null,updated_at=now() where id=%s""",
                (title,candidate.get('city_hint') or '',Jsonb(attrs),Jsonb(safe_candidate(candidate)),entry['id']))
            report['entries_scrubbed'] += 1
    # Payloads can contain provider candidates even when the entry has no pin.
    for table,column in [('entries','candidate'),('saves','raw_signals'),('saves','diagnostics'),('saves','cost'),('events','detail'),('jobs','payload'),('venue_identity_repairs','previous_candidate')]:
        key = 'entry_id' if table=='venue_identity_repairs' else 'id'
        for row in conn.execute(f'select {key},{column} from {table}').fetchall():
            clean = safe_candidate(row[column])
            if clean != row[column]:
                conn.execute(f'update {table} set {column}=%s where {key}=%s',(Jsonb(clean),row[key]))
                report['diagnostics_scrubbed'] += 1
    for row in conn.execute('select platform,platform_video_id,signals,extractions from fetch_cache').fetchall():
        conn.execute('update fetch_cache set signals=%s,extractions=%s where platform=%s and platform_video_id=%s',
            (Jsonb(safe_candidate(row['signals'])),Jsonb(safe_candidate(row['extractions'])),row['platform'],row['platform_video_id']))
    # Old website fallback mixed Google responses with external attribution.
    report['legacy_web_cache_rows_purged']=len(conn.execute("delete from natural_geocode_cache where cache_key like 'web:%%' returning cache_key").fetchall())
    for row in conn.execute('select id,payload from orphaned_items').fetchall():
        value=safe_candidate(row['payload'])
        if isinstance(value,dict):
            for key in ('place','place_name','formatted_address','location_text','lat','lng','city','details','imagery'): value.pop(key,None)
        conn.execute('update orphaned_items set payload=%s where id=%s',(Jsonb(value),row['id']))
    # Sync rows are snapshots. Force a fresh owner-scoped sync, then purge old
    # snapshots; entry/folder identifiers, outbox idempotency and notes survive.
    report['sync_snapshots_purged'] = conn.execute('select count(*) n from sync_changes').fetchone()['n']
    conn.execute('delete from sync_changes')
    conn.execute('update users set sync_floor=sync_version')
    conn.execute("select set_config('reelbot.seed_sync','on',true)")
    conn.execute("update entries set updated_at=now() where deleted_at is null")
    conn.execute("update folders set updated_at=now() where deleted_at is null")
    conn.execute("update saves set updated_at=now() where deleted_at is null")
    conn.execute("update folder_items set updated_at=now() where deleted_at is null")
    conn.execute("delete from city_bias_cache")
    conn.execute('drop table if exists venue_kind_unmapped')
    conn.execute('alter table entries drop column if exists venue_kind_primary_type')
    for column in DROP_PLACE: conn.execute(f'alter table places drop column if exists {column}')
    if before != conn.execute('select id,user_id,save_id,note from entries order by id').fetchall(): raise RuntimeError('Personal data preservation check failed')
    if members != conn.execute("select fi.folder_id,fi.entry_id,fi.user_id,fi.deleted_at from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall(): raise RuntimeError('Custom memberships changed')
    conn.execute('insert into schema_migrations(id,report) values(%s,%s)',(VERSION,Jsonb(report)))
    return report
