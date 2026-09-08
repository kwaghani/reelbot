"""Durable personal ingestion, bounded work and explicit platform retry states."""
from __future__ import annotations
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from psycopg.types.json import Jsonb
from worker.db import connect, claim, store_candidate, save_hash
from worker.pipeline import collect_signals, new_metrics, price_metrics
from worker.places import resolve
from worker.reel_urls import canonical_reel_url
LOG = logging.getLogger('reelbot.worker')
ROOT = Path(__file__).resolve().parents[1]


def checkpoint(save_id,metrics,signals=None,diagnostics=None):
    with connect() as conn:
        conn.execute("""update saves set cost=%s,raw_signals=coalesce(%s,raw_signals),
            diagnostics=diagnostics || %s,updated_at=now() where id=%s and status='processing' """,
            (Jsonb(price_metrics(metrics)),Jsonb(signals) if signals else None,Jsonb(diagnostics or {}),save_id))


def finish(save_id,status,metrics,error=None):
    from worker.ingestion_states import message
    from worker.fetch.http import settings
    with connect() as conn:
        saved=conn.execute("select * from saves where id=%s and status='processing' for update",(save_id,)).fetchone()
        if not saved:return
        blocked=saved['blocked_attempts']+(status=='fetch_blocked');delay=None
        if status=='fetch_blocked':
            delays=settings()['retry_seconds']
            if blocked<=len(delays):delay=delays[blocked-1]
        elif status=='failed' and saved['attempts']<2:delay=15
        if status not in ('resolved','needs_review') and not error:error=message(status,saved['platform'],retrying=delay is not None)
        conn.execute("""update saves set status=%s,error_reason=%s,resolved_at=now(),updated_at=now(),cost=%s,
            blocked_attempts=%s,retry_at=case when %s::int is null then null else now()+(%s * interval '1 second') end
            where id=%s""",(status,error,Jsonb(price_metrics(metrics)),blocked,delay,delay,save_id))
        conn.execute("""update jobs set status=s.status,error_reason=s.error_reason,updated_at=now()
            from saves s where jobs.save_id=s.id and s.id=%s""",(save_id,))
        conn.execute("insert into events(user_id,save_id,kind,detail) select user_id,id,'save_finished',%s from saves where id=%s",
                     (Jsonb({'status':status,'cost':metrics}),save_id))
    LOG.info('save_result %s',json.dumps({'save_id':str(save_id),'status':status,'cost':metrics}))


def process(save_id):
    from worker.url_resolve import resolve_url
    from worker.save_identity import bind_identity
    from worker.fetch.http import FetchError
    from worker.fetch.ladder import FETCH_VERSION
    from worker.pipeline import candidates_for
    from worker.registry import registry,registry_version,validate_candidates
    metrics=new_metrics();diagnostics={}
    try:
        with connect() as conn:save=conn.execute('select * from saves where id=%s',(save_id,)).fetchone()
        if not save or save['status']!='processing':return
        # Accumulate retries honestly; this attempt's tier logs remain in raw_signals.
        metrics={**metrics,**save['cost']}
        resolved=resolve_url(save['source_url'])
        with connect() as conn:
            save,merged=bind_identity(conn,save,resolved)
            if merged:
                if save['status'] not in ('resolved','needs_review','queued','processing'):
                    conn.execute("update saves set status='queued',attempts=0,retry_at=null,updated_at=now() where id=%s",(save['id'],))
                return
        with tempfile.TemporaryDirectory(prefix='reelbot-') as directory:
            existing=save['raw_signals']
            if existing.get('extraction_registry_version')==registry_version() and isinstance(existing.get('candidates'),list) and (existing.get('fetch_version')==FETCH_VERSION or 'fetch_state' not in existing):
                signals=existing;candidates=validate_candidates(existing['candidates'])
                metrics['extraction_replays']=metrics.get('extraction_replays',0)+1
                diagnostics['extraction_path']='durable_replay'
            else:
                signals=collect_signals(resolved,directory,metrics)
                checkpoint(save_id,metrics,signals)
                state=signals.get('fetch_state','needs_source_info')
                if state!='fetched':
                    if state=='fetch_ok_no_content':
                        assert not any(signals.get(k,'').strip() for k in ('caption','ocr','transcript'))
                        assert signals.get('fetch_cache_hit') or {1,2,3}<={t['tier'] for t in signals['tier_log']}
                    finish(save_id,state,metrics);return
                candidates=candidates_for(resolved,signals,metrics,diagnostics)
                signals['extraction_registry_version']=registry_version()
            signals['candidates']=candidates
            checkpoint(save_id,metrics,signals,diagnostics)
            if not candidates:finish(save_id,'extraction_empty',metrics);return
            from collections import Counter
            metrics['content_types']=dict(Counter(c['content_type'] for c in candidates))
            data=registry()
            with connect() as conn:
                for candidate in candidates:store_candidate(conn,save,candidate,None,candidate['confidence'])
            for candidate in candidates:
                policy=data[candidate['content_type']]['geo']
                if policy=='never' or not candidate.get('venue_name'):
                    counts=metrics.setdefault('geo_skipped',{});kind=candidate['content_type'];counts[kind]=counts.get(kind,0)+1
                    continue
                if (datetime.now(timezone.utc)-save['started_at']).total_seconds()>133:break
                before=metrics['places_calls']
                lookup={'name':candidate['venue_name'],'city_hint':candidate.get('city_hint') or signals.get('city_hint'),
                        'country_hint':None,'confidence':candidate['confidence'],'poi':candidate.get('poi') or {},
                        'address':candidate.get('address_hint') or (candidate.get('poi') or {}).get('address')}
                try:
                    with connect() as conn:
                        place,confidence,reason=resolve(conn,lookup,metrics)
                        if lookup.get('place_candidates'):candidate['place_candidates']=lookup['place_candidates']
                        store_candidate(conn,save,candidate,place,confidence,reason)
                except Exception as exc:
                    LOG.warning('Place lookup unavailable: %s',type(exc).__name__)
                    with connect() as conn:store_candidate(conn,save,candidate,None,candidate['confidence'],'unresolved_place')
                counts=metrics.setdefault('places_calls_by_type',{});kind=candidate['content_type'];counts[kind]=counts.get(kind,0)+metrics['places_calls']-before
                diagnostics['places_queries']=metrics.get('places_queries',[])
                checkpoint(save_id,metrics,signals,diagnostics)
            with connect() as conn:
                review=conn.execute('select exists(select 1 from entries where save_id=%s and needs_review) as value',(save_id,)).fetchone()['value']
            finish(save_id,'needs_review' if review else 'resolved',metrics)
    except FetchError as exc:
        checkpoint(save_id,metrics,diagnostics={'resolution_error':{'state':exc.state,'detail':exc.detail,'hops':getattr(exc,'trace',[])}})
        finish(save_id,exc.state,metrics)
    except Exception:
        LOG.exception('Save interrupted: %s',save_id)
        checkpoint(save_id,metrics,diagnostics=diagnostics)
        finish(save_id,'failed',metrics,'Processing was interrupted. Your save is kept; you can retry.')


