"""Last automatic tier: bounded media, six text samples, speech and vision fallback."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import random
import subprocess
import time
from pathlib import Path
import anthropic
import yt_dlp
from worker.fetch.http import FetchError, headers, settings, platform

MAX_BYTES=50_000_000
MAX_SECONDS=90

def classify_media_error(error):
    text=str(error).lower()
    if any(v in text for v in ('429','403','login required','sign in','log in','captcha','rate limit','not a bot','empty media response')):return 'fetch_blocked'
    if any(v in text for v in ('404','410','private video','video unavailable','video has been removed','does not exist','this video is not available','video not available')):return 'fetch_not_found'
    return 'needs_source_info'

def frame_times(duration):
    duration=min(MAX_SECONDS,max(1,float(duration or 1)))
    return [min(1,duration*.9)]+[duration*(i+.5)/5 for i in range(5)]

def vision_text(frames,metrics,checkpoint=None):
    prompt='Read the visible text in these chronological video frames, especially place names, addresses and instructional overlays. Return only text actually visible, no guesses, commentary or invented words. Return an empty string if there is no readable text.'
    blocks=[];images=[]
    for frame in frames[:6]:
        raw=frame.read_bytes();images.append({'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        blocks.append({'type':'image','source':{'type':'base64','media_type':'image/jpeg','data':base64.b64encode(raw).decode()}})
    blocks.append({'type':'text','text':prompt})
    metrics['vision_requests']=metrics.get('vision_requests',0)+1
    metrics['vision_requests_pending']=metrics.get('vision_requests_pending',0)+1
    if checkpoint:checkpoint()
    try:
        with anthropic.Anthropic(timeout=12,max_retries=0) as client:
            response=client.messages.create(model=os.getenv('ANTHROPIC_VISION_MODEL',os.getenv('ANTHROPIC_FAST_MODEL','claude-haiku-4-5-20251001')),max_tokens=1200,messages=[{'role':'user','content':blocks}])
        text=''.join(b.text for b in response.content if b.type=='text')
        metrics['vision_tokens_in']=metrics.get('vision_tokens_in',0)+response.usage.input_tokens
        metrics['vision_tokens_out']=metrics.get('vision_tokens_out',0)+response.usage.output_tokens
        metrics.setdefault('vision_diagnostics',[]).append({'prompt':prompt,'frames':images,'output':text})
        metrics['vision_requests_pending']-=1
        return text.strip()
    except Exception as exc:
        metrics['vision_requests_pending']-=1
        metrics['llm_usage_unavailable_requests']=metrics.get('llm_usage_unavailable_requests',0)+1
        raise FetchError('needs_source_info','Vision text recognition was unavailable.') from exc

def bounded_media_file(url,target,request_url):
    """Retain at most 50 MB, including for long videos; decoders never read remote URLs."""
    import httpx
    from urllib.parse import urljoin
    from worker.media import validate_public_url
    from worker.fetch.http import check_response,Response
    start=time.monotonic();total=0
    with httpx.Client(proxy=os.getenv('REELBOT_FETCH_PROXY') or None,trust_env=False,timeout=6,follow_redirects=False,
                      headers={**headers(request_url),'Range':f'bytes=0-{MAX_BYTES-1}','Accept-Encoding':'identity'}) as client:
        for _ in range(5):
            validate_public_url(url)
            with client.stream('GET',url) as response:
                check_response(Response(url,response.status_code,dict(response.headers),b''))
                if response.status_code in (301,302,303,307,308):url=urljoin(url,response.headers.get('location',''));continue
                expected=int(response.headers.get('content-length') or 0)
                with target.open('wb') as file:
                    for chunk in response.iter_bytes(chunk_size=64*1024):
                        usable=chunk[:MAX_BYTES-total];file.write(usable);total+=len(usable)
                        if total>=MAX_BYTES or time.monotonic()-start>18:
                            return total,True
                return total,expected>MAX_BYTES
    raise FetchError('needs_source_info','Media redirect limit exceeded.')


def collect_media(url,directory,metrics,checkpoint=None):
    from worker.media import ytdlp_options,extract_caption,stage_transcript,clean_ocr_text
    from worker.fetch.http import rate_limit
    from PIL import Image
    import pytesseract
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    result={'caption':'','ocr':'','transcript':'','unavailable':{},'metadata':{}}
    start=time.monotonic();downloaded=0;frames=[]
    class QuietLog:
        def debug(self,*_):pass
        def warning(self,*_):pass
        def error(self,*_):pass
    class LimitedDL(yt_dlp.YoutubeDL):
        def urlopen(self,request):
            rate_limit(url,6)
            response=super().urlopen(request);read=response.read
            def measured_read(size=-1):
                remaining=5_000_000-metrics.get('media_metadata_bytes',0)
                data=read(min(size,remaining+1) if size is not None and size>=0 else remaining+1)
                metrics['media_metadata_bytes']=metrics.get('media_metadata_bytes',0)+len(data)
                if metrics['media_metadata_bytes']>5_000_000:raise FetchError('needs_source_info','Media metadata exceeded its byte budget.')
                return data
            response.read=measured_read
            return response
    opts=ytdlp_options(url,directory)
    opts.update({'http_headers':headers(url),'max_filesize':MAX_BYTES,'logger':QuietLog(),
        'socket_timeout':6,'retries':0,'fragment_retries':0,'extractor_retries':0})
    if os.getenv('REELBOT_FETCH_PROXY'):opts['proxy']=os.environ['REELBOT_FETCH_PROXY']
    try:
        with LimitedDL(opts) as ydl:
            try:info=ydl.extract_info(url,download=False)
            except Exception as exc:raise FetchError(classify_media_error(exc),'The media source could not be read.') from exc
        if not isinstance(info,dict):raise FetchError('needs_source_info','The platform supplied no media metadata.')
        if info.get('availability') in ('private','subscriber_only','premium_only'):raise FetchError('fetch_not_found','The post is private.')
        result['caption']=extract_caption(info)[:24000]
        if result['caption'].lower().startswith('youtube video #'):raise FetchError('fetch_not_found','The video is unavailable.')
        result['creator_handle']=str(info.get('uploader_id') or '');result['hashtags']=info.get('tags') or []
        duration=float(info.get('duration') or 0);result['metadata']={'duration':duration,'caption_source':'yt-dlp'}
        if checkpoint:checkpoint(result)
        if len(result['caption'])>20:return result
        if os.getenv('REELBOT_ENABLE_VIDEO_DOWNLOAD','true').lower() not in ('true','1','yes','on'):
            result['unavailable'].update(frames='Media downloading is disabled.',transcript='Media downloading is disabled.');return result
        formats=[f for f in info.get('formats',[]) if f.get('vcodec') not in ('none',None)
            and f.get('protocol') in ('https','http') and str(f.get('url','')).startswith('https://')]
        formats.sort(key=lambda f:(f.get('acodec') in ('none',None),abs((f.get('height') or 360)-360),f.get('tbr') or 9999))
        chosen=formats[0] if formats else info
        source=chosen.get('url')
        if not source or chosen.get('protocol') not in ('https','http',None):raise FetchError('needs_source_info','Bounded video frames are unavailable for this stream.')
        video=directory/'source.mp4';downloaded,truncated=bounded_media_file(source,video,url)
        if not duration:
            try:
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(video)],capture_output=True,text=True,timeout=4,check=True)
                duration=float(probe.stdout.strip())
            except Exception:result['unavailable']['duration']='Video duration could not be read.'
        oversized=duration>MAX_SECONDS or truncated
        if oversized:
            result['unavailable']['transcript']='Audio skipped for media beyond 90 seconds or 50 MB.'
            result['metadata']['frames_only']=True
        elif not duration:result['unavailable']['transcript']='Audio skipped because video duration could not be verified.'
        elif info.get('music_only') is True and info.get('has_speech') is False:
            result['metadata']['audio_skipped']='explicitly identified music without speech'
        elif chosen.get('acodec')=='none':result['unavailable']['transcript']='No accessible audio track.'
        elif os.getenv('REELBOT_ENABLE_TRANSCRIPTION','true').lower() not in ('true','1','yes','on'):result['unavailable']['transcript']='Speech transcription is disabled.'
        else:
            at=time.monotonic()
            try:result['transcript']=stage_transcript(video,directory)[:24000]
            except Exception:result['unavailable']['transcript']='Speech transcription failed.'
            metrics['transcription_seconds']+=time.monotonic()-at
        for index,point in enumerate(frame_times(duration)):
            if time.monotonic()-start>74:
                result['unavailable']['frames']='Frame sampling reached its time limit.';break
            target=directory/f'sample-{index}.jpg'
            try:
                subprocess.run(['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-ss',str(point),'-i',str(video),
                    '-frames:v','1','-vf','scale=960:-2','-an',str(target)],capture_output=True,timeout=5,check=True)
                if not target.exists() or not target.stat().st_size:raise RuntimeError('No frame at this timestamp')
                frames.append(target)
            except Exception:result['unavailable']['frames']='Some sampled frames could not be read.'
        texts=[];at=time.monotonic()
        for frame in frames:
            try:
                with Image.open(frame) as image:texts.append(pytesseract.image_to_string(image,timeout=3))
            except Exception:result['unavailable']['ocr']='Some text recognition failed.'
        result['ocr']=clean_ocr_text(texts);metrics['ocr_seconds']+=time.monotonic()-at
        if checkpoint:checkpoint(result)
        result['metadata']['frame_count']=len(frames)
        if len(result['ocr'].strip())<40 and frames:
            try:
                result['vision_text']=vision_text(frames,metrics,checkpoint=(lambda:checkpoint(result)) if checkpoint else None)
                result['ocr']=clean_ocr_text([result['ocr'],result['vision_text']]);result['metadata']['ocr_includes_vision']=True
            except FetchError as exc:result['unavailable']['vision']=exc.detail
        if not frames:result['unavailable']['frames']='No sampled frames were available.'
        return result
    except FetchError as exc:
        exc.signals=result
        raise
    finally:
        metrics['media_download_bytes']=metrics.get('media_download_bytes',0)+downloaded
        metrics['media_seconds']=metrics.get('media_seconds',0)+round(time.monotonic()-start,3)
        for path in directory.rglob('*'):
            if path.is_file():path.unlink(missing_ok=True)
