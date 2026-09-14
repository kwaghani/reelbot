import json
from pathlib import Path
from unittest.mock import patch
import unittest
from psycopg.types.json import Jsonb
from worker.venue_identity import inputs_for,condition_rows,VenueInputs,VenueCandidate
from worker.sponsors import SponsorCandidate,is_sponsor
from worker.category_coherence import assess,contexts_for
from worker.venue_kinds import derive_kind,provider_kind,kind_for
from worker.pipeline import new_metrics
from worker.db import connect
from worker.places import resolve
import test_personal as base

FIXTURES=Path(__file__).parent/'fixtures/ingestion'
def candidate(name):
    return {'content_type':'place','title':name,'venue_name':name,'summary':'A place to visit.',
            'attributes':{'venue_kind':'restaurant'},'city_hint':'New York','confidence':.95,'evidence':'caption'}

class SponsorTests(unittest.TestCase):
    def test_twelve_role_fixtures(self):
        fixtures=json.loads((FIXTURES/'sponsored-reels.json').read_text())
        self.assertEqual(len(fixtures),12)
        self.assertEqual(sum('venue' in f for f in fixtures),6)
        for f in fixtures:
            with self.subTest(f['name']):
                typed=inputs_for(f)
                self.assertTrue(is_sponsor(f['sponsor'],typed.sponsor_candidates))
                self.assertFalse(any(is_sponsor(v.name,typed.sponsor_candidates) for v in typed.venue_candidates))
                rows=condition_rows([candidate(f['sponsor'])]+([candidate(f['venue'])] if f.get('venue') else []),f)
                self.assertFalse(any(is_sponsor(r['title'],typed.sponsor_candidates) for r in rows))
                if f.get('venue'):self.assertIn(f['venue'],[r['title'] for r in rows])
                else:self.assertIn('sponsor_not_venue',rows[0]['review_reasons'])

    def test_typed_lists_cannot_mix_or_overlap(self):
        sponsor=SponsorCandidate('Toast','test',1,'#ad')
        with self.assertRaises(ValueError):VenueInputs((sponsor,),(),(sponsor,))
        with self.assertRaises(ValueError):VenueInputs((VenueCandidate('Toast','test',1,1,'test'),),(),(sponsor,))
        with self.assertRaises(TypeError):VenueInputs((),(),({'name':'Toast'},))

    def test_handle_requires_independent_name_not_just_description(self):
        for caption in ('@goldendiner','Visit @goldendiner for pancakes','@goldendiner -> a diner in New York'):
            row=condition_rows([candidate('Golden Diner')],{'caption':caption})[0]
            self.assertLessEqual(row['confidence'],.4)
            self.assertIn('handle_only',row['review_reasons'])
        for signals in ({'caption':'Golden Diner makes pancakes. @goldendiner'},
                        {'caption':'@goldendiner','ocr':'Golden Diner'},
                        {'caption':'@goldendiner','poi':{'name':'Golden Diner','category':'diner'}}):
            row=condition_rows([candidate('Golden Diner')],signals)[0]
            self.assertNotIn('handle_only',row.get('review_reasons',[]))

    def test_platform_paid_metadata_selected_post_only(self):
        from worker.fetch.parsing import parse_html
        for field,value in [('branded_content_tag_info',{'branded_content_sponsor':{'username':'Toast'}}),
                            ('paidPartnership',{'brandName':'Toast'}),
                            ('edge_media_to_sponsor_user',{'edges':[{'node':{'username':'Toast'}}]})]:
            root={'items':[{'id':'123','desc':'Food here','video':{},'author':{},field:value},
                           {'id':'456','desc':'Other video','video':{},'author':{},'paidPartnership':{'brandName':'Foreign'}}]}
            signals=parse_html('<script type="application/json">'+json.dumps(root)+'</script>','123')
            self.assertEqual([v.name for v in inputs_for(signals).sponsor_candidates],['Toast'])

    def test_primary_precedence_and_order_independent_array_fallback(self):
        e={'title':'Chicken Place','candidate':{'kind_inference':{'kind':'bar'},'compilation_context':{'venue_kind':'bar','source':'reel_shared_context'}},'venue_kind_source':'provider'}
        self.assertEqual(derive_kind(e,{'primary_type':'fast_food_restaurant','types':['bar','restaurant']})[0],'restaurant')
        for values in (['bar','breakfast_restaurant'],['breakfast_restaurant','bar']):
            self.assertEqual(provider_kind({'types':values})[0],'restaurant')
        self.assertEqual(provider_kind({'types':['restaurant','coffee_shop','bar']})[0],'cafe')
        self.assertEqual(provider_kind({'types':['bar','restaurant']})[0],'restaurant')
        self.assertEqual(derive_kind(e,{'primary_type':'bar','types':['restaurant']})[0],'bar')
        e['candidate']['segment_signals']={'caption':'This cafe serves coffee'}
        self.assertEqual(derive_kind(e,None),('cafe','segment_signals'))
        e.update(venue_kind_source='user',venue_kind='wellness')
        self.assertEqual(derive_kind(e,{'primary_type':'bar'}),('wellness','user'))

    def test_required_restaurant_mapping(self):
        types='fast_food_restaurant american_restaurant breakfast_restaurant brunch_restaurant chicken_restaurant hamburger_restaurant sandwich_shop pizza_restaurant mexican_restaurant italian_restaurant japanese_restaurant chinese_restaurant thai_restaurant indian_restaurant korean_restaurant vietnamese_restaurant seafood_restaurant steak_house barbecue_restaurant ramen_restaurant sushi_restaurant vegan_restaurant vegetarian_restaurant meal_takeaway meal_delivery diner deli'.split()
        for primary in types:self.assertEqual(kind_for(primary),'restaurant',primary)

    def test_new_context_is_yaml_only_and_unknown_type_is_neutral(self):
        document={'category_compatibility':{'quiet_context':{'keywords':['reading'],'accepts':['library'],'rejects':['night_club']}}}
        with patch('worker.category_coherence.registry_document',return_value=document):
            contexts=contexts_for({'caption':'A reading corner'},inference={'kind':'other','scores':{}})
            self.assertEqual(contexts,['quiet_context'])
            self.assertEqual(assess({'primaryType':'night_club'},{'category_contexts':contexts})['reason'],'category_mismatch')
            unknown=assess({'primaryType':'future_type'},{'category_contexts':contexts})
            self.assertIsNone(unknown['reason']);self.assertEqual(unknown['type_match'],0)

class CategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):base.PersonalTests.setUpClass.__func__(cls)
    setUp=base.PersonalTests.setUp
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue

    def test_missing_primary_is_not_fabricated_from_first_array_type(self):
        from worker.places import persist_google
        with connect() as conn:
            place=persist_google(conn,{'id':'array-only','displayName':{'text':'Food Place'},'formattedAddress':'New York',
                'location':{'latitude':40.721,'longitude':-73.994},'types':['bar','fast_food_restaurant','restaurant']},
                {'name':'Food Place','city_hint':'New York'})
        self.assertEqual(place['primary_type'],'')
        self.assertEqual(provider_kind(place)[0],'restaurant')

    def test_ten_exact_name_vetoes_rerank_surviving_food_place(self):
        for f in json.loads((FIXTURES/'food-homonyms.json').read_text()):
            with self.subTest(f['name']):
                lookup={'name':f['name'],'city_hint':'New York','confidence':.95,'category_contexts':['food_context']}
                def search(c,m):
                    return [{'id':f['name']+str(i),'displayName':{'text':f['name'] if i==0 else f['name']+' Cafe'},
                             'formattedAddress':'123 Madison St, New York, USA','location':{'latitude':40.721,'longitude':-73.994},'primaryType':t}
                            for i,t in enumerate((f['wrong'],f['right']))]
                metrics=new_metrics()
                with self.assertLogs('worker.category_coherence',level='WARNING') as log,connect() as conn:
                    place,_,reason=resolve(conn,lookup,metrics,search=search)
                self.assertIsNone(reason);self.assertEqual(place['google_place_id'],f['name']+'1')
                self.assertEqual(metrics['category_vetoes'][0]['name_score'],1)
                self.assertIn(f['wrong'],log.output[0])

    def test_exhausted_fanout_and_cached_poison_are_vetoed(self):
        lookup={'name':'Toast','city_hint':'New York','confidence':.95,'category_contexts':['food_context']}
        calls=[]
        def search(c,m):
            calls.append(c['text_query'])
            return [{'id':'bad','displayName':{'text':'Toast'},'formattedAddress':'264 Elizabeth St, New York',
                     'location':{'latitude':40.724,'longitude':-73.993},'primaryType':'clothing_store'}]
        with connect() as conn:
            place,_,reason=resolve(conn,lookup,new_metrics(),search=search)
        self.assertIsNone(place);self.assertEqual(reason,'category_mismatch');self.assertGreaterEqual(len(calls),2)
        row,_=self.venue(self.save(self.a),self.a,name='Toast')
        lookup['city_hint']='San Francisco'
        lookup['country_hint']='USA'
        with connect() as conn:
            conn.execute("update places set primary_type='clothing_store' where id=%s",(row['place_id'],))
            place,_,reason=resolve(conn,lookup,new_metrics(),search=lambda c,m:[])
            self.assertIsNone(place);self.assertEqual(reason,'category_mismatch')

    def test_backfill_flags_wrong_pin_preserves_owner_override_and_note(self):
        from worker.identity_backfill import repair
        row,_=self.venue(self.save(self.a),self.a,name='Toast')
        with connect() as conn:
            conn.execute("update saves set raw_signals=%s where id=%s",(Jsonb({'caption':'Food review with @Toast #ToastPartner #ad'}),row['save_id']))
            conn.execute("update entries set venue_kind='bar',venue_kind_source='user',note='Keep this note' where id=%s",(row['id'],))
            conn.execute("update places set primary_type='clothing_store' where id=%s",(row['place_id'],))
            result=repair(conn)
            changed=conn.execute('select * from entries where id=%s',(row['id'],)).fetchone()
            self.assertEqual(result['flagged'][0]['reason'],'sponsor_not_venue')
            self.assertIsNone(changed['place_id']);self.assertEqual(changed['venue_kind'],'bar')
            self.assertEqual(changed['note'],'Keep this note');self.assertEqual(changed['user_id'],row['user_id'])
            self.assertEqual(repair(conn)['flagged'],[])
