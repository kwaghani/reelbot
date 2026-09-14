"""Seed UI-only examples into an explicitly designated loopback test account.

These synthetic examples are NOT extraction/geocoding evaluation labels.
No real user, source worker, or provider request is used by this script.
"""
import argparse
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from psycopg.types.json import Jsonb
from worker.db import connect, file_entry
from worker.registry import sync_registry


def seed(owner, count):
    url = urlsplit(os.environ['DATABASE_URL'])
    if url.hostname not in {'localhost', '127.0.0.1'} or 'test' not in url.path:
        raise ValueError('Only a disposable loopback test database is allowed')
    with connect() as conn:
        user = conn.execute('select device_id from users where id=%s', (owner,)).fetchone()
        if not user or not user['device_id'].startswith('redesign-fixture-'):
            raise ValueError('Account must be explicitly designated redesign-fixture-*')
        conn.execute('delete from saves where user_id=%s', (owner,))
        conn.execute('delete from folders where user_id=%s', (owner,))
        data = sync_registry(conn)
        examples = [
            ('place', 'Alisa Wine Friends', 'Venice Beach', 'restaurant', 'fMl1pwvXobk'),
            ('recipe', 'Greek yogurt mochi', 'Japanese', 'dessert', 'KyUNiRgraR8'),
            ('workout', 'Full-body strength', 'full_body', 'bodyweight', 'bRWcwVZQXKU'),
            ('place', 'A morning in Silver Lake', 'Silver Lake', 'cafe', 'dSFU6YFT9J0'),
            ('recipe', 'Pad Thai at home', 'Thai', 'dinner', 'k_xf8RwP9Wo'),
            ('place', 'Koreatown noodles', 'Koreatown', 'restaurant', 'c1QfsmBYakU'),
            ('place', 'An afternoon in Hollywood', 'Hollywood', 'attraction', 'dSFU6YFT9J0'),
            ('place', 'Downtown coffee', 'Downtown Los Angeles', 'cafe', 'c1QfsmBYakU'),
        ]
        has_geo = bool(conn.execute("select 1 from information_schema.columns where table_name='places' and column_name='organization_geography'").fetchone())
        for i in range(count):
            kind, title, facet, subkind, video = examples[i % len(examples)]
            if i >= len(examples): title += f' · {i // len(examples) + 1}'
            source = f'https://example.invalid/redesign-layout/{i}'
            source_hash = hashlib.sha256((str(owner) + source).encode()).hexdigest()
            created = datetime.now(timezone.utc) - timedelta(hours=i)
            save = conn.execute('''insert into saves(user_id,source_url,source_hash,url_hash,platform,status,raw_signals,created_at)
                values(%s,%s,%s,%s,'youtube','resolved',%s,%s) returning id''',
                (owner, source, source_hash, source_hash, Jsonb({'fixture': 'UI-only synthetic example', 'thumbnail_url': f'https://i.ytimg.com/vi/{video}/hqdefault.jpg'}), created)).fetchone()
            place = None
            attrs = {'venue_kind': subkind} if kind == 'place' else {'cuisine': facet, 'meal_type': subkind} if kind == 'recipe' else {'muscle_group': [facet], 'equipment': [subkind]}
            if kind == 'place':
                place = conn.execute('''insert into places(provider,provider_place_id,name,formatted_address,lat,lng,primary_type,city)
                    values('layout_fixture',%s,%s,%s,34.0522,-118.2437,%s,%s) returning *''',
                    (str(uuid4()), title, f'100 Example Street, {facet}, Los Angeles, CA, USA', subkind, facet)).fetchone()
                if has_geo:
                    geo = {'city': 'Los Angeles', 'neighborhood': facet, 'country': 'US', 'region': 'CA', 'source': 'synthetic_ui_fixture'}
                    conn.execute('update places set organization_geography=%s where id=%s', (Jsonb(geo), place['id']))
                    place['organization_geography'] = geo
            row = conn.execute('''insert into entries(user_id,save_id,place_id,content_type,title,summary,attributes,confidence,
                needs_review,review_reason,candidate,candidate_key,created_at)
                values(%s,%s,%s,%s,%s,'An example saved entry for layout testing.',%s,.9,%s,%s,%s,%s,%s) returning *''',
                (owner, save['id'], place['id'] if place else None, kind, title, Jsonb(attrs), count >= 20 and i == 5,
                 'low_confidence' if count >= 20 and i == 5 else None, Jsonb({'city_hint': facet if kind == 'place' else None}), str(uuid4()), created)).fetchone()
            file_entry(conn, row, place, data)
        return {'fixture': 'UI only; not geocoding labels', 'entries': count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--count', type=int, choices=[0, 1, 4, 5, 25], required=True)
    args = parser.parse_args()
    print(seed(args.owner, args.count))
