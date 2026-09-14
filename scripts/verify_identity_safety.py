"""Five bounded production regressions in a disposable, isolated device library."""
import argparse
import json
import secrets
import time
import uuid
import requests

CASES=[
 ('Four Barrel Coffee','https://www.instagram.com/reel/DKcN5chRxm2/','375 Valencia St, San Francisco, CA 94103, USA'),
 ('Madison Bar and Grill','https://www.tiktok.com/@imnickmayorga/video/7638770721098861855','1316 Washington St, Hoboken, NJ 07030, USA'),
 ("Cappone's",'https://www.tiktok.com/@imnickmayorga/video/7641652990679600414','11 Abingdon Square, New York, NY 10014, USA'),
 ('Golden Diner','https://www.tiktok.com/@treatyoselfeverywhere/video/7646815284082363678','123 Madison St, New York, NY 10002, USA'),
 ('sponsor_not_venue','https://www.tiktok.com/@tastebywill/video/7639109279198252318',None),
]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('base_url')
    args=parser.parse_args()
    base=args.base_url.rstrip('/')
    if not base.startswith('https://'):raise ValueError('Use an explicit HTTPS service URL')
    client=requests.Session()
    token=secrets.token_hex(32)
    client.headers['Authorization']='Bearer '+token
    def api(method,path,**kwargs):
        response=client.request(method,base+path,timeout=45,**kwargs)
        response.raise_for_status()
        return response.json()
    def emit(kind,value):print(kind,json.dumps(value,default=str),flush=True)
    api('POST','/devices',json={'device_id':str(uuid.uuid4()),'token':token})
    failures=[]
    try:
        for name,url,address in CASES:
            created=api('POST','/share',json={'url':url})
            deadline=time.monotonic()+240
            previous=None
            while True:
                state=api('GET','/jobs/'+created['job_id'])
                if state['status']!=previous:emit('STATE',{'name':name,'status':state['status']});previous=state['status']
                if state['status'] not in {'queued','processing'} or time.monotonic()>deadline:break
                time.sleep(5)
            saved=api('GET','/debug/saves/'+created['id'])
            entries=[e for e in api('GET','/items')['items'] if e['save_id']==created['id']]
            signals=saved.get('raw_signals') or {}
            if address:
                good=[e for e in entries if e['title']==name and e.get('formatted_address')==address and e.get('place_id')]
                passed=len(good)==1 and state['status']=='resolved'
            else:
                passed=state['status']=='needs_review' and bool(entries) and all(not e.get('place_id') and e['title'].casefold()!='toast' for e in entries)
                passed=passed and any(s['name'].casefold()=='toast' for s in signals.get('sponsor_candidates',[]))
                passed=passed and not any(v['name'].casefold()=='toast' for v in signals.get('venue_candidates',[]))
            report={'case':name,'passed':passed,'status':state['status'],'job_id':created['job_id'],
                    'sponsors':signals.get('sponsor_candidates',[]),'venues':signals.get('venue_candidates',[]),
                    'entries':[{k:e.get(k) for k in ('title','place_id','formatted_address','venue_kind','needs_review','review_reason')} for e in entries],
                    'geocoding_candidates':saved.get('diagnostics',{}).get('places_queries',[]),
                    'error_reason':saved.get('error_reason')}
            emit('REGRESSION',report)
            if not passed:failures.append(name)
        emit('READINESS',api('GET','/readyz'))
    finally:
        # Only the fresh random identity created above can be removed here.
        emit('DISPOSABLE_CLEANUP',api('DELETE','/account'))
    if failures:raise SystemExit('Regressions failed: '+', '.join(failures))

if __name__=='__main__':main()
