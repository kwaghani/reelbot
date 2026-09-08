"""Resolve shared links to stable platform identity before fetching content."""
from __future__ import annotations
import re
import time
from urllib.parse import urljoin, urlsplit
from worker.reel_urls import canonical_reel_url
from worker.fetch.http import get, check_response, FetchError, platform

def video_identity(url):
    url = canonical_reel_url(url)
    kind = platform(url)
    if kind == 'tiktok': match = re.search(r'/video/(\d+)', url)
    elif kind == 'instagram': match = re.fullmatch(r'/reel/([\w-]+)/', urlsplit(url).path)
    else: match = re.search(r'[?&]v=([\w-]{11})', url)
    return (kind, match[1]) if match else (kind, None)

def is_shortlink(url):
    return video_identity(url)[1] is None

def resolve_url(source_url, *, request=get, use_cache=True):
    import signal,threading
    if threading.current_thread() is not threading.main_thread():
        return _resolve_url(source_url,request=request,use_cache=use_cache)
    previous_handler=signal.getsignal(signal.SIGALRM)
    previous_timer=signal.getitimer(signal.ITIMER_REAL);start=time.monotonic()
    def expired(*_):raise FetchError('resolve_failed','Link resolution exceeded ten seconds.')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,min(10,previous_timer[0]) if previous_timer[0] else 10)
    try:return _resolve_url(source_url,request=request,use_cache=use_cache)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous_handler)
        if previous_timer[0]:signal.setitimer(signal.ITIMER_REAL,max(.001,previous_timer[0]-(time.monotonic()-start)),previous_timer[1])


def _resolve_url(source_url, *, request=get, use_cache=True):
    original = source_url.strip()
    current = canonical_reel_url(original)
    kind, identifier = video_identity(current)
    trace=[]
    if identifier and not urlsplit(current).path.startswith('/@/video/'):
        return {'source_url':original, 'canonical_url':current, 'platform':kind, 'platform_video_id':identifier, 'hops':trace, 'cached':False}
    if use_cache:
        from worker.db import connect
        with connect() as conn:
            cached = conn.execute("select canonical_url,platform,platform_video_id from source_url_cache where source_url=%s and fetched_at>now()-interval '30 days'", (current,)).fetchone()
        if cached: return {'source_url':original, **cached, 'hops':trace, 'cached':True}
    initial=current; visited=set(); deadline=time.monotonic()+10
    try:
        for _ in range(5):
            if current in visited: raise FetchError('resolve_failed','Redirect loop.')
            visited.add(current)
            remaining=deadline-time.monotonic()
            if remaining <= 0: raise FetchError('resolve_failed','Link resolution timed out.')
            response=request(current, timeout=min(remaining, 8))
            trace.append({'url':current,'http_status':response.status,'bytes':len(response.body)})
            check_response(response)
            target=response.headers.get('location') if response.status in (301,302,303,307,308) else None
            if not target:
                from worker.fetch.parsing import Page
                page=Page(response.text)
                target=page.refresh or page.canonical
                if kind=='tiktok' and identifier and (not target or '/@/video/' in target):
                    from worker.fetch.parsing import parse_html
                    post=parse_html(response.text,identifier)
                    creator=post.get('creator_handle','')
                    if re.fullmatch(r'[\w.-]+',creator):target=f'https://www.tiktok.com/@{creator}/video/{identifier}'
                if not target:
                    match=re.search(r'''(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)|location\.(?:replace|assign)\(\s*["']([^"']+)''',response.text)
                    if match: target=match[1] or match[2]
            if not target: raise FetchError('resolve_failed','No video redirect was supplied.')
            # Only supported public-platform links can continue the chain.
            current=canonical_reel_url(urljoin(current,target))
            kind,identifier=video_identity(current)
            if identifier and not urlsplit(current).path.startswith('/@/video/'):
                result={'source_url':original,'canonical_url':current,'platform':kind,'platform_video_id':identifier,'hops':trace,'cached':False}
                if use_cache:
                    from worker.db import connect
                    with connect() as conn:
                        for alias in visited | {initial}:
                            conn.execute('''insert into source_url_cache(source_url,canonical_url,platform,platform_video_id)
                                values(%s,%s,%s,%s) on conflict(source_url) do update set canonical_url=excluded.canonical_url,
                                platform_video_id=excluded.platform_video_id,platform=excluded.platform,fetched_at=now()''',(alias,current,kind,identifier))
                return result
        raise FetchError('resolve_failed','Link exceeded five redirect hops.')
    except Exception as exc:
        error=FetchError('resolve_failed', str(exc) if isinstance(exc,FetchError) else 'The redirect did not identify a supported video.')
        error.trace=trace
        raise error from exc
