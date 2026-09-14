"""Controlled video-card regression, explicitly separate from a real-reel recall benchmark.

Uses the configured extraction provider only with --live-model. No geocoding, no
personal saves, no media persistence in the public platform cache.
"""
import argparse,json,subprocess,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from dotenv import load_dotenv
from worker.compilations import collect_compilation,normalize_name
from worker.pipeline import new_metrics,price_metrics

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--live-model',action='store_true');parser.add_argument('--output',default='compilation-evidence/controlled-suite.json');args=parser.parse_args()
 if not args.live_model:parser.error('Pass --live-model to authorize the configured provider requests.')
 load_dotenv('.env')
 root=Path.home()/'Library/Caches/ReelBot/compilation-fixtures/suite';root.mkdir(parents=True,exist_ok=True)
 names=['elNico','One40','Juniper Table','Cedar Room','Olive Terrace','Maple Kitchen','Willow House','Birch Dining','Laurel Garden','Aspen Table','Sage Terrace','Basil Room','Rowan Kitchen','Hazel Garden','Elm House']
 counts=[3,4,5,6,7,8,9,10,12,15];results=[]
 for case,count in enumerate(counts):
  folder=root/str(count);folder.mkdir(exist_ok=True)
  font=ImageFont.truetype('/System/Library/Fonts/Supplemental/'+('Georgia.ttf' if case%2==0 else 'Arial.ttf'),27)
  selected=(names[case:]+names[:case])[:count];cards=[f'{count} Best Rooftop\nRestaurants\nNew York City',*selected];lines=[]
  for i,label in enumerate(cards):
   color=['#809ba2','#ac7970','#667f63','#8b82b1'][(i+case)%4]
   image=Image.new('RGB',(360,640),color);draw=ImageDraw.Draw(image)
   draw.rectangle((8,115,352,260),fill='white');draw.multiline_text((180,129),label,font=font,fill='black',anchor='ma',align='center')
   for j in range(6):draw.rectangle((j*65,330+j*18,j*65+45,640),fill=['#34434b','#887766','#445588','#776655'][i%4])
   path=folder/f'{i}.png';image.save(path);lines += [f"file '{path}'",'duration 2']
  lines += [f"file '{path}'"];(folder/'frames.txt').write_text('\n'.join(lines))
  video=folder/'control.mp4'
  subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(folder/'frames.txt'),'-vf','fps=12','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
  metrics=new_metrics();start=time.monotonic()
  result=collect_compilation(video,folder,len(cards)*2,{'caption':'Rooftop restaurants in New York City','creator_handle':'city_guide','title':cards[0]},metrics)
  found=[row['venue_name'] for row in result['compilation_candidates']]
  hits=set(map(normalize_name,selected))&set(map(normalize_name,found))
  report={'count':count,'labels_authored_before_extraction':selected,'found':found,'recall':len(hits)/count,'seconds':round(time.monotonic()-start,3),'metrics':price_metrics(metrics),'diagnostics':result['compilation']}
  results.append(report)
  Path(args.output).write_text(json.dumps({'origin':'Synthetic authored video cards, NOT real public compilation reels','on_screen_only':True,'cases':results},indent=2))
  print(count,'venues:',len(hits),'found; frames',report['diagnostics']['scan']['sampled_frames'],'OCR',report['diagnostics']['scan']['ocr_calls'],'vision',report['diagnostics']['scan']['vision_calls'],flush=True)
if __name__=='__main__':main()
