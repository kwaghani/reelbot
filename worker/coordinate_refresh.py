"""Bounded coordinate renewal. Deliberately does NOT import or run deletion."""
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from urllib.request import Request, urlopen
from psycopg.types.json import Jsonb
from config import settings
from worker.db import connect

LOG = logging.getLogger(__name__)
FIELD_MASK = 'location'


def fetch_location(identifier):
    request = Request('https://places.googleapis.com/v1/places/' + quote(identifier, safe=''), headers={
        'X-Goog-Api-Key': settings().google_maps_api_key or '', 'X-Goog-FieldMask': FIELD_MASK})
    with urlopen(request, timeout=8) as response: data = json.load(response)
    from worker.places import valid_location
    if not valid_location(data.get('location')): raise ValueError('missing_coordinates')
    return data['location']


def refresh_place(identifier, fetch=fetch_location, *, force=False, at=None):
    """One concurrent request per place, short bounded retries; no personal payloads."""
    from worker.retention_policy import valid_coordinates
    at = at or datetime.now(timezone.utc)
    stats = {'attempted': 0, 'succeeded': 0, 'failed': 0, 'provider_calls': 0, 'estimated_usd': 0.0}
    with connect() as conn:
        locked = conn.execute('select pg_try_advisory_xact_lock(hashtextextended(%s,9)) ok', ('coordinates:' + str(identifier),)).fetchone()['ok']
        if not locked: return stats
        place = conn.execute('select * from places where id=%s', (identifier,)).fetchone()
        if not place or not place.get('google_place_id'): return stats
        if valid_coordinates(place, at) and (not force or at-place['coords_fetched_at'] < timedelta(days=25)): return stats
        if place.get('coords_retry_at') and place['coords_retry_at'] > at: return stats
        stats['attempted'] = 1
        for attempt in range(settings().coords_retries):
            stats['provider_calls'] += 1
            stats['estimated_usd'] += settings().coords_usd_per_call
            try:
                point = fetch(place['google_place_id'])
                from worker.places import valid_location
                if not valid_location(point): raise ValueError('invalid_coordinates')
                conn.execute('update places set lat=%s,lng=%s,coords_fetched_at=%s,coords_retry_at=null where id=%s',
                             (point['latitude'], point['longitude'], at, identifier))
                # Place changes must reach every owner through commit-ordered sync.
                conn.execute("select set_config('reelbot.seed_sync','on',true)")
                conn.execute('update entries set updated_at=now() where place_id=%s and deleted_at is null', (identifier,))
                stats['succeeded'] = 1
                break
            except Exception as exc:
                LOG.warning('coordinate_refresh_failed place_id=%s attempt=%s error_type=%s', identifier, attempt + 1, type(exc).__name__)
                if attempt + 1 < settings().coords_retries: time.sleep(settings().coords_backoff_seconds * 2 ** attempt)
        if not stats['succeeded']:
            stats['failed'] = 1
            conn.execute("update places set coords_retry_at=%s + interval '1 hour' where id=%s", (at, identifier))
        conn.execute("insert into retention_events(kind,place_id,detail) values('coordinate_refresh',%s,%s)", (identifier, Jsonb(stats)))
    LOG.info('coordinate_refresh place_id=%s field_mask=location stats=%s', identifier, stats)
    return stats


def run(fetch=fetch_location, *, at=None):
    at = at or datetime.now(timezone.utc)
    totals = {'attempted': 0, 'succeeded': 0, 'failed': 0, 'provider_calls': 0, 'estimated_usd': 0.0}
    with connect() as conn:
        rows = conn.execute("""select id from places where google_place_id is not null
            and (coords_fetched_at is null or coords_fetched_at<=%s-interval '25 days')
            and (coords_retry_at is null or coords_retry_at<=%s)
            order by coords_fetched_at nulls first,id limit %s""", (at, at, settings().coords_max_places_per_run)).fetchall()
    started=time.monotonic()
    for offset in range(0,len(rows),settings().coords_batch_size):
        for row in rows[offset:offset+settings().coords_batch_size]:
            if time.monotonic()-started >= 2700: break  # bounded 45-minute provider work window
            result = refresh_place(row['id'], fetch, force=True, at=at)
            for key in totals: totals[key] += result[key]
            time.sleep(settings().coords_rate_seconds)
        if time.monotonic()-started >= 2700: break
    totals['unattempted_in_selection']=len(rows)-totals['attempted']
    with connect() as conn:
        conn.execute("insert into retention_runs(job,finished_at,stats) values('refresh',now(),%s)", (Jsonb(totals),))
    LOG.info('coordinate_refresh_run %s', totals)
    return totals


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    while True:
        try: print(json.dumps(run()), flush=True)
        except Exception: LOG.exception('coordinate_refresh_run_failed')
        if '--daily' not in sys.argv: break
        time.sleep(24 * 60 * 60)
