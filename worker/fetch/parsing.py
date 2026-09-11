"""Fragile platform adapters live here; no fixed platform script-ID dependency."""
from __future__ import annotations
import html
import json
import re
from html.parser import HTMLParser

class Page(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.meta={}; self.scripts=[]; self.canonical=None; self.refresh=None
        self._script=None; self._text=[]; self.feed(document)
    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes)
        if tag=='meta':
            key=attrs.get('property') or attrs.get('name')
            if key: self.meta[key.lower()]=attrs.get('content','')
            if attrs.get('http-equiv','').lower()=='refresh':
                match=re.search(r'url\s*=\s*[\"\']?([^\"\']+)',attrs.get('content',''),re.I)
                if match: self.refresh=html.unescape(match[1].strip())
        if tag=='link' and attrs.get('rel','').lower()=='canonical': self.canonical=attrs.get('href')
        if tag=='script': self._script=attrs; self._text=[]
    def handle_data(self,text):
        if self._script is not None: self._text.append(text)
    def handle_endtag(self,tag):
        if tag=='script' and self._script is not None:
            attrs=self._script
            if attrs.get('id') or 'json' in attrs.get('type',''):
                try:self.scripts.append((attrs,json.loads(''.join(self._text))))
                except (ValueError,RecursionError):pass
            self._script=None

def walk(value, video_id=None):
    stack=[value]; count=0
    while stack and count<40000:
        node=stack.pop();count+=1
        if isinstance(node,dict):
            if video_id and any(str(node[k])!=video_id for k in ('code','shortcode') if k in node and node[k]):continue
            if video_id and str(node.get('id',video_id))!=video_id and 'video' in node and 'author' in node:continue
            yield node
            stack.extend(reversed(list(node.values())))
        elif isinstance(node,list): stack.extend(reversed(node))

def normalize_poi(raw):
    if not isinstance(raw,dict):return None
    lowered={re.sub('[^a-z]','',k.lower()):v for k,v in raw.items()}
    name=lowered.get('name') or lowered.get('poiname') or lowered.get('title')
    if not isinstance(name,str) or not name.strip():return None
    def number(*keys):
        for obj in walk(raw):
            for key in keys:
                value=obj.get(key)
                if value is not None and not isinstance(value,bool):
                    try:return float(value)
                    except (ValueError,TypeError):pass
        return None
    lat=number('latitude','lat');lng=number('longitude','lng','lon')
    if lat is None or lng is None or not (-90<=lat<=90 and -180<=lng<=180):lat=lng=None
    address=lowered.get('address') or lowered.get('formattedaddress') or ''
    if isinstance(address,dict):address=', '.join(str(v) for v in address.values() if isinstance(v,str))
    return {'id':str(lowered.get('id') or lowered.get('poiid') or ''),'name':name.strip()[:200],
        'address':str(address)[:1000],'city':str(lowered.get('city') or ''),
        'category':str(lowered.get('primarytype') or lowered.get('type') or lowered.get('tttypenametiny') or lowered.get('category') or ''),
        'lat':lat,'lng':lng}

