from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from reel_urls import canonical_reel_url, content_identity
from pipeline import StageError, html_meta_content, public_metadata_info, normalize_structured, stage_ingest


class SourceValidationTests(unittest.TestCase):
    def test_unavailable_youtube_placeholder_is_not_content(self):
        with tempfile.TemporaryDirectory() as directory, patch('pipeline.ytdlp_extract', return_value={'id':'zzzzzzzzzzz', 'title':'youtube video #zzzzzzzzzzz', 'formats':[]}):
            with self.assertRaisesRegex(StageError, 'unavailable'):
                stage_ingest('https://www.youtube.com/watch?v=zzzzzzzzzzz', Path(directory))

    def test_supported_sources_are_canonical(self):
        cases = {
            'http://instagram.com/reels/AbC123/?igsh=tracking': 'https://www.instagram.com/reel/AbC123/',
            'https://www.tiktok.com/@creator/video/123?foo=1': 'https://www.tiktok.com/@creator/video/123',
            'https://youtu.be/abcdefghijk?t=10': 'https://www.youtube.com/watch?v=abcdefghijk',
            'https://m.youtube.com/shorts/abcdefghijk': 'https://www.youtube.com/watch?v=abcdefghijk',
            'https://vm.tiktok.com/ABc123/': 'https://vm.tiktok.com/ABc123/',
        }
        for source, expected in cases.items():
            with self.subTest(source=source): self.assertEqual(canonical_reel_url(source), expected)

    def test_unsupported_and_impersonating_hosts_are_rejected(self):
        for url in ['https://instagram.com/profile', 'https://youtube.com/results?search_query=cats', 'https://tiktok.com/', 'https://instagram.com.evil.test/reel/abc/', 'https://evil.instagram.com/reel/abc/', 'https://u:p@instagram.com/reel/abc/', 'https://instagram.com:8080/reel/abc/', 'file:///reel/abc', 'https://youtu.be/abc', 'https://instagram.com/reel/abc/ extra']:
            with self.subTest(url=url), self.assertRaises(ValueError): canonical_reel_url(url)

    def test_content_identity_is_platform_scoped_and_tracking_independent(self):
        self.assertNotEqual(content_identity('https://instagram.com/reel/123/'), content_identity('https://tiktok.com/@x/video/123'))
        self.assertEqual(content_identity('https://instagram.com/reel/123/?igsh=a'), content_identity('https://www.instagram.com/reel/123/'))

    def test_metadata_preserves_apostrophes_and_attribute_order(self):
        self.assertEqual(html_meta_content('<meta content="Chef\'s pasta &amp; sauce" property="og:description">', 'og:description'), "Chef's pasta & sauce")

    def test_login_and_removed_page_never_create_content(self):
        for final, html in [('https://instagram.com/accounts/login/', '<title>Login</title>'), ('https://www.instagram.com/reel/abc/', '<title>Instagram</title>'), ('https://www.instagram.com/reel/abc/', '<meta property="og:title" content="Page not found"><meta property="og:image" content="https://cdn.example/logo.png">')]:
            with self.subTest(final=final), tempfile.TemporaryDirectory() as directory, patch('pipeline.public_page_text', return_value=(final, html)):
                with self.assertRaises(StageError): public_metadata_info('https://www.instagram.com/reel/abc/', Path(directory))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'requires explicit disposable TEST_DATABASE_URL')
class DatabaseJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from urllib.parse import urlsplit
        target = os.environ['TEST_DATABASE_URL']
        parsed = urlsplit(target)
        if parsed.hostname not in {'127.0.0.1', 'localhost'} or 'audit' not in parsed.path:
            raise RuntimeError('Integration tests require a loopback audit database')
        os.environ.update(DATABASE_URL=target, API_KEY='local-audit-key', TEST_GROUP_ID='00000000-0000-0000-0000-000000000002', REELBOT_API_DRAIN_JOBS='false', ANTHROPIC_API_KEY='', GOOGLE_MAPS_API_KEY='', IG_COOKIES_PATH='')
        import api.main as api
        import db
        import worker
        from fastapi.testclient import TestClient
        cls.api, cls.db, cls.worker = api, db, worker
        api.load_settings.cache_clear()
        cls.client = TestClient(api.app)

    def setUp(self):
        # Three independently issued credentials; duplicate names on purpose.
        self.a, self.b, self.c = [self.device('Same Name') for _ in range(3)]
        self.group = self.post('/groups', self.a, {'name':'Audit private group'}).json()

    def device(self, name):
        response = self.client.post('/devices', headers={'x-api-key':'local-audit-key'}, json={'user_name':name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def headers(self, actor):
        return {'x-api-key':'local-audit-key', 'Authorization':'Bearer '+actor['token']}

    def post(self, path, actor, body):
        return self.client.post(path, headers=self.headers(actor), json={'device_id':actor['device_id'], 'user_name':'Same Name', **body})

    def items(self, actor, group=None):
        return self.client.get('/items', headers=self.headers(actor), params={'group_id':group or self.group['id'], 'device_id':actor['device_id']})

    def share(self, actor, url='https://www.instagram.com/reel/Audit123/', group=None, request_id=None):
        return self.post('/share', actor, {'url':url, 'group_id':group or self.group['id'], 'request_id':request_id})

    def process(self, job_id, *, empty=False, fail=False):
        # Only external extraction and embeddings are substituted. API, SQL,
        # queue claims, worker writes, transactions and reads are real.
        with self.db.connect() as conn:
            job = conn.execute("update jobs set status='processing' where id=%s returning *", (job_id,)).fetchone()
            conn.commit()
            result = {'has_content':not empty, 'title':'Audit pasta recipe', 'content_type':'recipe', 'reel_id':'Audit123', 'summary':'Cook pasta for ten minutes.', 'tags':['pasta'], 'source_url':job['payload']}
            with patch.object(self.worker, 'process_reel', side_effect=StageError('ingest','unavailable') if fail else None, return_value=result), patch.object(self.worker, 'embed_document', return_value=[1.0]+[0.0]*383):
                try:
                    self.worker.handle_job(conn, job)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    self.db.mark_job_error(conn, job_id, self.worker.job_error_reply(exc))
                    conn.commit()

    def test_private_group_denies_unrelated_spoof_and_missing_session(self):
        self.assertEqual(self.items(self.c).status_code, 403)
        forged = self.client.get('/items', headers=self.headers(self.c), params={'group_id':self.group['id'], 'device_id':self.a['device_id']})
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(self.client.get('/items', headers={'x-api-key':'local-audit-key'}).status_code, 401)
        self.assertEqual(self.client.get('/groups', headers=self.headers(self.c), params={'device_id':self.a['device_id']}).status_code, 403)
        self.assertEqual(self.post('/query', self.c, {'text':'Show saved reels', 'group_id':self.group['id']}).status_code, 403)
        self.assertEqual(self.share(self.c).status_code, 403)

    def test_save_recipient_persistence_counts_copy_and_delete(self):
        joined = self.post('/groups/join', self.b, {'code':self.group['join_code']})
        self.assertEqual(joined.status_code, 200, joined.text)
        first = self.share(self.a, request_id='audit-'+str(uuid4()))
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(self.items(self.b).json()[0]['status'], 'processing')
        self.process(first.json()['job_id'])
        saved = self.items(self.b).json()
        self.assertEqual(len(saved), 1)
        item = saved[0]
        self.assertEqual(item['status'], 'saved')
        self.assertEqual(item['list_name'], 'Recipes')
        self.assertEqual(item['save_count'], 1)
        second = self.share(self.b)
        self.process(second.json()['job_id'])
        self.assertEqual(self.items(self.a).json()[0]['save_count'], 2)
        with self.db.connect() as conn:
            members = conn.execute('select count(distinct member_id) as n from item_saves where item_id=%s', (item['id'],)).fetchone()['n']
            self.assertEqual(members,2)
        target = self.post('/groups', self.a, {'name':'Audit copy destination'}).json()
        body = {'source_group_id':self.group['id'],'item_ids':[item['id'],item['id']]}
        copied = self.post('/groups/'+target['id']+'/items',self.a,body)
        self.assertEqual(copied.status_code,200,copied.text)
        self.assertEqual(copied.json(), {'added':1,'already_present':0})
        self.assertEqual(self.post('/groups/'+target['id']+'/items',self.a,body).json(), {'added':0,'already_present':1})
        self.assertEqual(self.client.delete('/items/'+item['id'],headers=self.headers(self.c),params={'group_id':self.group['id']}).status_code,403)
        response = self.client.delete('/items/'+item['id'],headers=self.headers(self.a),params={'group_id':self.group['id']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.items(self.b).json(),[])
        self.assertEqual(len(self.items(self.a,target['id']).json()),1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from item_saves where item_id=%s',(item['id'],)).fetchone()['n'],0)
        self.assertEqual(self.items(self.c,target['id']).status_code,403)

    def test_invalid_unavailable_and_empty_imports_are_recoverable(self):
        self.assertEqual(self.share(self.a,'https://instagram.com/account').status_code,400)
        for suffix, empty, fail in [('Empty',True,False),('Gone',False,True)]:
            response=self.share(self.a,'https://instagram.com/reel/'+suffix+'/')
            self.process(response.json()['job_id'],empty=empty,fail=fail)
            result=self.client.get('/jobs/'+response.json()['job_id'],headers=self.headers(self.a))
            self.assertEqual(result.json()['status'],'error')
            self.assertTrue(result.json()['message'])
        self.assertTrue(all(item['status']=='error' for item in self.items(self.a).json()))

    def test_concurrent_share_debounce_and_job_privacy(self):
        request_id='audit-'+str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.share(self.a,request_id=request_id),range(4)))
        self.assertTrue(all(response.status_code==202 for response in results),[r.text for r in results])
        ids={r.json()['job_id'] for r in results}
        self.assertEqual(len(ids),1)
        job_id=ids.pop()
        self.assertEqual(self.client.get('/jobs/'+job_id,headers=self.headers(self.c)).status_code,404)
        self.assertEqual(self.share(self.a,'https://instagram.com/reel/Different/',request_id=request_id).status_code,409)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from jobs where group_id=%s',(self.group['id'],)).fetchone()['n'],1)

    def test_missing_copy_is_atomic(self):
        target=self.post('/groups',self.a,{'name':'Target'}).json()
        response=self.post('/groups/'+target['id']+'/items',self.a,{'source_group_id':self.group['id'],'item_ids':[str(uuid4())]})
        self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(self.items(self.a,target['id']).json(),[])

    def test_join_code_failure_and_rate_limit(self):
        for _ in range(10):
            response=self.post('/groups/join',self.c,{'code':'ZZZZZZ'})
            self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(self.post('/groups/join',self.c,{'code':self.group['join_code']}).status_code,429)

    def test_queue_claim_and_recovery_and_transaction_rollback(self):
        response=self.share(self.a)
        job_id=response.json()['job_id']
        with self.db.connect() as conn:
            conn.execute("update jobs set status='processing', updated_at=now()-interval '1 hour' where id=%s",(job_id,));conn.commit()
            job=self.db.claim_next_job(conn)
            self.assertIsNotNone(job)
        # Force saver failure after item upsert: no partial item may persist.
        with patch.object(self.worker,'add_item_save',side_effect=RuntimeError('test failure')):
            self.process(job_id)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from items where group_id=%s',(self.group['id'],)).fetchone()['n'],0)

    def test_nudges_ignore_app_groups_and_concurrent_cooldown(self):
        with self.db.connect() as conn:
            group_ids={str(g['id']) for g in self.db.list_groups(conn)}
            self.assertNotIn(self.group['id'],group_ids)
            wa=conn.execute("insert into groups(wa_chat_id,name) values(%s,'Audit WhatsApp') returning id",(str(uuid4())+'@g.us',)).fetchone()['id']
        def enqueue(_):
            with self.db.connect() as conn:
                return self.db.enqueue_nudge(conn,group_id=str(wa),chat_id='audit@g.us',cluster_key='audit',body='Sandbox message: never delivered.',cooldown_days=3,cluster_cooldown_days=14)
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(enqueue,range(2)))
        self.assertEqual(sorted(results),[False,True])

    def test_query_job_timeout_polling_retry_and_revoked_membership(self):
        original_wait = self.api.wait_for_job_reply
        request_id = 'audit-query-' + str(uuid4())
        body = {'text':'hello','group_id':self.group['id'],'request_id':request_id}
        with patch.object(self.api, 'wait_for_job_reply', side_effect=lambda job_id: original_wait(job_id, timeout_seconds=0.01)):
            response = self.post('/query', self.a, body)
        self.assertEqual(response.json()['status'], 'processing')
        self.assertEqual(response.json()['answer'], '')
        job_id = response.json()['job_id']
        with self.db.connect() as conn:
            job = conn.execute("select * from jobs where id=%s",(job_id,)).fetchone()
            self.worker.handle_query(conn,job)
            conn.commit()
        result = self.client.get('/jobs/'+job_id,headers=self.headers(self.a)).json()
        self.assertEqual(result['status'],'done')
        self.assertIn('Hey!',result['answer'])
        repeated=self.post('/query',self.a,body)
        self.assertEqual(repeated.status_code,200,repeated.text)
        with self.db.connect() as conn:
            self.db.mark_job_error(conn,job_id,'The question could not be answered. Retry.')
        failed=self.post('/query',self.a,body)
        self.assertEqual(failed.status_code,503)
        self.assertEqual(failed.headers.get('x-reelbot-job-terminal'),'true')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from jobs where sender_id=%s and request_id=%s',(self.a['device_id'],request_id)).fetchone()['n'],1)
            conn.execute('delete from members where group_id=%s and wa_user_id=%s',(self.group['id'],self.a['device_id']))
        self.assertEqual(self.items(self.a).status_code,403)
        self.assertEqual(self.client.get('/jobs/'+job_id,headers=self.headers(self.a)).status_code,403)

    def test_direct_database_client_roles_cannot_read_private_tables(self):
        import psycopg
        for role in ['anon','authenticated']:
            for table in ['app_devices','app_join_attempts','groups','members','items','item_saves','jobs','events','outbound_messages','nudges']:
                with self.subTest(role=role,table=table), self.db.connect() as conn:
                    conn.execute('set local role '+role)
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        conn.execute('select * from '+table)
                    conn.rollback()

    def test_concurrent_copy_with_null_place_id_is_idempotent(self):
        with self.db.connect() as conn:
            item_id=str(conn.execute("insert into items(group_id,source_url,place_name) values(%s,'https://instagram.com/reel/NullAudit/','Null identity') returning id",(self.group['id'],)).fetchone()['id'])
        target=self.post('/groups',self.a,{'name':'Concurrent copy'}).json()
        body={'source_group_id':self.group['id'],'item_ids':[item_id]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda _:self.post('/groups/'+target['id']+'/items',self.a,body),range(2)))
        self.assertTrue(all(reply.status_code==200 for reply in replies),[r.text for r in replies])
        self.assertEqual(sorted(reply.json()['added'] for reply in replies),[0,1])
        self.assertEqual(len(self.items(self.a,target['id']).json()),1)


if __name__=='__main__': unittest.main()
