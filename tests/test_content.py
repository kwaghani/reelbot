"""Registry, multi-type ingestion, review, search and ownership invariants on real Postgres."""
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import test_personal as personal
from worker.db import connect, claim, store_candidate, items
from worker.registry import registry, validate_attributes, validate_candidates, sync_registry
from psycopg.types.json import Jsonb
from psycopg.errors import CheckViolation

class ContentTests(unittest.TestCase):
    setUpClass=classmethod(personal.PersonalTests.setUpClass.__func__)
    setUp=personal.PersonalTests.setUp
    register=personal.PersonalTests.register
    save=personal.PersonalTests.save
    venue=personal.PersonalTests.venue
    def entry(self,kind='workout',attrs=None,title='Chest and arms circuit',save=None):
        candidate={'content_type':kind,'title':title,'summary':'A useful reel to save.','attributes':attrs if attrs is not None else {'muscle_group':['chest','arms']},
            'confidence':.95,'venue_name':None,'city_hint':None,'evidence':'Explicit caption'}
        saved=save or self.save(self.a,str(uuid4()).replace('-',''))
        with connect() as conn: row=store_candidate(conn,{'id':saved['id'],'user_id':self.a['user']},candidate,None,.95)
        return row
    def test_registry_ten_complete_types_and_shapes(self):
        data=registry()
        self.assertEqual(set(data),{'place','workout','recipe','product','travel','home','style','media','learning','other'})
        for key,spec in data.items():
            self.assertTrue(any(f.get('required') for f in spec['attributes'].values()))
            if key=='other': self.assertEqual(set(spec['attributes']),{'topic'})
            else: self.assertTrue(3<=len(spec['attributes'])<=5)
        with self.assertRaises(ValueError): validate_attributes('workout',{'muscle_group':['chest'],'surprise':'no'})
        with self.assertRaises(ValueError): validate_attributes('workout',{'muscle_group':['not_a_muscle']})
        with self.assertRaises(ValueError): validate_attributes('workout',{'duration_minutes':True})
    def test_unknown_attribute_rejected_by_api_and_database(self):
        row=self.entry()
        response=self.client.patch('/items/'+str(row['id']),headers=self.a['headers'],json={'attributes':{'muscle_group':['chest'],'unknown_key':'oops'}})
        self.assertEqual(response.status_code,422,response.text)
        with self.assertRaises(CheckViolation),connect() as conn:
            conn.execute("update entries set attributes=attributes || '{\"unknown_key\":1}'::jsonb where id=%s",(row['id'],))
    def test_extractor_drops_unknown_and_missing_required_is_reviewable(self):
        candidate={'content_type':'recipe','title':'Noodle bowl','summary':'A quick noodle bowl.','attributes':{'cuisine':None,'invented_key':'ignored'},'venue_name':None,'city_hint':None,'confidence':.9,'evidence':'caption'}
        with self.assertLogs('worker.registry',level='WARNING'):
            parsed=validate_candidates([candidate])[0]
        self.assertNotIn('invented_key',parsed['attributes'])
        self.assertIn('missing_required:cuisine',parsed['review_reasons'])
        row=self.entry('recipe',parsed['attributes'],title='Noodle bowl')
        self.assertTrue(row['needs_review']);self.assertIn('missing_required:cuisine',row['review_reason'])
    def test_multi_facet_folders_review_entries_and_no_empty_auto_folders(self):
        row=self.entry()
        with connect() as conn:
            folders=conn.execute('select * from folders order by kind,name').fetchall()
            self.assertEqual({f['name'] for f in folders},{'Workouts','Chest','Arms'})
            self.assertEqual(sum(f['kind']=='auto_facet' for f in folders),2)
            self.assertTrue(all(f['parent_folder_id'] for f in folders if f['kind']=='auto_facet'))
        self.client.delete('/items/'+str(row['id']),headers=self.a['headers'])
        with connect() as conn: self.assertEqual(conn.execute('select count(*) as n from folders').fetchone()['n'],0)
    def test_dismissal_persists_and_retry_cannot_restore_review(self):
        saved=self.save(self.a,'Dismiss')
        row=self.entry('recipe',{'cuisine':None},title='Unknown noodles',save=saved)
        self.assertTrue(row['needs_review'])
        path='/items/'+str(row['id'])+'/dismiss-review'
        self.assertEqual(self.client.post(path,headers=self.b['headers']).status_code,404)
        self.assertEqual(self.client.post(path,headers=self.a['headers']).status_code,200)
        again=self.entry('recipe',{'cuisine':None},title='Unknown noodles',save=saved)
        self.assertFalse(again['needs_review']);self.assertIsNotNone(again['verified_at'])
        self.assertFalse(self.client.get('/sync',headers=self.a['headers']).json()['items'][0]['needs_review'])
    def test_mixed_ingestion_and_geo_never_makes_no_place_calls(self):
        from worker.worker import process
        saved=self.save(self.a,'Mixed')
        with connect() as conn: claim(conn)
        candidates=[{'content_type':kind,'title':title,'summary':'Save this routine.','attributes':attrs,'venue_name':'A named gym','city_hint':'London','confidence':.95,'evidence':'caption'} for kind,title,attrs in [
            ('recipe','Thai breakfast',{'cuisine':'Thai'}),('workout','Chest circuit',{'muscle_group':['chest']})]]
        with patch('worker.worker.collect_signals',return_value={'caption':'Breakfast and workout','fetch_state':'fetched'}),patch('worker.pipeline.candidates_for',return_value=candidates),patch('worker.worker.resolve') as resolver,patch('worker.worker.signal.alarm'):
            process(saved['id']);resolver.assert_not_called()
        with connect() as conn:
            rows=items(conn,self.a['user']);self.assertEqual({r['content_type'] for r in rows},{'workout','recipe'})
            self.assertTrue(all(not r['needs_review'] and not r['place_id'] for r in rows))
            state=conn.execute('select status,cost from saves where id=%s',(saved['id'],)).fetchone()
            self.assertEqual(state['status'],'resolved');self.assertEqual(state['cost']['places_calls'],0)
    def test_optional_without_venue_is_complete_required_without_place_reviews(self):
        optional=self.entry('product',{'product_category':'electronics'},title='Desk lamp')
        required=self.entry('place',{'venue_kind':'restaurant'},title='Unlocated restaurant')
        self.assertFalse(optional['needs_review']);self.assertTrue(required['needs_review'])
        self.assertIn('unresolved_place',required['review_reason'])
    def test_search_attributes_summary_place_name_folder_and_personal_scope(self):
        self.entry('recipe',{'cuisine':'Thai'},title='Weeknight noodles')
        with patch('worker.search.embed_query',side_effect=RuntimeError('local test disables model')):
            for query in ['Thai','useful','Recipes','Weeknight']:
                response=self.client.get('/items',params={'q':query},headers=self.a['headers'])
                self.assertEqual(len(response.json()['items']),1)
            self.assertEqual(self.client.get('/items',params={'q':'Thai'},headers=self.b['headers']).json()['items'],[])
    def test_eleventh_category_runtime_without_any_code_changes(self):
        import yaml
        data=registry();data['craft']={'label':'Craft','plural_label':'Crafts','icon':'scissors-cutting','geo':'never','primary_facet':'material','attributes':{'material':{'type':'string','required':True},'method':{'type':'string'},'duration_minutes':{'type':'integer'}}}
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'content_types.yaml';file.write_text(yaml.safe_dump(data))
            with patch.dict(os.environ,{'CONTENT_TYPES_PATH':str(file)}):
                response=self.client.get('/content-types');self.assertIn('craft',response.json()['types'])
                row=self.entry('craft',{'material':'paper','method':'origami'},title='Paper crane')
                with connect() as conn:
                    output=items(conn,self.a['user'])[0]
                    self.assertEqual(output['content_type'],'craft')
                    self.assertEqual({f['name'] for f in output['folders']},{'Crafts','Paper'})
        self.assertNotIn('craft',registry())
    def test_link_geo_never_to_existing_owned_place_without_geocoding(self):
        venue,_=self.venue(self.save(self.a,'Venue'),self.a)
        row=self.entry()
        with patch('worker.places.text_search') as search:
            response=self.client.patch('/items/'+str(row['id']),headers=self.a['headers'],json={'place_id':str(venue['place_id'])})
            self.assertEqual(response.status_code,200,response.text);search.assert_not_called()
        self.assertEqual(response.json()['place_id'],str(venue['place_id']))
    def test_delete_account_and_export_are_private(self):
        self.entry();other=self.save(self.b,'Untouched')
        export=self.client.get('/export',headers=self.a['headers']).json()
        self.assertEqual(len(export['entries']),1)
        self.assertEqual(self.client.delete('/account',headers=self.a['headers']).status_code,200)
        self.assertEqual(self.client.get('/sync',headers=self.a['headers']).status_code,401)
        self.assertEqual(self.client.get('/sync',headers=self.b['headers']).json()['saves'][0]['id'],other['id'])
    def test_long_summary_does_not_drop_a_valid_listicle(self):
        candidate={'content_type':'recipe','title':'Pad Thai','summary':'A very detailed description of a useful recipe. '*7,'attributes':{'cuisine':'Thai'},'venue_name':None,'city_hint':None,'confidence':.9,'evidence':'caption'}
        with self.assertLogs('worker.registry',level='WARNING'): rows=validate_candidates([candidate,candidate])
        self.assertEqual(len(rows),2);self.assertTrue(all(len(r['summary'])<=140 for r in rows))
    def test_registry_shared_attribute_name_can_have_a_new_shape(self):
        from worker.registry import candidate_schema
        data=registry();data['craft']={'label':'Craft','icon':'scissors-cutting','geo':'never','primary_facet':'style','attributes':{'style':{'type':'enum','multi':True,'required':True,'values':['origami']}}}
        schema=candidate_schema(data)
        self.assertIn('attributes_craft',schema['$defs'])
        self.assertEqual(validate_attributes('craft',{'style':['origami']},data=data)[0]['style'],['origami'])
    def test_retry_reuses_durable_extraction_without_another_llm_call(self):
        from worker.worker import process
        from worker.registry import registry_version
        saved=self.save(self.a,'Resume');candidate={'content_type':'workout','title':'Chest circuit','summary':'A chest circuit.','attributes':{'muscle_group':['chest']},'venue_name':None,'city_hint':None,'confidence':.9,'evidence':'caption'}
        with connect() as conn:
            claim(conn);conn.execute('update saves set raw_signals=%s where id=%s',(Jsonb({'candidates':[candidate],'extraction_registry_version':registry_version()}),saved['id']))
        with patch('worker.worker.collect_signals') as media,patch('worker.pipeline.candidates_for') as llm,patch('worker.worker.signal.alarm'):
            process(saved['id']);media.assert_not_called();llm.assert_not_called()
        with connect() as conn:
            self.assertEqual(len(items(conn,self.a['user'])),1)
            self.assertEqual(conn.execute('select status from saves where id=%s',(saved['id'],)).fetchone()['status'],'resolved')