def run_one():
    with connect() as conn:save=claim(conn)
    if not save:return False
    started=time.monotonic()
    child=subprocess.Popen([sys.executable,'-m','worker.worker','--save',str(save['id'])],cwd=ROOT,start_new_session=True)
    try:code=child.wait(timeout=145)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL);child.wait();code=-1
    if code:
        with connect() as conn:current=conn.execute('select cost,raw_signals from saves where id=%s',(save['id'],)).fetchone()
        metrics={**new_metrics(),**(current['cost'] if current else {})}
        # A killed request has unknown provider usage. Never label its cost complete.
        metrics['llm_usage_unavailable_requests']=metrics.get('llm_usage_unavailable_requests',0)+1
        finish(save['id'],'failed',metrics,'Processing timed out. Your save is kept; you can retry.' if code==-1 else 'Processing was interrupted. Your save is kept; you can retry.')
    LOG.info('worker_elapsed save=%s seconds=%.3f',save['id'],time.monotonic()-started)
    return True


def maintain_embeddings():
    from worker.db import items,vector_literal
    from worker.embed import embed_document
    while True:
        try:
            with connect() as conn:
                missing=conn.execute('select id,user_id,updated_at from entries where embedding is null order by created_at limit 8').fetchall()
            for pending in missing:
                with connect() as conn:
                    row=next((r for r in items(conn,pending['user_id']) if r['id']==pending['id']),None)
                if row:
                    text=' '.join([row['title'],row['summary'],json.dumps(row['attributes']),row.get('place_name') or '',row['city'],row['note'],' '.join(f['name'] for f in row['folders'])])
                    vector=vector_literal(embed_document(text))
                    with connect() as conn:
                        conn.execute('update entries set embedding=%s::vector where id=%s and updated_at=%s',(vector,row['id'],pending['updated_at']))
            time.sleep(3)
        except Exception:
            LOG.exception('Search indexing will retry')
            time.sleep(15)


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if len(sys.argv)==2 and sys.argv[1]=='--index':
        maintain_embeddings(); return
    if len(sys.argv)==3 and sys.argv[1]=='--save':
        # A child still expires if its supervising worker is killed.
        signal.signal(signal.SIGALRM,lambda *_: os._exit(124))
        signal.alarm(144)
        process(sys.argv[2]); return
    indexer=subprocess.Popen([sys.executable,'-m','worker.worker','--index'],cwd=ROOT)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            try:
                if indexer.poll() is not None:
                    indexer=subprocess.Popen([sys.executable,'-m','worker.worker','--index'],cwd=ROOT)
                if not run_one(): time.sleep(1)
            except Exception:
                LOG.exception('Worker unavailable; durable queue retained')
                time.sleep(3)
    finally:
        indexer.terminate()
        try:
            indexer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            indexer.kill()
            indexer.wait()

if __name__=='__main__': main()
