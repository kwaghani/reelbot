"""Real disposable Postgres integration tests for isolation, recovery, cache and search."""
import os,sys,unittest,hashlib,subprocess,tempfile
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TEST_URL=os.getenv('TEST_DATABASE_URL','')
if not TEST_URL or urlsplit(TEST_URL).hostname not in {'localhost','127.0.0.1'} or 'test' not in urlsplit(TEST_URL).path:
    raise RuntimeError('TEST_DATABASE_URL must name a disposable loopback test database')
os.environ['DATABASE_URL']=TEST_URL
from fastapi.testclient import TestClient
from api.main import app
from worker.db import connect,claim,store_candidate
from worker.pipeline import new_metrics,CANDIDATES
from worker.places import resolve,FIELD_MASK

class PersonalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect() as conn: conn.execute((ROOT/'db/schema.sql').read_text())
        cls.client=TestClient(app)
    def setUp(self):
        with connect() as conn:
            conn.execute('truncate events,jobs,folder_items,folders,user_places,saves,devices,users,places cascade')
        self.a=self.register();self.b=self.register()
    def register(self):
        token=uuid4().hex+uuid4().hex
        response=self.client.post('/devices',json={'device_id':str(uuid4()),'token':token})
        self.assertEqual(response.status_code,200)
        return {'headers':{'Authorization':'Bearer '+token},'user':response.json()['user_id']}
    def save(self,identity,code='A'):
        response=self.client.post('/share',headers=identity['headers'],json={'url':f'https://instagram.com/reel/{code}/?igsh=tracking'})
        self.assertEqual(response.status_code,200,response.text);return response.json()
    def venue(self,save,identity,name='Tartine Bakery',city='San Francisco',search=None):
        candidate={'name':name,'city_hint':city,'country_hint':'USA','category_guess':'bakery','confidence':.95,'evidence':'caption names the venue'}
        metrics=new_metrics()
        def provider(c,m):
            m['places_calls']+=1;m.setdefault('places_field_masks',[]).append(FIELD_MASK)
            return [{'id':'verified-'+name,'displayName':{'text':name},'formattedAddress':'600 Guerrero St, '+city+', CA, USA','location':{'latitude':37.761,'longitude':-122.424},'primaryType':'bakery'}]
        with connect() as conn:
            place,confidence,reason=resolve(conn,candidate,metrics,search=search or provider)
            row=store_candidate(conn,{'id':save['id'],'user_id':identity['user']},candidate,place,confidence,reason)
        return row,metrics
    def test_url_idempotency_and_personal_isolation(self):
        first=self.save(self.a);second=self.save(self.a)
        other=self.save(self.b)
        self.assertEqual(first['id'],second['id']);self.assertNotEqual(first['id'],other['id'])
        self.venue(first,self.a)
        self.assertEqual(len(self.client.get('/items',headers=self.b['headers']).json()['items']),0)
        self.assertEqual(self.client.get('/jobs/'+first['job_id'],headers=self.b['headers']).status_code,404)
        self.assertEqual(self.client.get('/items').status_code,401)
    def test_global_cache_five_sources_one_call_and_autofiling(self):
        calls=hits=0
        for n in range(5):
            identity=self.a if n%2 else self.b
            row,cost=self.venue(self.save(identity,str(n)),identity)
            calls+=cost['places_calls'];hits+=cost['cache_hits']
            with connect() as conn:
                folders=conn.execute('select kind from folders f join folder_items fi on f.id=fi.folder_id where fi.user_place_id=%s',(row['id'],)).fetchall()
                self.assertEqual({f['kind'] for f in folders},{'auto_city','auto_category'})
        self.assertEqual((calls,hits),(1,4))
        with connect() as conn:
            self.assertEqual(conn.execute('select count(*) as n from places').fetchone()['n'],1)
            self.assertEqual(conn.execute("select count(*) as n from places where formatted_address='' or lat not between -90 and 90 or lng not between -180 and 180").fetchone()['n'],0)
    def test_ambiguous_candidate_retained_in_review(self):
        saved=self.save(self.a)
        def ambiguous(c,m):
            return [{'id':str(n),'displayName':{'text':'Tartine Bakery'},'formattedAddress':'San Francisco USA','location':{'latitude':37,'longitude':-122}} for n in range(2)]
        row,_=self.venue(saved,self.a,search=ambiguous)
        self.assertTrue(row['needs_review']);self.assertIsNone(row['place_id'])
        with connect() as conn:
            self.assertEqual(conn.execute('select kind from folders').fetchone()['kind'],'needs_review')
    def test_custom_delete_preserves_places_and_bulk_owner_checks(self):
        row,_=self.venue(self.save(self.a),self.a)
        folder=self.client.post('/folders',headers=self.a['headers'],json={'name':'Weekend'}).json()
        body={'item_ids':[str(row['id'])],'destination_id':folder['id'],'move':True}
        self.assertEqual(self.client.post('/folders/assign',headers=self.b['headers'],json=body).status_code,404)
        self.assertEqual(self.client.post('/folders/assign',headers=self.a['headers'],json=body).status_code,200)
        self.client.delete('/folders/'+folder['id'],headers=self.a['headers'])
        item=self.client.get('/items',headers=self.a['headers']).json()['items'][0]
        self.assertEqual(len(item['folders']),2)
        automatic=item['folders'][0]['id']
        self.assertEqual(self.client.delete('/folders/'+automatic,headers=self.a['headers']).status_code,422)
    def test_search_name_city_note_folder_and_vector(self):
        row,_=self.venue(self.save(self.a),self.a)
        self.client.patch('/items/'+str(row['id']),headers=self.a['headers'],json={'note':'Try the morning bun'})
        vector=[1.0]+[0.0]*383
        with connect() as conn: conn.execute('update user_places set embedding=%s::vector where id=%s',(str(vector),row['id']))
        with patch('worker.search.embed_query',return_value=vector):
            for query in ['Tartine','San Francisco','morning bun','Restaurants','pastry']:
                results=self.client.get('/items',params={'q':query},headers=self.a['headers']).json()['items']
                self.assertEqual(results[0]['id'],str(row['id']))
            self.assertEqual(self.client.get('/items',params={'q':'Tartine'},headers=self.b['headers']).json()['items'],[])
    def test_interrupted_lease_fails_and_retries_once(self):
        self.save(self.a)
        with connect() as conn:
            saved=claim(conn);self.assertEqual(saved['attempts'],1)
            conn.execute("update saves set started_at=now()-interval '61 seconds' where id=%s",(saved['id'],))
        state=self.client.get('/sync',headers=self.a['headers']).json()['saves'][0]
        self.assertEqual(state['status'],'failed');self.assertIn('timed out',state['error_reason'])
        with connect() as conn:
            conn.execute("update saves set retry_at=now()-interval '1 second'")
            self.assertEqual(claim(conn)['attempts'],2)
            conn.execute("update saves set started_at=now()-interval '61 seconds'")
            from worker.db import fail_expired
            fail_expired(conn)
            self.assertIsNone(claim(conn))
    def test_json_contract_listicle_and_empty(self):
        self.assertEqual(CANDIDATES.validate_python([]),[])
        candidate={'name':'A','city_hint':None,'country_hint':None,'category_guess':None,'confidence':.4,'evidence':'OCR A'}
        self.assertEqual(len(CANDIDATES.validate_python([{**candidate,'name':str(n)} for n in range(6)])),6)
        with self.assertRaises(ValueError): CANDIDATES.validate_python([{**candidate,'confidence':2}])
    def test_slow_media_retains_caption_for_extraction(self):
        from worker.pipeline import collect_signals
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'caption.txt').write_text('Five hikes: Point Dume, Hollywood Sign, Los Leones, El Matador, Point Mugu')
            with patch('worker.pipeline.subprocess.Popen') as spawn, patch('worker.pipeline.os.killpg') as kill:
                spawn.return_value.wait.side_effect=[subprocess.TimeoutExpired('collector',30),0]
                spawn.return_value.pid=12345
                signals=collect_signals('https://instagram.com/reel/A/',directory,new_metrics())
            self.assertIn('Five hikes',signals['caption'])
            self.assertTrue(signals['partial'])
            self.assertIn('transcript',signals['unavailable'])
            kill.assert_called_once()
    def test_server_queue_wait_expires_and_backoff_is_bounded(self):
        saved=self.save(self.a)
        with connect() as conn:
            conn.execute("update saves set created_at=now()-interval '61 seconds',updated_at=now()-interval '61 seconds' where id=%s",(saved['id'],))
        state=self.client.get('/sync',headers=self.a['headers']).json()['saves'][0]
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['attempts'],1)
        self.assertIsNotNone(state['retry_at'])
        with connect() as conn:
            self.assertIsNone(claim(conn))
            conn.execute("update saves set retry_at=now()-interval '1 second'")
            self.assertEqual(claim(conn)['attempts'],2)
    def test_manual_retry_gets_fresh_deadline_and_keeps_prior_cost(self):
        from worker.worker import process
        from worker.db import fail_expired
        saved=self.save(self.a)
        with connect() as conn:
            conn.execute('''update saves set status='failed',attempts=2,created_at=now()-interval '1 day',
                updated_at=now()-interval '1 day',cost='{"llm_tokens_in":100,"llm_tokens_out":10,"places_calls":1}'::jsonb where id=%s''',(saved['id'],))
        self.assertEqual(self.client.post('/saves/'+saved['id']+'/retry',headers=self.a['headers']).status_code,200)
        with connect() as conn:
            claimed=claim(conn)
            self.assertEqual(claimed['attempts'],1)
            self.assertEqual(fail_expired(conn),0)
        def extract(signals,metrics):
            metrics['llm_tokens_in']+=50
            return []
        with patch('worker.worker.collect_signals',return_value={'caption':'A cooking recipe'}),patch('worker.worker.extract_candidates',side_effect=extract),patch('worker.worker.signal.alarm'):
            process(saved['id'])
        with connect() as conn:
            final=conn.execute('select status,cost from saves where id=%s',(saved['id'],)).fetchone()
        self.assertEqual(final['status'],'no_places_found')
        self.assertEqual(final['cost']['places_calls'],1)
        self.assertEqual(final['cost']['llm_tokens_in'],150)
    def test_folder_edits_invalidate_semantic_index(self):
        row,_=self.venue(self.save(self.a),self.a)
        folder=self.client.post('/folders',headers=self.a['headers'],json={'name':'Weekend'}).json()
        self.client.post('/folders/assign',headers=self.a['headers'],json={'item_ids':[str(row['id'])],'destination_id':folder['id']})
        with connect() as conn: conn.execute('update user_places set embedding=%s::vector where id=%s',(str([1.0]+[0.0]*383),row['id']))
        self.assertEqual(self.client.patch('/folders/'+folder['id'],headers=self.a['headers'],json={'name':'Holiday'}).status_code,200)
        with connect() as conn:
            self.assertIsNone(conn.execute('select embedding from user_places where id=%s',(row['id'],)).fetchone()['embedding'])
    def test_linked_apple_library_cannot_merge_into_a_different_account(self):
        with connect() as conn:
            conn.execute("update users set apple_user_id='existing-a' where id=%s",(self.a['user'],))
            conn.execute("update users set apple_user_id='existing-b' where id=%s",(self.b['user'],))
        nonce=self.client.get('/auth/apple/challenge',headers=self.a['headers']).json()['nonce']
        with patch('jwt.PyJWKClient') as keys, patch('jwt.decode',return_value={'sub':'existing-b','nonce':nonce}):
            keys.return_value.get_signing_key_from_jwt.return_value.key='test-key'
            response=self.client.post('/auth/apple',headers=self.a['headers'],json={'identity_token':'x'*20,'nonce':nonce})
        self.assertEqual(response.status_code,409,response.text)
        with connect() as conn:
            owner=conn.execute('select user_id from devices where token_hash=%s',(hashlib.sha256(self.a['headers']['Authorization'][7:].encode()).hexdigest(),)).fetchone()
            self.assertEqual(str(owner['user_id']),self.a['user'])
    def test_invalid_apple_token_cannot_link(self):
        self.assertEqual(self.client.post('/auth/apple',headers=self.a['headers'],json={'identity_token':'invalid.'*6,'nonce':'a'*64}).status_code,401)

if __name__=='__main__':unittest.main()
