import unittest
from unittest.mock import patch
import test_personal as base
from worker.db import connect
from worker.photo_jobs import run_one
class PhotoJobTests(unittest.TestCase):
 setUpClass=classmethod(base.PersonalTests.setUpClass.__func__)
 setUp=base.PersonalTests.setUp
 register=base.PersonalTests.register
 save=base.PersonalTests.save
 venue=base.PersonalTests.venue
 def test_one_job_per_place_and_retry_is_owner_scoped(self):
  row,_=self.venue(self.save(self.a),self.a);self.venue(self.save(self.a,'B'),self.a)
  with connect() as c:self.assertEqual(c.execute('select count(*) n from photo_jobs').fetchone()['n'],1)
  path='/items/'+str(row['id'])+'/image/retry'
  self.assertEqual(self.client.post(path,headers=self.b['headers']).status_code,404)
  self.assertEqual(self.client.post(path,headers=self.a['headers']).status_code,200)
 def test_failure_backoff_and_success_are_durable(self):
  self.venue(self.save(self.a),self.a)
  with patch('worker.photo_jobs.acquire',return_value=[]):self.assertTrue(run_one())
  with connect() as c:
   job=c.execute('select * from photo_jobs').fetchone();self.assertEqual(job['status'],'failed');self.assertEqual(job['attempts'],1)
   self.assertGreater(job['retry_at'],job['updated_at']);c.execute('update photo_jobs set retry_at=now()')
  with patch('worker.photo_jobs.acquire',return_value=[{'source':'commons'}]):self.assertTrue(run_one())
  with connect() as c:self.assertEqual(c.execute('select status from photo_jobs').fetchone()['status'],'complete')
 def test_partial_retry_requests_deeper_scan_without_changing_entries(self):
  saved=self.save(self.a);row,_=self.venue(saved,self.a)
  with connect() as c:c.execute("update saves set status='partial_extraction',is_compilation=true,expected_venue_count=7,extracted_venue_count=1 where id=%s",(saved['id'],))
  response=self.client.post('/saves/'+saved['id']+'/retry?deeper=true',headers=self.a['headers'])
  self.assertEqual(response.status_code,200);self.assertTrue(response.json()['force_dense'])
  with connect() as c:self.assertEqual(c.execute('select id from entries').fetchone()['id'],row['id'])
 def test_map_thumbnail_is_owner_private_even_for_a_shared_place(self):
  import io,base64
  from PIL import Image
  a,_=self.venue(self.save(self.a),self.a);b,_=self.venue(self.save(self.b),self.b)
  image=io.BytesIO();Image.new('RGB',(360,270),'blue').save(image,'JPEG')
  path='/items/'+str(a['id'])+'/map-thumbnail'
  body={'jpeg':base64.b64encode(image.getvalue()).decode()}
  self.assertEqual(self.client.post(path,headers=self.b['headers'],json=body).status_code,404)
  self.assertEqual(self.client.post(path,headers=self.a['headers'],json=body).status_code,200)
  self.assertTrue(self.client.get(path,headers=self.a['headers']).json()['uri'].startswith('data:image/jpeg'))
  self.assertIsNone(self.client.get('/items/'+str(b['id'])+'/map-thumbnail',headers=self.b['headers']).json()['uri'])
  self.assertEqual(self.client.post(path,headers=self.a['headers'],json={'jpeg':'invalid'}).status_code,422)
