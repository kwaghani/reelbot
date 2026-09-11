"""Bounded, owner-authorized venue imagery. Google content is display-only, never cached."""
from __future__ import annotations
import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
from PIL import Image, ImageOps
from psycopg.types.json import Jsonb
from worker.db import connect

log = logging.getLogger(__name__)
MAX_THUMB = 64_000  # Base64 plus JSON still fits below 100KB per displayed image.
SOURCE_RANK = {'google': 1.0, 'site': .98, 'commons': .96, 'cover': .94}
COMMONS_KINDS = {'culture', 'attraction', 'outdoors', 'beach'}
_flights: dict[str, Future] = {}
_flight_lock = threading.Lock()
_network_slots = threading.BoundedSemaphore(4)


def now(): return datetime.now(timezone.utc)
def stamp(): return now().isoformat()
def digest(value): return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()
def cache_dir():
    root = Path(os.getenv('REELBOT_IMAGERY_CACHE', str(Path.home() / 'Library/Caches/ReelBot/imagery')))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def public_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Unsupported image address')
    if p.hostname.lower() in {'localhost', 'metadata.google.internal'}: raise ValueError('Private image address')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ip_address(a[4][0].split('%')[0]).is_global for a in addresses):
        raise ValueError('Private image address')
    return url


def fetch(url, *, limit=8_000_000, timeout=6):
    """No arbitrary proxy URLs reach the client; validate every redirect and cap decoded bytes."""
    deadline = time.monotonic() + timeout
    with _network_slots, httpx.Client(trust_env=False, follow_redirects=False, headers={'User-Agent': 'ReelBot/1.0 (personal venue imagery)'}) as client:
        for _ in range(4):
            public_url(url)
            with client.stream('GET', url, timeout=max(.2, deadline-time.monotonic())) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location']); continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit or time.monotonic() > deadline: raise ValueError('Image response exceeds limit')
                return bytes(data), str(response.url)
    raise ValueError('Too many image redirects')


def dimensions(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 40_000_000: raise ValueError('Image dimensions exceed limit')
        return ImageOps.exif_transpose(image).size


def thumbnail(data):
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > 40_000_000: raise ValueError('Image dimensions exceed limit')
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((540, 675), Image.Resampling.LANCZOS)
        for quality in (85, 72, 58, 44):
            out = io.BytesIO(); image.save(out, 'JPEG', quality=quality, optimize=True)
            if out.tell() <= MAX_THUMB: return out.getvalue(), image.size
        image.thumbnail((270, 338), Image.Resampling.LANCZOS)
        out = io.BytesIO(); image.save(out, 'JPEG', quality=55, optimize=True)
        if out.tell() > MAX_THUMB: raise ValueError('Thumbnail exceeds limit')
        return out.getvalue(), image.size


def rectangle_coverage(boxes, width, height):
    rects = [(max(0, x), max(0, y), min(width, x+w), min(height, y+h)) for x,y,w,h in boxes]
    rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
    xs = sorted({x for r in rects for x in (r[0], r[2])}); area = 0
    for left, right in zip(xs, xs[1:]):
        spans = sorted((r[1], r[3]) for r in rects if r[0] < right and r[2] > left)
        end = -1; covered = 0
        for start, stop in spans:
            covered += max(0, stop-max(start, end)); end = max(end, stop)
        area += (right-left)*covered
    return min(1.0, area/(width*height)) if width and height else 1.0


def text_coverage(data):
    """OCR failures fail closed for covers; no AI/provider call or image upload."""
    with tempfile.TemporaryDirectory(prefix='reelbot-cover-ocr-') as tmp:
        path = Path(tmp)/'cover.png'
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert('RGB'); image.thumbnail((1400,1400)); image.save(path)
            width, height = image.size
        cmd = [os.getenv('TESSERACT_CMD', '/opt/homebrew/bin/tesseract' if Path('/opt/homebrew/bin/tesseract').exists() else 'tesseract'), str(path), 'stdout', '--psm', '11', 'tsv']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True, env={**os.environ, 'OMP_THREAD_LIMIT':'1'})
        boxes = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t'):
            if row.get('text','').strip() and float(row['conf']) >= 30:
                boxes.append(tuple(int(row[k]) for k in ('left','top','width','height')))
        return rectangle_coverage(boxes[:2000], width, height)


