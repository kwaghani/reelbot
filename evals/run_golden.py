"""Run real URLs through the actual worker. Missing human labels fail acceptance."""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,sys,time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from dotenv import load_dotenv
from worker.db import connect,enqueue
from worker.worker import process
ROOT=Path(__file__).resolve().parents[1]

def one(fixture,owner):
    with connect() as conn:
        saved=enqueue(conn,owner,fixture['url'])
        conn.execute("update saves set status='processing',started_at=now(),attempts=1 where id=%s",(saved['id'],))
    start=time.monotonic()
    child=subprocess.Popen([sys.executable,'-m','worker.worker','--save',str(saved['id'])],cwd=ROOT,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try: child.wait(timeout=55)
    except subprocess.TimeoutExpired:
        import signal
        os.killpg(child.pid,signal.SIGKILL);child.wait()
        from worker.worker import finish
        from worker.pipeline import new_metrics
        with connect() as conn: cost=conn.execute('select cost from saves where id=%s',(saved['id'],)).fetchone()['cost']
        finish(saved['id'],'failed',{**new_metrics(),**cost},'Processing timed out. You can retry.')
    elapsed=time.monotonic()-start
    with connect() as conn:
        result=conn.execute('select status,error_reason,cost,raw_signals from saves where id=%s',(saved['id'],)).fetchone()
        produced=conn.execute('''select coalesce(p.name,u.candidate->>'name') as name,u.needs_review,u.candidate,p.google_place_id
            from user_places u left join places p on p.id=u.place_id where save_id=%s''',(saved['id'],)).fetchall()
    signals=result.pop('raw_signals')
    result['signals']={key:{'characters':len(str(signals.get(key,''))),
        'sha256':hashlib.sha256(str(signals.get(key,'')).encode()).hexdigest()}
        for key in ('caption','ocr','transcript')}
    result['unavailable_signals']=signals.get('unavailable',{})
    for venue in produced:
        venue.pop('candidate',None)
    return {**fixture,'produced':produced,'result':result,'elapsed_seconds':round(elapsed,3)}

def score(rows):
    from worker.places import normalized
    tp=fp=found=expected_count=0
    for row in rows:
        if not row.get('labels_verified'):row['pass']=False;row['failure']='Human labeling is incomplete.';continue
        expected=row['expected'];expected_count+=len(expected)
        matched=set()
        for place in row['produced']:
            index=next((i for i,e in enumerate(expected) if normalized(e) in normalized(place['name']) or normalized(place['name']) in normalized(e)),None)
            if index is not None:matched.add(index)
            if not place['needs_review']:
                if index is not None:tp+=1
                else:fp+=1
        found+=len(matched)
        row['pass']=len(matched)==len(expected) and (bool(expected) or row['result']['status']=='no_places_found')
    precision=tp/(tp+fp) if tp+fp else None
    recall=found/expected_count if expected_count else None
    verified=sum(bool(r.get('labels_verified')) for r in rows)
    distribution=Counter(r['category'] for r in rows)
    required={'caption':6,'on_screen':4,'spoken':3,'listicle':3,'no_place':2,'chain':2}
    composition=len(rows)==20 and distribution==required and any(r['category']=='listicle' and len(r['expected'])>=5 for r in rows)
    passed=composition and verified==20 and precision is not None and precision>=.85 and recall is not None and recall>=.75
    return {'precision':precision,'recall':recall,'true_positive_resolved':tp,'false_positive_resolved':fp,
            'found_labeled_venues':found,'labeled_venues':expected_count,'verified_rows':sum(bool(r.get('labels_verified')) for r in rows),
            'distribution':dict(distribution),'required_distribution':required,
            'required_distribution_met':composition,'acceptance':'PASS' if passed else 'FAIL: verified labels, composition, or accuracy thresholds are not satisfied.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixtures',default='evals/golden.json');parser.add_argument('--output',default='audit-evidence/golden-results.json');args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    url=os.environ['DATABASE_URL']
    if urlsplit(url).hostname not in {'127.0.0.1','localhost'} or 'test' not in urlsplit(url).path:raise SystemExit('Use a disposable loopback test database.')
    fixtures=json.loads((ROOT/args.fixtures).read_text())
    with connect() as conn:
        owner=conn.execute('insert into users(device_id) values(%s) returning id',('evaluation-'+str(uuid4()),)).fetchone()['id']
    with ThreadPoolExecutor(max_workers=3) as pool: rows=list(pool.map(lambda f:one(f,owner),fixtures))
    metrics=score(rows)
    costs=[r['result']['cost'] for r in rows];calls=sum(c.get('places_calls',0) for c in costs);hits=sum(c.get('cache_hits',0) for c in costs)
    output={'metrics':metrics,'cost':{'average_usd':sum(c.get('estimated_usd',0) for c in costs)/max(1,len(costs)),
            'cache_hit_rate':hits/max(1,hits+calls),'places_calls':calls,'cache_hits':hits,'basis':'Measured provider usage × configured rates; local compute excluded.'},'rows':rows}
    (ROOT/args.output).write_text(json.dumps(output,indent=2,default=str));print(json.dumps({'metrics':metrics,'cost':output['cost'],'states':[r['result']['status'] for r in rows]}))
if __name__=='__main__':main()
