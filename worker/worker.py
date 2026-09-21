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
from worker.observability import log_rss, queue_depth
from worker.db import check_pool_capacity
from config import validate_service_config
from worker.retention_policy import safe_candidate
LOG = logging.getLogger('reelbot.worker')
ROOT = Path(__file__).resolve().parents[1]


def checkpoint(save_id,metrics,signals=None,diagnostics=None):
    with connect() as conn:
        if signals:
            conn.execute('update saves set is_compilation=%s,expected_venue_count=%s,extracted_venue_count=%s where id=%s and status=\'processing\'',(bool(signals.get('is_compilation')),signals.get('expected_venue_count'),len(signals.get('compilation_candidates',[])),save_id))
        conn.execute("""update saves set cost=%s,raw_signals=coalesce(%s,raw_signals),
            diagnostics=diagnostics || %s,updated_at=now() where id=%s and status='processing' """,
            (Jsonb(safe_candidate(price_metrics(metrics))),Jsonb(safe_candidate(signals)) if signals else None,Jsonb(safe_candidate(diagnostics or {})),save_id))


def finish(save_id,status,metrics,error=None):
    from worker.ingestion_states import message
    from worker.fetch.http import settings
    with connect() as conn:
        saved=conn.execute("select * from saves where id=%s and deleted_at is null and status='processing' for update",(save_id,)).fetchone()
        if not saved:return
        blocked=saved['blocked_attempts']+(status=='fetch_blocked');delay=None
        if status=='fetch_blocked':
            delays=settings()['retry_seconds']
            if blocked<=len(delays):delay=delays[blocked-1]
        elif status=='failed' and saved['attempts']<2 and not metrics.get('provider_error'):delay=15
        if status not in ('resolved','needs_review') and not error:error=message(status,saved['platform'],retrying=delay is not None)
        conn.execute("""update saves set status=%s,error_reason=%s,resolved_at=now(),updated_at=now(),cost=%s,
            blocked_attempts=%s,retry_at=case when %s::int is null then null else now()+(%s * interval '1 second') end
            where id=%s""",(status,error,Jsonb(safe_candidate(price_metrics(metrics))),blocked,delay,delay,save_id))
        conn.execute("""update jobs set status=s.status,error_reason=s.error_reason,updated_at=now()
            from saves s where jobs.save_id=s.id and s.id=%s""",(save_id,))
        conn.execute("insert into events(user_id,save_id,kind,detail) select user_id,id,'save_finished',%s from saves where id=%s",
                     (Jsonb({'status':status,'cost':safe_candidate(metrics)}),save_id))
    LOG.info('save_result %s',json.dumps({'save_id':str(save_id),'status':status,'cost':safe_candidate(metrics)}))