def score(candidate):
    width, height = candidate['width'], candidate['height']
    aspect = width/height
    fit = min(aspect/.8, .8/aspect)
    resolution = min(1, min(width,height)/1200)
    text_free = 1-candidate.get('text_coverage',0) if candidate['source']=='cover' else 1
    return round(.40*SOURCE_RANK[candidate['source']] + .25*resolution + .20*fit + .15*text_free, 6)


def store_thumb(data):
    if not 0 < len(data) <= 100000: raise ValueError('Image cache exceeds byte budget')
    key = digest(data); path = cache_dir()/(key+'.jpg')
    with connect() as conn:
        conn.execute('insert into image_assets(content_hash,jpeg) values(%s,%s) on conflict do nothing',(key,data))
    if not path.exists():
        temp = path.with_suffix('.tmp.'+str(threading.get_ident())); temp.write_bytes(data); temp.chmod(0o600); temp.replace(path)
    return key


def cached_thumb(key):
    if not re.fullmatch('[a-f0-9]{64}',key): return None
    path=cache_dir()/(key+'.jpg')
    if path.is_file(): return path.read_bytes()
    with connect() as conn:
        row=conn.execute('select jpeg from image_assets where content_hash=%s',(key,)).fetchone()
    if not row: return None
    data=bytes(row['jpeg'])
    if digest(data)!=key: raise ValueError('Cached image checksum mismatch')
    temp=path.with_suffix('.tmp.'+str(threading.get_ident()));temp.write_bytes(data);temp.chmod(0o600);temp.replace(path)
    return data


def cover_candidate(entry, stats):
    if entry['save_entry_count'] > 1: return None, 'multi_entry_save'
    url = entry.get('cover_url')
    if not url: return None, 'missing_cover'
    saved = entry.get('cover_imagery') or {}
    same = saved.get('url_hash') == digest(url)
    if same and saved.get('retry_at','') > stamp():
        if saved.get('candidate'):
            candidate = saved['candidate']
            if cached_thumb(candidate['asset']) is not None:
                stats['cover_cache_hits'] += 1; return dict(candidate), None
        elif saved.get('rejection'): return None, saved['rejection']
    record = {'url_hash':digest(url),'checked_at':stamp(),'retry_at':(now()+timedelta(days=1)).isoformat()}
    try:
        raw, _ = fetch(url); stats['cover_original_bytes'] += len(raw)
        width,height = dimensions(raw)
        if min(width,height) < 400: raise ValueError('cover_below_400px')
        coverage = text_coverage(raw)
        if coverage > .25: raise ValueError('cover_text_above_25_percent')
        data, _ = thumbnail(raw)
        candidate = {'key':'cover','source':'cover','width':width,'height':height,'text_coverage':round(coverage,5),
                     'asset':store_thumb(data),'content_hash':digest(data),'original_bytes':len(raw),'thumbnail_bytes':len(data),
                     'attribution':{'label':'Creator’s reel','url':entry['source_url']},'license':'Creator source'}
        record.update(candidate=candidate,retry_at=(now()+timedelta(days=30)).isoformat())
    except Exception as exc:
        record['rejection'] = str(exc) if isinstance(exc,ValueError) else 'cover_unavailable_or_ocr_failed'
        candidate = None
    with connect() as conn:
        conn.execute('update saves set cover_imagery=%s where id=%s',(Jsonb(record),entry['save_id']))
    return candidate, record.get('rejection')


class Metadata(HTMLParser):
    def __init__(self): super().__init__(); self.values={}
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('property',a.get('name')) in ('og:image','og:title','og:url'):
            self.values[a.get('property',a.get('name'))]=a.get('content','')
    @classmethod
    def parse(cls, body):
        obj=cls();obj.feed(body.decode('utf-8','replace'));return obj.values


