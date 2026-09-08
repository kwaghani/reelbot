"""Global place resolution, using only the five required Text Search fields."""
from __future__ import annotations
import json
import math
import os
import re
import unicodedata
from urllib.request import Request, urlopen

FIELDS = ("id", "displayName", "formattedAddress", "location", "primaryType")
FIELD_MASK = ",".join("places."+field for field in FIELDS)


def normalized(text):
    value=''.join(c for c in unicodedata.normalize('NFKD',str(text or '').casefold()) if not unicodedata.combining(c))
    value=re.sub(r"['’`ʻ]",'',value)
    value=re.sub(r'\band\b|&',' ',value)
    return re.sub(r'[\W_]+',' ',value).strip()


def jaro_winkler(first,second):
    if first==second:return 1.0
    if not first or not second:return 0.0
    radius=max(0,max(len(first),len(second))//2-1)
    left=[False]*len(first);right=[False]*len(second)
    for i,char in enumerate(first):
        for j in range(max(0,i-radius),min(len(second),i+radius+1)):
            if not right[j] and char==second[j]:left[i]=right[j]=True;break
    matches=sum(left)
    if not matches:return 0.0
    a=[c for c,m in zip(first,left) if m];b=[c for c,m in zip(second,right) if m]
    transpositions=sum(x!=y for x,y in zip(a,b))/2
    score=(matches/len(first)+matches/len(second)+(matches-transpositions)/matches)/3
    prefix=0
    for x,y in zip(first[:4],second[:4]):
        if x!=y:break
        prefix+=1
    return score+prefix*.1*(1-score) if score>.7 else score


def name_similarity(first,second):
    # Whole normalized names plus explicit brand segments; no substring-only acceptance.
    variants=lambda value:[normalized(value)]+[normalized(v) for v in str(value).split('|') if v.strip()]
    return max(jaro_winkler(a,b) for a in variants(first) for b in variants(second))


def lookup_key(candidate):
    return "|".join(normalized(candidate.get(k)) for k in ("name", "city_hint", "country_hint"))


def category(primary_type):
    value = (primary_type or "").lower()
    if any(x in value for x in ("cafe", "coffee", "tea_house")):
        return "Cafés"
    if value in {"bar", "wine_bar", "pub", "night_club", "bar_and_grill"}:
        return "Bars"
    if any(x in value for x in ("restaurant", "food", "bakery", "meal")):
        return "Restaurants"
    if any(x in value for x in ("hotel", "lodging", "motel", "resort")):
        return "Hotels"
    if any(x in value for x in ("store", "shop", "mall")):
        return "Shops"
    if any(x in value for x in ("park", "museum", "tourist", "art_", "amusement", "zoo", "activity", "stadium")):
        return "Activities"
    return "Other"


def text_search(candidate, metrics):
    key=os.getenv('GOOGLE_MAPS_API_KEY','').strip()
    if not key:raise RuntimeError('Place lookup is unavailable: provider key is missing.')
    query=candidate.get('text_query') or ' '.join(str(candidate.get(k) or '') for k in ('name','city_hint','country_hint')).strip()
    body={'textQuery':query,'pageSize':5}
    if candidate.get('location_bias'):body['locationBias']={'circle':{'center':candidate['location_bias'],'radius':20000.0}}
    metrics['places_calls']+=1;metrics.setdefault('places_field_masks',[]).append(FIELD_MASK)
    request=Request('https://places.googleapis.com/v1/places:searchText',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','X-Goog-Api-Key':key,'X-Goog-FieldMask':FIELD_MASK},method='POST')
    try:
        with urlopen(request,timeout=7) as response:return json.load(response).get('places',[])
    except Exception as error:
        metrics.setdefault('places_queries',[]).append({'query':query,'location_bias':candidate.get('location_bias'),'results':[],'error':type(error).__name__})
        raise


def distance_m(a,b):
    lat1,lat2=math.radians(a['latitude']),math.radians(b['latitude'])
    dlat=lat2-lat1;dlng=math.radians(b['longitude']-a['longitude'])
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2)))


def valid_location(location):
    return isinstance(location,dict) and type(location.get('latitude')) in (int,float) and type(location.get('longitude')) in (int,float) and -90<=location['latitude']<=90 and -180<=location['longitude']<=180


def city_bias(conn,city,metrics,search):
    if not city:return None
    from worker.fetch.http import settings
    key=normalized(city)
    for name,point in settings()['city_centroids'].items():
        if normalized(name)==key:return {'latitude':point[0],'longitude':point[1]}
    cached=conn.execute("select lat,lng from city_bias_cache where city_key=%s and fetched_at>now()-interval '365 days'",(key,)).fetchone()
    if cached:return {'latitude':cached['lat'],'longitude':cached['lng']}
    results=search({'name':city,'text_query':city,'city_hint':None},metrics)
    metrics.setdefault('places_queries',[]).append({'purpose':'city_centroid','query':city,'location_bias':None,'results':results})
    for place in results:
        if valid_location(place.get('location')) and normalized(place.get('displayName',{}).get('text')) in (key,normalized(city.split(',')[0])):
            loc=place['location']
            conn.execute("""insert into city_bias_cache(city_key,lat,lng) values(%s,%s,%s)
                on conflict(city_key) do update set lat=excluded.lat,lng=excluded.lng,fetched_at=now()""",(key,loc['latitude'],loc['longitude']))
            return loc
    return None


