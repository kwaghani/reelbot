import io, json, tempfile, unittest
from unittest.mock import patch
from PIL import Image
import test_personal as base
from worker import imagery as im
from worker.db import connect
from psycopg.types.json import Jsonb

def photo(color='red', size=(1200,1500)):
    out=io.BytesIO();Image.new('RGB',size,color).save(out,'JPEG');return out.getvalue()
def google():
    return {'key':'google:0','source':'google','width':1600,'height':900,'_photo_name':'places/example/photos/private-reference','attribution':{'label':'Google Maps','authors':[{'displayName':'Photographer'}]},'license':'Google Places display-only'}

class ImageryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):base.PersonalTests.setUpClass.__func__(cls)
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue
    def setUp(self):
        base.PersonalTests.setUp(self)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cache=patch.object(im,'cache_dir',return_value=__import__('pathlib').Path(self.tmp.name));self.cache.start();self.addCleanup(self.cache.stop)
    def resolve(self, rows, **kw):return im.resolve_batch(self.a['user'],[r['id'] for r in rows],**{'gallery':True,'context':'detail',**kw})
    def test_five_entries_one_global_acquisition_and_no_google_content_persisted(self):
        rows=[self.venue(self.save(self.a,str(i)),self.a)[0] for i in range(5)]
        with patch.object(im,'google_candidates',return_value=[google()]) as metadata,patch.object(im,'google_bytes',return_value=photo()) as media:
            result=self.resolve(rows)
        self.assertEqual(metadata.call_count,1);self.assertEqual(media.call_count,1)
        self.assertEqual(result['metrics']['venue_cache_hits'],4)
        for value in result['items'].values():
            self.assertEqual(value['selected']['source'],'google')
            self.assertLess(len(json.dumps(value).encode()),100_000)
        with connect() as conn:
            cache=conn.execute('select imagery from places').fetchone()['imagery']
            self.assertNotIn('private-reference',json.dumps(cache));self.assertNotIn('authors',json.dumps(cache));self.assertNotIn('candidates',cache)
    def test_multi_entry_cover_is_never_fetched_and_nonplace_is_untouched(self):
        saved=self.save(self.a);a=self.venue(saved,self.a)[0];b=self.venue(saved,self.a,'Another venue')[0]
        with connect() as conn:conn.execute('update saves set raw_signals=%s where id=%s',(Jsonb({'thumbnail_url':'https://example.com/hollywood.jpg'}),saved['id']))
        with patch.object(im,'acquire',return_value=[]),patch.object(im,'fetch') as fetch:
            result=self.resolve([a,b])
        fetch.assert_not_called()
        for item in result['items'].values():self.assertEqual(item['selection']['cover_rejection'],'multi_entry_save');self.assertIsNone(item['selected'])
        self.assertEqual(self.client.patch('/items/'+str(a['id']),headers=self.a['headers'],json={'content_type':'recipe','attributes':{}}).status_code,200)
        with patch.object(im,'acquire') as acq:
            self.assertIn(str(a['id']),self.resolve([a])['items']);acq.assert_not_called()
    def test_cover_quality_boundaries_and_cached_ocr(self):
        row=self.venue(self.save(self.a),self.a)[0]
        with connect() as conn:conn.execute('update saves set raw_signals=%s where id=%s',(Jsonb({'thumbnail_url':'https://example.com/dish.jpg'}),row['save_id']))
        entry=im.load_entries(self.a['user'],[row['id']])[0][0]
        for size,coverage,expected in [((399,800),0,'cover_below_400px'),((400,800),.251,'cover_text_above_25_percent'),((1200,1500),.25,None)]:
            entry['cover_imagery']={}
            with patch.object(im,'fetch',return_value=(photo(size=size),'https://example.com/dish.jpg')),patch.object(im,'text_coverage',return_value=coverage):
                candidate,rejection=im.cover_candidate(entry,im.metrics());self.assertEqual(rejection,expected)
        entry=im.load_entries(self.a['user'],[row['id']])[0][0]
        with patch.object(im,'fetch') as fetch:self.assertIsNotNone(im.cover_candidate(entry,im.metrics())[0]);fetch.assert_not_called()
        self.assertGreater(im.score({'source':'cover','width':1200,'height':1500,'text_coverage':.01}),im.score(google()))
    def test_ocr_union_and_thumbnail_budget(self):
        self.assertEqual(im.rectangle_coverage([(0,0,60,50),(40,0,60,50)],100,100),.5)
        self.assertEqual(im.rectangle_coverage([(-10,-10,20,20)],100,100),.01)
        raw=Image.effect_noise((2000,2400),100).convert('RGB');out=io.BytesIO();raw.save(out,'JPEG')
        thumb,size=im.thumbnail(out.getvalue());self.assertLessEqual(len(thumb),64000);self.assertLessEqual(size[0],540)
    def test_photo_failure_advances_to_site_and_negative_results_are_cached(self):
        row=self.venue(self.save(self.a),self.a)[0];place=im.load_entries(self.a['user'],[row['id']])[1][str(row['place_id'])]
        with self.assertLogs('worker.imagery',level='INFO') as captured,patch.object(im,'google_candidates',return_value=[google()]),patch.object(im,'google_bytes',side_effect=ValueError('broken')),patch.object(im,'site_candidate',return_value=[] ) as site,patch.object(im,'commons_candidates',return_value=[]):
            self.assertEqual(im.venue_candidates(place,'restaurant',im.metrics()),[]);self.assertEqual(site.call_count,1)
            place=im.load_entries(self.a['user'],[row['id']])[1][str(row['place_id'])]
            self.assertEqual(im.venue_candidates(place,'restaurant',im.metrics()),[]);self.assertEqual(site.call_count,1)
        self.assertTrue(any('broken' in line and 'google' in line for line in captured.output))
        self.assertTrue(any('no_usable_image' in line and 'site' in line for line in captured.output))
    def test_hash_collision_rejects_unrelated_venue_and_allows_same_place(self):
        a=self.venue(self.save(self.a,'a'),self.a)[0];b=self.venue(self.save(self.a,'b'),self.a,'Other')[0]
        candidate={'source':'cover','content_hash':'abc'}
        self.assertTrue(im.claim_image(candidate,a));self.assertTrue(im.claim_image(candidate,a));self.assertFalse(im.claim_image(candidate,b))
    def test_owner_choice_persists_reresolution_merge_and_isolation(self):
        row=self.venue(self.save(self.a),self.a)[0];url='/items/'+str(row['id'])
        self.assertEqual(self.client.patch(url,headers=self.a['headers'],json={'image_choice':'google:0'}).status_code,200)
        self.assertEqual(self.client.patch(url,headers=self.b['headers'],json={'image_choice':'cover'}).status_code,404)
        self.assertEqual(self.client.post('/imagery/resolve',headers=self.b['headers'],json={'entry_ids':[str(row['id'])]}).status_code,404)
        with patch.object(im,'google_candidates',return_value=[google()]),patch.object(im,'google_bytes',return_value=photo()):
            self.assertEqual(self.resolve([row])['items'][str(row['id'])]['selection']['reason'],'Owner choice retained')
        from api.main import merge_library
        with connect() as conn:
            merge_library(conn,self.a['user'],self.b['user'])
            self.assertEqual(conn.execute('select image_choice from entries where user_id=%s',(self.b['user'],)).fetchone()['image_choice'],'google:0')
    def test_map_context_does_not_request_google_photos(self):
        row=self.venue(self.save(self.a),self.a)[0]
        with patch.object(im,'google_candidates') as google,patch.object(im,'site_candidate',return_value=[]),patch.object(im,'commons_candidates',return_value=[]):self.resolve([row],context='map');google.assert_not_called()
    def test_public_fetch_guard(self):
        for url in ['file:///etc/passwd','http://127.0.0.1/a','http://169.254.169.254/latest','https://user:pass@example.com/a','http://localhost/a']:
            with self.assertRaises(ValueError):im.public_url(url)

    def usable_cover(self):
        raw=photo('blue');asset=im.store_thumb(raw)
        return {'key':'cover','source':'cover','width':1200,'height':1500,'asset':asset,'content_hash':im.digest(raw),'thumbnail_bytes':len(raw),'attribution':{'label':'Creator'},'license':'Creator source'}

    def test_card_cover_precedence_skips_all_venue_calls(self):
        row=self.venue(self.save(self.a),self.a)[0];cover=self.usable_cover()
        with patch.object(im,'cover_candidate',return_value=(cover,None)),patch.object(im,'acquire') as acquire:
            value=self.resolve([row],gallery=False)['items'][str(row['id'])]
        acquire.assert_not_called();self.assertEqual(value['selected']['source'],'cover')

    def test_nonplace_linked_to_place_still_uses_only_cover(self):
        row=self.venue(self.save(self.a),self.a)[0]
        self.client.patch('/items/'+str(row['id']),headers=self.a['headers'],json={'content_type':'workout','attributes':{'muscle_group':['arms']}})
        with patch.object(im,'cover_candidate',return_value=(self.usable_cover(),None)),patch.object(im,'acquire') as acquire:
            value=self.resolve([row])['items'][str(row['id'])]
        acquire.assert_not_called();self.assertEqual(value['selected']['source'],'cover')

    def test_live_google_gallery_then_cover_and_failure_degrades(self):
        row=self.venue(self.save(self.a),self.a)[0];cover=self.usable_cover()
        with patch.object(im,'cover_candidate',return_value=(cover,None)),patch.object(im,'acquire',return_value=[google()]),patch.object(im,'google_bytes',return_value=photo()):
            value=self.resolve([row],context='detail')['items'][str(row['id'])]
            self.assertEqual([c['source'] for c in value['gallery']],['google','cover'])
        with patch.object(im,'cover_candidate',return_value=(cover,None)),patch.object(im,'acquire',return_value=[google()]),patch.object(im,'google_bytes',side_effect=RuntimeError('unavailable')):
            value=self.resolve([row],context='detail')['items'][str(row['id'])]
            self.assertEqual(value['selected']['source'],'cover')

    def test_text_heavy_cover_falls_through_to_venue_sources(self):
        row=self.venue(self.save(self.a),self.a)[0]
        commons={**self.usable_cover(),'source':'commons','key':'commons:1'}
        with patch.object(im,'cover_candidate',return_value=(None,'cover_text_above_25_percent')),patch.object(im,'acquire',return_value=[commons]) as acquire:
            value=self.resolve([row],gallery=False)['items'][str(row['id'])]
        acquire.assert_called_once();self.assertEqual(value['selected']['source'],'commons')

    def test_library_gallery_never_requests_google(self):
        row=self.venue(self.save(self.a),self.a)[0]
        with patch.object(im,'google_candidates') as google,patch.object(im,'site_candidate',return_value=[]),patch.object(im,'commons_candidates',return_value=[]):
            self.resolve([row],context='library',gallery=True)
        google.assert_not_called()

    def test_gallery_thumbnails_have_bounded_distinct_keys_and_owner_cleanup(self):
        row=self.venue(self.save(self.a),self.a)[0];cover=self.usable_cover()
        site={**cover,'source':'site','key':'site:1','_bytes':photo('green')}
        from worker.storage import _thumbnail
        with patch.object(im,'cover_candidate',return_value=(cover,None)),patch.object(im,'acquire',return_value=[site]),patch('worker.storage._thumbnail',wraps=_thumbnail) as thumbnail:
            value=self.resolve([row])['items'][str(row['id'])]
        self.assertEqual([c['source'] for c in value['gallery']],['cover','site'])
        self.assertEqual({call.args[1] for call in thumbnail.call_args_list},{str(row['id'])+'-cover',str(row['id'])+'-site'})
        from api.account_deletion import request_deletion
        request_deletion(self.a['user'])
        with connect() as conn:keys=conn.execute('select object_keys from account_deletions where user_id=%s',(self.a['user'],)).fetchone()['object_keys']
        for variant in ('','-cover','-site','-commons-0','-commons-1','-commons-2'):
            for suffix in ('jpg','webp'):self.assertIn('thumbs/'+str(row['id'])+variant+'.'+suffix,keys)

    def test_distinct_reel_covers_at_same_place_do_not_share_render_bytes(self):
        rows=[self.venue(self.save(self.a,str(i)),self.a)[0] for i in range(2)]
        blue=self.usable_cover();raw=photo('green')
        green={**blue,'asset':im.store_thumb(raw),'content_hash':im.digest(raw)}
        with patch.object(im,'cover_candidate',side_effect=[(blue,None),(green,None)]):
            result=self.resolve(rows,gallery=False)
        images=[result['items'][str(row['id'])]['gallery'][0]['uri'] for row in rows]
        self.assertNotEqual(*images)

    def test_multiple_commons_gallery_images_have_distinct_bounded_keys(self):
        row=self.venue(self.save(self.a),self.a)[0];cover=self.usable_cover()
        candidates=[{**cover,'source':'commons','key':'commons:'+str(i),'_bytes':photo(color)} for i,color in enumerate(['red','green','blue'])]
        from worker.storage import _thumbnail
        with patch.object(im,'cover_candidate',return_value=(None,'unavailable')),patch.object(im,'acquire',return_value=candidates),patch('worker.storage._thumbnail',wraps=_thumbnail) as thumbnail:
            self.resolve([row])
        self.assertEqual({call.args[1] for call in thumbnail.call_args_list},{str(row['id'])+'-commons-'+str(i) for i in range(3)})

    def test_commons_identity_license_and_thumbnail_are_kept_together(self):
        document={'query':{'pages':{'42':{'pageid':42,'title':'File:Example Museum Test City.jpg','imageinfo':[{'width':1800,'height':1200,'thumburl':'https://upload.wikimedia.org/example.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Example','extmetadata':{'ImageDescription':{'value':'Example Museum in Test City'},'Artist':{'value':'<a>Jane Photographer</a>'},'LicenseShortName':{'value':'CC BY-SA 4.0'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0/'}}}]}}}}
        with patch.object(im,'fetch',side_effect=[(json.dumps(document).encode(),'https://commons.wikimedia.org'),(photo(),'https://upload.wikimedia.org/example.jpg')]):
            candidates=im.commons_candidates({'name':'Example Museum','city':'Test City'},'culture',im.metrics())
        self.assertEqual(len(candidates),1);self.assertIn('Jane Photographer',candidates[0]['attribution']['label']);self.assertEqual(candidates[0]['license'],'CC BY-SA 4.0')
        self.assertTrue((im.cache_dir()/(candidates[0]['asset']+'.jpg')).exists())
        with patch.object(im,'fetch') as fetch:self.assertEqual(im.commons_candidates({'name':'Cafe','city':'Test City'},'cafe',im.metrics()),[]);fetch.assert_not_called()
    def test_site_is_proxy_only_and_resolution_masks_never_request_photos(self):
        with patch.object(im,'google_details',return_value={'websiteUri':'https://venue.example/'}),patch.object(im,'fetch',side_effect=[(b'<meta property="og:image" content="/food.jpg">','https://venue.example/'),(photo(),'https://venue.example/food.jpg')]):
            candidate=im.site_candidate({'id':'place'},im.metrics())[0]
        # websiteUri is Google content: neither it nor the derived image URL may persist.
        persisted={k:v for k,v in candidate.items() if not k.startswith('_')}
        self.assertNotIn('venue.example',json.dumps(persisted));self.assertIn('asset',candidate);self.assertIn('_bytes',candidate)
        from worker.places import FIELD_MASK
        self.assertNotIn('photos',FIELD_MASK)

    def test_shared_cache_survives_an_empty_second_instance(self):
        raw=photo(size=(360,270));key=im.store_thumb(raw)
        with tempfile.TemporaryDirectory() as other,patch.object(im,'cache_dir',return_value=__import__('pathlib').Path(other)):
            self.assertEqual(im.cached_thumb(key),raw)
            self.assertEqual(im.image_bytes({'source':'cover','asset':key},im.metrics()),raw)
            self.assertIsNone(im.cached_thumb('../private'))
        with connect() as conn:
            self.assertTrue(conn.execute("select relrowsecurity from pg_class where oid='image_assets'::regclass").fetchone()['relrowsecurity'])
        with self.assertRaises(ValueError):im.store_thumb(b'x'*100001)