def google_details(place, fields, stats):
    key=os.getenv('GOOGLE_MAPS_API_KEY','').strip()
    if not key or not place.get('google_place_id'): return {}
    stats['google_details_calls'] += 1; stats['field_masks'].append(fields)
    stats['estimated_usd'] += .020 if 'websiteUri' in fields else 0
    log.info('imagery_google_details field_mask=%s',fields)
    with _network_slots, httpx.Client(trust_env=False, timeout=5) as client:
        response=client.get('https://places.googleapis.com/v1/places/'+quote(place['google_place_id'],safe=''),
                            headers={'X-Goog-Api-Key':key,'X-Goog-FieldMask':fields})
        response.raise_for_status(); data=response.json()
        if data.get('id') != place['google_place_id']: raise ValueError('Photo place identity mismatch')
        return data


def google_candidates(place, stats):
    data=google_details(place,'id,photos',stats)
    photos=sorted(data.get('photos',[]),key=lambda p:(p.get('widthPx',0)>=p.get('heightPx',0),p.get('widthPx',0)*p.get('heightPx',0)),reverse=True)[:3]
    result=[]
    for index,p in enumerate(photos):
        if min(p.get('widthPx',0),p.get('heightPx',0))<=0: continue
        result.append({'key':'google:'+str(index),'source':'google','width':p['widthPx'],'height':p['heightPx'],
            '_photo_name':p['name'],'attribution':{'label':'Google Maps','url':'https://www.google.com/maps/search/?api=1&query='+quote(place['name'])+'&query_place_id='+quote(place['google_place_id']),
            'authors':p.get('authorAttributions',[]),'source_url':p.get('googleMapsUri')},'license':'Google Places display-only'})
    return result


def google_bytes(candidate, stats):
    name=candidate['_photo_name']
    if not re.fullmatch(r'places/[^/]+/photos/[^/]+',name): raise ValueError('Invalid photo reference')
    for width,height in [(360,450),(240,300)]:
        stats['google_photo_calls']+=1; stats['estimated_usd']+=.007
        with _network_slots, httpx.Client(trust_env=False,timeout=5) as client:
            response=client.get('https://places.googleapis.com/v1/'+quote(name,safe='/')+'/media',params={'maxWidthPx':width,'maxHeightPx':height,'skipHttpRedirect':'true'},headers={'X-Goog-Api-Key':os.environ['GOOGLE_MAPS_API_KEY']})
            response.raise_for_status(); uri=response.json()['photoUri']
        raw,_=fetch(uri,limit=300_000)
        dimensions(raw)
        if len(raw)<=MAX_THUMB: return raw
    raise ValueError('Google thumbnail exceeds transfer budget')


def site_candidate(place, stats):
    data=google_details(place,'id,websiteUri',stats)
    website=data.get('websiteUri')
    if not website: return []
    stats['site_requests']+=1; body,canonical=fetch(website,limit=1_500_000)
    meta=Metadata.parse(body); image=meta.get('og:image')
    if not image: return []
    image=urljoin(canonical,image); raw,_=fetch(image); width,height=dimensions(raw); thumb,_=thumbnail(raw)
    # Venue-site thumbnails are returned only for this authenticated request.
    return [{'key':'site:0','source':'site','width':width,'height':height,'url':image,'content_hash':digest(thumb),
        'original_bytes':len(raw),'thumbnail_bytes':len(thumb),'_bytes':thumb,
        'attribution':{'label':urlparse(canonical).hostname,'url':canonical},'license':'Venue website / rights unconfirmed'}]


