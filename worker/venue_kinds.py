"""Write-time venue classification. Does not resolve a place or alter any pin."""
import json
import logging
import os
from urllib.parse import quote
from urllib.request import Request, urlopen
from psycopg.types.json import Jsonb
from worker.registry import venue_kinds

LOG = logging.getLogger(__name__)
DETAILS_FIELD_MASK = 'id,primaryType'
DETAILS_ESTIMATED_USD = .017


def kind_for(primary_type, kinds=None):
    kinds = kinds or venue_kinds()
    return next((key for key, spec in kinds.items() if primary_type in spec['google_types']), 'other')


def classify_entry(conn, entry, place=None):
    if entry['content_type'] != 'place': return entry
    if place is None and entry.get('place_id'):
        place = conn.execute('select * from places where id=%s', (entry['place_id'],)).fetchone()
    primary = (place or {}).get('primary_type') or '<missing>'
    if entry.get('venue_kind_source') == 'user':
        kind = entry['venue_kind']
    else:
        from worker.venue_identity import infer_kind,administrative_type
        evidence=entry.get('candidate') or {}
        inferred=evidence.get('kind_inference') or infer_kind({'caption':evidence.get('evidence','')},entry.get('title',''))
        shared=evidence.get('compilation_context') or {}
        shared_kind=shared.get('venue_kind') if shared.get('source')=='reel_shared_context' else None
        kind = shared_kind if shared_kind in venue_kinds() else kind_for(primary) if place and not administrative_type(primary) else 'other'
        if kind=='other':kind=inferred['kind']
        if place and inferred['kind']!='other' and kind!=inferred['kind']:
            LOG.info('venue_kind_disagreement entry=%s signal=%s primary_type=%s provider_kind=%s',entry['id'],inferred['kind'],primary,kind)
        if kind == 'other' and entry.get('venue_kind_primary_type') != primary:
            LOG.warning('venue_kind_unmapped primary_type=%s', primary)
            conn.execute('''insert into venue_kind_unmapped(primary_type) values(%s)
                on conflict(primary_type) do update set occurrences=venue_kind_unmapped.occurrences+1,last_seen_at=now()''', (primary,))
    from worker.registry import attribute_fields
    fields=attribute_fields('place',kind)
    attrs = {**{k:v for k,v in entry['attributes'].items() if k in fields}, 'venue_kind': kind}
    conn.execute('''update entries set venue_kind=%s,venue_kind_primary_type=%s,attributes=%s
        where id=%s and user_id=%s''', (kind, primary, Jsonb(attrs), entry['id'], entry['user_id']))
    entry.update(venue_kind=kind, venue_kind_primary_type=primary, attributes=attrs)
    return entry


def fetch_primary_type(identifier):
    request = Request('https://places.googleapis.com/v1/places/' + quote(identifier, safe=''), headers={
        'X-Goog-Api-Key': os.environ['GOOGLE_MAPS_API_KEY'], 'X-Goog-FieldMask': DETAILS_FIELD_MASK})
    with urlopen(request, timeout=7) as response: data = json.load(response)
    if data.get('id') != identifier: raise ValueError('Provider place identity changed')
    return data.get('primaryType') or None


def backfill(conn, fetch=fetch_primary_type, *, max_lookups=2, enabled=False):
    # One attempt per cached place, persisted even on failure; callers use bounded batches.
    before = conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    links = conn.execute('select * from folder_items order by folder_id,entry_id').fetchall()
    calls = 0
    if enabled:
        rows = conn.execute('''select p.* from places p where coalesce(p.primary_type,'') in ('','other')
            and p.google_place_id is not null and p.venue_primary_checked_at is null
            and exists(select 1 from entries e where e.place_id=p.id and e.content_type='place')
            order by p.id limit %s for update skip locked''', (max_lookups,)).fetchall()
        for place in rows:
            calls += 1
            try: primary = fetch(place['google_place_id'])
            except Exception: primary = None
            conn.execute('''update places set primary_type=coalesce(%s,primary_type),venue_primary_checked_at=now(),
                venue_kind_calls=venue_kind_calls+1,venue_kind_cost_usd=venue_kind_cost_usd+%s where id=%s''',
                (primary, DETAILS_ESTIMATED_USD, place['id']))
    for entry in conn.execute("select * from entries where content_type='place'").fetchall():
        place = conn.execute('select * from places where id=%s', (entry['place_id'],)).fetchone() if entry['place_id'] else None
        classify_entry(conn, entry, place)
    assert before == conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    assert links == conn.execute('select * from folder_items order by folder_id,entry_id').fetchall()
    return {'entries_before': len(before), 'entries_after': len(before), 'pins_notes_ownership_memberships_preserved': True,
        'provider_calls': calls, 'estimated_usd': calls * DETAILS_ESTIMATED_USD,
        'by_kind': conn.execute("select venue_kind,count(*) as entries from entries where content_type='place' group by venue_kind order by venue_kind").fetchall(),
        'unmapped': conn.execute('select primary_type,occurrences from venue_kind_unmapped order by occurrences desc,primary_type').fetchall()}
