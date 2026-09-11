"""Bounded local text detection, temporal evidence and compilation completeness.

Only admitted frames reach OCR/vision. Budgets cover both passes of one attempt.
All media is decoded locally; source text remains untrusted evidence.
"""
from __future__ import annotations
import hashlib,json,re,subprocess,time
from dataclasses import dataclass,field
from pathlib import Path
import numpy as np
from PIL import Image,ImageFilter,ImageOps

VERSION='compilation-v1'
MAX_FRAMES=120
MAX_OCR=25
MAX_VISION=15
MAX_DURATION=180
SCENE_THRESHOLD=.30
RETRY_SCENE_THRESHOLD=.20
CARDINAL=re.compile(r'(?:^|\n|\btop\s+)(?:top\s+)?(\d{1,2})\s+(?:(?:most|spectacular|best|amazing|hidden|must[- ]visit|great|incredible|favorite|favourite|hottest|new|rooftop|vibrant|party|trendy|trendiest|affordable)\s+)*(?:places?|spots?|restaurants?|rooftops?|bars?|caf[eé]s?|pools?|brunches?|things\s+to\s+do|spectacular|best)\b',re.I)
OUTRO=re.compile(r'follow\s+(?:us\s+)?for\s+more|part\s+\d+\s+coming|thanks?\s+for\s+watching|subscribe|save\s+this\s+for',re.I)

def provider_error(exc):
    import os,anthropic
    cause=exc.__cause__ or exc
    body=getattr(cause,'body',{}) or {}
    item=body.get('error',body) if isinstance(body,dict) else {}
    message=str(item.get('message') or type(cause).__name__)[:300]
    for key in ('ANTHROPIC_API_KEY','OPENAI_API_KEY'):
        secret=os.getenv(key)
        if secret:message=message.replace(secret,'[redacted]')
    status=getattr(cause,'status_code',None)
    return {'blocking':isinstance(cause,anthropic.APIError),'code':'provider_credit_exhausted' if 'credit balance' in message.lower() else 'provider_unavailable','status':status,'message':message}

def classify(signals,*,duration=0,cuts=0,first_text=''):
    text='\n'.join(str(signals.get(k) or '') for k in ('title','caption','creator_name','creator_handle'))+'\n'+first_text
    match=CARDINAL.search(text);n=int(match[1]) if match and 2<=int(match[1])<=30 else None
    reasons=[]
    if n:reasons.append('expected_count')
    enumeration=bool(re.search(r'\b(?:spots|places|ranked|restaurants|rooftops)\b',text,re.I))
    guide=bool(re.search(r'guide|travel.*tips|city.*picks',str(signals.get('creator_handle',''))+' '+str(signals.get('creator_name','')),re.I))
    if enumeration:reasons.append('enumeration')
    if guide:reasons.append('guide_creator')
    if duration>25 and (enumeration or guide):reasons.append('long_list')
    if cuts>4:reasons.append('scene_cuts')
    return {'is_compilation':bool(reasons),'expected_venue_count':n,'reasons':reasons,'duration':duration}

def shared_context(signals,opening=''):
    from worker.fetch.hints import identify
    hints=identify({**signals,'caption':opening+'\n'+str(signals.get('caption',''))})
    text=opening+'\n'+str(signals.get('title',''))+'\n'+str(signals.get('caption',''))
    city=hints.get('city_hint') or signals.get('city_hint')
    if re.search(r'new\s+york(?:\s+city)?|\bnyc\b',text,re.I):city='New York'
    return {'title':signals.get('title') or opening,'city_hint':city,
            'venue_kind':'restaurant' if re.search(r'rooftop\s+restaurants?|restaurants?',text,re.I) else None,
            'rooftop':bool(re.search(r'rooftop',text,re.I)),
            'creator_handle':signals.get('creator_handle'),'hashtags':signals.get('hashtags',[]),
            'location_hints':signals.get('location_hints',[])}

def scene_cuts(video,duration,threshold=SCENE_THRESHOLD):
    command=['ffmpeg','-hide_banner','-nostdin','-protocol_whitelist','file,pipe','-i',str(video),'-t',str(min(MAX_DURATION,duration)),
        '-vf',f"scale=320:-2,select='gt(scene,{threshold})',showinfo",'-an','-f','null','-']
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('scene_detection_failed')
    return sorted({round(float(t),3) for t in re.findall(r'pts_time:([\d.]+)',result.stderr) if float(t)<min(MAX_DURATION,duration)})[:120]

