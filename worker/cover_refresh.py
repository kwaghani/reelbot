"""Refresh signed reel-cover URLs before they expire.

TikTok (`x-expires`, unix seconds) and Instagram (`oe`, hex unix seconds) cover
URLs are signed CDN links that stop resolving after two to five days. The save
keeps the URL, not the image, so without renewal every cover silently falls to
the placeholder a few days after it was shared. This job re-reads the public
embed data (TikTok/YouTube oEmbed, Instagram oEmbed or OG tags) and stores the
new URL with its parsed expiry. It never copies creator images anywhere.
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from psycopg.types.json import Jsonb
from worker.db import connect

LOG = logging.getLogger(__name__)
LEAD = timedelta(hours=12)          # renew this long before the signed URL lapses
FAILURE_BACKOFF = timedelta(hours=6)
MAX_PER_RUN = 200
RUN_BUDGET_SECONDS = 600
# A signed URL whose expiry cannot be parsed is renewed on this cadence instead.
UNKNOWN_EXPIRY_TTL = timedelta(days=2)
SIGNED_HOSTS = ('tiktokcdn', 'cdninstagram', 'fbcdn')


def url_expiry(url):
    """Parse the signed-URL expiry; None when the URL carries no expiry parameter."""
    if not url: return None
    query = parse_qs(urlparse(url).query)
    try:
        if query.get('x-expires'): return datetime.fromtimestamp(int(query['x-expires'][0]), timezone.utc)
        if query.get('oe'): return datetime.fromtimestamp(int(query['oe'][0], 16), timezone.utc)
        if query.get('Expires'): return datetime.fromtimestamp(int(query['Expires'][0]), timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def is_signed(url):
    host = (urlparse(url or '').hostname or '').lower()
    return url_expiry(url) is not None or any(part in host for part in SIGNED_HOSTS)


def cover_url(signals):
    signals = signals or {}
    return (signals.get('thumbnail_url') or '') or (signals.get('thumbnail') or '')


def expired(signals, at=None):
    """True when the stored cover URL is known to be past its signed expiry."""
    at = at or datetime.now(timezone.utc)
    stored = (signals or {}).get('thumbnail_expires_at')
    when = datetime.fromisoformat(stored) if stored else url_expiry(cover_url(signals))
    return bool(when and when <= at)


def fresh_cover_url(source_url):
    """Re-read public embed data for a new signed cover URL. No media is downloaded."""
    from worker.url_resolve import resolve_url
    from worker.fetch.ladder import tier_one, tier_two
    from worker.fetch.http import get
    resolved = resolve_url(source_url)
    if not resolved.get('platform_video_id'): raise ValueError('unresolved_source')
    errors = []
    # Tier one is oEmbed (Instagram only with a configured token); tier two is OG/HTML.
    for tier in (tier_one, tier_two):
        try:
            result, _ = tier(resolved, get)
            url = result.get('thumbnail_url')
            if url: return url
        except Exception as exc:
            errors.append(type(exc).__name__)
    raise ValueError('no_fresh_cover:' + ','.join(errors))


def refresh_save(save_id, *, fetch=None, at=None, force=False):
    """Replace one save's cover URL. The previous cover state is kept on failure."""
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        locked = conn.execute('select pg_try_advisory_xact_lock(hashtextextended(%s,11)) ok', ('cover:' + str(save_id),)).fetchone()['ok']
        if not locked: return {'save_id': str(save_id), 'outcome': 'locked'}
        save = conn.execute('select id,source_url,raw_signals from saves where id=%s and deleted_at is null', (save_id,)).fetchone()
        if not save: return {'save_id': str(save_id), 'outcome': 'missing'}
        signals = dict(save['raw_signals'] or {})
        retry = signals.get('thumbnail_retry_at')
        if not force and retry and datetime.fromisoformat(retry) > at:
            return {'save_id': str(save_id), 'outcome': 'backoff'}
        try:
            url = (fetch or fresh_cover_url)(save['source_url'])
            expiry = url_expiry(url) or (at + UNKNOWN_EXPIRY_TTL if is_signed(url) else None)
            patch = {'thumbnail_url': url, 'thumbnail_refreshed_at': at.isoformat(), 'thumbnail_retry_at': None,
                     'thumbnail_expires_at': expiry.isoformat() if expiry else None, 'thumbnail_refresh_error': None}
            outcome = 'refreshed'
        except Exception as exc:
            reason = str(exc)[:120] if isinstance(exc, ValueError) else type(exc).__name__
            patch = {'thumbnail_retry_at': (at + FAILURE_BACKOFF).isoformat(), 'thumbnail_refresh_error': reason}
            outcome = 'failed'
        # jsonb merge: extraction signals, provenance and tier logs are untouched.
        conn.execute("update saves set raw_signals=raw_signals || %s::jsonb where id=%s", (Jsonb(patch), save_id))
    LOG.info('cover_refresh save_id=%s outcome=%s', save_id, outcome)
    return {'save_id': str(save_id), 'outcome': outcome, **({'error': patch['thumbnail_refresh_error']} if outcome == 'failed' else {})}


