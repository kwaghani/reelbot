"""Global place resolution, using only the five required Text Search fields."""
from __future__ import annotations
import difflib
import json
import os
import re
import unicodedata
from urllib.request import Request, urlopen

FIELDS = ("id", "displayName", "formattedAddress", "location", "primaryType")
FIELD_MASK = ",".join("places."+field for field in FIELDS)


def normalized(text):
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", str(text or "")).casefold()).strip()


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
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Place lookup is unavailable: provider key is missing.")
    query = " ".join(str(candidate.get(k) or "") for k in ("name", "city_hint", "country_hint")).strip()
    metrics["places_calls"] += 1
    metrics.setdefault("places_field_masks", []).append(FIELD_MASK)
    request = Request("https://places.googleapis.com/v1/places:searchText",
                      data=json.dumps({"textQuery": query, "pageSize": 5}).encode(),
                      headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                               "X-Goog-FieldMask": FIELD_MASK}, method="POST")
    with urlopen(request, timeout=12) as response:
        return json.load(response).get("places", [])


def resolve(conn, candidate, metrics, search=text_search):
    cache_key = lookup_key(candidate)
    # Serialize cache misses across workers/users, including the provider call.
    conn.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("place:"+cache_key,))
    cached = conn.execute("select * from places where lookup_key=%s", (cache_key,)).fetchone()
    if cached:
        metrics["cache_hits"] += 1
        return dict(cached), float(candidate["confidence"]), None
    results = search(candidate, metrics)
    ranked = []
    for place in results:
        loc = place.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if not isinstance(lat, (int,float)) or not isinstance(lng, (int,float)):
            continue
        if not -90 <= lat <= 90 or not -180 <= lng <= 180 or not place.get("formattedAddress") or not place.get("id"):
            continue
        name = (place.get("displayName") or {}).get("text", "")
        score = difflib.SequenceMatcher(None, normalized(candidate["name"]), normalized(name)).ratio()
        city = normalized(candidate.get("city_hint"))
        if city and city not in normalized(place["formattedAddress"]):
            score *= 0.5
        ranked.append((score, place))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    if not ranked:
        return None, min(float(candidate["confidence"]), 0.5), "No matching address was found."
    score, best = ranked[0]
    if len(ranked) > 1 and score-ranked[1][0] < 0.08:
        return None, min(float(candidate["confidence"]), 0.5), "Several locations match. Choose the intended venue."
    confidence = min(float(candidate["confidence"]), score)
    if confidence < 0.6:
        return None, confidence, "The venue match needs confirmation."
    city = str(candidate.get("city_hint") or "").strip()
    if not city:
        return None, min(confidence,0.5), "A city is needed to confirm this venue."
    loc = best["location"]
    row = conn.execute(
        """insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,city,lookup_key)
           values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict(google_place_id) do update set
           name=excluded.name,formatted_address=excluded.formatted_address,lat=excluded.lat,lng=excluded.lng,
           primary_type=excluded.primary_type,city=excluded.city,lookup_key=excluded.lookup_key,last_refreshed_at=now()
           returning *""",
        (best["id"],best["displayName"]["text"],best["formattedAddress"],loc["latitude"],loc["longitude"],
         best.get("primaryType","other"),city,cache_key)).fetchone()
    return dict(row), confidence, None


def details(conn,place):
    """Explicit detail views only; never called during ingestion. Cache for 30 days."""
    from datetime import datetime,timezone,timedelta
    from psycopg.types.json import Jsonb
    from urllib.parse import quote
    conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('details:'+str(place['id']),))
    current=conn.execute('select * from places where id=%s',(place['id'],)).fetchone()
    if current['details'] is not None and current['details_refreshed_at'] and datetime.now(timezone.utc)-current['details_refreshed_at']<timedelta(days=30):
        return current['details']
    request=Request('https://places.googleapis.com/v1/places/'+quote(place['google_place_id'],safe=''),
        headers={'X-Goog-Api-Key':os.environ['GOOGLE_MAPS_API_KEY'],
                 'X-Goog-FieldMask':'rating,reviews,photos,regularOpeningHours'})
    with urlopen(request,timeout=10) as response: result=json.load(response)
    conn.execute('update places set details=%s,details_refreshed_at=now() where id=%s',(Jsonb(result),place['id']))
    return result