@dataclass
class Budget:
    frames:int=0
    ocr:int=0
    vision:int=0
    seen:set=field(default_factory=set)
    groups:list=field(default_factory=list)
    evidence:list=field(default_factory=list)
    events:list=field(default_factory=list)
    provider_failure:dict|None=None
    started:float=field(default_factory=time.monotonic)
    def available(self):return time.monotonic()-self.started<150
    def report(self):return {'sampled_frames':self.frames,'ocr_calls':self.ocr,'vision_calls':self.vision,
        'text_frames':sum(e['text_present'] for e in self.events),'unique_text_frames':len(self.groups),
        'events':self.events,'caps':{'frames':MAX_FRAMES,'ocr':MAX_OCR,'vision':MAX_VISION}}

def sample_times(duration,cuts,*,deeper=False,seen=(),limit=90):
    duration=min(MAX_DURATION,max(.1,float(duration)))
    scene=[round(c+offset,3) for c in [0,*cuts] for offset in (.3,1.,2.) if c+offset<duration]
    uniform=[round(i/(2 if deeper else 1),3) for i in range(int(duration*(2 if deeper else 1))) if i/(2 if deeper else 1)<duration]
    candidates=sorted(set(scene+uniform)-set(seen))
    if len(candidates)<=limit:return candidates
    # Preserve scene cards and coverage. A 180 s input cannot fit 1 fps plus cuts
    # and a retry under the global 120-frame limit; expose that cap in diagnostics.
    priority=list(dict.fromkeys(scene));priority=[t for t in priority if t in candidates]
    chosen=priority[:max(1,limit//2)]
    rest=[t for t in candidates if t not in chosen]
    if rest:chosen += [rest[min(len(rest)-1,int(i*len(rest)/max(1,limit-len(chosen))))] for i in range(limit-len(chosen))]
    return sorted(set(chosen))

def text_gate(image):
    """Cheap connected-edge components; no OCR/model calls in this gate."""
    from scipy.ndimage import label,find_objects,binary_dilation
    gray=np.asarray(ImageOps.grayscale(image),dtype=np.uint8)
    edge=np.asarray(ImageOps.grayscale(image).filter(ImageFilter.FIND_EDGES))>48
    edge[:3]=False;edge[-3:]=False;edge[:,:3]=False;edge[:,-3:]=False
    joined=edge
    labels,_=label(joined); boxes=[]
    for part in find_objects(labels):
        if part is None:continue
        h=part[0].stop-part[0].start;w=part[1].stop-part[1].start
        if 6<=h<=min(95,gray.shape[0]*.14) and 3<=w<=180 and .12<=w/h<=14:boxes.append(part)
    # Prefer a high-contrast title band for deduplication over moving b-roll.
    white=(gray>220).mean(axis=1); bands=np.where(white>.70)[0]
    crop=image
    if len(bands)>12:
        runs=np.split(bands,np.where(np.diff(bands)>35)[0]+1);run=max(runs,key=len)
        if len(run)>12:crop=image.crop((0,max(0,int(run[0])-8),image.width,min(image.height,int(run[-1])+9)))
    small=np.asarray(ImageOps.grayscale(crop).resize((65,16)),dtype=np.int16)
    phash=np.packbits(small[:,1:]>small[:,:-1]).tobytes().hex()
    return len(boxes)>=3,phash,len(boxes)

def hash_distance(left,right):return (int(left,16)^int(right,16)).bit_count()

def ocr_frame(path):
    import pytesseract
    image=Image.open(path).convert('RGB')
    gray=np.asarray(ImageOps.grayscale(image));bands=np.where((gray>220).mean(axis=1)>.70)[0]
    psm=11
    if len(bands)>12:
        runs=np.split(bands,np.where(np.diff(bands)>35)[0]+1);run=max(runs,key=len)
        if 12<len(run)<image.height*.5:
            image=image.crop((0,max(0,int(run[0])-4),image.width,min(image.height,int(run[-1])+5)));psm=6
    data=pytesseract.image_to_data(image,config=f'--psm {psm}',output_type=pytesseract.Output.DICT,timeout=3)
    lines={};conf=[]
    for i,word in enumerate(data['text']):
        word=word.strip();score=float(data['conf'][i])
        if word and score>=15:
            lines.setdefault((data['block_num'][i],data['par_num'][i],data['line_num'][i]),[]).append(word);conf.append(score)
    return '\n'.join(' '.join(v) for v in lines.values()),(sum(conf)/len(conf) if conf else 0)

def scan(video,directory,duration,cuts,budget,metrics,*,deeper=False):
    from worker.fetch.media import vision_text
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    limit=min(MAX_FRAMES-budget.frames,30 if deeper else 90)
    times=sample_times(duration,cuts,deeper=deeper,seen=budget.seen,limit=limit)
    admitted=[]
    for point in times:
        if budget.frames>=MAX_FRAMES or not budget.available():break
        budget.frames+=1;budget.seen.add(point);target=root/f'frame-{point:.3f}.jpg'
        event={'timestamp':point,'pass':2 if deeper else 1,'text_present':False};budget.events.append(event)
        try:
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file,pipe','-ss',str(point),'-i',str(video),'-frames:v','1','-vf','scale=640:-2','-an',str(target)],capture_output=True,timeout=4,check=True)
            with Image.open(target) as image:present,phash,quality=text_gate(image)
            event.update(text_present=present,text_components=quality,perceptual_hash=phash)
            if not present:continue
            duplicate=next((g for g in reversed(budget.groups) if (not deeper or g.get('processed')) and abs(point-g['timestamp'])<=6 and hash_distance(phash,g['hash'])<=10),None)
            if duplicate:event['deduplicated_to']=duplicate['timestamp'];continue
            budget.groups.append({'timestamp':point,'hash':phash});admitted.append((point,target,event))
        except Exception as exc:event['failure']=type(exc).__name__
    # Time-spaced selection prevents opening-card repetitions exhausting OCR.
    remaining=min(MAX_OCR-budget.ocr, MAX_OCR if deeper else 17)
    if len(admitted)>remaining:
        admitted=[admitted[int(i*len(admitted)/remaining)] for i in range(remaining)] if remaining else []
    for point,path,event in admitted:
        if budget.ocr>=MAX_OCR or not budget.available():break
        budget.ocr+=1;text='';confidence=0
        for group in budget.groups:
            if group['timestamp']==point:group['processed']=True
        try:text,confidence=ocr_frame(path)
        except Exception as exc:event['ocr_failure']=type(exc).__name__
        source='ocr';event['ocr_confidence']=round(confidence,2)
        if (confidence<65 or sum(c.isalpha() for c in text)<4) and budget.vision<(MAX_VISION if deeper else 10) and budget.available() and not budget.provider_failure:
            budget.vision+=1
            try:
                value=vision_text([path],metrics)
                if value.strip():text=value;source='vision'
            except Exception as exc:
                event['vision_failure']=provider_error(exc)
                if event['vision_failure']['blocking']:budget.provider_failure=event['vision_failure'];metrics['provider_error']=event['vision_failure']
        ref={'timestamp':point,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'file':path.name}
        budget.evidence.append({'timestamp':point,'text':text[:2500],'confidence':confidence,'source':source,'frame_ref':ref})
        event.update(text=text[:2500],source=source)
    metrics['compilation_scan']=budget.report()
    return budget.evidence

def timeline(duration,cuts,evidence,transcript_segments):
    usable=sorted([e for e in evidence if e.get('text','').strip()],key=lambda e:e['timestamp'])
    changes=[];previous=''
    for e in usable:
        text=re.sub(r'\W+','',e['text']).lower()
        if text!=previous:changes.append(e['timestamp']);previous=text
    edges=sorted({0.,min(float(duration),MAX_DURATION),*[float(c) for c in cuts if 0<c<duration],*[t for t in changes if 0<t<duration]})
    rows=[]
    for start,end in zip(edges,edges[1:]):
        frames=[e for e in usable if start<=e['timestamp']<end]
        speech=[t for t in transcript_segments if float(t.get('start',0))<end and float(t.get('end',0))>start]
        text='\n'.join(e['text'] for e in frames);words=' '.join(str(t.get('text','')) for t in speech)
        if not text.strip() and not words.strip():continue
        intro=bool(CARDINAL.search(text)) and start<min(12,duration*.25)
        outro=bool(OUTRO.search(text)) and not words.strip() and start>duration*.65
        rows.append({'segment_id':len(rows),'t_start':round(start,3),'t_end':round(end,3),'ocr_text':[e['text'] for e in frames],
            'transcript_text':words,'transcript_segments':speech,'frame_refs':[e['frame_ref'] for e in frames],
            'sources':sorted({e['source'] for e in frames}|({'transcript'} if words else set())),
            'excluded':'intro' if intro else 'outro' if outro else None})
    return rows

def normalize_name(value):return re.sub(r'[^\w]','',str(value or '').casefold())
def deduplicate(rows):
    result={}
    for row in rows:
        name=row.get('venue_name') or row.get('title','')
        if not name or normalize_name(name) in {'unidentifiedplace','unknown','newyork','newyorkcity'}:continue
        key=normalize_name(name)
        if key not in result or row.get('confidence',0)>result[key].get('confidence',0):result[key]=row
    return list(result.values())

def extract_segments(segments,context,metrics,cache=None):
    from concurrent.futures import ThreadPoolExecutor
    from worker.pipeline import extract_candidates,new_metrics
    from worker.venue_identity import inputs_for
    cache=cache if cache is not None else {}
    pending=[s for s in segments if not s['excluded']][:MAX_OCR]
    def extract(segment):
        if cache.get('_provider_error'):return [],None
        key=hashlib.sha256(json.dumps([segment['ocr_text'],segment['transcript_text'],context],sort_keys=True).encode()).hexdigest()
        if key in cache:return cache[key],None
        signals={'caption':'','ocr':'\n'.join(segment['ocr_text']),'transcript':segment['transcript_text'],
            'city_hint':context.get('city_hint'),'hashtags':context.get('hashtags',[]),'creator_handle':context.get('creator_handle'),
            'segment_context':{**context,'t_start':segment['t_start'],'t_end':segment['t_end'],'max_venues':1},'unavailable':{}}
        signals.update(inputs_for(signals).payload())
        local=new_metrics();diag={}
        try:
            rows=extract_candidates(signals,local,diagnostics=diag)
            rows=deduplicate([r for r in rows if r['content_type']=='place' and r.get('venue_name')])
            rows=sorted(rows,key=lambda r:r['confidence'],reverse=True)[:1]
            for row in rows:
                row['city_hint']=row.get('city_hint') or context.get('city_hint')
                if context.get('venue_kind'):
                    row['attributes']['venue_kind']=context['venue_kind']
                    row['compilation_context']={'venue_kind':context['venue_kind'],'title':context.get('title',''),'source':'reel_shared_context'}
                if context.get('rooftop'):row['attributes']['rooftop']=True
                row['temporal_segment']={k:segment[k] for k in ('t_start','t_end','sources','frame_refs')}
            cache[key]=rows
            return rows,local
        except Exception as exc:
            segment['extraction_failure']=provider_error(exc)
            if segment['extraction_failure']['blocking']:cache['_provider_error']=segment['extraction_failure']
            return [],local
    rows=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for found,local in pool.map(extract,pending):
            rows.extend(found)
            if local:
                for key,value in local.items():
                    if isinstance(value,(int,float)) and not isinstance(value,bool):metrics[key]=metrics.get(key,0)+value
    return deduplicate(rows)

def collect_compilation(video,directory,duration,signals,metrics,transcript_segments=(),checkpoint=None):
    budget=Budget();diagnostics={'version':VERSION,'passes':[]};all_cuts=[];cache={};rows=[]
    for attempt in range(2):
        threshold=RETRY_SCENE_THRESHOLD if attempt else SCENE_THRESHOLD
        try:cuts=scene_cuts(video,duration,threshold)
        except Exception as exc:cuts=[];diagnostics['scene_error']=type(exc).__name__
        all_cuts=sorted(set(all_cuts+cuts))
        evidence=scan(video,Path(directory)/'compilation',duration,cuts,budget,metrics,deeper=bool(attempt))
        opening='\n'.join(e['text'] for e in evidence if e['timestamp']<min(12,duration*.25))
        classification=classify(signals,duration=duration,cuts=len(cuts),first_text=opening)
        context=shared_context(signals,opening);segments=timeline(duration,all_cuts,evidence,transcript_segments)
        if budget.provider_failure:cache['_provider_error']=budget.provider_failure
        rows=deduplicate(rows+extract_segments(segments,context,metrics,cache))
        if cache.get('_provider_error'):
            diagnostics['provider_error']=cache['_provider_error'];metrics['provider_error']=cache['_provider_error']
        expected=classification['expected_venue_count'] or signals.get('expected_venue_count')
        diagnostics['passes'].append({'pass':attempt+1,'scene_threshold':threshold,'scene_cuts':cuts,'found':len(rows),'expected':expected,'budget':{k:v for k,v in budget.report().items() if k!='events'}})
        diagnostics.update(classification=classification,expected_venue_count=expected,timeline=segments,shared_context=context,scan=budget.report(),found=len(rows))
        result={'is_compilation':True,'expected_venue_count':expected,'compilation':diagnostics,'compilation_candidates':rows,
            'ocr':'\n'.join(e['text'] for e in evidence),'city_hint':context.get('city_hint')}
        if checkpoint:checkpoint(result)
        if diagnostics.get('provider_error') or not expected or len(rows)>=expected or attempt:break
        diagnostics['escalation_reason']=f'found_{len(rows)}_of_{expected}'
        metrics['compilation_escalations']=metrics.get('compilation_escalations',0)+1
    metrics['reel_class']='compilation';metrics['compilation_scan']=budget.report()
    return result