def due(at=None, *, limit=MAX_PER_RUN):
    """Saves with live entries whose signed cover URL lapses within the lead window."""
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        rows = conn.execute("""select s.id,s.raw_signals from saves s
            where s.deleted_at is null and coalesce(nullif(s.raw_signals->>'thumbnail_url',''),s.raw_signals->>'thumbnail') is not null
              and exists(select 1 from entries e where e.save_id=s.id and e.deleted_at is null)
            order by s.created_at""").fetchall()
    result = []
    for row in rows:
        signals = row['raw_signals'] or {}
        url = cover_url(signals)
        if not is_signed(url): continue
        retry = signals.get('thumbnail_retry_at')
        if retry and datetime.fromisoformat(retry) > at: continue
        stored = signals.get('thumbnail_expires_at')
        expiry = datetime.fromisoformat(stored) if stored else url_expiry(url)
        if expiry is None or expiry - LEAD <= at: result.append(row['id'])
        if len(result) >= limit: break
    return result


INTRINSIC = {'cover_below_400px', 'cover_text_above_25_percent', 'multi_entry_save', 'missing_cover', 'image_belongs_to_another_venue'}


def revalidate(save_id):
    """Resolve a save's card image now, through the owner-scoped imagery path."""
    from worker.imagery import resolve_batch
    with connect() as conn:
        rows = conn.execute('select id,user_id from entries where save_id=%s and deleted_at is null order by id', (save_id,)).fetchall()
    if rows: resolve_batch(rows[0]['user_id'], [row['id'] for row in rows][:8])


def stale_placeholders(at=None):
    """Single-entry saves showing the placeholder although their cover is not known bad."""
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        rows = conn.execute("""select s.id,s.raw_signals,s.cover_imagery from saves s
            where s.deleted_at is null and coalesce(nullif(s.raw_signals->>'thumbnail_url',''),s.raw_signals->>'thumbnail') is not null
              and (select count(*) from entries e where e.save_id=s.id and e.deleted_at is null)=1
              and exists(select 1 from entries e where e.save_id=s.id and e.deleted_at is null and e.image_selection->>'selected'='placeholder')""").fetchall()
    return [row['id'] for row in rows if not expired(row['raw_signals'], at)
            and (row['cover_imagery'] or {}).get('rejection') not in INTRINSIC]


def scrub_legacy_site_urls():
    """Older site-image cache records held Google-derived website/image URLs."""
    with connect() as conn:
        rows = conn.execute("""select id,imagery from places where imagery::text like '%"source": "site"%'""").fetchall()
        changed = 0
        for row in rows:
            imagery = row['imagery'] or {}
            cleaned = strip_site_urls(imagery)
            if 'map' in imagery: cleaned['map'] = strip_site_urls(imagery['map'] or {})
            if cleaned != imagery:
                conn.execute('update places set imagery=%s where id=%s', (Jsonb(cleaned), row['id'])); changed += 1
    return changed


