"""ReelBot API: anonymous personal libraries with optional Apple sync."""
from __future__ import annotations
import hashlib
import os
import secrets
from uuid import UUID, uuid4
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from psycopg.types.json import Jsonb
from psycopg.errors import UniqueViolation, CheckViolation
from worker.registry import registry, registry_version, sync_registry, validate_attributes
from worker.db import file_entry, prune_auto_folders
from worker.db import connect, enqueue, items, fail_expired, save_hash, store_candidate

app = FastAPI(title='ReelBot',version='3.0.0')

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Device(Input):
    device_id: UUID
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
class Share(Input):
    url: str = Field(min_length=10,max_length=2000)
class EntryEdit(Input):
    note: str | None = Field(default=None,max_length=5000)
    title: str | None = Field(default=None,min_length=1,max_length=200)
    summary: str | None = Field(default=None,max_length=140)
    content_type: str | None = None
    attributes: dict | None = None
    place_id: UUID | None = None
class Preference(Input):
    enabled: bool
class Folder(Input):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1,max_length=100)
class FolderEdit(Input):
    name: str | None = Field(default=None,min_length=1,max_length=100)
    sort_order: int | None = Field(default=None,ge=0,le=100000)
    hidden: bool | None = None
class Assignment(Input):
    item_ids: list[UUID] = Field(min_length=1,max_length=200)
    destination_id: UUID
    source_id: UUID | None = None
    move: bool = False
class Apple(Input):
    identity_token: str = Field(min_length=20,max_length=20000)
    nonce: str = Field(min_length=32,max_length=200)
class SourceInfo(Input):
    entry_id: UUID
    title: str = Field(min_length=1,max_length=200)
    content_type: str
    city: str | None = Field(default=None,max_length=200)
class PlaceChoice(Input):
    place_id: str = Field(min_length=1,max_length=300)
class Review(Input):
    name: str = Field(min_length=1,max_length=200)
    city: str = Field(min_length=1,max_length=200)
    country: str | None = Field(default=None,max_length=200)


