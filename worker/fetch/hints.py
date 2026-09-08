"""Deterministic evidence hints; candidates are never assumed to be facts."""
import re
from collections import Counter
from worker.fetch.http import settings

def identify(signals):
    caption=str(signals.get('caption',''));ocr=str(signals.get('ocr',''))
    tags=list(dict.fromkeys([str(v).lower().lstrip('#') for v in signals.get('hashtags',[])]+re.findall(r'#([\w]+)',caption.lower())))
    votes=Counter(settings()['city_hashtags'][tag] for tag in tags if tag in settings()['city_hashtags'])
    city=votes.most_common(1)[0][0] if votes else None
    # Explicit neighborhood text is more precise than a metropolitan hashtag.
    for known in sorted(settings()['city_centroids'],key=len,reverse=True):
        if re.search(r'\b'+re.escape(known)+r'\b',caption+'\n'+ocr,re.I):city=known;break
    candidates=[]
    for source,text in [('caption',caption),('ocr',ocr)]:
        for handle in re.findall(r'@([\w.]+)',text):candidates.append({'name':handle,'source':source,'kind':'handle','confidence':.65})
        for line in text.splitlines():
            clean=line.strip().lstrip('📍@').strip();clean=re.sub(r'#[\w]+','',clean).strip()
            words=clean.split()
            verb=bool(re.search(r'\b(is|are|was|were|try|save|visit|watch|follow|love|went|made|make|eat|eating|check)\b',clean,re.I))
            titled=bool(words) and all(w[:1].isupper() or w.lower() in ('and','the','of','&') for w in words)
            if 1<len(words)<=7 and len(clean)<=100 and not verb and (titled or line.strip().startswith(('📍','@'))):
                candidates.append({'name':clean,'source':source,'kind':'standalone_line','confidence':.85})
        # oEmbed sometimes flattens line breaks. Keep a named business fragment as a hint.
        for match in re.finditer(r'\b(?:[A-Z][\w’\'-]*\s+){1,5}(?:House|Cafe|Café|Restaurant|Bakery|Bar|Friends|Hotel)\b',text):
            candidates.append({'name':match[0],'source':source,'kind':'named_fragment','confidence':.8})
    unique={c['name'].casefold():c for c in candidates}
    return {'hashtags':tags,'city_hint':city,'city_evidence':[tag for tag in tags if settings()['city_hashtags'].get(tag)==city], 'deterministic_candidates':list(unique.values())[:30]}
