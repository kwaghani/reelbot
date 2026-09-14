import json,unittest,tempfile
from pathlib import Path
from unittest.mock import patch,Mock
from psycopg.types.json import Jsonb
import test_personal as base
from worker.db import connect,store_candidate
from worker.venue_identity import inputs_for,condition_rows,infer_kind,ADMIN_TYPES,caption_lines
from worker.places import resolve,resolution_guard
from worker.pipeline import new_metrics,candidates_for,extraction_prefix
from worker.registry import registry,venue_kinds,validate_attributes,extraction_registry

class VenueIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):base.PersonalTests.setUpClass.__func__(cls)
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue
    def setUp(self):base.PersonalTests.setUp(self)
    def test_ten_geotag_only_saves_never_have_a_place(self):
        cities=['Los Angeles','San Francisco','New York','London','Mumbai','Tokyo','Kyoto','Santa Monica','California','Canada']
        for i,city in enumerate(cities):
            signals={'geotag':{'name':city},'caption':'','ocr':'','transcript':''}
            with patch('worker.pipeline.extract_candidates',side_effect=AssertionError('Geotag needs no model')):
                rows=candidates_for({'platform':'instagram','platform_video_id':str(i)},signals,new_metrics(),{})
            self.assertEqual(len(rows),1);row=rows[0]
            self.assertIsNone(row['venue_name']);self.assertLessEqual(row['confidence'],.3)
            saved=self.save(self.a,'Tag'+str(i))
            with connect() as c:
                result=store_candidate(c,{'id':saved['id'],'user_id':self.a['user']},row,None,row['confidence'])
                self.assertTrue(result['needs_review']);self.assertIsNone(result['place_id'])
        with connect() as c:self.assertEqual(c.execute('select count(*) as n from places').fetchone()['n'],0)
    def test_all_administrative_types_rejected_even_with_matching_name(self):
        for kind in ADMIN_TYPES:
            candidate={'name':'False Venue','city_hint':'Los Angeles','confidence':.95}
            response={'id':'bad','displayName':{'text':'False Venue'},'formattedAddress':'Los Angeles','location':{'latitude':34.1,'longitude':-118.2},'primaryType':kind}
            with connect() as c:
                place,_,reason=resolve(c,candidate,new_metrics(),search=lambda *_:[response])
                self.assertIsNone(place);self.assertEqual(reason,'resolved_to_administrative_area')
                self.assertEqual(c.execute('select count(*) as n from places').fetchone()['n'],0)
    def test_city_name_is_rejected_before_outbound_lookup(self):
        search=Mock(side_effect=AssertionError('No lookup for identity'))
        with connect() as c:place,confidence,reason=resolve(c,{'name':'Los Angeles, California','confidence':.95},new_metrics(),search=search)
        self.assertIsNone(place);self.assertEqual(reason,'resolved_to_administrative_area');search.assert_not_called()
    def test_caption_emoji_pointer_and_hashtag_parsing(self):
        for emoji in ['⛰️','📍','🍜','🏖️','🏕️','🦋','🧑🏽‍🍳']:
            self.assertEqual(caption_lines(emoji+' Sturtevant Falls Trail #hiking')[0].name,'Sturtevant Falls Trail')
        for pointer in ['location is below','location:','where:','spot:','find it at','📍']:
            self.assertEqual(caption_lines(pointer+'\n\nSturtevant Falls Trail')[0].name,'Sturtevant Falls Trail')
    def test_reported_caption_geotag_precedence_and_failed_geo_kind(self):
        signals={'caption':'The location is below☺️🌈✨\n\n⛰️Sturtevant Falls Trail\n#losangeles #trailsw #waterfalls','geotag':{'name':'Los Angeles, California'}}
        row=condition_rows([{'content_type':'place','title':'Los Angeles, California','venue_name':'Los Angeles, California','attributes':{},'confidence':.95}],signals)[0]
        self.assertEqual(row['title'],'Sturtevant Falls Trail');self.assertEqual(row['attributes']['venue_kind'],'outdoors')
        self.assertEqual(row['venue_candidates'][0]['rank'],2);self.assertNotIn('Los Angeles',[v['name'] for v in row['venue_candidates']])
        self.assertEqual(row['location_hints'][0]['source'],'platform_geotag')
        for tag in ['#waterfalls','#hiking','#trail']:self.assertEqual(infer_kind({'caption':tag})['kind'],'outdoors')
    def test_distance_centroid_and_valid_poi_guards(self):
        base_place={'name':'Named Trail','primary_type':'hiking_area','lat':34.2,'lng':-118.1}
        bias=[{'latitude':34.0522,'longitude':-118.2437}]
        self.assertIsNone(resolution_guard(base_place,{'venue_kind':'outdoors'},bias))
        self.assertEqual(resolution_guard({**base_place,'lat':48.85,'lng':2.35},{},bias),'distance_implausible')
        self.assertEqual(resolution_guard({**base_place,'lat':34.0522,'lng':-118.2437},{},bias),'probable_city_centroid')
        self.assertIsNone(resolution_guard({**base_place,'primary_type':'tourist_attraction','lat':34.0522,'lng':-118.2437},{},bias))
    def test_schema_conditioning_numbers_booleans_and_seventeenth_kind(self):
        data=registry();self.assertEqual(len(data['place']['kind_attributes']),16)
        attrs={'venue_kind':'outdoors','distance_km':5.4,'permit_required':False,'best_season':['spring','fall']}
        self.assertEqual(validate_attributes('place',attrs)[0],attrs)
        for invalid in [{'distance_km':True},{'distance_km':-1},{'permit_required':'yes'},{'cuisine':'Thai'}]:
            with self.assertRaises(ValueError):validate_attributes('place',{'venue_kind':'outdoors',**invalid})
        data['place']['attributes']['venue_kind']['values'].append('observatory')
        data['place']['kind_attributes']['observatory']={'telescope':{'type':'string'}}
        self.assertEqual(validate_attributes('place',{'venue_kind':'observatory','telescope':'Reflector'},data=data)[0]['telescope'],'Reflector')
        conditioned=extraction_registry({'caption':'⛰️ Sturtevant Falls Trail #hiking'})
        self.assertEqual(list(conditioned['place']['kind_attributes']),['outdoors'])
        self.assertNotIn('cuisine',conditioned['place']['attributes'])
    def test_osm_fallback_is_bounded_and_rejects_city(self):
        from worker import noncommercial as osm
        candidate={'name':'Sturtevant Falls Trail','city_hint':'Los Angeles','venue_kind':'outdoors','confidence':.9}
        results=[{'name':'Sturtevant Falls Trail','lat':'34.0522','lon':'-118.2437','type':'city','osm_type':'relation','osm_id':1},
                 {'name':'Sturtevant Falls','lat':'34.2097','lon':'-118.0184','type':'waterfall','osm_type':'node','osm_id':2,'display_name':'Sturtevant Falls, Angeles National Forest'}]
        with patch.object(osm,'nominatim_search',return_value=results) as search,patch.object(osm,'web_candidates') as web:
            with connect() as c:
                place,_,reason=resolve(c,candidate,new_metrics(),search=lambda *_:[])
                self.assertEqual(place['provider'],'osm');self.assertIsNone(reason)
                self.assertEqual(place['resolution_attribution']['label'],'© OpenStreetMap contributors')
            self.assertEqual(search.call_count,1);web.assert_not_called()
    def test_old_administrative_anchor_repaired_without_losing_note(self):
        from db.identity_migrate import repair
        entry,_=self.venue(self.save(self.a),self.a)
        with connect() as c:
            c.execute("update entries set note='Keep me' where id=%s",(entry['id'],))
            c.execute("update places set primary_type='locality' where id=%s",(entry['place_id'],))
            report=repair(c);row=c.execute('select * from entries where id=%s',(entry['id'],)).fetchone()
            self.assertEqual(report['flagged_count'],1);self.assertEqual(row['note'],'Keep me');self.assertIsNone(row['place_id']);self.assertTrue(row['needs_review'])
    def test_database_kind_schema_rejects_restaurant_fields_on_trail(self):
        entry,_=self.venue(self.save(self.a),self.a)
        with connect() as c:
            with self.assertRaises(Exception):
                c.execute('update entries set attributes=%s where id=%s',(Jsonb({'venue_kind':'outdoors','cuisine':'Thai'}),entry['id']))
        with connect() as c:
            c.execute('update entries set attributes=%s where id=%s',(Jsonb({'venue_kind':'outdoors','distance_km':4.8,'permit_required':False}),entry['id']))
    def test_uncategorized_poi_is_bias_not_identity(self):
        typed=inputs_for({'poi':{'name':'Unknown Region','lat':35,'lng':-117}})
        self.assertEqual(typed.venue_candidates,());self.assertEqual(typed.location_hints[0].source,'platform_geotag')
    def test_secondary_types_block_route_without_primary_type(self):
        candidate={'name':'Eaton Canyon Falls','confidence':.9,'city_hint':'Los Angeles'}
        response={'id':'road','displayName':{'text':'Eaton Canyon Falls'},'formattedAddress':'California','location':{'latitude':34.1946,'longitude':-118.1025},'types':['route']}
        with connect() as c:
            place,_,reason=resolve(c,candidate,new_metrics(),search=lambda *_:[response])
        self.assertIsNone(place);self.assertEqual(reason,'resolved_to_administrative_area')
    def test_replacing_rejected_cache_key_does_not_move_existing_entry(self):
        from worker.noncommercial import persist_external
        entry,_=self.venue(self.save(self.a),self.a)
        with connect() as c:
            old=c.execute('select * from places where id=%s',(entry['place_id'],)).fetchone()
            new={'provider':'osm','provider_place_id':'node:999','name':'Tartine Bakery','formatted_address':'San Francisco','lat':37.761,'lng':-122.424,'primary_type':'bakery','resolution_attribution':{}}
            candidate={'name':'Tartine Bakery','city_hint':'San Francisco','country_hint':'USA'}
            persisted=persist_external(c,new,candidate)
            self.assertNotEqual(persisted['id'],old['id'])
            self.assertEqual(c.execute('select place_id from entries where id=%s',(entry['id'],)).fetchone()['place_id'],old['id'])
    def test_extracted_attributes_need_source_evidence(self):
        from worker.venue_identity import grounded_attributes
        result=grounded_attributes({'venue_kind':'outdoors','activity':'hike','best_season':['summer'],'distance_km':4.8,'permit_required':True},{'caption':'⛰️ Sturtevant Falls Trail #waterfalls'},'Sturtevant Falls Trail')
        self.assertEqual(result,{'venue_kind':'outdoors','activity':'hike'})
    def test_web_coordinates_address_and_administrative_rejection(self):
        from worker.noncommercial import web_candidates
        candidate={'name':'Hidden Falls','city_hint':'Los Angeles','venue_kind':'outdoors'}
        results=b'<a class="result__a" href="https://example.org/falls">Hidden Falls</a>'
        geo=b'<script type="application/ld+json">{"name":"Hidden Falls","geo":{"latitude":34.21,"longitude":-118.02}}</script>'
        address=b'<script type="application/ld+json">{"name":"Hidden Falls","address":{"streetAddress":"100 Forest Road","addressLocality":"Arcadia"}}</script>'
        with patch('worker.noncommercial.fetch',side_effect=[(results,''),(geo,'')]):
            rows=web_candidates(candidate,new_metrics());self.assertEqual(rows[0]['lat'],34.21)
        match={'id':'falls','displayName':{'text':'Hidden Falls'},'formattedAddress':'100 Forest Road','location':{'latitude':34.21,'longitude':-118.02},'primaryType':'natural_feature'}
        with patch('worker.noncommercial.fetch',side_effect=[(results,''),(address,'')]),patch('worker.places.text_search',return_value=[match]) as search:
            rows=web_candidates(candidate,new_metrics());self.assertEqual(rows[0]['provider'],'web');self.assertIn('100 Forest Road',search.call_args.args[0]['text_query'])
        match['primaryType']='street_address'
        with patch('worker.noncommercial.fetch',side_effect=[(results,''),(address,'')]),patch('worker.places.text_search',return_value=[match]):
            self.assertEqual(web_candidates(candidate,new_metrics()),[])
    def test_nominatim_cache_is_shared_across_repeated_lookups(self):
        from worker.noncommercial import nominatim_search
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'[]'
        with patch('urllib.request.urlopen',return_value=response) as call,patch('worker.noncommercial.time.sleep'):
            metrics=new_metrics()
            for _ in range(5):
                with connect() as c:self.assertEqual(nominatim_search(c,'No Such Test Falls',metrics),[])
            self.assertEqual(call.call_count,1);self.assertEqual(metrics['osm_cache_hits'],4)
            self.assertIn('ReelBot',call.call_args.args[0].get_header('User-agent'))
    def test_ranked_bias_and_durable_candidate_metadata(self):
        from worker.registry import validate_candidates
        signals={'caption':'⛰️ Sturtevant Falls Trail #losangeles','geotag':{'name':'Arcadia'}}
        rows=condition_rows([],signals)
        self.assertEqual(rows[0]['city_hint'],'Arcadia')
        rows=condition_rows(validate_candidates(rows),signals)
        self.assertEqual(rows[0]['identity_source'],'caption');self.assertEqual(rows[0]['location_hints'][0]['source'],'platform_geotag')
    def test_pointer_pin_and_keycap_emoji(self):
        self.assertEqual(caption_lines('📍\nsturtevant falls')[0].name,'sturtevant falls')
        self.assertEqual(caption_lines('1️⃣ Sturtevant Falls Trail')[0].name,'Sturtevant Falls Trail')