def require_user(authorization: str = Header(default='')):
    if not authorization.startswith('Bearer '):
        raise HTTPException(401,'This device has not connected yet.')
    token = authorization[7:]
    if len(token)!=64: raise HTTPException(401,'Invalid device credentials.')
    with connect() as conn:
        row = conn.execute('select user_id from devices where token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row: raise HTTPException(401,'Invalid device credentials.')
    return row['user_id']


def owned(conn,table,identifier,user):
    if table not in {'saves','entries','folders','jobs'}: raise ValueError('Invalid relation')
    row = conn.execute(f'select * from {table} where id=%s and user_id=%s',(identifier,user)).fetchone()
    if not row: raise HTTPException(404,'This item is not in your library.')
    return row

@app.exception_handler(UniqueViolation)
def duplicate(_request,_error):
    return JSONResponse(status_code=409,content={'detail':'That name or item already exists.'})

@app.exception_handler(CheckViolation)
def invalid_attribute(_request,_error):
    return JSONResponse(status_code=422,content={'detail':'An entry attribute does not match its content type.'})

@app.get('/content-types')
def content_types():
    data=registry()
    with connect() as conn: sync_registry(conn,data)
    return {'types':data,'version':registry_version(data)}

@app.get('/healthz')
def health():
    with connect() as conn: conn.execute('select 1')
    return {'status':'ok','product':'personal-reels'}

@app.post('/devices')
def register(body:Device):
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    with connect() as conn:
        conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',(str(body.device_id),))
        existing = conn.execute('select * from devices where id=%s',(body.device_id,)).fetchone()
        if existing:
            if not secrets.compare_digest(existing['token_hash'],digest): raise HTTPException(409,'Device identity is already registered.')
            return {'user_id':existing['user_id'],'device_id':body.device_id}
        if conn.execute('select id from users where device_id=%s',(str(body.device_id),)).fetchone():
            raise HTTPException(409,'Device identity is already registered.')
        user = conn.execute('insert into users(device_id) values(%s) returning id',(str(body.device_id),)).fetchone()
        conn.execute('insert into devices(id,user_id,token_hash) values(%s,%s,%s)',(body.device_id,user['id'],digest))
        return {'user_id':user['id'],'device_id':body.device_id}

@app.post('/share')
def share(body:Share,user=Depends(require_user)):
    try:
        with connect() as conn: return enqueue(conn,user,body.url)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc

@app.get('/items')
def library(q:str=Query(default='',max_length=500),user=Depends(require_user)):
    if q:
        from worker.search import search
        return {'items':search(str(user),q)}
    with connect() as conn: return {'items':items(conn,user)}

@app.get('/sync')
def sync(user=Depends(require_user)):
    with connect() as conn:
        fail_expired(conn)
        return {'registry':registry(),'items':items(conn,user),
            'saves':conn.execute('''select id,source_url,canonical_url,platform,platform_video_id,status,created_at,error_reason,retry_at,
                coalesce(raw_signals->'deterministic_candidates'->0->>'name','') as source_info_hint from saves where user_id=%s order by created_at desc''',(user,)).fetchall(),
            'folders':conn.execute('select * from folders where user_id=%s order by sort_order,name',(user,)).fetchall(),
            'apple_linked':bool(conn.execute('select apple_user_id from users where id=%s',(user,)).fetchone()['apple_user_id'])}

@app.get('/jobs/{job_id}')
def job(job_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        fail_expired(conn)
        return owned(conn,'jobs',job_id,user)

@app.post('/saves/{save_id}/retry')
def retry(save_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        row=owned(conn,'saves',save_id,user)
        from worker.ingestion_states import MANUAL_RETRY
        if row['status'] not in MANUAL_RETRY | {'needs_review'}: return row
        conn.execute("update jobs set status='queued',error_reason=null,updated_at=now() where save_id=%s and user_id=%s",(save_id,user))
        return conn.execute("update saves set status='queued',attempts=0,blocked_attempts=0,error_reason=null,retry_at=null,updated_at=now() where id=%s returning *",(save_id,)).fetchone()

@app.get('/debug/saves/{save_id}')
def save_diagnostics(save_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        saved=owned(conn,'saves',save_id,user)
        saved['shared_urls']=conn.execute('select source_url from save_source_urls where save_id=%s and user_id=%s',(save_id,user)).fetchall()
        return saved

@app.post('/saves/{save_id}/source-info')
def source_info(save_id:UUID,body:SourceInfo,user=Depends(require_user)):
    with connect() as conn:
        saved=owned(conn,'saves',save_id,user);data=sync_registry(conn)
        if saved['status'] in ('queued','processing'):raise HTTPException(409,'This reel is still being processed.')
        if body.content_type not in data:raise HTTPException(422,'Choose a content type.')
        title=body.title.strip()
        if not title:raise HTTPException(422,'Enter a venue or topic name.')
        attrs={'venue_kind':'other'} if body.content_type=='place' else {}
        candidate={'content_type':body.content_type,'title':title,'summary':'Saved from your description.',
            'attributes':attrs,'venue_name':title if body.content_type=='place' else None,'city_hint':body.city or None,
            'confidence':1.0,'evidence':'Owner supplied this venue or topic.','review_reasons':[]}
        row=store_candidate(conn,saved,candidate,None,1.0,entry_id=body.entry_id)
        conn.execute("update saves set raw_signals=raw_signals || %s,status=%s,error_reason=null,retry_at=null,updated_at=now() where id=%s",
            (Jsonb({'user_source_info':body.model_dump(mode='json')}),'needs_review' if row['needs_review'] else 'resolved',save_id))
        conn.execute('update jobs set status=(select status from saves where id=%s),error_reason=null,updated_at=now() where save_id=%s',(save_id,save_id))
        row.pop('embedding',None);return row

@app.post('/items/{item_id}/choose-place')
def choose_place(item_id:UUID,body:PlaceChoice,user=Depends(require_user)):
    from worker.places import persist_google
    with connect() as conn:
        item=owned(conn,'entries',item_id,user)
        options=item['candidate'].get('place_candidates',[])
        selected=next((v['place'] for v in options if v.get('place',{}).get('id')==body.place_id),None)
        if not selected:raise HTTPException(422,'Choose one of the places offered for this entry.')
        place=persist_google(conn,selected,{'name':item['candidate'].get('venue_name') or item['title'],'city_hint':item['candidate'].get('city_hint')},manual=True)
        attrs,reasons=validate_attributes(item['content_type'],item['attributes'])
        row=conn.execute("""update entries set place_id=%s,confidence=1,needs_review=%s,review_reason=%s,
            verified_at=case when %s then null else now() end,embedding=null,updated_at=now() where id=%s returning *""",
            (place['id'],bool(reasons),';'.join(reasons) or None,bool(reasons),item_id)).fetchone()
        file_entry(conn,row,place);update_save_review(conn,item['save_id'],user)
        row.pop('embedding',None);return row

@app.delete('/saves/{save_id}')
def delete_save(save_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        owned(conn,'saves',save_id,user)
        conn.execute('delete from saves where id=%s and user_id=%s',(save_id,user))
        prune_auto_folders(conn,user)
    return {'deleted':True}

@app.delete('/items/{item_id}')
def delete_item(item_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        # Idempotent for an offline outbox retry; never affects another owner.
        conn.execute('delete from entries where id=%s and user_id=%s',(item_id,user))
        prune_auto_folders(conn,user)
    return {'deleted':True}

def update_save_review(conn,save_id,user):
    conn.execute("""update saves set status=case when exists(select 1 from entries where save_id=%s and needs_review)
        then 'needs_review' else 'resolved' end,updated_at=now() where id=%s and user_id=%s
        and status in ('resolved','needs_review')""",(save_id,save_id,user))

@app.patch('/items/{item_id}')
def edit_entry(item_id:UUID,body:EntryEdit,user=Depends(require_user)):
    from datetime import datetime,timezone
    with connect() as conn:
        item=owned(conn,'entries',item_id,user)
        data=sync_registry(conn)
        key=body.content_type or item['content_type']
        changing=body.content_type is not None and body.content_type!=item['content_type']
        try: attrs,reasons=validate_attributes(key,body.attributes if body.attributes is not None else {} if changing else item['attributes'],data=data)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        place_id=body.place_id if 'place_id' in body.model_fields_set else item['place_id']
        if place_id and not conn.execute('select id from entries where user_id=%s and place_id=%s',(user,place_id)).fetchone():
            raise HTTPException(422,'Choose a place already in your library.')
        if data[key]['geo']=='required' and not place_id: reasons.append('unresolved_place')
        structural=bool({'content_type','attributes','title','summary','place_id'} & body.model_fields_set)
        if not structural:
            reasons=item['review_reason'].split(';') if item['needs_review'] and item['review_reason'] else []
        row=conn.execute("""update entries set content_type=%s,title=%s,summary=%s,attributes=%s,note=%s,
            place_id=%s,needs_review=%s,review_reason=%s,verified_at=%s,embedding=null,updated_at=now()
            where id=%s and user_id=%s returning *""",
            (key,(body.title or item['title']).strip(),body.summary if body.summary is not None else item['summary'],Jsonb(attrs),
             body.note if body.note is not None else item['note'],place_id,bool(reasons),';'.join(reasons) or None,
             None if reasons else datetime.now(timezone.utc) if structural else item['verified_at'],item_id,user)).fetchone()
        place=conn.execute('select * from places where id=%s',(place_id,)).fetchone() if place_id else None
        file_entry(conn,row,place,data); update_save_review(conn,item['save_id'],user)
    row.pop('embedding',None)
    return row

@app.post('/items/{item_id}/dismiss-review')
def dismiss_review(item_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        item=owned(conn,'entries',item_id,user)
        conn.execute('update entries set verified_at=now(),needs_review=false,review_reason=null,updated_at=now() where id=%s and user_id=%s',(item_id,user))
        update_save_review(conn,item['save_id'],user)
    return {'saved':True}

@app.post('/items/{item_id}/confirm')
def confirm(item_id:UUID,body:Review,user=Depends(require_user)):
    from worker.places import resolve
    from worker.pipeline import new_metrics,price_metrics
    metrics=new_metrics()
    candidate={'name':body.name.strip(),'city_hint':body.city.strip(),'country_hint':body.country,
               'confidence':1.0,'evidence':'Venue and city confirmed by the owner.'}
    with connect() as conn:
        item=owned(conn,'entries',item_id,user)
        data=sync_registry(conn)
        if data[item['content_type']]['geo']=='never':
            raise HTTPException(422,'This type does not use address lookup. You can link a saved place in the editor.')
        place,confidence,reason=resolve(conn,candidate,metrics)
        attrs,reasons=validate_attributes(item['content_type'],item['attributes'],data=data)
        if not place: reasons.append(reason or 'unresolved_place')
        row=conn.execute("""update entries set candidate=candidate || %s,place_id=%s,confidence=%s,needs_review=%s,
            review_reason=%s,verified_at=case when %s then null else now() end,
            embedding=null,updated_at=now() where id=%s and user_id=%s returning *""",
            (Jsonb({'venue_name':body.name,'city_hint':body.city,'place_candidates':candidate.get('place_candidates',[])}),place['id'] if place else None,confidence,bool(reasons),
             ';'.join(reasons) or None,bool(reasons),item_id,user)).fetchone()
        file_entry(conn,row,place,data);update_save_review(conn,item['save_id'],user)
        conn.execute("insert into events(user_id,save_id,kind,detail) values(%s,%s,'owner_confirmation',%s)",
                     (user,item['save_id'],Jsonb(price_metrics(metrics))))
    row.pop('embedding',None)
    return row

@app.get('/export')
def export_data(user=Depends(require_user)):
    with connect() as conn:
        return {'format':'reelbot.entries.v1','entries':items(conn,user),
            'folders':conn.execute('select * from folders where user_id=%s',(user,)).fetchall(),
            'saves':conn.execute('select id,source_url,canonical_url,platform_video_id,status,created_at,error_reason from saves where user_id=%s',(user,)).fetchall()}

def erase_owner(conn,user):
    conn.execute('delete from events where user_id=%s',(user,))
    conn.execute('delete from jobs where user_id=%s',(user,))
    conn.execute('delete from folders where user_id=%s',(user,))
    conn.execute('delete from saves where user_id=%s',(user,))
    conn.execute('delete from devices where user_id=%s',(user,))
    conn.execute('delete from users where id=%s',(user,))

@app.delete('/account')
def delete_account(user=Depends(require_user)):
    with connect() as conn: erase_owner(conn,user)
    return {'deleted':True}

@app.post('/folders')
def create_folder(body:Folder,user=Depends(require_user)):
    if not body.name.strip(): raise HTTPException(422,'Enter a folder name.')
    with connect() as conn:
        existing=conn.execute('select * from folders where id=%s',(body.id,)).fetchone()
        if existing:
            if existing['user_id']!=user: raise HTTPException(409,'Folder identity is already used.')
            return existing
        return conn.execute("insert into folders(id,user_id,name,kind) values(%s,%s,%s,'custom') returning *",(body.id,user,body.name.strip())).fetchone()

@app.patch('/folders/{folder_id}')
def edit_folder(folder_id:UUID,body:FolderEdit,user=Depends(require_user)):
    with connect() as conn:
        folder=owned(conn,'folders',folder_id,user)
        if body.name is not None and folder['kind']!='custom': raise HTTPException(422,'Automatic folder names follow their content.')
        if body.name is not None and not body.name.strip(): raise HTTPException(422,'Enter a folder name.')
        if body.name is not None:
            conn.execute('''update entries set embedding=null,updated_at=now() where user_id=%s and id in
                (select entry_id from folder_items where folder_id=%s and user_id=%s)''',(user,folder_id,user))
        return conn.execute('''update folders set name=coalesce(%s,name),sort_order=coalesce(%s,sort_order),
            hidden=coalesce(%s,hidden) where id=%s and user_id=%s returning *''',
            (body.name.strip() if body.name else None,body.sort_order,body.hidden,folder_id,user)).fetchone()

@app.delete('/folders/{folder_id}')
def delete_folder(folder_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        row=conn.execute('select * from folders where id=%s and user_id=%s',(folder_id,user)).fetchone()
        if row and row['kind']!='custom': raise HTTPException(422,'You can hide this automatic folder.')
        conn.execute('''update entries set embedding=null,updated_at=now() where user_id=%s and id in
            (select entry_id from folder_items where folder_id=%s and user_id=%s)''',(user,folder_id,user))
        conn.execute("delete from folders where id=%s and user_id=%s and kind='custom'",(folder_id,user))
    return {'deleted':True}

@app.post('/folders/assign')
def assign(body:Assignment,user=Depends(require_user)):
    with connect() as conn:
        destination=owned(conn,'folders',body.destination_id,user)
        if destination['kind']!='custom': raise HTTPException(422,'Choose a custom folder. Automatic filing is kept for you.')
        source=owned(conn,'folders',body.source_id,user) if body.source_id else None
        for identifier in body.item_ids:
            owned(conn,'entries',identifier,user)
            conn.execute('update entries set embedding=null,updated_at=now() where id=%s and user_id=%s',(identifier,user))
            conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s) on conflict do nothing',
                         (body.destination_id,identifier,user))
            if body.move and source and source['kind']=='custom' and body.source_id!=body.destination_id:
                conn.execute('delete from folder_items where folder_id=%s and entry_id=%s and user_id=%s',
                             (body.source_id,identifier,user))
    return {'saved':True}

@app.get('/debug/cost')
def cost(user=Depends(require_user)):
    with connect() as conn:
        rows=conn.execute('''select s.cost,coalesce((select jsonb_object_agg(content_type,n) from
            (select content_type,count(*) n from entries where user_id=s.user_id and save_id=s.id group by content_type) counts),'{}'::jsonb) as entry_types
            from saves s where s.user_id=%s and s.resolved_at is not null order by s.created_at desc limit 100''',(user,)).fetchall()
    costs=[row['cost'] for row in rows]; calls=sum(c.get('places_calls',0) for c in costs); hits=sum(c.get('cache_hits',0) for c in costs)
    types={};unknown=sum(c.get('llm_usage_unavailable_requests',0) for c in costs)
    for row in rows:
        c=row['cost'];counts=c.get('content_types',{}) or row['entry_types'] or {'no_entries':1};total=sum(counts.values())
        for key,n in counts.items():
            bucket=types.setdefault(key,{'save_equivalents':0,'estimated_usd':0,'llm_usd':0,'llm_uncached_equivalent_usd':0})
            bucket['save_equivalents']+=n/total
            for field in ('estimated_usd','llm_usd','llm_uncached_equivalent_usd'): bucket[field]+=c.get(field,0)*n/total
    for bucket in types.values():
        for field in ('estimated_usd','llm_usd','llm_uncached_equivalent_usd'): bucket[field]/=max(bucket['save_equivalents'],1e-9)
    return {'saves':len(costs),'average_usd':sum(c.get('estimated_usd',0) for c in costs)/max(1,len(costs)),
            'cache_hit_rate':hits/max(1,hits+calls),'places_calls':calls,'cache_hits':hits,'by_type':types,
            'prompt_cache_hit_rate':sum(c.get('llm_cache_hits',0) for c in costs)/max(1,sum(c.get('llm_requests',0) for c in costs)),
            'usage_unavailable_requests':unknown,
            'basis':('Known usage only; some request usage was unavailable. ' if unknown else '')+'Measured provider usage × configured rates; mixed saves allocated by entry count. Uncached equivalent is a counterfactual; excludes local compute.'}

@app.get('/auth/apple/challenge')
def apple_challenge(user=Depends(require_user)):
    nonce=secrets.token_hex(32)
    with connect() as conn:
        conn.execute("delete from events where kind='apple_nonce' and (user_id=%s or created_at<now()-interval '10 minutes')",(user,))
        conn.execute("insert into events(user_id,kind,detail) values(%s,'apple_nonce',%s)",
                     (user,Jsonb({'hash':hashlib.sha256(nonce.encode()).hexdigest()})))
    return {'nonce':nonce}


def merge_library(conn,source,target):
    """Merge into a verified Apple account without losing entry edits or custom folders."""
    folder_map={}
    for folder in conn.execute("select * from folders where user_id=%s and kind='custom'",(source,)).fetchall():
        row=conn.execute("""insert into folders(user_id,name,kind,icon,sort_order,hidden) values(%s,%s,'custom',%s,%s,%s)
            on conflict(user_id,kind,content_type,parent_folder_id,name) do update set name=excluded.name returning id""",
            (target,folder['name'],folder['icon'],folder['sort_order'],folder['hidden'])).fetchone()
        folder_map[folder['id']]=row['id']
    for saved in conn.execute('select * from saves where user_id=%s',(source,)).fetchall():
        copied=enqueue(conn,target,saved['source_url'])
        if copied['status']=='queued' and saved['status'] not in {'queued','processing'}:
            conn.execute('update saves set status=%s,raw_signals=%s,cost=%s,error_reason=%s,resolved_at=%s where id=%s',
                (saved['status'],Jsonb(saved['raw_signals']),Jsonb(saved['cost']),saved['error_reason'],saved['resolved_at'],copied['id']))
            conn.execute('update jobs set status=%s where save_id=%s',(saved['status'],copied['id']))
        for item in conn.execute('select * from entries where save_id=%s and user_id=%s',(saved['id'],source)).fetchall():
            row=conn.execute("""insert into entries(user_id,save_id,place_id,note,content_type,title,summary,attributes,
                confidence,needs_review,review_reason,verified_at,candidate,candidate_key)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(user_id,save_id,candidate_key)
                do update set note=case when entries.note='' then excluded.note else entries.note end returning *""",
                (target,copied['id'],item['place_id'],item['note'],item['content_type'],item['title'],item['summary'],Jsonb(item['attributes']),
                 item['confidence'],item['needs_review'],item['review_reason'],item['verified_at'],Jsonb(item['candidate']),item['candidate_key'])).fetchone()
            place=conn.execute('select * from places where id=%s',(row['place_id'],)).fetchone() if row['place_id'] else None
            file_entry(conn,row,place)
            for link in conn.execute('select folder_id from folder_items where entry_id=%s and user_id=%s',(item['id'],source)).fetchall():
                if link['folder_id'] in folder_map:
                    conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s) on conflict do nothing',
                                 (folder_map[link['folder_id']],row['id'],target))
    conn.execute('update devices set user_id=%s where user_id=%s',(target,source))
    erase_owner(conn,source)

@app.post('/auth/apple')
def apple(body:Apple,user=Depends(require_user)):
    import jwt
    digest=hashlib.sha256(body.nonce.encode()).hexdigest()
    try:
        signing=jwt.PyJWKClient('https://appleid.apple.com/auth/keys',timeout=8).get_signing_key_from_jwt(body.identity_token)
        claims=jwt.decode(body.identity_token,signing.key,algorithms=['RS256'],issuer='https://appleid.apple.com',
            audience=os.getenv('APPLE_BUNDLE_ID','com.krishwaghani.reelbot'),options={'require':['exp','iat','iss','aud','sub','nonce']})
        if not secrets.compare_digest(claims['nonce'],body.nonce): raise ValueError('Nonce mismatch')
    except Exception as exc: raise HTTPException(401,'Apple sign-in could not be verified. Please retry.') from exc
    with connect() as conn:
        nonce=conn.execute("delete from events where user_id=%s and kind='apple_nonce' and detail->>'hash'=%s and created_at>now()-interval '10 minutes' returning id",(user,digest)).fetchone()
        if not nonce: raise HTTPException(401,'Sign-in expired. Please retry.')
        conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('apple:'+claims['sub'],))
        linked=conn.execute('select apple_user_id from users where id=%s for update',(user,)).fetchone()
        if linked['apple_user_id'] and linked['apple_user_id']!=claims['sub']:
            raise HTTPException(409,'This library is linked to another Apple account.')
        existing=conn.execute('select id from users where apple_user_id=%s',(claims['sub'],)).fetchone()
        if existing and existing['id']!=user:
            merge_library(conn,user,existing['id']); user=existing['id']
        else:
            conn.execute('update users set apple_user_id=%s where id=%s',(claims['sub'],user))
    return {'user_id':user,'apple_linked':True}

@app.get('/items/{item_id}/details')
def place_details(item_id:UUID,user=Depends(require_user)):
    from worker.places import details
    with connect() as conn:
        item=owned(conn,'entries',item_id,user)
        if not item['place_id']: return {}
        place=conn.execute('select * from places where id=%s',(item['place_id'],)).fetchone()
        try: return details(conn,place)
        except Exception as exc: raise HTTPException(503,'Extra place details are unavailable. Your saved address is still available.') from exc
