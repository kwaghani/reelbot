"""One durable acquisition job per globally resolved place; no personal data in jobs."""
from __future__ import annotations
import logging,time
from worker.db import connect
from worker.imagery import acquire,metrics,failure
LOG=logging.getLogger(__name__)

def enqueue_photo(conn,place_id,force=False):
    if force:
        conn.execute("update places set imagery='{}',image_failure_reason=null where id=%s",(place_id,))
        conn.execute("insert into photo_jobs(place_id) values(%s) on conflict(place_id) do update set status='queued',attempts=0,retry_at=now(),updated_at=now() where photo_jobs.status!='processing'",(place_id,))
    else:conn.execute('insert into photo_jobs(place_id) values(%s) on conflict do nothing',(place_id,))

def run_one():
    with connect() as conn:
        conn.execute("update photo_jobs set status='failed',retry_at=now(),error_reason='lease_expired' where status='processing' and started_at<now()-interval '120 seconds'")
        job=conn.execute("""update photo_jobs set status='processing',attempts=attempts+1,started_at=now(),updated_at=now()
            where place_id=(select place_id from photo_jobs where status in ('queued','failed') and attempts<3 and retry_at<=now()
            order by retry_at for update skip locked limit 1) returning *""").fetchone()
        if not job:return False
        place=conn.execute('select * from places where id=%s',(job['place_id'],)).fetchone()
        kind=conn.execute("select venue_kind from entries where place_id=%s order by created_at limit 1",(job['place_id'],)).fetchone()
    stats=metrics();error=None
    try:
        result=acquire(place,(kind or {}).get('venue_kind') or 'other',stats)
        if not result:error=';'.join(stats['failures']) or 'no_usable_venue_image'
    except Exception as exc:error=failure(exc)
    with connect() as conn:
        conn.execute("update photo_jobs set status=%s,error_reason=%s,retry_at=now()+(%s * interval '1 second'),updated_at=now() where place_id=%s",('failed' if error else 'complete',error,[60,900,86400][min(job['attempts']-1,2)],job['place_id']))
    LOG.info('photo_job place=%s attempt=%s failure=%s',job['place_id'],job['attempts'],error)
    return True

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            if not run_one():time.sleep(2)
        except Exception:LOG.exception('Photo queue retained');time.sleep(5)
