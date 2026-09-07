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
from psycopg.errors import UniqueViolation
from worker.db import connect, enqueue, items, fail_expired, save_hash, store_candidate

app = FastAPI(title='ReelBot',version='2.0.0')

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Device(Input):
    device_id: UUID
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
class Share(Input):
    url: str = Field(min_length=10,max_length=2000)
class Note(Input):
    note: str = Field(max_length=5000)
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
    if table not in {'saves','user_places','folders','jobs'}: raise ValueError('Invalid relation')
    row = conn.execute(f'select * from {table} where id=%s and user_id=%s',(identifier,user)).fetchone()
    if not row: raise HTTPException(404,'This item is not in your library.')
    return row

@app.exception_handler(UniqueViolation)
def duplicate(_request,_error):
    return JSONResponse(status_code=409,content={'detail':'That name or item already exists.'})

@app.get('/healthz')
def health():
    with connect() as conn: conn.execute('select 1')
    return {'status':'ok','product':'personal-places'}

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
        return {'items':items(conn,user),
            'saves':conn.execute('select * from saves where user_id=%s order by created_at desc',(user,)).fetchall(),
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
        if row['status'] not in {'failed','needs_review','no_places_found'}: return row
        conn.execute("update jobs set status='queued',error_reason=null,updated_at=now() where save_id=%s and user_id=%s",(save_id,user))
        return conn.execute("update saves set status='queued',attempts=0,error_reason=null,retry_at=null,updated_at=now() where id=%s returning *",(save_id,)).fetchone()

@app.delete('/saves/{save_id}')
def delete_save(save_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        owned(conn,'saves',save_id,user)
        conn.execute('delete from saves where id=%s and user_id=%s',(save_id,user))
    return {'deleted':True}

@app.delete('/items/{item_id}')
def delete_item(item_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        # Idempotent for an offline outbox retry; never affects another owner.
        conn.execute('delete from user_places where id=%s and user_id=%s',(item_id,user))
    return {'deleted':True}

@app.patch('/items/{item_id}')
def edit_note(item_id:UUID,body:Note,user=Depends(require_user)):
    with connect() as conn:
        owned(conn,'user_places',item_id,user)
        conn.execute('update user_places set note=%s,updated_at=now() where id=%s and user_id=%s',(body.note,item_id,user))
    return {'saved':True}

@app.post('/items/{item_id}/confirm')
def confirm(item_id:UUID,body:Review,user=Depends(require_user)):
    from worker.places import resolve
    from worker.pipeline import new_metrics,price_metrics
    from worker.db import file_place
    metrics=new_metrics()
    candidate={'name':body.name.strip(),'city_hint':body.city.strip(),'country_hint':body.country,
               'category_guess':None,'confidence':1.0,'evidence':'Venue and city confirmed by the owner.'}
    with connect() as conn:
        item=owned(conn,'user_places',item_id,user)
        place,confidence,reason=resolve(conn,candidate,metrics)
        row=conn.execute('''update user_places set candidate=%s,place_id=%s,confidence=%s,needs_review=%s,
            embedding=null,updated_at=now() where id=%s and user_id=%s returning *''',
            (Jsonb({**candidate,'review_reason':reason}),place['id'] if place else None,confidence,not place or confidence<.6,item_id,user)).fetchone()
        file_place(conn,row,place)
        conn.execute('''update saves set status=case when exists(select 1 from user_places where save_id=%s and needs_review)
            then 'needs_review' else 'resolved' end,updated_at=now() where id=%s and user_id=%s''',(item['save_id'],item['save_id'],user))
        conn.execute("insert into events(user_id,save_id,kind,detail) values(%s,%s,'owner_confirmation',%s)",
                     (user,item['save_id'],Jsonb(price_metrics(metrics))))
    return row

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
        if body.name is not None and folder['kind']!='custom': raise HTTPException(422,'Automatic folder names follow their places.')
        if body.name is not None and not body.name.strip(): raise HTTPException(422,'Enter a folder name.')
        return conn.execute('''update folders set name=coalesce(%s,name),sort_order=coalesce(%s,sort_order),
            hidden=coalesce(%s,hidden) where id=%s and user_id=%s returning *''',
            (body.name.strip() if body.name else None,body.sort_order,body.hidden,folder_id,user)).fetchone()

@app.delete('/folders/{folder_id}')
def delete_folder(folder_id:UUID,user=Depends(require_user)):
    with connect() as conn:
        row=conn.execute('select * from folders where id=%s and user_id=%s',(folder_id,user)).fetchone()
        if row and row['kind']!='custom': raise HTTPException(422,'You can hide this automatic folder.')
        conn.execute("delete from folders where id=%s and user_id=%s and kind='custom'",(folder_id,user))
    return {'deleted':True}

@app.post('/folders/assign')
def assign(body:Assignment,user=Depends(require_user)):
    with connect() as conn:
        destination=owned(conn,'folders',body.destination_id,user)
        if destination['kind']!='custom': raise HTTPException(422,'Choose a custom folder. Automatic filing is kept for you.')
        source=owned(conn,'folders',body.source_id,user) if body.source_id else None
        for identifier in body.item_ids:
            owned(conn,'user_places',identifier,user)
            conn.execute('insert into folder_items(folder_id,user_place_id,user_id) values(%s,%s,%s) on conflict do nothing',
                         (body.destination_id,identifier,user))
            if body.move and source and source['kind']=='custom' and body.source_id!=body.destination_id:
                conn.execute('delete from folder_items where folder_id=%s and user_place_id=%s and user_id=%s',
                             (body.source_id,identifier,user))
    return {'saved':True}

@app.get('/debug/cost')
def cost(user=Depends(require_user)):
    with connect() as conn:
        rows=conn.execute('select cost from saves where user_id=%s and resolved_at is not null order by created_at desc limit 100',(user,)).fetchall()
    costs=[row['cost'] for row in rows]; calls=sum(c.get('places_calls',0) for c in costs); hits=sum(c.get('cache_hits',0) for c in costs)
    return {'saves':len(costs),'average_usd':sum(c.get('estimated_usd',0) for c in costs)/max(1,len(costs)),
            'cache_hit_rate':hits/max(1,hits+calls),'places_calls':calls,'cache_hits':hits,
            'basis':'Measured provider usage × configured rates; excludes local compute.'}

@app.get('/auth/apple/challenge')
def apple_challenge(user=Depends(require_user)):
    nonce=secrets.token_hex(32)
    with connect() as conn:
        conn.execute("delete from events where kind='apple_nonce' and (user_id=%s or created_at<now()-interval '10 minutes')",(user,))
        conn.execute("insert into events(user_id,kind,detail) values(%s,'apple_nonce',%s)",
                     (user,Jsonb({'hash':hashlib.sha256(nonce.encode()).hexdigest()})))
    return {'nonce':nonce}


def merge_library(conn,source,target):
    """Copy an authenticated local library to the verified Apple account, preserving notes/folders."""
    folder_map={}
    for folder in conn.execute('select * from folders where user_id=%s',(source,)).fetchall():
        row=conn.execute('''insert into folders(user_id,name,kind,icon,sort_order,hidden) values(%s,%s,%s,%s,%s,%s)
            on conflict(user_id,kind,name) do update set name=excluded.name returning id''',
            (target,folder['name'],folder['kind'],folder['icon'],folder['sort_order'],folder['hidden'])).fetchone()
        folder_map[folder['id']]=row['id']
    for saved in conn.execute('select * from saves where user_id=%s',(source,)).fetchall():
        copied=enqueue(conn,target,saved['source_url'])
        if copied['status']=='queued' and saved['status'] in {'resolved','needs_review','no_places_found','failed'}:
            conn.execute('update saves set status=%s,raw_signals=%s,cost=%s,error_reason=%s,resolved_at=%s where id=%s',
                (saved['status'],Jsonb(saved['raw_signals']),Jsonb(saved['cost']),saved['error_reason'],saved['resolved_at'],copied['id']))
        for item in conn.execute('select * from user_places where save_id=%s and user_id=%s',(saved['id'],source)).fetchall():
            row=conn.execute('''insert into user_places(user_id,save_id,place_id,note,confidence,needs_review,candidate,candidate_key,embedding)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(user_id,save_id,candidate_key)
                do update set note=case when user_places.note='' then excluded.note else user_places.note end returning id''',
                (target,copied['id'],item['place_id'],item['note'],item['confidence'],item['needs_review'],Jsonb(item['candidate']),item['candidate_key'],item['embedding'])).fetchone()
            for link in conn.execute('select folder_id from folder_items where user_place_id=%s and user_id=%s',(item['id'],source)).fetchall():
                conn.execute('insert into folder_items(folder_id,user_place_id,user_id) values(%s,%s,%s) on conflict do nothing',
                             (folder_map[link['folder_id']],row['id'],target))
    conn.execute('update devices set user_id=%s where user_id=%s',(target,source))

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
        existing=conn.execute('select id from users where apple_user_id=%s',(claims['sub'],)).fetchone()
        if existing and existing['id']!=user:
            merge_library(conn,user,existing['id']); user=existing['id']
        else:
            linked=conn.execute('select apple_user_id from users where id=%s for update',(user,)).fetchone()
            if linked['apple_user_id'] and linked['apple_user_id']!=claims['sub']: raise HTTPException(409,'This library is linked to another Apple account.')
            conn.execute('update users set apple_user_id=%s where id=%s',(claims['sub'],user))
    return {'user_id':user,'apple_linked':True}
