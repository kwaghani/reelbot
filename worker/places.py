"""Global place resolution, using minimal identity, coordinate and classification fields."""
from __future__ import annotations
import json
import math
import os
import re
import unicodedata
from urllib.request import Request, urlopen

FIELDS = ("id", "displayName", "formattedAddress", "location", "primaryType", "types")
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
    from worker.venue_identity import administrative_type,administrative_name
    if administrative_type(place) or administrative_name((place.get('displayName') or {}).get('text')):
        raise ValueError('resolved_to_administrative_area')
    target_key='choice:'+place['id'] if manual else lookup_key(candidate)
    conn.execute('update places set lookup_key=null where lookup_key=%s and google_place_id is distinct from %s',(target_key,place['id']))
    loc=place['location'];city=str(candidate.get('city_hint') or '')
    row=conn.execute("""insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key,resolution_types)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(google_place_id) do update set
        name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,
        primary_type=excluded.primary_type,city=excluded.city,lookup_key=excluded.lookup_key,resolution_types=excluded.resolution_types,last_refreshed_at=now() returning *""",
        (place['id'],place['displayName']['text'],place['formattedAddress'],loc['latitude'],loc['longitude'],
         place.get('primaryType') or next(iter(place.get('types',[])),'other'),city,'choice:'+place['id'] if manual else lookup_key(candidate),place.get('types',[]))).fetchone()
    return dict(row)


def resolution_guard(place,candidate,biases=()):
    from worker.venue_identity import administrative_type,administrative_name
    primary=place.get('primaryType') or place.get('primary_type') or place.get('category')
    title=(place.get('displayName') or {}).get('text') or place.get('name','')
    if administrative_type(place) or administrative_name(title):return 'resolved_to_administrative_area'
    loc=place.get('location') or {'latitude':place.get('lat'),'longitude':place.get('lng')}
    if not valid_location(loc):return 'invalid_coordinates'
    points=[v for v in biases if valid_location(v)]
    if points and all(distance_m(loc,v)>150000 for v in points):return 'distance_implausible'
    from worker.venue_kinds import kind_for
    kind=kind_for(primary)
    if kind=='other':kind=candidate.get('venue_kind','other')
    if kind!='attraction':
        from worker.fetch.http import settings
        centroids=[{'latitude':p[0],'longitude':p[1]} for p in settings()['city_centroids'].values()]
        centroids+=candidate.get('city_centroids',[])
        if any(valid_location(p) and distance_m(loc,p)<200 for p in centroids):return 'probable_city_centroid'
    return None