def plain(value): return re.sub('<[^>]+>','',unescape(str(value or ''))).strip()
def commons_candidates(place, kind, stats):
    if kind not in COMMONS_KINDS: return []
    query=place['name']+' '+(place.get('city') or '')
    stats['commons_requests']+=1
    params={'action':'query','format':'json','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':4,'prop':'imageinfo','iiprop':'url|size|extmetadata','iiurlwidth':540}
    url='https://commons.wikimedia.org/w/api.php?'+str(httpx.QueryParams(params))
    body,_=fetch(url,limit=1_500_000)
    tokens={w for w in re.findall(r'\w+',place['name'].lower()) if len(w)>2 and w not in {'the','and','los','san'}}
    result=[]
    for page in json.loads(body).get('query',{}).get('pages',{}).values():
        info=(page.get('imageinfo') or [{}])[0];meta=info.get('extmetadata',{})
        text=plain(page.get('title','')+' '+meta.get('ImageDescription',{}).get('value','')).lower()
        if not tokens or sum(t in text for t in tokens)/len(tokens)<.75: continue
        city=place.get('city','').lower()
        if city and city not in text: continue
        license=plain(meta.get('LicenseShortName',{}).get('value',''))
        if not re.match(r'^(CC BY|CC0|Public domain)',license,re.I): continue
        image=info.get('thumburl')
        if not image: continue
        raw,_=fetch(image);thumb,_=thumbnail(raw)
        result.append({'key':'commons:'+str(page['pageid']),'source':'commons','width':info['width'],'height':info['height'],
            'asset':store_thumb(thumb),'content_hash':digest(thumb),'original_bytes':len(raw),'thumbnail_bytes':len(thumb),
            'attribution':{'label':plain(meta.get('Artist',{}).get('value','Unknown author'))+' / '+license,
            'url':info.get('descriptionurl'),'license_url':meta.get('LicenseUrl',{}).get('value')},'license':license})
        if len(result)==3: break
    return result


def failure(exc):
    if isinstance(exc,httpx.HTTPStatusError):return 'http_'+str(exc.response.status_code)
    if isinstance(exc,httpx.TimeoutException):return 'timeout'
    if isinstance(exc,ValueError):return str(exc)[:120]
    return type(exc).__name__

def trace(stats,source,outcome,reason=None):
    item={'source':source,'outcome':outcome,'reason':reason,'at':stamp()}
    stats.setdefault('trace',[]).append(item)
    log.info('imagery_step %s',json.dumps(item))

def metrics():
    return dict(google_details_calls=0,google_photo_calls=0,site_requests=0,commons_requests=0,venue_cache_hits=0,
                cover_cache_hits=0,cover_original_bytes=0,estimated_usd=0.0,field_masks=[],failures=[])


def venue_candidates(place,kind,stats,*,skip_google=False):
    cached=(place.get('imagery') or {}).get('map',{}) if skip_google else place.get('imagery') or {}
    if cached.get('retry_at','')>stamp():
        candidates=cached.get('candidates',[])
        if candidates and all(c.get('source')=='commons' and cached_thumb(c['asset']) is not None for c in candidates):
            stats['venue_cache_hits']+=1;return candidates
        if cached.get('negative'):
            stats['venue_cache_hits']+=1;return []
        if candidates and candidates[0].get('source')=='site':
            try:
                candidate=dict(candidates[0]);raw,_=fetch(candidate['url']);thumb,_=thumbnail(raw)
                candidate.update(_bytes=thumb,content_hash=digest(thumb),thumbnail_bytes=len(thumb))
                stats['venue_cache_hits']+=1;return [candidate]
            except Exception as exc:
                stats['failures'].append('site_cache:'+failure(exc));trace(stats,'site','cached_image_failed',failure(exc))
    result=[]
    sources=[('site',lambda:site_candidate(place,stats)),('commons',lambda:commons_candidates(place,kind,stats))]
    if not skip_google:sources.insert(0,('google',lambda:google_candidates(place,stats)))
    for name,source in sources:
        try:
            result=source()
            if name=='google':
                result.sort(key=score,reverse=True)
                # A metadata response is not success until at least one image is usable.
                while result:
                    try:
                        result[0]['_bytes']=google_bytes(result[0],stats);break
                    except Exception as exc:
                        stats['failures'].append('google_media:'+failure(exc));trace(stats,'google','rejected',failure(exc));result.pop(0)
        except Exception as exc:
            result=[];stats['failures'].append(name+':'+failure(exc));trace(stats,name,'failed',failure(exc))
        trace(stats,name,'usable' if result else 'empty',None if result else 'not_applicable' if name=='commons' and kind not in COMMONS_KINDS else 'no_usable_image')
        if result: break
    record={'checked_at':stamp(),'provider':result[0]['source'] if result else 'none'}
    if result and result[0]['source']=='google':
        # No photo names, attribution, URIs or image bytes are written to a durable store.
        record['retry_at']=stamp()
    else:
        record.update(retry_at=(now()+timedelta(days=7 if result else 0, seconds=60 if not result and stats['failures'] else 86400 if not result else 0)).isoformat(),negative=not bool(result),
                      candidates=[{k:v for k,v in c.items() if not k.startswith('_')} for c in result])
    if skip_google:
        with connect() as conn:conn.execute("update places set imagery=jsonb_set(imagery,'{map}',%s) where id=%s",(Jsonb(record),place['id']))
        return result
    with connect() as conn:
        conn.execute("update places set imagery=%s::jsonb || jsonb_build_object('map',coalesce(imagery->'map','{}'::jsonb)),image_source=%s,image_acquired_at=case when %s then now() else image_acquired_at end,image_failure_reason=%s,image_diagnostics=%s where id=%s",(Jsonb(record),result[0]['source'] if result else None,bool(result),';'.join(stats['failures']) or ('no_usable_venue_image' if not result else None),Jsonb(stats.get('trace',[])),place['id']))
    return result