def process(save_id):
    from worker.url_resolve import resolve_url
    from worker.save_identity import bind_identity
    from worker.fetch.http import FetchError
    from worker.fetch.ladder import FETCH_VERSION
    from worker.pipeline import candidates_for
    from worker.registry import registry,registry_version,validate_candidates
    metrics=new_metrics();diagnostics={}; log_rss('job_start', save_id)
    try:
        with connect() as conn:save=conn.execute('select * from saves where id=%s and deleted_at is null',(save_id,)).fetchone()
        if not save or save['status']!='processing':return
        # Accumulate retries honestly; this attempt's tier logs remain in raw_signals.
        metrics={**metrics,**save['cost']};metrics.pop('provider_error',None)
        resolved=resolve_url(save['source_url'])
        with connect() as conn:
            save,merged=bind_identity(conn,save,resolved)
            if merged:
                if save['status'] not in ('resolved','needs_review','queued','processing'):
                    conn.execute("update saves set status='queued',attempts=0,retry_at=null,updated_at=now() where id=%s",(save['id'],))
                return
        resolved['force_dense']=bool(save.get('force_dense'))
        resolved['expected_venue_count']=save.get('expected_venue_count')
        with tempfile.TemporaryDirectory(prefix='reelbot-') as directory:
            existing=save['raw_signals']
            if not resolved['force_dense'] and not existing.get('compilation',{}).get('provider_error') and existing.get('extraction_registry_version')==registry_version() and isinstance(existing.get('candidates'),list) and (existing.get('fetch_version')==FETCH_VERSION or 'fetch_state' not in existing):
                from worker.venue_identity import condition_rows
                signals=existing;candidates=validate_candidates(existing['candidates'])
                if not signals.get('is_compilation'):candidates=condition_rows(candidates,signals)
                metrics['extraction_replays']=metrics.get('extraction_replays',0)+1
                diagnostics['extraction_path']='durable_replay'
            else:
                signals=collect_signals(resolved,directory,metrics,on_progress=lambda partial:checkpoint(save_id,metrics,partial))
                checkpoint(save_id,metrics,signals)
                state=signals.get('fetch_state','needs_source_info')
                if state!='fetched':
                    if state=='fetch_ok_no_content':
                        assert not any(signals.get(k,'').strip() for k in ('caption','ocr','transcript'))
                        assert signals.get('fetch_cache_hit') or {1,2,3}<={t['tier'] for t in signals['tier_log']}
                    finish(save_id,state,metrics);return
                candidates=candidates_for(resolved,signals,metrics,diagnostics)
                signals['extraction_registry_version']=registry_version()
            # Re-apply role gates even to historical durable/compilation caches.
            from worker.venue_identity import inputs_for,condition_rows
            signals.update(inputs_for(signals).payload())
            if signals.get('is_compilation'):
                safe=[]
                for candidate in candidates:
                    own=candidate.get('segment_signals') or {'caption':candidate.get('evidence','')}
                    safe.extend(condition_rows([candidate],{**own,'sponsor_candidates':signals['sponsor_candidates'],
                        'city_hint':candidate.get('city_hint') or signals.get('city_hint')}))
                candidates=safe
            else:
                candidates=condition_rows(candidates,signals)
            signals['candidates']=candidates
            checkpoint(save_id,metrics,signals,diagnostics)
            if signals.get('is_compilation'):
                # Retire only untouched generated stubs; keep an owner-readable recovery copy.
                with connect() as conn:
                    stubs=conn.execute("""select * from entries e where save_id=%s and user_id=%s and title='Unidentified place'
                        and place_id is null and verified_at is null and note='' and nullif(candidate->>'venue_name','') is null
                        and not exists(select 1 from folder_items fi join folders f on f.id=fi.folder_id where fi.entry_id=e.id and f.kind='custom') for update""",(save_id,save['user_id'])).fetchall()
                    if stubs:
                        recovery=json.loads(json.dumps([{k:v for k,v in row.items() if k!='embedding'} for row in stubs],default=str))
                        conn.execute("update saves set diagnostics=diagnostics || %s where id=%s",(Jsonb({'retired_generated_placeholders':recovery}),save_id))
                        conn.execute('delete from entries where id=any(%s::uuid[])',([str(row['id']) for row in stubs],))
            if not candidates:
                if signals.get('compilation',{}).get('provider_error'):
                    metrics['provider_error']=signals['compilation']['provider_error']
                    finish(save_id,'failed',metrics,'The extraction service is unavailable. Your reel is kept; retry after service is restored.')
                else:finish(save_id,'extraction_empty',metrics)
                return
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
                if (datetime.now(timezone.utc)-save['started_at']).total_seconds()>(340 if signals.get('is_compilation') else 133):break
                before=metrics['places_calls']
                assert policy in {'required','optional'}, 'A geo: never entry reached place resolution'
                assert candidate['content_type']!='workout', 'Workout reached place resolution'
                lookup={'name':candidate['venue_name'],'city_hint':candidate.get('city_hint') or signals.get('city_hint'),
                        'country_hint':None,'confidence':candidate['confidence'],'poi':candidate.get('poi') or {},
                        'venue_kind':candidate['attributes'].get('venue_kind','other'),
                        'category_contexts':candidate.get('category_contexts'),
                        'sponsor_candidates':candidate.get('sponsor_candidates',[]),
                        'handle_only':'handle_only' in candidate.get('review_reasons',[]),
                        'identity_source':candidate.get('identity_source'), 'location_hints':candidate.get('location_hints',[]),
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
            expected=signals.get('expected_venue_count')
            if signals.get('is_compilation') and expected and len(candidates)<expected:
                finish(save_id,'partial_extraction',metrics,f'found_{len(candidates)}_of_{expected}')
            else:finish(save_id,'needs_review' if review else 'resolved',metrics)
    except FetchError as exc:
        checkpoint(save_id,metrics,diagnostics={'resolution_error':{'state':exc.state,'detail':exc.detail,'hops':getattr(exc,'trace',[])}})
        finish(save_id,exc.state,metrics)
    except Exception:
        LOG.exception('Save interrupted: %s',save_id)
        checkpoint(save_id,metrics,diagnostics=diagnostics)
        finish(save_id,'failed',metrics,'Processing was interrupted. Your save is kept; you can retry.')
    finally:
        log_rss('job_end', save_id)


def run_one():
    with connect() as conn:save=claim(conn)
    if not save:return False
    started=time.monotonic()
    child=subprocess.Popen([sys.executable,'-m','worker.worker','--save',str(save['id'])],cwd=ROOT,start_new_session=True)
    try:
        try:code=child.wait(timeout=145)
        except subprocess.TimeoutExpired:
            with connect() as conn:current=conn.execute('select is_compilation,force_dense from saves where id=%s',(save['id'],)).fetchone()
            if current and (current['is_compilation'] or current['force_dense']):code=child.wait(timeout=max(1,355-(time.monotonic()-started)))
            else:raise
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
    from worker.embed import embed_document,entry_document
    while True:
        try:
            with connect() as conn:
                missing=conn.execute('select id,user_id,updated_at from entries where deleted_at is null and embedding is null order by created_at limit 8').fetchall()
            for pending in missing:
                with connect() as conn:
                    row=next((r for r in items(conn,pending['user_id']) if r['id']==pending['id']),None)
                    signals=conn.execute('select raw_signals from saves where id=%s',(row['save_id'],)).fetchone() if row else None
                if row:
                    vector=vector_literal(embed_document(entry_document(row,(signals or {}).get('raw_signals'))))
                    with connect() as conn:
                        conn.execute('update entries set embedding=%s::vector where id=%s and updated_at=%s',(vector,row['id'],pending['updated_at']))
            time.sleep(3)
        except Exception:
            LOG.exception('Search indexing will retry')
            time.sleep(15)


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    validate_service_config()
    check_pool_capacity()
    if len(sys.argv)==2 and sys.argv[1]=='--index':
        maintain_embeddings(); return
    if len(sys.argv)==3 and sys.argv[1]=='--save':
        # A child still expires if its supervising worker is killed.
        signal.signal(signal.SIGALRM,lambda *_: os._exit(124))
        signal.alarm(359)
        process(sys.argv[2]); return
    # Separate heartbeat while the supervisor waits on a bounded extraction.
    import threading
    from worker.operations import heartbeat
    heartbeat_stop=threading.Event()
    def pulse():
        while not heartbeat_stop.is_set():
            try:heartbeat()
            except Exception:LOG.error('worker_heartbeat_unavailable')
            heartbeat_stop.wait(30)
    threading.Thread(target=pulse,daemon=True).start()
    operations=subprocess.Popen([sys.executable,'-m','worker.operations'],cwd=ROOT)
    photos=subprocess.Popen([sys.executable,'-m','worker.photo_jobs'],cwd=ROOT)
    # Signed reel-cover URLs lapse in days; renew them hourly, ahead of expiry.
    covers=subprocess.Popen([sys.executable,'-m','worker.cover_refresh','--watch'],cwd=ROOT)
    indexer=subprocess.Popen([sys.executable,'-m','worker.worker','--index'],cwd=ROOT)
    retention=[subprocess.Popen([sys.executable,'-m',module,flag],cwd=ROOT) for module,flag in [('worker.coordinate_sweep','--daily'),('worker.coordinate_refresh','--daily'),('worker.retention_monitor','--watch')]]
    cleanup=subprocess.Popen([sys.executable,'-m','api.account_deletion'],cwd=ROOT)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_queue_log = 0.0
    try:
        while True:
            try:
                if operations.poll() is not None:operations=subprocess.Popen([sys.executable,'-m','worker.operations'],cwd=ROOT)
                for i,(module,flag) in enumerate([('worker.coordinate_sweep','--daily'),('worker.coordinate_refresh','--daily'),('worker.retention_monitor','--watch')]):
                    if retention[i].poll() is not None:retention[i]=subprocess.Popen([sys.executable,'-m',module,flag],cwd=ROOT)
                if cleanup.poll() is not None:cleanup=subprocess.Popen([sys.executable,'-m','api.account_deletion'],cwd=ROOT)
                if time.monotonic() - last_queue_log >= 30:
                    LOG.info('worker_queue_depth depth=%s concurrency=1', queue_depth()); last_queue_log=time.monotonic()
                if photos.poll() is not None:photos=subprocess.Popen([sys.executable,'-m','worker.photo_jobs'],cwd=ROOT)
                if covers.poll() is not None:covers=subprocess.Popen([sys.executable,'-m','worker.cover_refresh','--watch'],cwd=ROOT)
                if indexer.poll() is not None:
                    indexer=subprocess.Popen([sys.executable,'-m','worker.worker','--index'],cwd=ROOT)
                if not run_one(): time.sleep(1)
            except Exception:
                LOG.exception('Worker unavailable; durable queue retained')
                time.sleep(3)
    finally:
        heartbeat_stop.set()
        operations.terminate()
        try:operations.wait(timeout=5)
        except subprocess.TimeoutExpired:operations.kill();operations.wait()
        for process in retention:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        cleanup.terminate()
        try:cleanup.wait(timeout=5)
        except subprocess.TimeoutExpired:cleanup.kill();cleanup.wait()
        for child in (photos,covers):
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        indexer.terminate()
        try:
            indexer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            indexer.kill()
            indexer.wait()

if __name__=='__main__': main()
