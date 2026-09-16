"""Real Postgres / two independent device sessions; no live Apple or R2 calls."""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import test_personal as personal
from worker.db import connect
from api import accounts
from psycopg.types.json import Jsonb

class AccountsSyncTests(unittest.TestCase):
    setUpClass=classmethod(personal.PersonalTests.setUpClass.__func__)
    setUp=personal.PersonalTests.setUp
    register=personal.PersonalTests.register
    save=personal.PersonalTests.save
    venue=personal.PersonalTests.venue

    def login(self,device,sub='apple-one',**fields):
        nonce=self.client.post('/auth/apple/challenge',headers=device['headers'],json={'nonce':uuid4().hex+uuid4().hex}).json()['nonce']
        claims={'sub':sub,'nonce':nonce,'email':'private@privaterelay.appleid.com','email_verified':True}
        claims.update(fields.pop('claims',{}))
        with patch('api.accounts.verify_apple',return_value=claims),patch('api.accounts.exchange_apple_code',return_value='encrypted-provider-token'):
            response=self.client.post('/auth/apple',headers=device['headers'],json={'identity_token':'x'*30,'authorization_code':'one-time-code','nonce':nonce,**fields})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        return {**result,'headers':{'Authorization':'Bearer '+result['access_token']},'user':result['user_id']}

    def sync(self,device,since='0',limit=200):
        return self.client.get('/sync',headers=device['headers'],params={'since':since,'limit':limit})

    def mutation(self,device,path,method='PATCH',body=None,stamp=None,identifier=None):
        return self.client.post('/sync',headers=device['headers'],json={'mutations':[{'id':identifier or str(uuid4()),'path':path,'method':method,'body':body,'client_timestamp':stamp or datetime.now(timezone.utc).isoformat()}]})

    def test_a_claim_keeps_ids_notes_folders_and_profile_once(self):
        entry,_=self.venue(self.save(self.a),self.a)
        self.client.patch('/items/'+str(entry['id']),headers=self.a['headers'],json={'note':'keep me','venue_kind':'cafe'})
        signed=self.login(self.a,full_name={'givenName':'Krish'})
        self.assertEqual(signed['merge_case'],'A');self.assertEqual(signed['user'],self.a['user'])
        row=self.client.get('/items',headers=signed['headers']).json()['items'][0]
        self.assertEqual(row['id'],str(entry['id']));self.assertEqual(row['note'],'keep me');self.assertEqual(row['venue_kind_source'],'user')
        again=self.login(self.a,claims={'email':None})
        page=self.sync(again).json()
        self.assertEqual(page['account']['email'],'private@privaterelay.appleid.com')
        self.assertEqual(page['account']['full_name'],{'givenName':'Krish'})
        self.assertEqual(self.client.get('/items',headers=self.a['headers']).status_code,401)

    def test_b_empty_device_joins_existing_full_library(self):
        entry,_=self.venue(self.save(self.a),self.a)
        first=self.login(self.a); second=self.login(self.b)
        self.assertEqual(second['merge_case'],'B');self.assertEqual(second['user'],first['user'])
        self.assertEqual(second['added_saves'],0)
        self.assertEqual(self.client.get('/items',headers=second['headers']).json()['items'][0]['id'],str(entry['id']))

    def test_c_overlaps_and_new_saves_union_notes_overrides_folders(self):
        target,_=self.venue(self.save(self.a,'overlap'),self.a)
        source,_=self.venue(self.save(self.b,'overlap'),self.b)
        extra,_=self.venue(self.save(self.b,'new'),self.b,name='Second Bakery')
        self.client.patch('/items/'+str(source['id']),headers=self.b['headers'],json={'note':'anonymous note','venue_kind':'cafe'})
        for device,entry in ((self.a,target),(self.b,source),(self.b,extra)):
            folder=self.client.post('/folders',headers=device['headers'],json={'name':'Weekend'}).json()
            if 'id' not in folder:
                with connect() as conn:folder=conn.execute("select * from folders where user_id=%s and name='Weekend'",(device['user'],)).fetchone()
            self.client.post('/folders/assign',headers=device['headers'],json={'item_ids':[str(entry['id'])],'destination_id':str(folder['id'])})
        first=self.login(self.a); merged=self.login(self.b)
        self.assertEqual(merged['merge_case'],'C');self.assertEqual(merged['added_saves'],1)
        rows=self.client.get('/items',headers=merged['headers']).json()['items']
        self.assertEqual(len(rows),2)
        kept=next(row for row in rows if row['id']==str(target['id']))
        self.assertEqual(kept['note'],'anonymous note');self.assertEqual(kept['venue_kind'],'cafe')
        self.assertTrue(all(any(f['name']=='Weekend' for f in row['folders']) for row in rows))
        self.assertEqual(first['user'],merged['user'])

    def test_c_mid_merge_failure_rolls_back_everything(self):
        self.venue(self.save(self.a,'account'),self.a)
        self.venue(self.save(self.b,'anon'),self.b)
        first=self.login(self.a)
        from api.main import merge_library
        with connect() as conn: before=conn.execute('select id,user_id,note from entries order by id').fetchall()
        with self.assertRaisesRegex(RuntimeError,'injected'):
            with connect() as conn,patch('api.main.file_entry',side_effect=RuntimeError('injected')):merge_library(conn,self.b['user'],first['user'])
        with connect() as conn: self.assertEqual(before,conn.execute('select id,user_id,note from entries order by id').fetchall())

    def test_c_deduplicates_canonical_identity_even_when_original_urls_differ(self):
        account=self.save(self.a,'canonical');anonymous=self.save(self.b,'original-alias')
        self.venue(account,self.a);self.venue(anonymous,self.b)
        with connect() as conn:
            conn.execute('update saves set canonical_url=%s,platform_video_id=%s where id=%s',(account['canonical_url'],'canonical',anonymous['id']))
        self.login(self.a);merged=self.login(self.b)
        self.assertEqual(merged['added_saves'],0)
        self.assertEqual(len(self.client.get('/items',headers=merged['headers']).json()['items']),1)
        with connect() as conn:
            from worker.db import enqueue
            repeated=enqueue(conn,merged['user'],anonymous['source_url'])
            self.assertEqual(str(repeated['id']),account['id'])

    def test_real_signature_issuer_audience_expiry_nonce_and_forgery(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        valid={'iss':accounts.ISSUER,'aud':'com.krishwaghani.reelbot','iat':int(time.time()),'exp':int(time.time())+60,'sub':'verified','nonce':'n'*64}
        with patch.object(accounts.apple_keys,'get',return_value=key.public_key()):
            self.assertEqual(accounts.verify_apple(jwt.encode(valid,key,algorithm='RS256'),valid['nonce'])['sub'],'verified')
            for field,value in [('iss','https://evil.test'),('aud','another.app'),('exp',1),('nonce','wrong')]:
                with self.assertRaises(Exception):accounts.verify_apple(jwt.encode({**valid,field:value},key,algorithm='RS256'),valid['nonce'])
            other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            with self.assertRaises(Exception):accounts.verify_apple(jwt.encode(valid,other,algorithm='RS256'),valid['nonce'])

    def test_jwks_honors_cache_control(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk['kid']='test'
        response=SimpleNamespace(headers={'cache-control':'max-age=600'},json=lambda:{'keys':[jwk]},raise_for_status=lambda:None)
        token=jwt.encode({'sub':'x'},key,algorithm='RS256',headers={'kid':'test'})
        cache=accounts.AppleKeys()
        with patch('api.accounts.httpx.get',return_value=response) as fetch:
            cache.get(token);cache.get(token);self.assertEqual(fetch.call_count,1)
            cache.expires=0;cache.get(token);self.assertEqual(fetch.call_count,2)

    def test_refresh_rotation_reuse_revokes_family(self):
        signed=self.login(self.a)
        with connect() as conn:conn.execute("update account_sessions set access_expires_at=now()-interval '1 second'")
        self.assertEqual(self.client.get('/items',headers=signed['headers']).status_code,401)
        fresh=self.client.post('/auth/refresh',json={'refresh_token':signed['refresh_token']})
        self.assertEqual(fresh.status_code,200);pair=fresh.json()
        self.assertNotEqual(pair['refresh_token'],signed['refresh_token'])
        self.assertEqual(self.client.get('/items',headers={'Authorization':'Bearer '+pair['access_token']}).status_code,200)
        self.assertEqual(self.client.post('/auth/refresh',json={'refresh_token':signed['refresh_token']}).status_code,401)
        self.assertEqual(self.client.get('/items',headers={'Authorization':'Bearer '+pair['access_token']}).status_code,401)

    def test_two_devices_save_edit_delete_offline_lww_and_set_union(self):
        a=self.login(self.a); b=self.login(self.b)
        before=self.sync(b).json()['cursor']
        entry,_=self.venue(self.save(a,'sync'),a)
        page=self.sync(b,before).json();self.assertTrue(any(c['entity']=='entries' and c['payload']['id']==str(entry['id']) for c in page['changes']))
        path='/items/'+str(entry['id']); old=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        self.assertEqual(self.mutation(a,path,body={'note':'newer'}).status_code,200)
        stale=self.mutation(b,path,body={'note':'offline older'},stamp=old).json()
        self.assertEqual(stale['results'][0]['status'],'superseded')
        for device,name in ((a,'A folder'),(b,'B folder')):
            folder=self.client.post('/folders',headers=device['headers'],json={'name':name}).json()
            self.assertEqual(self.mutation(device,'/folders/assign','POST',{'item_ids':[str(entry['id'])],'destination_id':folder['id']}).status_code,200)
        row=self.client.get('/items',headers=b['headers']).json()['items'][0]
        self.assertEqual(row['note'],'newer');self.assertTrue({'A folder','B folder'}<={f['name'] for f in row['folders']})
        self.assertEqual(self.mutation(a,path,'DELETE',stamp=old).status_code,200)
        self.assertEqual(self.mutation(b,path,body={'note':'cannot resurrect'}).json()['results'][0]['status'],'deleted')
        tombstones=self.sync(b,page['cursor']).json()['changes']
        self.assertTrue(any(c['entity']=='entries' and c['payload'].get('deleted_at') for c in tombstones))
        self.assertEqual(self.client.get('/items',headers=b['headers']).json()['items'],[])

    def test_batch_atomicity_idempotence_and_owner_cursor(self):
        row,_=self.venue(self.save(self.a),self.a); path='/items/'+str(row['id']); identifier=str(uuid4())
        first=self.mutation(self.a,path,body={'note':'once'},identifier=identifier)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),self.mutation(self.a,path,body={'note':'ignored retry'},identifier=identifier).json())
        stamp=datetime.now(timezone.utc).isoformat()
        response=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[
            {'id':str(uuid4()),'path':path,'method':'PATCH','body':{'note':'should rollback'},'client_timestamp':stamp},
            {'id':str(uuid4()),'path':'/unsupported','method':'POST','client_timestamp':stamp}]})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.get('/items',headers=self.a['headers']).json()['items'][0]['note'],'once')
        self.assertEqual(self.sync(self.b,self.sync(self.a).json()['cursor']).status_code,400)

    def test_logout_revokes_refresh_without_touching_other_device(self):
        a=self.login(self.a);b=self.login(self.b)
        self.assertEqual(self.client.post('/auth/logout',json={'refresh_token':a['refresh_token']}).status_code,200)
        self.assertEqual(self.client.post('/auth/refresh',json={'refresh_token':a['refresh_token']}).status_code,401)
        self.assertEqual(self.client.get('/items',headers=b['headers']).status_code,200)

    def test_delete_immediate_then_provider_cleanup_and_grace_purge(self):
        entry,_=self.venue(self.save(self.a),self.a)
        signed=self.login(self.a)
        exported=self.client.get('/export',headers=signed['headers']).json();self.assertEqual(len(exported['entries']),1)
        self.assertEqual(self.client.delete('/account',headers=signed['headers']).status_code,200)
        self.assertEqual(self.client.get('/items',headers=signed['headers']).status_code,401)
        with connect() as conn:self.assertIsNotNone(conn.execute('select deleted_at from entries where id=%s',(entry['id'],)).fetchone()['deleted_at'])
        from api.account_deletion import maintenance
        cipher=SimpleNamespace(decrypt=lambda value:b'provider-refresh')
        with patch('api.account_deletion.token_cipher',return_value=cipher),patch('api.account_deletion.apple_client_secret',return_value='client-secret'),patch('api.account_deletion.httpx.post') as revoke,patch('api.account_deletion.storage.delete') as objects:
            maintenance();self.assertEqual(revoke.call_count,1);self.assertGreaterEqual(objects.call_count,2)
            with connect() as conn:
                self.assertIsNotNone(conn.execute('select id from users where id=%s',(signed['user'],)).fetchone())
                conn.execute("update account_deletions set purge_after=now()-interval '1 day',retry_at=now()")
            maintenance()
            with connect() as conn:self.assertIsNone(conn.execute('select id from users where id=%s',(signed['user'],)).fetchone())

    def test_200_entry_fresh_device_paginated_benchmark(self):
        # Populate immutable entries without provider calls, then measure real API paging.
        with connect() as conn:
            for i in range(200):
                saved=conn.execute("insert into saves(user_id,source_url,platform,source_hash,status) values(%s,%s,'instagram',%s,'resolved') returning id",(self.a['user'],f'https://instagram.com/reel/bench{i}/',str(uuid4()))).fetchone()
                conn.execute("insert into entries(user_id,save_id,content_type,title,attributes,confidence,needs_review,candidate_key) values(%s,%s,'other',%s,%s,1,false,%s)",(self.a['user'],saved['id'],f'Entry {i}',Jsonb({'topic':'benchmark'}),str(i)))
        self.login(self.a); b=self.login(self.b)
        started=time.perf_counter();cursor='0';ids=set();pages=0
        while True:
            response=self.sync(b,cursor);self.assertEqual(response.status_code,200,response.text);page=response.json();pages+=1
            ids.update(c['payload']['id'] for c in page['changes'] if c['entity']=='entries' and not c['payload'].get('deleted_at'))
            cursor=page['cursor']
            if not page['has_more']:break
        elapsed=time.perf_counter()-started
        print(f'\nSYNC_200_BENCHMARK entries={len(ids)} pages={pages} seconds={elapsed:.3f}',flush=True)
        self.assertEqual(len(ids),200);self.assertLess(elapsed,10)

    def test_deleted_parent_blocks_worker_resurrection_and_old_cursor_requests_full_reset(self):
        saved=self.save(self.a);entry,_=self.venue(saved,self.a)
        old=self.sync(self.a).json()['cursor']
        self.assertEqual(self.client.delete('/saves/'+saved['id'],headers=self.a['headers']).status_code,200)
        from worker.db import store_candidate
        with connect() as conn:
            candidate={'content_type':'other','title':'Late worker','summary':'','attributes':{'topic':'late'},'confidence':1,'evidence':'test'}
            self.assertIsNone(store_candidate(conn,{'id':saved['id'],'user_id':self.a['user']},candidate,None,1))
            self.assertEqual(conn.execute('select count(*) as n from entries where user_id=%s and deleted_at is null',(self.a['user'],)).fetchone()['n'],0)
            conn.execute('update users set sync_floor=sync_version where id=%s',(self.a['user'],))
        self.assertEqual(self.sync(self.a,old).status_code,409)
        self.assertEqual(self.sync(self.a).status_code,200)

    def test_embedding_and_no_op_updates_do_not_advance_sync_or_user_edit_clock(self):
        entry,_=self.venue(self.save(self.a),self.a)
        with connect() as conn:
            before=conn.execute('select sync_version from users where id=%s',(self.a['user'],)).fetchone()
            stamp=conn.execute('select updated_at from entries where id=%s',(entry['id'],)).fetchone()
            conn.execute('update entries set embedding=null,updated_at=now() where id=%s',(entry['id'],))
            self.assertEqual(before,conn.execute('select sync_version from users where id=%s',(self.a['user'],)).fetchone())
            self.assertEqual(stamp,conn.execute('select updated_at from entries where id=%s',(entry['id'],)).fetchone())

    def test_deleted_account_acknowledgment_is_idempotent_but_does_not_restore_access(self):
        a=self.login(self.a)
        self.assertEqual(self.client.delete('/account',headers=a['headers']).status_code,200)
        self.assertEqual(self.client.delete('/account',headers=a['headers']).status_code,200)
        self.assertEqual(self.client.get('/export',headers=a['headers']).status_code,401)
        self.assertEqual(self.client.get('/items',headers=self.b['headers']).status_code,200)

    def test_old_membership_add_cannot_undo_a_newer_move(self):
        entry,_=self.venue(self.save(self.a),self.a)
        folders=[self.client.post('/folders',headers=self.a['headers'],json={'name':name}).json()['id'] for name in ('First','Second')]
        old=datetime.now(timezone.utc)
        self.mutation(self.a,'/folders/assign','POST',{'item_ids':[str(entry['id'])],'destination_id':folders[0]},old.isoformat())
        self.mutation(self.a,'/folders/assign','POST',{'item_ids':[str(entry['id'])],'destination_id':folders[1],'source_id':folders[0],'move':True})
        self.mutation(self.a,'/folders/assign','POST',{'item_ids':[str(entry['id'])],'destination_id':folders[0]},old.isoformat())
        with connect() as conn:
            row=conn.execute('select deleted_at from folder_items where folder_id=%s and entry_id=%s',(folders[0],entry['id'])).fetchone()
            self.assertIsNotNone(row['deleted_at'])
