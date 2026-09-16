"""Cached administrative locality for organization; never selects or moves a pin."""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml
from psycopg.types.json import Jsonb
from config import settings as runtime_settings

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
        'X-Goog-Api-Key': runtime_settings().google_maps_api_key or '', 'X-Goog-FieldMask': FIELD_MASK})
    with urlopen(request, timeout=7) as response:
        data = json.load(response)
    if data.get('id') != identifier:
        raise ValueError('Provider identity differs from the saved place')
    return from_components(data.get('addressComponents') or [])


def enrich_owner(owner, fetch=fetch_geography, *, enabled=None):
    """Google locality caching is disabled; organization uses extraction only."""
    return {'provider_calls':0,'estimated_usd':0.0,'refiled_entries':0}


def migrate_folders(conn):
    """Refiles automatic memberships transactionally, retaining custom organization."""
    from worker.db import file_entry
    before = conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    custom_before = conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    old_folders = conn.execute("select * from folders where deleted_at is null and kind='auto_facet' and content_type='place' and facet_key='city'").fetchall()
    legacy = {}
    for folder in old_folders:
        for metro in settings()['metros']:
            if folder['name'].casefold() in {name.casefold() for name in metro['neighborhoods']}:
                for row in conn.execute('select entry_id from folder_items where deleted_at is null and folder_id=%s and user_id=%s', (folder['id'], folder['user_id'])).fetchall():
                    legacy[str(row['entry_id'])] = {'city': metro['city'], 'neighborhood': folder['name'], 'source': 'legacy_folder_migration'}
    for entry in conn.execute("select * from entries where content_type='place'").fetchall():
        place = conn.execute('select * from places where id=%s', (entry['place_id'],)).fetchone() if entry['place_id'] else None
        geo = {'city':(entry.get('candidate') or {}).get('city_hint') or (place or {}).get('extracted_city') or '',
               'neighborhood':entry['attributes'].get('neighborhood','')}
        prior = legacy.get(str(entry['id']))
        if prior:
            if not geo.get('city'): geo = prior
            elif geo['city'] == prior['city']: geo['neighborhood'] = prior['neighborhood']
        file_entry(conn, entry, place, organization=geo)
    after = conn.execute('select id,user_id,save_id,place_id,note from entries order by id').fetchall()
    custom_after = conn.execute("select fi.* from folder_items fi join folders f on f.id=fi.folder_id where f.kind='custom' order by fi.folder_id,fi.entry_id").fetchall()
    if before != after or custom_before != custom_after:
        raise RuntimeError('Organization preservation check failed; transaction must roll back')
    removed = sum(not conn.execute('select 1 from folders where id=%s and deleted_at is null', (f['id'],)).fetchone() for f in old_folders)
    return {'entries_before': len(before), 'entries_after': len(after), 'identities_owners_notes_pins_preserved': True,
            'custom_memberships_preserved': True, 'old_city_folders_removed': removed, 'legacy_neighborhood_entries': len(legacy)}
