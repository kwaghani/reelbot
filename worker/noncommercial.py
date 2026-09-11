"""Small, cached natural-feature fallback; no public autocomplete or bulk crawling."""
import hashlib,json,os,re,time
from urllib.parse import urlencode,urlparse,parse_qs,unquote
from psycopg.types.json import Jsonb
from worker.places import normalized,name_similarity,resolution_guard,lookup_key
from worker.imagery import fetch

OSM_ATTRIBUTION={'label':'© OpenStreetMap contributors','url':'https://www.openstreetmap.org/copyright'}

def nominatim_search(conn,query,metrics):
    endpoint=os.getenv('NOMINATIM_URL','https://nominatim.openstreetmap.org/search')
    key=hashlib.sha256((endpoint+'|'+query).encode()).hexdigest()
    # Shared across owners and processes. No repeated public query for the same name.
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('osm-query:'+key,))
    cached=conn.execute('select results from natural_geocode_cache where cache_key=%s and expires_at>now()',(key,)).fetchone()
    if cached:metrics['osm_cache_hits']=metrics.get('osm_cache_hits',0)+1;return cached['results']
    from worker.db import connect
    # A separate committed reservation prevents concurrent workers from exceeding
    # the service-wide ceiling. Never bypass this with the platform test flag.
    with connect() as rate:
        rate.execute("insert into fetch_rate_limits(platform) values('nominatim') on conflict do nothing")
        delay=float(rate.execute("select greatest(0,extract(epoch from next_at-clock_timestamp())) as delay from fetch_rate_limits where platform='nominatim' for update").fetchone()['delay'])
        if delay>8:raise RuntimeError('Nominatim queue busy')
        rate.execute("update fetch_rate_limits set next_at=greatest(next_at,clock_timestamp())+interval '1.1 second' where platform='nominatim'")
    if delay:time.sleep(delay)
    from urllib.request import Request,urlopen
    request=Request(endpoint+'?'+urlencode({'q':query,'format':'jsonv2','limit':5,'addressdetails':1,'namedetails':1}),headers={'User-Agent':os.getenv('NOMINATIM_USER_AGENT','ReelBot/1.0 personal saved-venue lookup'),'Accept':'application/json'})
    metrics['osm_calls']=metrics.get('osm_calls',0)+1
    try:
        with urlopen(request,timeout=7) as response:
            raw=response.read(250001)
            if len(raw)>250000:raise ValueError('Nominatim response too large')
            result=json.loads(raw)
        if not isinstance(result,list):raise ValueError('Invalid Nominatim response')
        ttl=30*86400 if result else 86400
    except Exception as exc:
        metrics.setdefault('osm_errors',[]).append(type(exc).__name__);result=[];ttl=3600
    conn.execute("insert into natural_geocode_cache(cache_key,results,expires_at) values(%s,%s,now()+(%s*interval '1 second')) on conflict(cache_key) do update set results=excluded.results,expires_at=excluded.expires_at",(key,Jsonb(result),ttl))
    return result

def osm_place(row):
    category=row.get('class') or row.get('category');kind=row.get('type') or row.get('addresstype')
    if category=='boundary' or kind in {'city','town','village','hamlet','suburb','county','state','country','postcode','administrative'}:kind='locality'
    name=(row.get('namedetails') or {}).get('name') or row.get('name') or str(row.get('display_name','')).split(',')[0]
    return {'name':name,'formatted_address':row.get('display_name',''),'lat':float(row['lat']),'lng':float(row['lon']),
        'primary_type':kind,'provider':'osm','provider_place_id':str(row.get('osm_type',''))+':'+str(row.get('osm_id','')),
        'resolution_attribution':OSM_ATTRIBUTION}

def persist_external(conn,place,candidate):
    conn.execute('update places set lookup_key=null where lookup_key=%s and (provider is distinct from %s or provider_place_id is distinct from %s)',(lookup_key(candidate),place['provider'],place['provider_place_id']))
    return dict(conn.execute("""insert into places(provider,provider_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key,resolution_attribution)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(provider,provider_place_id) where provider_place_id is not null
        do update set name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,
        primary_type=excluded.primary_type,lookup_key=excluded.lookup_key,resolution_attribution=excluded.resolution_attribution,last_refreshed_at=now() returning *""",
        (place['provider'],place['provider_place_id'],place['name'],place['formatted_address'],place['lat'],place['lng'],place['primary_type'],candidate.get('city_hint') or '',lookup_key(candidate),Jsonb(place['resolution_attribution']))).fetchone())