def acquire(place,kind,stats,*,skip_google=False):
    key=str(place['id'])+(':map' if skip_google else ':library')
    with _flight_lock:
        existing=_flights.get(key)
        if existing is None: future=_flights[key]=Future()
    if existing is not None:
        stats['venue_cache_hits']+=1
        return existing.result(timeout=35)
    try:
        value=venue_candidates(place,kind,stats,skip_google=skip_google);future.set_result(value);return value
    except Exception as exc:
        future.set_exception(exc);raise
    finally:
        with _flight_lock:_flights.pop(key,None)


def same_venue(a,b):
    return a.get('google_place_id') and a.get('google_place_id')==b.get('google_place_id') or a.get('place_id') and a.get('place_id')==b.get('place_id')


def claim_image(candidate, entry):
    """Prevent exact-image reuse across unrelated venues; Google hashes stay request-only."""
    if candidate['source']=='google' or not entry.get('place_id'):return True
    hashed=candidate['content_hash']
    with connect() as conn:
        conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,4))',(hashed,))
        row=conn.execute('select * from imagery_claims where content_hash=%s',(hashed,)).fetchone()
        if row and not same_venue(row,entry):return False
        if not row:conn.execute('insert into imagery_claims(content_hash,place_id,google_place_id) values(%s,%s,%s)',(hashed,entry.get('place_id'),entry.get('google_place_id')))
    return True


def image_bytes(candidate,stats):
    if candidate.get('_bytes'):return candidate['_bytes']
    if candidate['source']=='google':return google_bytes(candidate,stats)
    data=cached_thumb(candidate['asset'])
    if data is None: raise FileNotFoundError('Cached image is unavailable')
    return data


def load_entries(user,ids):
    with connect() as conn:
        entries=conn.execute('''select e.*,s.source_url,coalesce(nullif(s.raw_signals->>'thumbnail_url',''),s.raw_signals->>'thumbnail') cover_url,
            s.cover_imagery,p.google_place_id,(select count(*) from entries x where x.save_id=e.save_id) save_entry_count
            from entries e join saves s on s.id=e.save_id left join places p on p.id=e.place_id
            where e.user_id=%s and e.id=any(%s::uuid[])''',(user,[str(x) for x in ids])).fetchall()
        places={str(p['id']):p for p in conn.execute('select * from places where id=any(%s::uuid[])',([str(e['place_id']) for e in entries if e['place_id']],)).fetchall()}
    return entries,places