def resolve(conn,candidate,metrics,search=text_search):
    from worker.venue_identity import administrative_name,administrative_type,specific_poi
    candidate.setdefault('confidence',.5)
    if candidate.get('identity_source')=='platform_geotag':
        return None,min(.3,float(candidate['confidence'])),'resolved_to_administrative_area'
    if not candidate.get('name') or administrative_name(candidate['name']):
        candidate.setdefault('location_hints',[]).append({'name':candidate.get('name',''),'source':'administrative_name','rank':2})
        return None,min(.3,float(candidate['confidence'])),'resolved_to_administrative_area'
    cache_key=lookup_key(candidate)
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('place:'+cache_key,))
    poi=candidate.get('poi') or {}
    hints=candidate.get('location_hints',[])
    biases=[{'latitude':h.get('latitude'),'longitude':h.get('longitude')} for h in hints]
    biases=[p for p in biases if valid_location(p)]
    from worker.fetch.http import settings
    for hint in hints:
        for known,point in settings()['city_centroids'].items():
            if normalized(str(hint.get('name','')).split(',')[0])==normalized(known):
                biases.append({'latitude':point[0],'longitude':point[1]})
    city=candidate.get('city_hint')
    try: bias=city_bias(conn,city,metrics,search)
    except Exception: bias=None
    if bias:biases.append(bias);candidate['city_centroids']=[bias]
    ranked_points=[{'latitude':h.get('latitude'),'longitude':h.get('longitude')} for h in sorted(hints,key=lambda h:h.get('rank',99)) if h.get('source')=='platform_poi_coordinates']
    if ranked_points and valid_location(ranked_points[0]):bias=ranked_points[0]
    if biases and not bias:bias=biases[0]
    candidate['location_bias']=bias
    rejected=[]
    loc={'latitude':poi.get('lat'),'longitude':poi.get('lng')}
    if valid_location(loc) and specific_poi(poi) and poi.get('platform')!='instagram':
        reason=resolution_guard(poi,candidate,biases)
        if not reason:
            import hashlib
            provider=poi.get('platform') or 'tiktok'
            identifier=str(poi.get('id') or hashlib.sha256(json.dumps(poi,sort_keys=True).encode()).hexdigest())
            row=conn.execute("""insert into places(provider,provider_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(provider,provider_place_id) where provider_place_id is not null
                do update set name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,
                primary_type=excluded.primary_type,city=excluded.city,last_refreshed_at=now() returning *""",
                (provider,identifier,poi['name'],poi.get('address') or '',loc['latitude'],loc['longitude'],poi.get('category') or 'other',poi.get('city') or city or '','poi:'+provider+':'+identifier)).fetchone()
            metrics.setdefault('places_queries',[]).append({'purpose':'platform_poi','query':None,'results':[poi],'accepted':True,'score':.95})
            return dict(row),min(.95,candidate['confidence']),None
        rejected.append(reason)
    radius=150000 if candidate.get('venue_kind') in {'outdoors','beach','attraction'} else 20000
    cached=conn.execute('select * from places where lookup_key=%s',(cache_key,)).fetchone()
    if cached:
        reason=resolution_guard(cached,candidate,biases)
        score=name_similarity(candidate['name'],cached['name'])
        inside=not city or (bias is not None and distance_m(bias,{'latitude':cached['lat'],'longitude':cached['lng']})<=radius)
        if not reason and score>.7 and inside and (cached.get('primary_type') not in {'other',''} or cached.get('resolution_types')):
            metrics['cache_hits']+=1
            return dict(cached),min(float(candidate['confidence']),score),None
        if reason:rejected.append(reason)
    name=candidate['name'];address=candidate.get('address') or poi.get('address')
    queries=[]
    if city:queries.append((name+' '+city,bias))
    if address:queries.append((name+' '+address,bias))
    queries.extend([(name,bias),(name,None)])
    seen=set();ranked={};attempts=0
    for query,request_bias in queries:
        identity=(query,json.dumps(request_bias,sort_keys=True))
        if identity in seen:continue
        seen.add(identity);attempts+=1
        if radius==150000 and attempts>2:break
        try:results=search({**candidate,'text_query':query,'location_bias':request_bias},metrics)
        except Exception as exc:
            metrics.setdefault('places_errors',[]).append(type(exc).__name__);continue
        log={'query':query,'location_bias':request_bias,'radius_m':radius if request_bias else None,'results':[]}
        metrics.setdefault('places_queries',[]).append(log);acceptable=[]
        for place in results:
            loc=place.get('location');title=(place.get('displayName') or {}).get('text','')
            if not valid_location(loc) or not place.get('id') or not place.get('formattedAddress') or not title:continue
            score=name_similarity(name,title);distance=distance_m(bias,loc) if bias else None
            inside=(distance is not None and distance<=radius) if city else True
            reason=resolution_guard(place,candidate,biases)
            row={'place':place,'score':round(score,5),'distance_m':round(distance,1) if distance is not None else None,'inside_city_radius':inside,'rejection':reason}
            log['results'].append(row)
            if reason:
                rejected.append(reason);continue
            ranked[place['id']]=row
            if score>.7 and inside:acceptable.append((score,place))
        if acceptable:
            acceptable.sort(key=lambda p:p[0],reverse=True)
            score,best=acceptable[0];log['accepted_id']=best['id']
            return persist_google(conn,best,candidate),min(float(candidate['confidence']),score),None
    if candidate.get('venue_kind') in {'outdoors','beach','attraction'}:
        from worker.noncommercial import resolve_natural
        place,reason=resolve_natural(conn,candidate,metrics,biases)
        if place:return place,min(float(candidate['confidence']),.9),None
        if reason:rejected.append(reason)
    candidate['place_candidates']=sorted(ranked.values(),key=lambda p:(p['inside_city_radius'],p['score']),reverse=True)[:3]
    reason=next((r for r in ('resolved_to_administrative_area','distance_implausible','probable_city_centroid') if r in rejected),None)
    return None,float(candidate['confidence']),reason or ('ambiguous_place' if ranked else 'unresolved_place')


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
