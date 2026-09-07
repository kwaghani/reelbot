"""Exercise 40 public source URLs. Provisional source labels never pass acceptance."""
from __future__ import annotations
import argparse, hashlib, json, os, signal, subprocess, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from dotenv import load_dotenv
from worker.db import connect, enqueue
ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'resolved','needs_review','no_content_found','failed'}

def one(fixture, owner, log_dir):
    with connect() as conn:
        saved=enqueue(conn,owner,fixture['url'])
        conn.execute("update saves set status='processing',started_at=now(),attempts=1 where id=%s",(saved['id'],))
    start=time.monotonic()
    with (log_dir/(fixture['id']+'.log')).open('w') as log:
        child=subprocess.Popen([sys.executable,'-m','worker.worker','--save',str(saved['id'])],cwd=ROOT,start_new_session=True,stdout=log,stderr=log)
        try: child.wait(timeout=55)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL);child.wait()
        with connect() as conn: current=conn.execute('select status,cost from saves where id=%s',(saved['id'],)).fetchone()
        if current['status'] not in TERMINAL:
            from worker.worker import finish
            from worker.pipeline import new_metrics
            finish(saved['id'],'failed',{**new_metrics(),**current['cost']},'Processing timed out. You can retry.')
    elapsed=time.monotonic()-start
    with connect() as conn:
        result=conn.execute('select status,error_reason,cost,raw_signals from saves where id=%s',(saved['id'],)).fetchone()
        produced=conn.execute('''select e.id,e.content_type,e.title,e.summary,e.attributes,e.needs_review,e.review_reason,
            e.place_id,p.name as place_name,p.city,p.formatted_address,p.lat,p.lng
            from entries e left join places p on p.id=e.place_id where save_id=%s order by e.created_at,e.id''',(saved['id'],)).fetchall()
    signals=result.pop('raw_signals')
    result['signals']={key:{'characters':len(str(signals.get(key,''))),'sha256':hashlib.sha256(str(signals.get(key,'')).encode()).hexdigest()} for key in ('caption','ocr','transcript')}
    result['unavailable_signals']=signals.get('unavailable',{})
    row={**fixture,'save_id':str(saved['id']),'produced':produced,'result':result,'elapsed_seconds':round(elapsed,3)}
    print(json.dumps({'id':fixture['id'],'status':result['status'],'entries':len(produced),'seconds':round(elapsed,2)}),flush=True)
    return row

def normalized(value):
    import re,unicodedata
    text=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',text).strip()

def identity_match(expected,actual):
    terms=expected.get('aliases') or [expected.get('title','')]
    if not any(terms): return expected['content_type']==actual['content_type']
    text=normalized(actual['title']+' '+(actual.get('place_name') or '')).replace(' ','')
    return any(normalized(term).removeprefix('the ').replace(' ','') in text for term in terms if term)

def attribute_equal(expected,actual):
    if isinstance(expected,list): return isinstance(actual,list) and {normalized(v) for v in expected}=={normalized(v) for v in actual}
    return normalized(expected)==normalized(actual)

