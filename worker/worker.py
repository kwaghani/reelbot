"""Durable personal ingestion with an enforced process deadline and one automatic retry."""
from __future__ import annotations
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from psycopg.types.json import Jsonb
from worker.db import connect, claim, store_candidate, save_hash
from worker.pipeline import collect_signals, extract_candidates, new_metrics, price_metrics
from worker.places import resolve
from worker.reel_urls import canonical_reel_url
LOG = logging.getLogger('reelbot.worker')
ROOT = Path(__file__).resolve().parents[1]


def canonical_source(url):
    # Redirects are resolved only by the main service, never by the extension.
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname
    if host in {'vm.tiktok.com','vt.tiktok.com'} or '/t/' in url:
        from worker.media import public_urlopen, request_headers
        from urllib.request import Request
        with public_urlopen(Request(url,headers=request_headers()),timeout=8) as response:
            return canonical_reel_url(response.geturl())
    return canonical_reel_url(url)


def checkpoint(save_id,metrics,signals=None):
    with connect() as conn:
        conn.execute('''update saves set cost=%s,raw_signals=coalesce(%s,raw_signals),updated_at=now()
            where id=%s and status='processing' ''',(Jsonb(price_metrics(metrics)),Jsonb(signals) if signals else None,save_id))


def finish(save_id,status,metrics,error=None):
    with connect() as conn:
        conn.execute('''update saves set status=%s,error_reason=%s,resolved_at=now(),updated_at=now(),cost=%s,
            retry_at=case when %s='failed' and attempts<2 then now()+interval '15 seconds' else null end
            where id=%s and status='processing' ''',(status,error,Jsonb(price_metrics(metrics)),status,save_id))
        conn.execute('''update jobs set status=s.status,error_reason=s.error_reason,updated_at=now()
            from saves s where jobs.save_id=s.id and s.id=%s''',(save_id,))
        conn.execute('insert into events(user_id,save_id,kind,detail) select user_id,id,\'save_finished\',%s from saves where id=%s',
                     (Jsonb({'status':status,'cost':metrics}),save_id))
    LOG.info('save_result %s',json.dumps({'save_id':str(save_id),'status':status,'cost':metrics}))


def process(save_id):
    metrics = new_metrics()
    try:
        with connect() as conn:
            save = conn.execute('select * from saves where id=%s',(save_id,)).fetchone()
        if not save or save['status']!='processing':
            return
        url = canonical_source(save['source_url'])
        with connect() as conn:
            duplicate = conn.execute('select id from saves where url_hash=%s and id<>%s',
                (save_hash(save['user_id'],url),save_id)).fetchone()
            if duplicate:
                # The canonical reel already has durable work. Remove this redundant shortlink row.
                conn.execute('delete from saves where id=%s',(save_id,))
                return
            conn.execute('update saves set source_url=%s,url_hash=%s where id=%s',
                         (url,save_hash(save['user_id'],url),save_id))
        with tempfile.TemporaryDirectory(prefix='reelbot-') as directory:
            signals = collect_signals(url,directory,metrics)
            checkpoint(save_id,metrics,signals)
            candidates = extract_candidates(signals,metrics)
            checkpoint(save_id,metrics,{**signals,'candidates':candidates})
            if not candidates:
                finish(save_id,'no_places_found',metrics)
                return
            # Persist every candidate before any address lookup, so interrupted work remains reviewable.
            with connect() as conn:
                for candidate in candidates:
                    store_candidate(conn,save,candidate,None,min(candidate['confidence'],0.5),'Address lookup is pending.')
            for candidate in candidates:
                try:
                    with connect() as conn:
                        place,confidence,reason = resolve(conn,candidate,metrics)
                        store_candidate(conn,save,candidate,place,confidence,reason)
                except Exception as exc:
                    LOG.warning('Place lookup unavailable: %s',type(exc).__name__)
                    with connect() as conn:
                        store_candidate(conn,save,candidate,None,min(candidate['confidence'],0.5),
                                        'Address lookup is unavailable. Retry to confirm this venue.')
                checkpoint(save_id,metrics)
            with connect() as conn:
                review = conn.execute('select exists(select 1 from user_places where save_id=%s and needs_review) as value',(save_id,)).fetchone()['value']
            finish(save_id,'needs_review' if review else 'resolved',metrics)
    except Exception as exc:
        LOG.exception('Save failed: %s',save_id)
        # Avoid exposing provider response bodies, credentials or infrastructure addresses.
        reason = 'The source could not be processed. Check that it is public, then retry.'
        if isinstance(exc, RuntimeError) and str(exc).startswith(('The source provided','Extraction did not finish')):
            reason = str(exc)
        finish(save_id,'failed',metrics,reason)


def run_one():
    with connect() as conn:
        save = claim(conn)
    if not save:
        return False
    started = time.monotonic()
    child = subprocess.Popen([sys.executable,'-m','worker.worker','--save',str(save['id'])],cwd=ROOT,start_new_session=True)
    try:
        code = child.wait(timeout=55)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL)
        child.wait()
        code = -1
    if code:
        with connect() as conn:
            current = conn.execute('select cost from saves where id=%s',(save['id'],)).fetchone()
        metrics = {**new_metrics(),**(current['cost'] if current else {})}
        finish(save['id'],'failed',metrics,'Processing timed out. You can retry.' if code==-1 else 'Processing was interrupted. You can retry.')
    LOG.info('worker_elapsed save=%s seconds=%.3f',save['id'],time.monotonic()-started)
    return True


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if len(sys.argv)==3 and sys.argv[1]=='--save':
        process(sys.argv[2]); return
    while True:
        try:
            if not run_one(): time.sleep(1)
        except Exception:
            LOG.exception('Worker unavailable; durable queue retained')
            time.sleep(3)

if __name__=='__main__': main()