def web_candidates(candidate,metrics):
    """A bounded public search fallback, requiring identity and explicit coordinates.

    Never infer coordinates from a city mention or invent an address from prose.
    """
    from html import unescape
    query=candidate['name']+' '+str(candidate.get('city_hint') or '')+' coordinates'
    metrics['web_search_calls']=metrics.get('web_search_calls',0)+1
    try:data,_=fetch('https://html.duckduckgo.com/html/?'+urlencode({'q':query}),limit=400000,timeout=6)
    except Exception as exc:metrics.setdefault('web_errors',[]).append(type(exc).__name__);return []
    document=data.decode(errors='replace');results=[]
    links=re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',document,re.S)
    for href,title in links[:3]:
        title=unescape(re.sub('<[^>]+>',' ',title))
        if name_similarity(candidate['name'],title)<.7:continue
        href=unescape(href);url=parse_qs(urlparse(href).query).get('uddg',[href])[0]
        if url.startswith('//'):url='https:'+url
        try:page,_=fetch(url,limit=600000,timeout=5)
        except Exception:continue
        html=unescape(page.decode(errors='replace'))
        patterns=[r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?.{0,150}?"longitude"\s*:\s*"?(-?\d+\.\d+)',r'(?:@|[?&](?:ll|q|query)=)(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)']
        pair=next((m for pat in patterns if (m:=re.search(pat,html,re.S))),None)
        if pair:
            results.append({'provider':'web','provider_place_id':hashlib.sha256(url.encode()).hexdigest(),'name':candidate['name'],
                'formatted_address':candidate.get('city_hint') or '', 'lat':float(pair[1]),'lng':float(pair[2]),
                'primary_type':'natural_feature','resolution_attribution':{'label':urlparse(url).hostname,'url':url}})
            break
        # A named venue page may provide an address instead of coordinates.
        # Search that address with the venue name, and still reject bare address
        # or administrative results. An address alone never creates a venue.
        from worker.fetch.parsing import Page,walk
        address=None
        for _,document in Page(html).scripts:
            for node in walk(document):
                if not isinstance(node.get('name'),str) or name_similarity(candidate['name'],node['name'])<.8:continue
                value=node.get('address')
                if isinstance(value,dict):address=', '.join(str(value[k]) for k in ('streetAddress','addressLocality','addressRegion','postalCode') if value.get(k))
                elif isinstance(value,str):address=value
                if address:break
        if address:
            from worker.places import text_search
            try:matches=text_search({**candidate,'text_query':candidate['name']+' '+address},metrics)
            except Exception:matches=[]
            for match in matches:
                if resolution_guard(match,candidate,[candidate.get('location_bias')]) or name_similarity(candidate['name'],match.get('displayName',{}).get('text',''))<.8:continue
                loc=match['location']
                results.append({'provider':'web','provider_place_id':hashlib.sha256(url.encode()).hexdigest(),'name':match['displayName']['text'],
                    'formatted_address':match['formattedAddress'],'lat':loc['latitude'],'lng':loc['longitude'],
                    'primary_type':match.get('primaryType') or 'natural_feature','resolution_attribution':{'label':urlparse(url).hostname,'url':url}})
                break
            if results:break
    return results

def resolve_natural(conn,candidate,metrics,biases):
    attempts=metrics.setdefault('natural_attempts',[]);rejected=[]
    name=candidate['name'];region=candidate.get('city_hint') or ''
    # Trail suffixes often refer to the route to a named waterfall rather than
    # an independently indexed feature. Keep the original identity in the entry.
    queries=list(dict.fromkeys([f'{name}, {region}'.strip(', '),re.sub(r'\s+Trail$','',name,flags=re.I)]))
    for query in queries[:2]:
        for raw in nominatim_search(conn,query,metrics):
            try:place=osm_place(raw)
            except (KeyError,ValueError,TypeError):continue
            reason=resolution_guard(place,candidate,biases)
            similarity=name_similarity(name,place['name'])
            attempts.append({'source':'osm','query':query,'name':place['name'],'lat':place['lat'],'lng':place['lng'],'score':similarity,'rejection':reason})
            if reason:rejected.append(reason);continue
            if similarity>.7:
                # Keep the OSM type as evidence, classify from extraction signals
                # unless the type has a known registry mapping.
                return persist_external(conn,place,candidate),None
    key='web:'+hashlib.sha256(lookup_key(candidate).encode()).hexdigest()
    cached=conn.execute('select results from natural_geocode_cache where cache_key=%s and expires_at>now()',(key,)).fetchone()
    if cached:
        web=cached['results'];metrics['web_cache_hits']=metrics.get('web_cache_hits',0)+1
    else:
        web=web_candidates(candidate,metrics)
        conn.execute("insert into natural_geocode_cache(cache_key,results,expires_at) values(%s,%s,now()+(%s*interval '1 hour')) on conflict(cache_key) do update set results=excluded.results,expires_at=excluded.expires_at",(key,Jsonb(web),168 if web else 1))
    for place in web:
        reason=resolution_guard(place,candidate,biases)
        attempts.append({'source':'web','name':place['name'],'lat':place['lat'],'lng':place['lng'],'rejection':reason})
        if not reason:return persist_external(conn,place,candidate),None
        rejected.append(reason)
    return None,next(iter(rejected),None)