def parse_html(document, video_id):
    page=Page(document); ordinary=[value for attrs,value in page.scripts if 'ld+json' not in attrs.get('type','')]
    roots=[];fallback=[]
    for data in ordinary:
        nodes=list(walk(data))
        matched=[n for n in nodes if any(str(n.get(k,''))==video_id for k in ('id','itemId','item_id','videoId','video_id','shortcode','code')) and any(k in n for k in ('desc','caption','video','text','edge_media_to_caption'))]
        roots.extend(matched)
        if not matched:
            fallback.extend(n for n in nodes if any(k in n for k in ('desc','caption','edge_media_to_caption'))
                and not ('video' in n and 'author' in n and str(n.get('id',video_id))!=video_id))
    def caption_value(root):
        value=root.get('desc') or root.get('caption')
        return value.get('text','') if isinstance(value,dict) else value if isinstance(value,str) else ''
    roots=[root for root in roots if caption_value(root) or root.get('poi') or root.get('edge_media_to_caption')] or roots
    roots=roots or [root for root in fallback if not any(k in root for k in ('code','shortcode','videoId','video_id'))]
    result={'caption':'','hashtags':[],'poi':None,'creator_handle':'','creator_name':'','region':'','music':{},'metadata':{}}
    captions=[]
    primary=[caption_value(root) for root in roots if caption_value(root).strip()]
    for root in roots:
        for node in walk(root,video_id):
            foreign_id=node.get('id')
            if foreign_id and str(foreign_id)!=video_id and 'video' in node and 'author' in node:continue
            for key in ('desc','caption','description'):
                value=node.get(key)
                if key=='caption' and isinstance(value,dict):value=value.get('text')
                if isinstance(value,str) and value.strip() and value not in captions:captions.append(value)
            for key,value in node.items():
                lowered=key.lower()
                if lowered in ('poi','poiinfo','poi_info'):
                    result['poi']=result['poi'] or normalize_poi(value)
                if lowered=='location':
                    result['geotag']=result.get('geotag') or normalize_poi(value)
                if lowered in ('locationcreated','region') and isinstance(value,str) and len(value)<=8:result['region']=result['region'] or value
                if lowered=='textextra' and isinstance(value,list):
                    result['hashtags'].extend(str(v.get('hashtagName')) for v in value if isinstance(v,dict) and v.get('hashtagName'))
                if lowered=='author' and isinstance(value,dict):
                    result['creator_handle']=result['creator_handle'] or value.get('uniqueId','')
                    result['creator_name']=result['creator_name'] or value.get('nickname','')
                if lowered=='music' and isinstance(value,dict):result['music']={'title':value.get('title'),'original':value.get('original')}
                if lowered=='video' and isinstance(value,dict):
                    for k in ('duration','cover','originCover'):
                        if value.get(k):result['metadata'][k]=value[k]
                if lowered=='edge_media_to_caption' and isinstance(value,dict):
                    captions.extend(v['text'] for v in walk(value) if isinstance(v.get('text'),str))
    if primary or captions:result['caption']=(primary[0] if primary else max(captions,key=len))[:24000]
    meta_text=' '.join(page.meta.get(k,'') for k in ('og:description','twitter:description','og:title','twitter:title')).lower()
    meta_blocked=any(v in meta_text for v in ('login • instagram','log in • instagram','log in to instagram','login · instagram','log in to tiktok','log in | tiktok','before you continue to youtube','consent to cookies'))
    meta_missing=any(v in meta_text for v in ('page not found','this video is unavailable','this post is unavailable',"sorry, this page isn't available"))
    for key in ('og:description','twitter:description','og:title','twitter:title'):
        text=page.meta.get(key,'').strip()
        if not result['caption'] and not (meta_blocked or meta_missing) and text and text.lower() not in ('instagram','tiktok','youtube','tiktok - make your day'):result['caption']=text[:24000]
    result['thumbnail_url']=page.meta.get('og:image') or page.meta.get('twitter:image') or result['metadata'].get('cover')
    result['video_url']=page.meta.get('og:video') or page.meta.get('og:video:url')
    for attrs,data in page.scripts:
        if 'ld+json' not in attrs.get('type',''):continue
        for node in walk(data):
            if node.get('@type') not in ('VideoObject','SocialMediaPosting'):continue
            if not result['caption']:result['caption']=str(node.get('description') or node.get('name') or '')[:24000]
            if not result['thumbnail_url']:result['thumbnail_url']=node.get('thumbnailUrl')
            if not result['video_url']:result['video_url']=node.get('contentUrl')
    result['hashtags']=list(dict.fromkeys(result['hashtags']+re.findall(r'#([\w]+)',result['caption'])))
    # Only identify walls after trying actual post data; platform navigation often includes login text.
    lower=document.lower()
    no_post=not result['poi'] and not result['caption']
    blocked=no_post and (meta_blocked or any(x in lower for x in ('captcha','verify you are human','login to continue','log in to continue','consent_required','consent to cookies','login • instagram','login &#8226; instagram','login required','login_required','accounts/login','consentflow')))
    missing=no_post and (meta_missing or any(x in lower for x in ('this video is unavailable','video currently unavailable','this post is unavailable','sorry, this page isn\'t available','video has been removed','private video')))
    result['access_state']='fetch_not_found' if missing else 'fetch_blocked' if blocked else None
    return result