def persist_google(conn,place,candidate,*,manual=False):
    loc=place['location'];city=str(candidate.get('city_hint') or '')
    row=conn.execute("""insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key)
        values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict(google_place_id) do update set
        name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,
        primary_type=excluded.primary_type,city=excluded.city,lookup_key=excluded.lookup_key,last_refreshed_at=now() returning *""",
        (place['id'],place['displayName']['text'],place['formattedAddress'],loc['latitude'],loc['longitude'],
         place.get('primaryType','other'),city,'choice:'+place['id'] if manual else lookup_key(candidate))).fetchone()
    return dict(row)


def resolve(conn,candidate,metrics,search=text_search):
    cache_key=lookup_key(candidate)
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('place:'+cache_key,))
    poi=candidate.get('poi') or {}
    loc={'latitude':poi.get('lat'),'longitude':poi.get('lng')}
    if valid_location(loc):
        import hashlib
        provider=poi.get('platform') or 'tiktok'
        identifier=str(poi.get('id') or hashlib.sha256(json.dumps(poi,sort_keys=True).encode()).hexdigest())
        row=conn.execute("""insert into places(provider,provider_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(provider,provider_place_id) where provider_place_id is not null
            do update set name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,city=excluded.city,last_refreshed_at=now() returning *""",
            (provider,identifier,poi['name'],poi.get('address') or '',loc['latitude'],loc['longitude'],poi.get('category') or 'other',poi.get('city') or candidate.get('city_hint') or '','poi:'+provider+':'+identifier)).fetchone()
        metrics.setdefault('places_queries',[]).append({'purpose':'platform_poi','query':None,'results':[poi],'accepted':True,'score':.95})
        return dict(row),.95,None
    city=candidate.get('city_hint');bias=city_bias(conn,city,metrics,search)
    cached=conn.execute('select * from places where lookup_key=%s',(cache_key,)).fetchone()
    if cached:
        loc={'latitude':cached['lat'],'longitude':cached['lng']}
        score=name_similarity(candidate['name'],cached['name'])
        inside=not city or (bias is not None and valid_location(loc) and distance_m(bias,loc)<=20000)
        if score>.7 and inside:
            metrics['cache_hits']+=1
            return dict(cached),min(float(candidate['confidence']),score),None
    name=candidate['name'];address=candidate.get('address') or poi.get('address')
    queries=[]
    if city:queries.append((name+' '+city,bias))
    if address:queries.append((name+' '+address,bias))
    queries.extend([(name,bias),(name,None)])
    seen=set();ranked={}
    for query,request_bias in queries:
        identity=(query,json.dumps(request_bias,sort_keys=True))
        if identity in seen:continue
        seen.add(identity)
        results=search({**candidate,'text_query':query,'location_bias':request_bias},metrics)
        log={'query':query,'location_bias':request_bias,'radius_m':20000 if request_bias else None,'results':[]}
        metrics.setdefault('places_queries',[]).append(log)
        acceptable=[]
        for place in results:
            loc=place.get('location');title=(place.get('displayName') or {}).get('text','')
            if not valid_location(loc) or not place.get('id') or not place.get('formattedAddress') or not title:continue
            score=name_similarity(name,title)
            distance=distance_m(bias,loc) if bias else None
            inside=(distance is not None and distance<=20000) if city else True
            row={'place':place,'score':round(score,5),'distance_m':round(distance,1) if distance is not None else None,'inside_city_radius':inside}
            log['results'].append(row)
            ranked[place['id']]=row
            if score>.7 and inside:acceptable.append((score,place))
        if acceptable:
            # Provider ordering breaks ties, as in Text Search. Never reward a location outside the city.
            acceptable.sort(key=lambda p:p[0],reverse=True)
            score,best=acceptable[0];log['accepted_id']=best['id']
            return persist_google(conn,best,candidate),min(float(candidate['confidence']),score),None
    candidates=sorted(ranked.values(),key=lambda p:(p['inside_city_radius'],p['score']),reverse=True)[:3]
    candidate['place_candidates']=candidates
    return None,float(candidate['confidence']),'ambiguous_place' if candidates else 'unresolved_place'


def details(conn,place):
    """Explicit detail views only; never called during ingestion. Cache for 30 days."""
    from datetime import datetime,timezone,timedelta
    from psycopg.types.json import Jsonb
    from urllib.parse import quote
    if not place.get('google_place_id'):return {}
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('details:'+str(place['id']),))
    current=conn.execute('select * from places where id=%s',(place['id'],)).fetchone()
    if current['details'] is not None and current['details_refreshed_at'] and datetime.now(timezone.utc)-current['details_refreshed_at']<timedelta(days=30):
        return current['details']
    request=Request('https://places.googleapis.com/v1/places/'+quote(place['google_place_id'],safe=''),
        headers={'X-Goog-Api-Key':os.environ['GOOGLE_MAPS_API_KEY'],
                 'X-Goog-FieldMask':'rating,regularOpeningHours'})
    with urlopen(request,timeout=10) as response: result=json.load(response)
    conn.execute('update places set details=%s,details_refreshed_at=now() where id=%s',(Jsonb(result),place['id']))
    return result
