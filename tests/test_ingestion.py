"""Ingestion invariants: real public BCD metadata, controlled access states, real SQL ownership."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from uuid import uuid4
import test_personal as personal
ROOT=personal.ROOT
from worker.db import connect,enqueue,claim,store_candidate
from worker.pipeline import new_metrics,candidates_for
from worker.url_resolve import resolve_url
from worker.fetch.http import Response,FetchError
from worker.fetch.ladder import fetch_signals,sufficient
from worker.fetch.parsing import parse_html
from worker.fetch.hints import identify
from worker.places import normalized,resolve,FIELD_MASK
from worker.worker import process,finish
from worker.save_identity import bind_identity

FIXTURE=json.loads((ROOT/'tests/fixtures/ingestion/bcd-public-signals.json').read_text())
CANONICAL=FIXTURE['canonical_url']
RESOLVED={'source_url':FIXTURE['source_url'],'canonical_url':CANONICAL,'platform':'tiktok','platform_video_id':'7511087612157873450'}

def response(body='',status=200,location=None):
    return Response(CANONICAL,status,{'location':location} if location else {},body.encode())

class IngestionTests(unittest.TestCase):
    setUpClass=classmethod(personal.PersonalTests.setUpClass.__func__)
    setUp=personal.PersonalTests.setUp
    register=personal.PersonalTests.register
    save=personal.PersonalTests.save

    def fetch(self,request,media=None,cache=False):
        with tempfile.TemporaryDirectory() as folder:
            return fetch_signals(RESOLVED,folder,new_metrics(),request=request,media=media or (lambda *a:{'caption':'','ocr':'','transcript':''}),use_cache=cache)

    def test_bcd_real_caption_tier_one_only(self):
        request=Mock(return_value=response(json.dumps(FIXTURE['oembed'])))
        media=Mock(side_effect=AssertionError('Media must not run'))
        signals=self.fetch(request,media)
        self.assertEqual([x['tier'] for x in signals['tier_log']],[1])
        self.assertIn('BCD Tofu House',signals['caption']);self.assertEqual(signals['city_hint'],'Los Angeles')
        self.assertEqual(request.call_count,1);media.assert_not_called()

    def test_resolution_http_meta_js_and_instagram_share(self):
        for html in ['<meta http-equiv="refresh" content="0; url='+CANONICAL+'">','<link rel="canonical" href="'+CANONICAL+'">','<script>window.location.href="'+CANONICAL+'"</script>']:
            result=resolve_url(FIXTURE['source_url'],request=Mock(return_value=response(html)),use_cache=False)
            self.assertEqual(result['canonical_url'],CANONICAL);self.assertEqual(result['source_url'],FIXTURE['source_url'])
        result=resolve_url('https://instagram.com/share/reel/token/',request=Mock(return_value=response(status=302,location='https://instagram.com/p/DcuTC_5jWDW/?igsh=x')),use_cache=False)
        self.assertEqual(result['canonical_url'],'https://www.instagram.com/reel/DcuTC_5jWDW/')
        self.assertEqual(result['platform_video_id'],'DcuTC_5jWDW')

    def test_tiktok_redirect_without_username_resolves_author_from_state(self):
        code='7478602824494877969'
        post={'id':code,'desc':'Ramyeon Bar and Restaurant','video':{'duration':20},'author':{'uniqueId':'quezoncitygovt'}}
        request=Mock(side_effect=[response(status=302,location='https://www.tiktok.com/@/video/'+code),response('<script type="application/json">'+json.dumps(post)+'</script>')])
        result=resolve_url('https://vt.tiktok.com/ZSM46hvLU/',request=request,use_cache=False)
        self.assertEqual(result['canonical_url'],'https://www.tiktok.com/@quezoncitygovt/video/'+code)
        self.assertEqual(request.call_count,2)

    def test_login_metadata_is_an_access_wall_not_a_caption(self):
        document='<meta property="og:title" content="Login • Instagram"><meta property="og:description" content="Create an account or log in to Instagram to see posts.">'
        parsed=parse_html(document,'PostCode')
        self.assertEqual(parsed['caption'],'');self.assertEqual(parsed['access_state'],'fetch_blocked')
        state={'code':'PostCode','caption':{'text':'BCD Tofu House in Los Angeles'}}
        parsed=parse_html(document+'<script type="application/json">'+json.dumps(state)+'</script>','PostCode')
        self.assertIn('BCD Tofu House',parsed['caption']);self.assertIsNone(parsed['access_state'])

    def test_resolution_hop_limit_and_private_redirect(self):
        calls=[]
        def redirect(url,**kwargs):
            calls.append(url);return response(status=302,location='https://vt.tiktok.com/token'+str(len(calls))+'/')
        with self.assertRaises(FetchError) as error:resolve_url(FIXTURE['source_url'],request=redirect,use_cache=False)
        self.assertEqual(error.exception.state,'resolve_failed');self.assertEqual(len(calls),5)
        with self.assertRaises(FetchError):resolve_url(FIXTURE['source_url'],request=lambda *a,**k:response(status=302,location='http://127.0.0.1/private'),use_cache=False)

    def test_original_and_canonical_deduplicate_without_losing_notes(self):
        with connect() as conn:
            direct=enqueue(conn,self.a['user'],CANONICAL+'?tracking=original')
            short=enqueue(conn,self.a['user'],FIXTURE['source_url'])
            candidate={'content_type':'place','title':'BCD Tofu House','summary':'Saved venue.','attributes':{'venue_kind':'restaurant'},'venue_name':'BCD Tofu House','city_hint':'Los Angeles','confidence':.9,'evidence':'caption'}
            for saved,note in [(direct,'Try the tofu'),(short,'Bring appetite')]:
                row=store_candidate(conn,saved,candidate,None,.9)
                conn.execute('update entries set note=%s where id=%s',(note,row['id']))
            winner,merged=bind_identity(conn,short,RESOLVED)
            self.assertTrue(merged);self.assertEqual(winner['id'],direct['id'])
            self.assertEqual(conn.execute('select count(*) as n from saves').fetchone()['n'],1)
            row=conn.execute('select note from entries').fetchone();self.assertIn('Try the tofu',row['note']);self.assertIn('Bring appetite',row['note'])
            self.assertEqual(conn.execute('select count(*) as n from save_source_urls').fetchone()['n'],2)
            self.assertTrue(winner['source_url'].endswith('?tracking=original'))

    def test_fetch_cache_has_zero_outbound(self):
        self.fetch(Mock(return_value=response(json.dumps(FIXTURE['oembed']))),cache=True)
        request=Mock(side_effect=AssertionError('network forbidden'));media=Mock(side_effect=AssertionError('media forbidden'))
        signals=self.fetch(request,media,cache=True)
        self.assertTrue(signals['fetch_cache_hit']);request.assert_not_called();media.assert_not_called()

    def test_caption_of_any_length_never_means_no_content(self):
        for caption in ['x','BCD Tofu House','A caption longer than twenty characters']:
            signals=self.fetch(lambda *a,**k:response(json.dumps({'title':caption}) if '/oembed?' in a[0] else '<html></html>'))
            self.assertNotEqual(signals['fetch_state'],'fetch_ok_no_content')
        empty=self.fetch(lambda *a,**k:response('{}' if '/oembed?' in a[0] else '<html></html>'))
        self.assertEqual(empty['fetch_state'],'fetch_ok_no_content');self.assertEqual([x['tier'] for x in empty['tier_log']],[1,2,3,4])

    def test_429_autoretries_all_four_delays_then_manual(self):
        signals=self.fetch(lambda *a,**k:response(status=429),media=lambda *a:(_ for _ in ()).throw(FetchError('fetch_blocked','blocked')))
        self.assertEqual(signals['fetch_state'],'fetch_blocked')
        saved=self.save(self.a,'Blocked')
        for delay in [60,900,7200,43200,None]:
            with connect() as conn:
                claimed=claim(conn);self.assertIsNotNone(claimed)
            finish(saved['id'],'fetch_blocked',new_metrics())
            with connect() as conn:
                row=conn.execute('select *,extract(epoch from retry_at-now()) as delay from saves where id=%s',(saved['id'],)).fetchone()
                self.assertEqual(row['status'],'fetch_blocked');self.assertNotIn('no content',row['error_reason'])
                if delay:
                    self.assertAlmostEqual(float(row['delay']),delay,delta=2)
                    self.assertIsNone(claim(conn))
                    conn.execute("update saves set retry_at=now()-interval '1 second' where id=%s",(saved['id'],))
                else:self.assertIsNone(row['retry_at']);self.assertIsNone(claim(conn))

    def test_deleted_and_unavailable_speech_are_distinct(self):
        deleted=self.fetch(lambda *a,**k:response(status=404))
        self.assertEqual(deleted['fetch_state'],'fetch_not_found')
        unavailable=self.fetch(lambda *a,**k:response('{}' if '/oembed?' in a[0] else '<html></html>'),media=lambda *a:{'unavailable':{'transcript':'disabled'}})
        self.assertEqual(unavailable['fetch_state'],'needs_source_info')
        saved=self.save(self.a,'Deleted')
        with connect() as conn:claim(conn)
        finish(saved['id'],'fetch_not_found',new_metrics())
        row=self.client.post('/saves/'+saved['id']+'/retry',headers=self.a['headers']).json()
        self.assertEqual(row['status'],'fetch_not_found');self.assertIsNone(row['retry_at'])

    def test_renamed_state_script_and_poi_skip_llm(self):
        state=FIXTURE['platform_state']
        doc='<script id="platform-renamed-tomorrow" type="application/json">'+json.dumps({'nested':[state]})+'</script>'
        signals=parse_html(doc,RESOLVED['platform_video_id']);signals.update(identify(signals))
        self.assertEqual(signals['poi']['name'],'BCD Tofu House');self.assertIn('3575',signals['poi']['address'])
        with patch('worker.pipeline.extract_candidates',side_effect=AssertionError('POI must skip venue inference')):
            rows=candidates_for(RESOLVED,signals,new_metrics(),{})
        self.assertEqual(rows[0]['confidence'],.95);self.assertEqual(rows[0]['title'],'BCD Tofu House')

    def test_instagram_requested_caption_beats_longer_recommendations(self):
        target={'id':'POLARIS_123','code':'DcuTC_5jWDW','caption':{'text':'Fetty Wap performs Trap Queen.'}}
        recommendation={'code':'unrelated','caption':{'text':'A much longer unrelated caption about a different artist. '*10}}
        root={'shortcode':'DcuTC_5jWDW','caption':None,'media':target,'recommendations':[recommendation]}
        document='<script type="application/json">'+json.dumps({'data':root})+'</script>'
        result=parse_html(document,'DcuTC_5jWDW')
        self.assertEqual(result['caption'],target['caption']['text'])

    def test_poi_coordinates_zero_google_calls(self):
        candidate={'name':'Tagged Venue','city_hint':'Los Angeles','confidence':.95,'poi':{'id':'real-style-tag','category':'restaurant','name':'Tagged Venue','address':'Los Angeles','lat':34.11,'lng':-118.2437}}
        provider=Mock(side_effect=AssertionError('Google must not run'))
        with connect() as conn:place,confidence,reason=resolve(conn,candidate,new_metrics(),search=provider)
        self.assertEqual(confidence,.95);self.assertEqual(place['provider'],'tiktok');provider.assert_not_called()
        candidate['poi']['platform']='instagram'
        # An Instagram location object can bias a caption lookup, never supply the pin.
        with connect() as conn:instagram,confidence,_=resolve(conn,candidate,new_metrics(),search=lambda *_:[])
        self.assertIsNone(instagram);provider.assert_not_called()

    def test_name_normalization_bias_and_outside_radius_choices(self):
        self.assertEqual(normalized('Vees Cafe'),normalized("Vee’s Café"))
        self.assertEqual(normalized('Wine & Friends'),normalized('Wine and Friends'))
        seen=[]
        def provider(query,metrics):
            seen.append(query)
            return [{'id':'elsewhere','displayName':{'text':'Vee’s Café'},'formattedAddress':'Paris, France','location':{'latitude':48.85,'longitude':2.35}}]
        lookup={'name':'Vees Cafe','city_hint':'Venice Beach','confidence':.9}
        with connect() as conn:place,confidence,reason=resolve(conn,lookup,new_metrics(),search=provider)
        self.assertIsNone(place);self.assertEqual(reason,'distance_implausible');self.assertEqual(len(seen),3)
        self.assertEqual(seen[0]['location_bias'],{'latitude':33.985,'longitude':-118.4695})
        self.assertIsNone(seen[-1]['location_bias']);self.assertEqual(lookup['place_candidates'],[])
        from worker.places import persist_google
        with connect() as conn:
            persist_google(conn,provider({}, {})[0],lookup)
            seen.clear();place,_,_=resolve(conn,lookup,new_metrics(),search=provider)
            self.assertIsNone(place);self.assertEqual(len(seen),3)  # Old cache entries must pass today's city bounds too.


    def test_legacy_extracted_name_retains_entry_id_and_note(self):
        from psycopg.types.json import Jsonb
        saved=self.save(self.a,'Legacy')
        c={'content_type':'place','title':'Los Liones Canyon Trail','summary':'Saved trail.','attributes':{'venue_kind':'activity'},'venue_name':'Los Leones Trail','city_hint':'Los Angeles','confidence':.9,'evidence':'caption'}
        with connect() as conn:
            old=store_candidate(conn,saved,c,None,.9)
            conn.execute('update entries set note=%s,candidate=%s where id=%s',('Bring water',Jsonb({'name':'Los Leones Trail','city_hint':'Los Angeles'}),old['id']))
            c['title']='Los Leones Trail'
            new=store_candidate(conn,saved,c,None,.9,'ambiguous_place')
            self.assertEqual(new['id'],old['id']);self.assertEqual(new['note'],'Bring water')
            self.assertEqual(conn.execute('select count(*) as n from entries where save_id=%s',(saved['id'],)).fetchone()['n'],1)

    def test_offered_place_choice_verifies_coordinates_and_retains_note(self):
        saved=self.save(self.a,'Choice')
        option={'id':'offered-place','displayName':{'text':'Test Cafe'},'formattedAddress':'123 Test St, Los Angeles, CA','location':{'latitude':34.01,'longitude':-118.3},'primaryType':'cafe'}
        c={'content_type':'place','title':'Test Cafe','summary':'A cafe.','attributes':{'venue_kind':'cafe'},'venue_name':'Test Cafe','city_hint':'Los Angeles','confidence':.8,'evidence':'caption','place_candidates':[{'place':option,'score':.9}]}
        with connect() as conn:
            row=store_candidate(conn,saved,c,None,.8,'ambiguous_place')
            conn.execute('update entries set note=%s where id=%s',('Order coffee',row['id']))
        selected=self.client.post('/items/'+str(row['id'])+'/choose-place',headers=self.a['headers'],json={'place_id':'offered-place'})
        self.assertEqual(selected.status_code,200);self.assertFalse(selected.json()['needs_review']);self.assertEqual(selected.json()['note'],'Order coffee')
        with connect() as conn:
            place=conn.execute('select p.* from places p join entries e on e.place_id=p.id where e.id=%s',(row['id'],)).fetchone()
            self.assertEqual(place['lat'],34.01);self.assertEqual(place['lng'],-118.3)
            self.assertTrue(place['lookup_key'].startswith('choice:'))

    def test_owner_only_debug_manual_and_place_choice(self):
        saved=self.save(self.a,'Manual')
        with connect() as conn:conn.execute("update saves set status='needs_source_info' where id=%s",(saved['id'],))
        body={'entry_id':str(uuid4()),'title':'Thai noodles','content_type':'recipe'}
        self.assertEqual(self.client.post('/saves/'+saved['id']+'/source-info',headers=self.b['headers'],json=body).status_code,404)
        for _ in range(2):self.assertEqual(self.client.post('/saves/'+saved['id']+'/source-info',headers=self.a['headers'],json=body).status_code,200)
        items=self.client.get('/items',headers=self.a['headers']).json()['items'];self.assertEqual(len(items),1);self.assertEqual(items[0]['id'],body['entry_id'])
        self.assertEqual(self.client.get('/debug/saves/'+saved['id'],headers=self.b['headers']).status_code,404)
        self.assertEqual(self.client.get('/debug/saves/'+saved['id'],headers=self.a['headers']).status_code,200)
        self.assertNotIn('raw_signals',self.client.get('/sync',headers=self.a['headers']).json()['saves'][0])
        for user,code in [(self.a,422),(self.b,404)]:
            self.assertEqual(self.client.post('/items/'+items[0]['id']+'/choose-place',headers=user['headers'],json={'place_id':'injected'}).status_code,code)

class MediaTests(unittest.TestCase):
    def test_six_real_frames_vision_fallback_and_immediate_cleanup(self):
        import shutil
        from worker.fetch.media import collect_media
        from yt_dlp import YoutubeDL
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'fixture.mp4';work=root/'temporary-media'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=white:s=160x120:r=10','-t','4','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
            metadata={'description':'','duration':4,'music_only':True,'has_speech':False,'url':'https://example.com/video.mp4','protocol':'https','vcodec':'h264','acodec':'aac'}
            def download(url,target,request_url):shutil.copyfile(video,target);return video.stat().st_size,False
            metrics=new_metrics()
            with patch.object(YoutubeDL,'extract_info',return_value=metadata),patch('worker.fetch.media.bounded_media_file',side_effect=download),patch('worker.media.stage_transcript') as speech,patch('pytesseract.image_to_string',return_value=''),patch('worker.fetch.media.vision_text',return_value='Controlled fixture overlay, read from six sampled frames.') as vision:
                signals=collect_media('https://www.tiktok.com/@fixture/video/123',work,metrics)
            self.assertEqual(signals['metadata']['frame_count'],6);self.assertIn('Controlled fixture',signals['ocr'])
            speech.assert_not_called();vision.assert_called_once();self.assertEqual(list(work.iterdir()),[])
            self.assertGreater(metrics['media_download_bytes'],0)

    def test_media_body_cap_stops_reading_and_keeps_no_more_than_budget(self):
        from worker.fetch.media import bounded_media_file
        chunks=[]
        def body(**kwargs):
            for _ in range(20):chunks.append(1);yield b'x'*4096
        network=Mock();network.status_code=200;network.headers={'content-length':'81920'};network.iter_bytes=body
        client=Mock();client.stream.return_value.__enter__=Mock(return_value=network);client.stream.return_value.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder,patch('httpx.Client') as factory,patch('worker.media.validate_public_url'),patch('worker.fetch.media.MAX_BYTES',32768):
            factory.return_value.__enter__.return_value=client
            target=Path(folder)/'bounded.mp4';size,truncated=bounded_media_file('https://example.com/video',target,CANONICAL)
            self.assertEqual(size,32768);self.assertEqual(target.stat().st_size,32768);self.assertTrue(truncated);self.assertEqual(len(chunks),8)

    def test_missing_format_is_not_a_deleted_post(self):
        from worker.fetch.media import classify_media_error
        self.assertEqual(classify_media_error('Requested format is not available'),'needs_source_info')
        self.assertEqual(classify_media_error('HTTP Error 429 Too Many Requests'),'fetch_blocked')

if __name__=='__main__':unittest.main()