def strip_site_urls(record):
    record = dict(record)
    if any(c.get('source') == 'site' and ('url' in c or 'url' in (c.get('attribution') or {})) for c in record.get('candidates') or []):
        # Without a cached asset the record is dropped, forcing a compliant re-lookup.
        record.pop('candidates', None); record['retry_at'] = ''
    return record


def run(fetch=None, *, at=None):
    at = at or datetime.now(timezone.utc)
    try: scrubbed = scrub_legacy_site_urls()
    except Exception: LOG.exception('site_url_scrub_failed'); scrubbed = None
    totals = {'selected': 0, 'refreshed': 0, 'failed': 0, 'skipped': 0}
    started = time.monotonic()
    ids = due(at); renewed = []
    totals['selected'] = len(ids)
    totals['site_urls_scrubbed'] = scrubbed
    for save_id in ids:
        if time.monotonic() - started >= RUN_BUDGET_SECONDS: break
        outcome = refresh_save(save_id, fetch=fetch, at=at)['outcome']
        if outcome == 'refreshed': renewed.append(save_id)
        totals[outcome if outcome in ('refreshed', 'failed') else 'skipped'] += 1
        time.sleep(0.5)
    totals['unattempted'] = totals['selected'] - totals['refreshed'] - totals['failed'] - totals['skipped']
    # Re-resolve renewed and stale-placeholder saves so cards and /readyz agree
    # without waiting for the owner to scroll past each card.
    totals['revalidated'] = 0
    for save_id in [*renewed, *stale_placeholders(at)][:MAX_PER_RUN]:
        if time.monotonic() - started >= RUN_BUDGET_SECONDS: break
        try:
            revalidate(save_id); totals['revalidated'] += 1
        except Exception as exc:
            LOG.warning('cover_revalidate_failed save_id=%s error_type=%s', save_id, type(exc).__name__)
    with connect() as conn:
        conn.execute("insert into retention_runs(job,finished_at,stats) values('cover_refresh',now(),%s)", (Jsonb(totals),))
    LOG.info('cover_refresh_run %s', totals)
    return totals


def status(at=None):
    """Counts for /readyz: how many live covers are expired or failing right now."""
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        rows = conn.execute("""select s.raw_signals,s.cover_imagery,
              (select count(*) from entries e where e.save_id=s.id and e.deleted_at is null) as live,
              (select count(*) from entries e where e.save_id=s.id and e.deleted_at is null and e.image_selection->>'selected'='placeholder') as placeholders
            from saves s where s.deleted_at is null""").fetchall()
        last = conn.execute("select max(finished_at) as at from retention_runs where job='cover_refresh'").fetchone()['at']
    expired_urls = failing = placeholder_with_source = 0
    intrinsic = INTRINSIC
    for row in rows:
        if not row['live']: continue
        signals = row['raw_signals'] or {}
        url = cover_url(signals)
        if not url: continue
        if expired(signals, at): expired_urls += 1
        rejection = (row['cover_imagery'] or {}).get('rejection')
        if signals.get('thumbnail_refresh_error') or (rejection and rejection not in intrinsic): failing += 1
        # A single-entry save with a live, unexpired cover must never show the placeholder.
        if row['live'] == 1 and row['placeholders'] and not expired(signals, at) and rejection not in intrinsic:
            placeholder_with_source += 1
    return {'expired_cover_urls': expired_urls, 'failing_covers': failing,
            'placeholder_with_available_cover': placeholder_with_source,
            'last_cover_refresh_at': last.isoformat() if last else None}


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    while True:
        try: print(json.dumps(run()), flush=True)
        except Exception: LOG.exception('cover_refresh_run_failed')
        if '--watch' not in sys.argv: break
        time.sleep(60 * 60)
