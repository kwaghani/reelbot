"""Verified Apple identity and opaque, hashed, rotating first-party sessions."""
import hashlib
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException
from config import settings
from worker.db import connect

ISSUER='https://appleid.apple.com'
def digest(value): return hashlib.sha256(value.encode()).hexdigest()

class AppleKeys:
    def __init__(self): self.keys={}; self.expires=0; self.lock=threading.Lock(); self.last_fetch=0
    def get(self,token):
        header=jwt.get_unverified_header(token)
        if header.get('alg')!='RS256' or not isinstance(header.get('kid'),str): raise ValueError('Invalid signing header')
        with self.lock:
            now=time.monotonic()
            if now>=self.expires or header['kid'] not in self.keys and now-self.last_fetch>30:
                response=httpx.get(ISSUER+'/auth/keys',timeout=8); response.raise_for_status()
                self.keys={k['kid']:jwt.PyJWK(k).key for k in response.json()['keys'] if k.get('kty')=='RSA'}
                cache=response.headers.get('cache-control','')
                age=re.search(r'max-age=(\d+)',cache)
                self.expires=now+(0 if 'no-store' in cache or 'no-cache' in cache else max(0,min(int(age[1]) if age else 300,86400)-int(response.headers.get('age','0'))))
                self.last_fetch=now
            return self.keys[header['kid']]

apple_keys=AppleKeys()
def verify_apple(token,nonce):
    try:
        claims=jwt.decode(token,apple_keys.get(token),algorithms=['RS256'],issuer=ISSUER,
            audience=settings().apple_bundle_id,options={'require':['exp','iat','iss','aud','sub','nonce']})
        if not isinstance(claims['sub'],str) or not claims['sub'] or not secrets.compare_digest(claims['nonce'],nonce):
            raise ValueError('Invalid identity or nonce')
        return claims
    except Exception as exc: raise HTTPException(401,'Apple sign-in could not be verified. Please retry.') from exc

def token_cipher():
    key=os.getenv('ACCOUNT_TOKEN_ENCRYPTION_KEY','')
    if not key: raise HTTPException(503,'Apple account setup is not complete. Your local library is safe.')
    return Fernet(key.encode())

def apple_client_secret():
    value=os.getenv('APPLE_CLIENT_SECRET','')
    if not value: raise HTTPException(503,'Apple account setup is not complete. Your local library is safe.')
    return value

def exchange_apple_code(code,sub,nonce):
    # Provider I/O happens before the ownership-merge transaction.
    cipher=token_cipher()
    response=httpx.post(ISSUER+'/auth/token',data={'client_id':settings().apple_bundle_id,
        'client_secret':apple_client_secret(),'code':code,'grant_type':'authorization_code'},timeout=12)
    if response.status_code!=200: raise HTTPException(401,'Apple authorization expired. Please sign in again.')
    data=response.json()
    claims=verify_apple(data.get('id_token',''),nonce)
    if claims['sub']!=sub or not data.get('refresh_token'): raise HTTPException(401,'Apple identity changed during sign-in.')
    return cipher.encrypt(data['refresh_token'].encode()).decode()

def issue_session(conn,user,device,family=None):
    access=secrets.token_hex(32); refresh=secrets.token_hex(32)
    now=datetime.now(timezone.utc)
    conn.execute('''insert into account_sessions(family_id,user_id,device_id,access_hash,refresh_hash,access_expires_at,refresh_expires_at)
        values(%s,%s,%s,%s,%s,%s,%s)''',(family or uuid4(),user,device,digest(access),digest(refresh),now+timedelta(hours=1),now+timedelta(days=30)))
    return {'access_token':access,'refresh_token':refresh,'expires_at':(now+timedelta(hours=1)).isoformat(),'token_type':'Bearer'}

def authenticate(token,allow_device_linked=False):
    if len(token)!=64: raise HTTPException(401,'Please sign in again.')
    with connect() as conn:
        session=conn.execute('''select s.user_id,s.device_id,s.family_id from account_sessions s join users u on u.id=s.user_id
            join devices d on d.id=s.device_id where s.access_hash=%s and s.access_expires_at>now()
            and s.revoked_at is null and s.consumed_at is null and u.deleted_at is null and d.revoked_at is null''',(digest(token),)).fetchone()
        if session:return session
        device=conn.execute('''select d.user_id,d.id as device_id from devices d join users u on u.id=d.user_id
            where d.token_hash=%s and d.revoked_at is null and u.deleted_at is null and (u.apple_user_id is null or %s)''',(digest(token),allow_device_linked)).fetchone()
        if device:return {**device,'family_id':None}
    raise HTTPException(401,'Your session expired. Please sign in again.')

def rotate(refresh):
    result=None
    with connect() as conn:
        row=conn.execute('select * from account_sessions where refresh_hash=%s',(digest(refresh),)).fetchone()
        if row:
            # Serialize the entire family, so concurrent refresh/reuse cannot leave a live child.
            conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('session:'+str(row['family_id']),))
            row=conn.execute('select * from account_sessions where id=%s for update',(row['id'],)).fetchone()
            active=conn.execute('select u.id from users u join devices d on d.user_id=u.id where u.id=%s and d.id=%s and u.deleted_at is null and d.revoked_at is null',(row['user_id'],row['device_id'])).fetchone()
            if row['consumed_at'] or row['revoked_at'] or row['refresh_expires_at']<=datetime.now(timezone.utc) or not active:
                conn.execute('update account_sessions set revoked_at=now() where family_id=%s',(row['family_id'],))
            else:
                conn.execute('update account_sessions set consumed_at=now() where id=%s',(row['id'],))
                result=issue_session(conn,row['user_id'],row['device_id'],row['family_id'])
    # Raise AFTER the revocation commits, never roll it back with the response.
    if not result:raise HTTPException(401,'Please sign in again. This session is no longer valid.')
    return result
