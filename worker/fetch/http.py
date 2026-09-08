"""Bounded public HTTP, one browser profile, optional proxy, shared rate limits."""
from __future__ import annotations
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]

def settings():
    return yaml.safe_load((ROOT / 'config/ingestion.yaml').read_text())

def platform(url):
    host = (urlsplit(url).hostname or '').lower()
    return 'instagram' if 'instagram' in host or host == 'instagr.am' or host.endswith('facebook.com') else 'tiktok' if host.endswith('tiktok.com') else 'youtube'

def headers(url):
    result = dict(settings()['header_profile'])
    result['Referer'] = {'tiktok': 'https://www.tiktok.com/', 'instagram': 'https://www.instagram.com/', 'youtube': 'https://www.youtube.com/'}[platform(url)]
    return result

@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes
    @property
    def text(self): return self.body.decode('utf-8', errors='replace')

class FetchError(Exception):
    def __init__(self, state, detail, *, status=None, bytes_read=0):
        super().__init__(detail)
        self.state, self.detail, self.status, self.bytes_read = state, detail, status, bytes_read

def rate_limit(url, timeout):
    """Reserve platform slots transactionally across worker processes, then sleep unlocked."""
    if os.getenv('REELBOT_DISABLE_RATE_LIMIT') == '1': return
    from worker.db import connect
    key = platform(url)
    interval = settings()['request_interval_seconds'][key] + random.uniform(0, .25)
    with connect() as conn:
        conn.execute('insert into fetch_rate_limits(platform) values(%s) on conflict do nothing', (key,))
        row = conn.execute('select greatest(0,extract(epoch from next_at-now())) as delay from fetch_rate_limits where platform=%s for update', (key,)).fetchone()
        delay = float(row['delay'])
        if delay >= timeout: raise FetchError('fetch_blocked', 'Local platform request queue is busy.')
        conn.execute("update fetch_rate_limits set next_at=greatest(next_at,now())+(%s * interval '1 second') where platform=%s", (interval, key))
    if delay: time.sleep(delay)

def get(url, *, timeout=8, max_bytes=2_000_000, rate=True, extra_headers=None):
    from worker.media import validate_public_url
    deadline = time.monotonic() + timeout
    validate_public_url(url)
    if rate: rate_limit(url, timeout)
    timeout=max(.1,deadline-time.monotonic())
    proxy = os.getenv('REELBOT_FETCH_PROXY', '').strip() or None
    try:
        with httpx.Client(proxy=proxy, headers={**headers(url), **(extra_headers or {})}, timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url) as response:
                chunks=[]; count=0
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > max_bytes: raise FetchError('needs_source_info', 'Source response exceeded the byte limit.', bytes_read=count)
                    if time.monotonic() > deadline: raise FetchError('needs_source_info', 'Source response exceeded the time limit.', bytes_read=count)
                    chunks.append(chunk)
                return Response(str(response.url), response.status_code, dict(response.headers), b''.join(chunks))
    except FetchError: raise
    except httpx.HTTPError as exc:
        # Proxy credentials and provider bodies never appear in diagnostics.
        raise FetchError('needs_source_info', type(exc).__name__) from exc

def check_response(response):
    if response.status in (401, 403, 429):
        raise FetchError('fetch_blocked', 'The platform blocked this request.', status=response.status, bytes_read=len(response.body))
    if response.status in (404, 410):
        raise FetchError('fetch_not_found', 'The post is deleted or private.', status=response.status, bytes_read=len(response.body))
    if response.status >= 400:
        raise FetchError('needs_source_info', 'The platform could not complete this request.', status=response.status, bytes_read=len(response.body))
