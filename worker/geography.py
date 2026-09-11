"""Cached administrative locality for organization; never selects or moves a pin."""
import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml
from psycopg.types.json import Jsonb

FIELD_MASK = 'id,addressComponents'


@lru_cache(maxsize=1)
def settings():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / 'config/organization.yaml').read_text())


def normalize_geography(geography):
    """Municipality and country/state come from the provider, not extraction text."""
    result = dict(geography or {})
    locality = result.get('city') or ''
    for metro in settings()['metros']:
        if result.get('country') != metro['country'] or result.get('region') != metro['region']:
            continue
        aliases = metro['localities'] + metro['neighborhoods']
        if locality.casefold() in {value.casefold() for value in aliases}:
            result['provider_city'] = locality
            result['city'] = metro['city']
            if locality.casefold() != metro['city'].casefold() and not result.get('neighborhood'):
                result['neighborhood'] = locality
            break
    return result


def from_components(components):
    def value(kind, short=False):
        return next((part.get('shortText' if short else 'longText', '') for part in components if kind in part.get('types', [])), '')
    return {'city': value('locality') or value('postal_town'),
            'neighborhood': value('neighborhood') or value('sublocality_level_2') or value('sublocality_level_1') or value('sublocality'),
            'country': value('country', True), 'region': value('administrative_area_level_1', True), 'source': 'google_address_components'}


def fetch_geography(identifier):
    request = Request('https://places.googleapis.com/v1/places/' + quote(identifier, safe=''), headers={
        'X-Goog-Api-Key': os.environ['GOOGLE_MAPS_API_KEY'], 'X-Goog-FieldMask': FIELD_MASK})
    with urlopen(request, timeout=7) as response:
        data = json.load(response)
    if data.get('id') != identifier:
        raise ValueError('Provider identity differs from the saved place')
    return from_components(data.get('addressComponents') or [])


def enrich_owner(owner, fetch=fetch_geography, *, enabled=None):
    """At most two cached metadata lookups after sync; no extraction/geocoding work."""
    from worker.db import connect, file_entry
    if enabled is None:
        enabled = os.getenv('REELBOT_GEOGRAPHY_LOOKUPS', '1') != '0' and bool(os.getenv('GOOGLE_MAPS_API_KEY'))
    metrics = {'provider_calls': 0, 'estimated_usd': 0.0, 'refiled_entries': 0}
    try:
        with connect() as conn:
            if enabled:
                candidates = conn.execute('''select p.* from places p where p.google_place_id is not null
                    and exists(select 1 from entries e where e.place_id=p.id and e.user_id=%s and e.content_type='place')
                    and (p.organization_checked_at is null or p.organization_checked_at < now() -
                      case when p.organization_geography is null then %s * interval '1 hour' else %s * interval '1 day' end)
                    order by p.organization_checked_at nulls first,p.id limit %s for update skip locked''',
                    (owner, settings()['failed_lookup_retry_hours'], settings()['locality_cache_days'], settings()['max_lookups_per_sync'])).fetchall()
                for place in candidates:
                    metrics['provider_calls'] += 1
                    metrics['estimated_usd'] += settings()['google_details_essentials_usd']
                    try:
                        geography = fetch(place['google_place_id'])
                    except Exception:
                        geography = None  # A failed lookup never invents a locality or changes the pin.
                    conn.execute('''update places set organization_geography=coalesce(%s,organization_geography),
                        organization_checked_at=now(),organization_calls=organization_calls+1,
                        organization_cost_usd=organization_cost_usd+%s where id=%s''',
                        (Jsonb(geography) if geography is not None else None, settings()['google_details_essentials_usd'], place['id']))
            rows = conn.execute('''select e.*,p.organization_geography as cached_geography from entries e join places p on p.id=e.place_id
                where e.user_id=%s and e.content_type='place' and p.organization_geography is not null''', (owner,)).fetchall()
            for entry in rows:
                place = {'organization_geography': entry.pop('cached_geography')}
                geo = normalize_geography(place['organization_geography'])
                neighborhood = geo.get('neighborhood', '')
                if entry.get('organization_city') == geo.get('city') and entry['attributes'].get('neighborhood'):
                    neighborhood = entry['attributes']['neighborhood']
                if entry.get('organization_city', '') != geo.get('city', '') or entry['attributes'].get('neighborhood', '') != neighborhood:
                    file_entry(conn, entry, place)
                    metrics['refiled_entries'] += 1
    except Exception:
        # Background organization must never make /sync or durable saving fail.
        import logging
        logging.getLogger(__name__).exception('Organization refresh failed')
    return metrics


def migrate_folders(conn):
    """Refiles automatic memberships transactionally, retaining custom organization."""
    from worker.db import file_entry
    before = conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    custom_before = conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    old_folders = conn.execute("select * from folders where kind='auto_facet' and content_type='place' and facet_key='city'").fetchall()
    legacy = {}
    for folder in old_folders:
        for metro in settings()['metros']:
            if folder['name'].casefold() in {name.casefold() for name in metro['neighborhoods']}:
                for row in conn.execute('select entry_id from folder_items where folder_id=%s and user_id=%s', (folder['id'], folder['user_id'])).fetchall():
                    legacy[str(row['entry_id'])] = {'city': metro['city'], 'neighborhood': folder['name'], 'source': 'legacy_folder_migration'}
    for entry in conn.execute("select * from entries where content_type='place'").fetchall():
        place = conn.execute('select * from places where id=%s', (entry['place_id'],)).fetchone() if entry['place_id'] else None
        geo = normalize_geography((place or {}).get('organization_geography'))
        prior = legacy.get(str(entry['id']))
        if prior:
            if not geo.get('city'): geo = prior
            elif geo['city'] == prior['city']: geo['neighborhood'] = prior['neighborhood']
        file_entry(conn, entry, place, organization=geo)
    after = conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    custom_after = conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    if before != after or custom_before != custom_after:
        raise RuntimeError('Organization preservation check failed; transaction must roll back')
    removed = sum(not conn.execute('select 1 from folders where id=%s', (f['id'],)).fetchone() for f in old_folders)
    return {'entries_before': len(before), 'entries_after': len(after), 'identities_owners_notes_pins_preserved': True,
            'custom_memberships_preserved': True, 'old_city_folders_removed': removed, 'legacy_neighborhood_entries': len(legacy)}
