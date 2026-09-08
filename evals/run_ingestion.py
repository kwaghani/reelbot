"""Run the fixed 30-link ingestion fixture on an isolated local database; no fake signals."""
import json,os,sys,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
url=os.environ.get('INGESTION_EVAL_DATABASE_URL','')
if urlsplit(url).hostname not in ('127.0.0.1','localhost') or 'test' not in urlsplit(url).path:raise SystemExit('Set a disposable loopback INGESTION_EVAL_DATABASE_URL containing test.')
os.environ['DATABASE_URL']=url
from worker.db import connect,enqueue
from worker.registry import sync_registry
from worker.worker import run_one
fixture=json.loads((ROOT/'evals/ingestion.json').read_text())
output=Path(os.environ.get('INGESTION_EVAL_OUTPUT','/tmp/reelbot-ingestion-30-results.json'))
with connect() as conn:
 conn.execute((ROOT/'db/schema.sql').read_text());sync_registry(conn)
 owner=conn.execute('insert into users(device_id) values(%s) returning id',('ingestion-eval-'+str(uuid4()),)).fetchone()['id']
 for entry in fixture:entry['save_id']=str(enqueue(conn,owner,entry['url'])['id'])
lock=threading.Lock()
def record():
 with lock:
  with connect() as conn:
   result=[]
   for entry in fixture:
    saved=conn.execute('select * from saves where id=%s',(entry['save_id'],)).fetchone()
    rows=conn.execute('''select e.title,e.content_type,e.attributes,e.candidate,e.confidence,e.needs_review,e.review_reason,
      p.name as place_name,p.formatted_address,p.lat,p.lng from entries e left join places p on p.id=e.place_id where save_id=%s''',(entry['save_id'],)).fetchall()
    result.append({**entry,'save':saved,'entries':rows})
  output.write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2))
  print('progress',sum(r['save']['status'] not in ('queued','processing') for r in result),'/',len(result),flush=True)
def consume():
 while run_one():record()
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:consume(),range(2)))
record()
