import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from worker.compilations import classify,sample_times,timeline,deduplicate,MAX_FRAMES,MAX_OCR,MAX_VISION,Budget,scan
from worker.pipeline import new_metrics
class CompilationTests(unittest.TestCase):
 def test_counts_and_single_venue_classification(self):
  for n in (3,5,7,10,12,15):
   for text in (f'{n} Spectacular Rooftop Restaurants New York City',f'Top {n} places in London',f'{n} Best cafes in Paris'):
    self.assertEqual(classify({'title':text})['expected_venue_count'],n)
  singles=['BCD Tofu House in Los Angeles','Best ramen at Ichiran','Dinner at elNico','One40 rooftop dinner','Tartine Bakery in San Francisco','Visit Meduza Mediterrania','Vees Cafe breakfast','Alisa Wine Friends','Hike Sturtevant Falls','Karachi BBQ Tonight LA','The Louvre museum','Central Park picnic','Sunset at Malibu Beach','Coffee at Blue Bottle','Lunch at Dishoom','A room at The Hoxton','Dinner at Le Bernardin','Visit Uffizi Gallery','A stay at Aman Tokyo','Brunch at Buvette']
  self.assertEqual(sum(classify({'caption':x})['is_compilation'] for x in singles),0)
  self.assertTrue(classify({'creator_handle':'the_rooftopguide'})['is_compilation'])
  self.assertTrue(classify({},cuts=5)['is_compilation'])
 def test_two_pass_total_frame_admission_cap_and_no_repeated_times(self):
  for duration in (17.2,90,180,500):
   cuts=list(range(0,int(min(duration,180)),2))
   first=sample_times(duration,cuts,limit=90);second=sample_times(duration,cuts,deeper=True,seen=first,limit=min(30,MAX_FRAMES-len(first)))
   self.assertLessEqual(len(first)+len(second),120);self.assertFalse(set(first)&set(second));self.assertTrue(all(0<=t<min(180,duration) for t in first+second))
 def test_timeline_excludes_intro_outro_and_preserves_speech_timestamps(self):
  evidence=[{'timestamp':t,'text':text,'source':'ocr','frame_ref':{'file':str(t)}} for t,text in [(0.3,'7 Spectacular Rooftop Restaurants New York City'),(3,'elNico'),(5,'One40'),(15,'Follow for more')]]
  rows=timeline(17,[2.7,4.7,14.7],evidence,[{'start':3,'end':4,'text':'elNico'}])
  self.assertEqual(rows[0]['excluded'],'intro');self.assertEqual(rows[-1]['excluded'],'outro')
  venue=next(r for r in rows if r['ocr_text']==['elNico']);self.assertEqual(venue['transcript_segments'][0]['start'],3)
 def test_no_placeholder_or_duplicate_venues(self):
  rows=deduplicate([{'venue_name':x,'confidence':.8} for x in ('elNico','El Nico','One40','Unidentified place','New York')])
  self.assertEqual([r['venue_name'] for r in rows],['elNico','One40'])
 def test_ocr_and_vision_global_caps_survive_deeper_pass(self):
  from PIL import Image
  def frame(command,**kw):Image.new('RGB',(64,64),'white').save(command[-1])
  counter=iter(range(300))
  with tempfile.TemporaryDirectory() as tmp,patch('worker.compilations.subprocess.run',side_effect=frame),patch('worker.compilations.text_gate',side_effect=lambda image:(True,format(next(counter)*257,'032x'),9)),patch('worker.compilations.hash_distance',return_value=20),patch('worker.compilations.ocr_frame',return_value=('',0)),patch('worker.fetch.media.vision_text',return_value='') as vision:
   b=Budget();m=new_metrics();scan('unused',Path(tmp),180,list(range(0,180,2)),b,m);scan('unused',Path(tmp),180,list(range(0,180,2)),b,m,deeper=True)
   self.assertEqual(b.frames,MAX_FRAMES);self.assertEqual(b.ocr,MAX_OCR);self.assertEqual(b.vision,MAX_VISION);self.assertEqual(vision.call_count,15)
 def test_credit_failure_stops_remaining_segment_requests(self):
  import anthropic,httpx
  from worker.compilations import extract_segments
  response=httpx.Response(400,request=httpx.Request('POST','https://api.anthropic.com/v1/messages'))
  error=anthropic.BadRequestError('credit balance too low',response=response,body={'error':{'message':'Your credit balance is too low'}})
  segments=[{'excluded':None,'ocr_text':['elNico'],'transcript_text':'','t_start':i,'t_end':i+1,'sources':['ocr'],'frame_refs':[]} for i in range(25)]
  cache={}
  with patch('worker.pipeline.extract_candidates',side_effect=error) as model:
   self.assertEqual(extract_segments(segments,{'city_hint':'New York'},new_metrics(),cache),[])
   self.assertLessEqual(model.call_count,3)
  self.assertEqual(cache['_provider_error']['code'],'provider_credit_exhausted')
