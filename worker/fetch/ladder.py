"""Ordered accumulation of provenance-labelled signals with honest terminal states."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from psycopg.types.json import Jsonb
from worker.fetch.http import get, check_response, FetchError
from worker.fetch.parsing import parse_html
from worker.fetch.hints import identify

FETCH_VERSION='2026-09-08.2'

def sufficient(signals):
    return bool(signals.get('poi')) or len(signals.get('caption','').strip())>20 or len((signals.get('ocr','')+signals.get('transcript','')).strip())>40

def merge(signals,incoming,tier):
    for key,value in incoming.items():
        if value in (None,'',[],{}):continue
        if key in ('caption','ocr','transcript'):
            if not signals.get(key):signals[key]=value
            elif value not in signals[key]:signals[key]=(signals[key]+'\n'+value)[:24000]
        elif key in ('metadata','unavailable'):signals.setdefault(key,{}).update(value)
        elif key=='hashtags':signals[key]=list(dict.fromkeys(signals.get(key,[])+value))
        else:signals[key]=value
        signals.setdefault('provenance',{}).setdefault(key,[]).append(tier)

def tier_one(resolved,request):
    canonical=resolved['canonical_url'];kind=resolved['platform']
    if kind=='instagram':
        token=os.getenv('INSTAGRAM_OEMBED_TOKEN','').strip()
        if not token:return {},{'outcome':'skipped','reason':'No Instagram oEmbed token configured.','bytes':0}
        url='https://graph.facebook.com/v24.0/instagram_oembed?'+urlencode({'url':canonical,'access_token':token})
    else:
        base='https://www.tiktok.com/oembed' if kind=='tiktok' else 'https://www.youtube.com/oembed'
        url=base+'?'+urlencode({'url':canonical,'format':'json'})
    response=request(url,timeout=6,max_bytes=100_000);check_response(response)
    try:data=json.loads(response.text)
    except ValueError as exc:raise FetchError('fetch_blocked','The embed endpoint returned a non-data page.',bytes_read=len(response.body)) from exc
    if not isinstance(data,dict):raise FetchError('needs_source_info','Invalid embed data.')
    if data.get('error'):raise FetchError('fetch_not_found','The embed endpoint could not find the post.',bytes_read=len(response.body))
    # A YouTube video title alone is not a full caption; preserve it without claiming speech availability.
    result={'caption':str(data.get('title') or '')[:24000],'creator_name':data.get('author_name'),
        'creator_handle':data.get('author_url','').rstrip('/').split('/')[-1], 'thumbnail_url':data.get('thumbnail_url')}
    return result,{'outcome':'ok','bytes':len(response.body),'http_status':response.status}

def tier_two(resolved,request):
    response=request(resolved['canonical_url'],timeout=8);check_response(response)
    if response.status in (301,302,303,307,308):
        target=response.headers.get('location','')
        if any(v in target.lower() for v in ('login','consent','challenge')):raise FetchError('fetch_blocked','The platform returned a login or consent wall.',bytes_read=len(response.body))
        from worker.reel_urls import canonical_reel_url
        target=canonical_reel_url(urljoin(resolved['canonical_url'],target))
        response=request(target,timeout=6);check_response(response)
    result=parse_html(response.text,resolved['platform_video_id'])
    if result.get('access_state'):raise FetchError(result['access_state'],'Platform access page instead of post data.',bytes_read=len(response.body))
    return result,{'outcome':'ok','bytes':len(response.body),'http_status':response.status,'poi_found':bool(result.get('poi'))}

def fetch_signals(resolved,directory,metrics,*,request=get,media=None,use_cache=True,checkpoint=None):
    from worker.db import connect
    if not use_cache:return _fetch_signals(resolved,directory,metrics,request=request,media=media,use_cache=False,checkpoint=checkpoint)
    with connect() as conn:
        conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('fetch:'+resolved['platform']+':'+resolved['platform_video_id'],))
        return _fetch_signals(resolved,directory,metrics,request=request,media=media,use_cache=True,checkpoint=checkpoint)


def _fetch_signals(resolved,directory,metrics,*,request=get,media=None,use_cache=True,checkpoint=None):
    from worker.db import connect
    if use_cache:
        with connect() as conn:
            cached=conn.execute("select signals,outcome from fetch_cache where platform=%s and platform_video_id=%s and fetched_at>now()-interval '30 days' and signals->>'fetch_version'=%s",(resolved['platform'],resolved['platform_video_id'],FETCH_VERSION)).fetchone()
        if cached:
            metrics['fetch_cache_hits']=metrics.get('fetch_cache_hits',0)+1
            return {**cached['signals'],'fetch_state':cached['outcome'],'origin_tier_log':cached['signals'].get('tier_log',[]),'tier_log':[{'tier':'cache','outcome':'hit','bytes':0}], 'fetch_cache_hit':True}
    signals={'fetch_version':FETCH_VERSION,'caption':'','ocr':'','transcript':'','hashtags':[],'poi':None,'unavailable':{},'provenance':{},'tier_log':[]}
    failures=[]
    for tier in (1,2,3):
        start=time.monotonic()
        try:
            if tier==1:incoming,log=tier_one(resolved,request)
            elif tier==2:incoming,log=tier_two(resolved,request)
            else:
                before=metrics.get('media_download_bytes',0)+metrics.get('media_metadata_bytes',0)
                if media is None:
                    from worker.fetch.media import collect_media
                    def progress(partial):
                        merge(signals,partial,'tier_3')
                        if checkpoint:checkpoint(signals)
                    incoming=collect_media(resolved['canonical_url'],directory,metrics,checkpoint=progress)
                else:incoming=media(resolved['canonical_url'],directory,metrics)
                log={'outcome':'ok','bytes':metrics.get('media_download_bytes',0)+metrics.get('media_metadata_bytes',0)-before}
            merge(signals,incoming,f'tier_{tier}')
        except FetchError as exc:
            if getattr(exc,'signals',None):merge(signals,exc.signals,f'tier_{tier}')
            failures.append(exc.state);log={'outcome':exc.state,'detail':exc.detail,'http_status':exc.status,'bytes':exc.bytes_read}
        except Exception as exc:
            failures.append('needs_source_info');log={'outcome':'needs_source_info','detail':type(exc).__name__,'bytes':0}
        if tier==3:log['bytes']=metrics.get('media_download_bytes',0)+metrics.get('media_metadata_bytes',0)-before
        log.update(tier=tier,seconds=round(time.monotonic()-start,3));signals['tier_log'].append(log)
        metrics['fetch_bytes']=metrics.get('fetch_bytes',0)+log['bytes']
        metrics.setdefault('fetch_tiers',[]).append(log)
        if checkpoint:checkpoint(signals)
        if sufficient(signals):break
    signals.update(identify(signals))
    if sufficient(signals):outcome='fetched'
    elif any(t['outcome']=='fetch_not_found' and t['tier'] in (2,3) for t in signals['tier_log']):outcome='fetch_not_found'
    elif 'fetch_blocked' in failures:outcome='fetch_blocked'
    elif failures or signals['unavailable'] or any(signals[k].strip() for k in ('caption','ocr','transcript')):outcome='needs_source_info'
    else:
        assert [row['tier'] for row in signals['tier_log']]==[1,2,3], 'No-content requires all three automatic tiers'
        assert not any(signals[k].strip() for k in ('caption','ocr','transcript')), 'A captured caption/text/speech is never no-content'
        outcome='fetch_ok_no_content'
    signals['fetch_state']=outcome
    if outcome in ('needs_source_info','fetch_ok_no_content'):
        signals['tier_log'].append({'tier':4,'outcome':'needs_source_info','bytes':0,'seconds':0})
    if use_cache and outcome in ('fetched','fetch_ok_no_content'):
        with connect() as conn:
            conn.execute('''insert into fetch_cache(platform,platform_video_id,canonical_url,signals,outcome)
                values(%s,%s,%s,%s,%s) on conflict(platform,platform_video_id) do update
                set signals=excluded.signals,outcome=excluded.outcome,canonical_url=excluded.canonical_url,fetched_at=now(),extractions='{}' ''',
                (resolved['platform'],resolved['platform_video_id'],resolved['canonical_url'],Jsonb(signals),outcome))
    return signals