def resolve_batch(user,ids,*,gallery=False,context='library'):
    entries,places=load_entries(user,ids)
    if len(entries)!=len(set(map(str,ids))):raise PermissionError('An entry is not in your library')
    stats=metrics();shared={};results={};google_hashes={};rendered={}
    for entry in entries:
        if entry['content_type']!='place':continue
        started=time.monotonic();entry_id=str(entry['id']);place=places.get(str(entry['place_id']))
        cover=None;rejection=None
        candidates=[]
        if place:
            key=str(place['id'])
            if key not in shared:shared[key]=acquire(place,entry.get('venue_kind','other'),stats,skip_google=context=='map')
            else:stats['venue_cache_hits']+=1
            candidates=[dict(c) for c in shared[key] if context!='map' or c['source']!='google']
        # The explicit ladder avoids unnecessary cover downloads when a venue photo works.
        if not candidates or gallery or entry.get('image_choice')=='cover':
            cover,rejection=cover_candidate(entry,stats)
            trace(stats,'cover','usable' if cover else 'rejected',rejection)
        if cover:candidates.append(cover)
        for c in candidates:c['score']=score(c)
        candidates.sort(key=lambda c:c['score'],reverse=True)
        choice=entry.get('image_choice','auto')
        if choice!='auto':candidates.sort(key=lambda c:c['key']!=choice)
        shown=[];choice_keys=[{'key':c['key'],'source':c['source'],'score':c['score']} for c in candidates]
        for candidate in candidates:
            try:
                image_key=(str(entry.get('place_id') or entry['save_id']),candidate['key'])
                if image_key not in rendered:rendered[image_key]=image_bytes(candidate,stats)
                raw=rendered[image_key]
                if not claim_image(candidate,entry):
                    if candidate['source']=='cover':rejection='image_belongs_to_another_venue'
                    continue
                if len(raw)>MAX_THUMB:raise ValueError('Thumbnail budget exceeded')
                if candidate['source']=='google':
                    hashed=digest(raw)
                    if hashed in google_hashes and not same_venue(google_hashes[hashed],entry):continue
                    google_hashes[hashed]=entry
                mime=Image.MIME.get(Image.open(io.BytesIO(raw)).format,'image/jpeg')
                public={k:v for k,v in candidate.items() if not k.startswith('_') and k not in ('asset','url','content_hash')}
                public.update(uri='data:'+mime+';base64,'+base64.b64encode(raw).decode(),thumbnail_bytes=len(raw))
                shown.append(public)
                if not gallery:break
            except Exception as exc:stats['failures'].append(candidate['source']+':'+failure(exc));trace(stats,candidate['source'],'render_failed',failure(exc))
        selected=shown[0] if shown else None
        reason=('Owner choice retained' if choice!='auto' and selected and selected['key']==choice else 'Highest weighted score' if selected else 'No usable image; venue placeholder')
        summary={'selected':selected['key'] if selected else 'placeholder','runner_up':next((c['key'] for c in candidates if not selected or c['key']!=selected['key']),None),'reason':reason,'cover_rejection':rejection,'scores':{c['key']:c['score'] for c in candidates}}
        with connect() as conn:
            conn.execute('update entries set image_selection=%s where id=%s and user_id=%s',(Jsonb(summary),entry['id'],user))
        results[entry_id]={'place_id':str(entry['place_id']) if entry.get('place_id') else None,'save_id':str(entry['save_id']),'selected':{k:selected[k] for k in ('key','source')} if selected else None,'gallery':shown,'choices':choice_keys,'selection':summary,'diagnostics':stats.get('trace',[]),'failure_reason':';'.join(stats['failures']) or rejection,'seconds':round(time.monotonic()-started,3)}
    stats['estimated_usd']=round(stats['estimated_usd'],6)
    # Operational measurements only: no Google photo metadata or credentials in this table.
    with connect() as conn:
        conn.execute('insert into imagery_runs(user_id,metrics) values(%s,%s)',(user,Jsonb(stats)))
    return {'items':results,'metrics':stats}