def score(rows):
    from worker.registry import registry
    data=registry(); produced=correct=found=expected_total=attrs=attrs_correct=resolved=place_correct=review=silent=0
    reasons=Counter()
    for row in rows:
        expected=row['expected'];expected_total+=len(expected);used=set();matched=[]
        for actual in row['produced']:
            produced+=1;review+=bool(actual['needs_review'])
            reasons.update(filter(None,(actual.get('review_reason') or '').split(';')))
            # More specific names win (e.g. iPhone 16 Plus before iPhone 16).
            candidates=sorted((i for i,e in enumerate(expected) if i not in used and identity_match(e,actual)),key=lambda i:len(expected[i].get('title','')),reverse=True)
            index=candidates[0] if candidates else None
            if index is not None:
                used.add(index);match=expected[index];same=match['content_type']==actual['content_type'];correct+=same;found+=same
                matched.append({'expected_index':index,'produced_id':str(actual['id']),'correct_type':same})
                if same:
                    for key,spec in data[match['content_type']]['attributes'].items():
                        if spec.get('required') and key in match['attributes']:
                            attrs+=1;attrs_correct+=attribute_equal(match['attributes'][key],actual['attributes'].get(key))
            if actual['content_type']=='place' and actual.get('place_id'):
                resolved+=1
                if index is not None and expected[index]['content_type']=='place':
                    city=expected[index].get('city')
                    place_correct+=not city or normalized(city) in normalized((actual.get('city') or '')+' '+(actual.get('formatted_address') or ''))
        missing=[i for i in range(len(expected)) if i not in used]
        if row['result']['status']!='failed': silent+=len(missing)
        provisional=len(used)==len(expected) and len(row['produced'])==len(expected) and all(m['correct_type'] for m in matched)
        if not expected: provisional=row['category']=='empty' and row['result']['status']=='no_content_found'
        row.update(matches=matched,missed_expected_indices=missing,provisional_match=provisional,passed=bool(provisional and row.get('labels_verified')),failure=None if row.get('labels_verified') else 'Full independent labels are incomplete; provisional results cannot pass acceptance.')
    distribution=Counter(r['category'] for r in rows)
    required={'place':10,'workout':6,'recipe':6,'product':4,'style_home':4,'media_learning':4,'empty':4,'mixed':2}
    verified=all(r.get('labels_verified') for r in rows)
    classification=correct/produced if produced else 0;attribute_precision=attrs_correct/attrs if attrs else 0;place_precision=place_correct/resolved if resolved else 0;recall=found/expected_total if expected_total else 0
    checks={'composition':distribution==required and sum('listicle' in r.get('tags',[]) for r in rows)>=3 and any('five_plus' in r.get('tags',[]) and len(r['expected'])>=5 for r in rows) and sum('chain' in r.get('tags',[]) for r in rows)>=2 and verified,
        'classification_at_least_85_percent':verified and classification>=.85,'required_attribute_precision_at_least_80_percent':verified and attribute_precision>=.8,'place_precision_at_least_85_percent':verified and place_precision>=.85,'recall_at_least_75_percent':verified and recall>=.75,
        'zero_silent_drops':verified and silent==0,'four_empty_reels':all(r['result']['status']=='no_content_found' and not r['produced'] for r in rows if r['category']=='empty'),
        'five_plus_listicle':all(len(r['produced'])>=5 for r in rows if 'five_plus' in r.get('tags',[])),
        'both_mixed_have_two_types':all(len({e['content_type'] for e in r['produced']})>=2 for r in rows if r['category']=='mixed'),
        'review_rate_under_20_percent':produced>0 and review/produced<.2,'terminal_under_60_seconds':all(r['result']['status'] in TERMINAL and r['elapsed_seconds']<60 for r in rows)}
    return {'classification_accuracy':classification,'required_attribute_precision':attribute_precision,'place_precision':place_precision,'recall':recall,'provisional_only':not verified,'verified_rows':sum(bool(r.get('labels_verified')) for r in rows),'produced_entries':produced,'expected_entries':expected_total,'matched_correct_type':found,'labeled_required_attributes':attrs,'correct_required_attributes':attrs_correct,'resolved_places':resolved,'correct_resolved_places':place_correct,'needs_review_rate':review/produced if produced else None,'review_reasons':dict(reasons),'silent_drops':silent,'distribution':dict(distribution),'checks':{k:'PASS' if v else 'FAIL' for k,v in checks.items()},'acceptance':'PASS' if all(checks.values()) else 'FAIL'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixtures',default='evals/golden.json');parser.add_argument('--output',default='/tmp/reelbot-content-golden-results.json');parser.add_argument('--workers',type=int,default=3);args=parser.parse_args()
    load_dotenv(ROOT/'.env');url=os.environ['DATABASE_URL']
    if urlsplit(url).hostname not in {'127.0.0.1','localhost'} or 'test' not in urlsplit(url).path:raise SystemExit('Use a disposable loopback test database.')
    fixtures=json.loads((ROOT/args.fixtures).read_text());log_dir=Path('/tmp/reelbot-content-golden-logs');log_dir.mkdir(exist_ok=True)
    with connect() as conn:owner=conn.execute('insert into users(device_id) values(%s) returning id',('evaluation-'+str(uuid4()),)).fetchone()['id']
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:rows=list(pool.map(lambda f:one(f,owner,log_dir),fixtures))
    metrics=score(rows);costs=[r['result']['cost'] for r in rows];by_type=defaultdict(lambda:{'save_equivalents':0,'llm_usd':0,'total_usd':0})
    for row,cost in zip(rows,costs):
        counts=Counter(e['content_type'] for e in row['produced']) or {'no_entries':1}
        for kind,n in counts.items():
            share=n/sum(counts.values());by_type[kind]['save_equivalents']+=share;by_type[kind]['llm_usd']+=share*cost.get('llm_usd',0);by_type[kind]['total_usd']+=share*cost.get('estimated_usd',0)
    for bucket in by_type.values():
        bucket['mean_llm_usd']=bucket['llm_usd']/bucket['save_equivalents'];bucket['mean_total_usd']=bucket['total_usd']/bucket['save_equivalents']
    cost={'total_usd':sum(c.get('estimated_usd',0) for c in costs),'average_usd':sum(c.get('estimated_usd',0) for c in costs)/max(1,len(costs)),'prompt_cache_hit_rate':sum(c.get('llm_cache_hits',0) for c in costs)/max(1,sum(c.get('llm_requests',0) for c in costs)),'llm_requests':sum(c.get('llm_requests',0) for c in costs),'places_calls':sum(c.get('places_calls',0) for c in costs),'place_cache_hits':sum(c.get('cache_hits',0) for c in costs),'by_type':dict(by_type),'basis':'Measured tokens/requests times configured rates; local compute excluded. Mixed saves allocated by entry count.'}
    cost['usage_unavailable_requests']=sum(c.get('llm_usage_unavailable_requests',0) for c in costs)
    cost['complete']=cost['usage_unavailable_requests']==0
    if not cost['complete']: cost['basis']='Known usage lower bound; provider usage unavailable for some requests. '+cost['basis']
    from worker.registry import registry_version, candidate_schema
    configuration={'registry_sha256':registry_version(),'response_schema_sha256':hashlib.sha256(json.dumps(candidate_schema(),sort_keys=True).encode()).hexdigest(),
        'pipeline_sha256':hashlib.sha256((ROOT/'worker/pipeline.py').read_bytes()).hexdigest(),'fixtures_sha256':hashlib.sha256((ROOT/args.fixtures).read_bytes()).hexdigest()}
    output={'configuration':configuration,'metrics':metrics,'cost':cost,'rows':rows};Path(args.output).write_text(json.dumps(output,indent=2,default=str)+'\n');print(json.dumps({'metrics':metrics,'cost':cost}),flush=True)
if __name__=='__main__':main()
