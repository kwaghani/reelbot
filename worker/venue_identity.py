"""Typed, independently ranked venue identity and geographic evidence."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import re
import unicodedata
from worker.fetch.http import settings

ADMIN_TYPES = frozenset('city town village state region county locality sublocality political country postal_code neighborhood route street_address continent archipelago'.split()) | frozenset('administrative_area_level_'+str(n) for n in range(1,5))

def norm(value):
    return re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKD',str(value or '')).casefold()).strip()

def administrative_type(value):
    if isinstance(value,dict):
        values=[value.get(k) for k in ('primaryType','primary_type','type','category','addresstype')]
        values+=value.get('types',[]) if isinstance(value.get('types'),list) else []
        values+=value.get('resolution_types',[]) if isinstance(value.get('resolution_types'),list) else []
        return any(administrative_type(v) for v in values if v)
    return str(value or '').lower() in ADMIN_TYPES or str(value or '').startswith('sublocality_level_')

def specific_poi(poi):
    category=str(poi.get('primaryType') or poi.get('category') or poi.get('type') or '').strip().lower()
    return bool(poi.get('name') and category and category not in {'other','location','place'} and not administrative_type(poi) and not administrative_name(poi['name']))

def administrative_name(name):
    known=set(settings()['city_centroids']) | set(settings()['city_hashtags'].values())
    known.update(settings().get('administrative_names',[]))
    # A comma-separated city/state label is still a hint, not a venue.
    return norm(str(name or '').split(',')[0]) in {norm(v) for v in known}

@dataclass(frozen=True)
class VenueCandidate:
    name: str
    source: str
    rank: int
    confidence: float
    evidence: str
    kind: str = 'venue_line'

@dataclass(frozen=True)
class LocationHint:
    name: str
    source: str
    rank: int
    latitude: float | None = None
    longitude: float | None = None

@dataclass(frozen=True)
class VenueInputs:
    venue_candidates: tuple[VenueCandidate,...]
    location_hints: tuple[LocationHint,...]
    def payload(self):return {'venue_candidates':[asdict(v) for v in self.venue_candidates], 'location_hints':[asdict(v) for v in self.location_hints]}

# Unicode properties, not a curated list of creator emojis. Symbols, modifiers,
# variation selectors and joiners cover both single and composed emoji prefixes.
def clean_line(line):
    value=re.sub(r'(?:\s*#[\w]+)+\s*$','',line).strip()
    value=re.sub(r'^\d+[.)]\s+','',value)
    value=re.sub(r'^(?:[0-9#*]\ufe0f?\u20e3\s*)+','',value)
    i=0
    while i<len(value) and (value[i].isspace() or unicodedata.category(value[i]) in {'So','Sk','Mn','Me','Cf'}):i+=1
    return value[i:].strip()

POINTER=re.compile(r'(?:location\s+is\s+below|location\s*:|where\s*:|spot\s*:|find\s+it\s+at)',re.I)
VERB=re.compile(r'\b(is|are|was|were|try|save|visit|watch|follow|love|went|made|make|eat|eating|check|find|contains|offers|enjoy|see|go|get|has|have)\b',re.I)

def caption_lines(text,source='caption'):
    result=[];pending=False
    for original in str(text or '').splitlines():
        if not original.strip():continue
        if original.strip()=='📍':pending=True;continue
        clean=clean_line(original);pointer=POINTER.search(clean)
        if pointer:
            tail=clean_line(clean[pointer.end():])
            pending=True
            if not tail:continue
            clean=tail
        prefix=clean_line(original)!=re.sub(r'(?:\s*#[\w]+)+\s*$','',original).strip()
        words=clean.split()
        titled=bool(words) and all(w[0].isupper() or w[0].isdigit() or w.casefold() in {'and','the','of','at','in','on','&','de','la','del'} for w in words)
        short=0<len(clean)<60 and not re.search(r'[.!?]$',clean) and not VERB.search(clean)
        if short and (pending or (titled and (prefix or len(words)>1))):
            result.append(VenueCandidate(clean,source,2 if source=='caption' else 5,.9 if pending or prefix else .8,original.strip()))
        pending=False
    return result

def inputs_for(signals):
    venues=[];hints=[]
    poi=signals.get('poi') or {};geotag=signals.get('geotag') or signals.get('platform_geotag')
    if isinstance(geotag,str):geotag={'name':geotag}
    if poi:
        if not specific_poi(poi):geotag=geotag or poi
        elif poi.get('name'):
            hints.append(LocationHint(poi.get('city') or '', 'platform_poi_coordinates',1,poi.get('lat'),poi.get('lng')))
            venues.append(VenueCandidate(clean_line(poi['name']),'platform_poi',1,.95,'Specific platform POI','poi'))
    for source in ('caption','ocr','transcript'):
        text=str(signals.get(source) or '')
        if source!='transcript':venues+=caption_lines(text,source)
        else:
            for v in caption_lines(text,source):venues.append(VenueCandidate(v.name,source,6,.7,v.evidence))
        for match in re.finditer(r'\b\d{1,6}\s+(?:[\w.-]+\s+){1,5}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Highway|Hwy)\b[^\n#]*',text,re.I):
            address=match[0].strip()[:200]
            venues.append(VenueCandidate(address,source+'_address',3,.85,address,'address'))
            hints.append(LocationHint(address,'explicit_address',2))
        for handle in re.findall(r'@([\w.]+)',text):venues.append(VenueCandidate(handle,source+'_handle',4,.65,'@'+handle,'handle'))
    caption=str(signals.get('caption') or '')
    caption_prose=re.sub(r'#\w+','',caption)
    for city in sorted(settings()['city_centroids'],key=len,reverse=True):
        if re.search(r'\b'+re.escape(city)+r'\b',caption_prose,re.I):hints.append(LocationHint(city,'caption_city',2))
    if geotag:
        hints.append(LocationHint(str(geotag.get('name') or geotag.get('city') or ''),'platform_geotag',3,geotag.get('lat'),geotag.get('lng')))
    tags=list(dict.fromkeys([str(t).lower().lstrip('#') for t in signals.get('hashtags',[])]+re.findall(r'#(\w+)',caption.lower())))
    for tag in tags:
        if tag in settings()['city_hashtags']:hints.append(LocationHint(settings()['city_hashtags'][tag],'hashtag_city',4))
    if signals.get('creator_location'):hints.append(LocationHint(str(signals['creator_location']),'creator_prior',5))
    if signals.get('city_hint') and not any(norm(h.name)==norm(signals['city_hint']) for h in hints):
        hints.append(LocationHint(str(signals['city_hint']),'legacy_location_hint',5))
    actual=[];seen=set()
    for v in sorted(venues,key=lambda v:v.rank):
        if administrative_name(v.name):hints.append(LocationHint(v.name,v.source+'_administrative',2));continue
        if norm(v.name) not in seen:actual.append(v);seen.add(norm(v.name))
    hints=sorted(hints,key=lambda h:h.rank);unique={}
    for h in hints:
        if h.name or (h.latitude is not None and h.longitude is not None):unique.setdefault((norm(h.name),h.latitude,h.longitude),h)
    return VenueInputs(tuple(actual[:30]),tuple(list(unique.values())[:12]))

def infer_kind(signals,name=''):
    from worker.registry import registry_document
    mapping=registry_document().get('venue_kind_signals',{})
    text=' '.join([str(name),*[str(signals.get(k) or '') for k in ('caption','ocr','transcript')]]).casefold()
    tags={str(t).casefold().lstrip('#') for t in signals.get('hashtags',[])} | set(re.findall(r'#(\w+)',text))
    scores={};evidence={}
    for kind,spec in mapping.items():
        hits=['#'+t for t in spec.get('hashtags',[]) if t.casefold() in tags]
        words=[w for w in spec.get('keywords',[]) if re.search(r'\b'+re.escape(w.casefold())+r'\b',text)]
        # Identity-specific keywords win over incidental mentions in a long caption.
        score=3*len(hits)+sum(3 if re.search(r'\b'+re.escape(w.casefold())+r'\b',name.casefold()) else 1 for w in words)
        if score:scores[kind]=score;evidence[kind]=hits+words
    best=max(scores,key=scores.get) if scores else 'other'
    return {'kind':best,'confidence':min(.98,.6+.05*scores.get(best,0)) if scores else 0,'evidence':evidence.get(best,[]),'scores':scores}

def grounded_attributes(attrs,signals,name):
    from worker.registry import attribute_fields
    fields=attribute_fields('place',attrs.get('venue_kind'))
    text=' '.join([name,*[str(signals.get(k) or '') for k in ('caption','ocr','transcript')]]).casefold()
    clean={}
    for key,value in attrs.items():
        if key=='venue_kind' or value is None:clean[key]=value;continue
        field=fields.get(key,{})
        def supported(item):
            if isinstance(item,bool):
                return any(w in text for w in key.split('_') if w not in {'required','level','friendly'}) and (item or bool(re.search(r'\b(no|not|without|free)\b',text)))
            if isinstance(item,(int,float)):
                return any(float(n)==item for n in re.findall(r'\d+(?:\.\d+)?',text))
            aliases=field.get('evidence_aliases',{}).get(str(item),[])
            if any(re.search(r'\b'+re.escape(a)+r'\b',text) for a in aliases):return True
            words=re.findall(r'\w+',str(item).replace('_',' ').casefold())
            return bool(words) and sum(bool(re.search(r'\b'+re.escape(w)+r'\b',text)) for w in words)/len(words)>=.75
        if isinstance(value,list):
            matching=[v for v in value if supported(v)]
            if matching:clean[key]=matching
        elif supported(value):clean[key]=value
    return clean

def condition_rows(rows,signals):
    """Make the model's output obey source provenance without discarding other content."""
    typed=inputs_for(signals);payload=typed.payload();venues=list(typed.venue_candidates)
    city=next((h.name for h in typed.location_hints if h.name and h.source!='explicit_address'),None)
    usable=[v for v in venues if v.kind!='handle']
    result=[]
    for row in rows:
        if row['content_type']!='place':result.append(row);continue
        row=dict(row);name=row.get('venue_name') or row['title']
        if administrative_name(name):
            if not usable:continue
            name=usable[0].name;row.update(title=name,venue_name=name)
        matching=next((v for v in venues if norm(v.name)==norm(name)),None)
        if not matching and not any(norm(name) in norm(signals.get(k)) for k in ('caption','ocr','transcript')):
            if not usable:continue
            matching=usable[0];name=matching.name;row.update(title=name,venue_name=name)
        inference=infer_kind(signals,name)
        attrs=dict(row.get('attributes',{}))
        if inference['kind']!='other':attrs['venue_kind']=inference['kind']
        attrs.setdefault('venue_kind','other')
        from worker.registry import attribute_fields
        attrs={k:v for k,v in attrs.items() if k in attribute_fields('place',attrs['venue_kind'])}
        attrs=grounded_attributes(attrs,signals,name)
        # A model city is usable only when it is actually present in source text.
        model_city=row.get('city_hint')
        grounded_city=model_city if model_city and norm(model_city) in norm(' '.join(str(signals.get(k) or '') for k in ('caption','ocr','transcript'))) else None
        row.update(attributes=attrs,city_hint=grounded_city or city,**payload,kind_inference=inference,
                   identity_source=matching.source if matching else 'model_source_text')
        if matching and matching.rank==1:row['poi']=signals.get('poi')
        result.append(row)
    # Deterministic caption lines are independent named venues, including those
    # omitted by a classifier distracted by a broad platform tag.
    for v in usable:
        if any(norm(r.get('venue_name') or r['title'])==norm(v.name) for r in result):continue
        inference=infer_kind(signals,v.name)
        if inference['kind']=='other' and not v.evidence.lstrip().startswith('📍') and v.rank!=1:continue
        result.append({'content_type':'place','title':v.name,'venue_name':v.name,'summary':'Saved place: '+v.name+'.',
            'attributes':{'venue_kind':inference['kind']},'city_hint':city,'address_hint':v.name if v.kind=='address' else None,
            'confidence':v.confidence,'evidence':v.evidence,'review_reasons':[],**payload,'kind_inference':inference,
            'identity_source':v.source,**({'poi':signals['poi']} if v.rank==1 else {})})
    if not result and typed.location_hints and not venues:
        result=[{'content_type':'place','title':'Unidentified place','venue_name':None,'city_hint':city,'address_hint':None,
            'summary':'A location was tagged, but the specific place needs review.','attributes':{'venue_kind':infer_kind(signals)['kind']},
            'confidence':.3,'evidence':'Platform location hint only','identity_source':'platform_geotag',
            'review_reasons':['resolved_to_administrative_area','unresolved_place','low_confidence'],**payload}]
    return result
