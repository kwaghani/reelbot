"""Backfill stable identities and retry prior failures without removing personal entries."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from psycopg.types.json import Jsonb
from worker.db import connect,save_hash
from worker.url_resolve import resolve_url
from worker.reel_urls import canonical_reel_url
from worker.save_identity import bind_identity
from worker.fetch.http import FetchError
from worker.ingestion_states import message

RETRY={'failed','no_content_found','resolve_failed','needs_source_info','fetch_ok_no_content','extraction_empty','fetch_blocked','needs_review'}
def backfill():
    with connect() as conn:
        rows=conn.execute('''select s.*,j.payload->>'url' as original_job_url from saves s
            left join jobs j on j.save_id=s.id order by s.created_at''').fetchall()
    results=[]
    for saved in rows:
        source=saved['original_job_url'] or saved['source_url']
        try:canonical_reel_url(source)
        except ValueError:source=saved['source_url']
        row={'save_id':str(saved['id']),'source_url':source,'before':saved['status']}
        try:
            resolved=resolve_url(source)
            with connect() as conn:
                current=conn.execute('select * from saves where id=%s for update',(saved['id'],)).fetchone()
                if not current:row['action']='already_consolidated';results.append(row);continue
                current,merged=bind_identity(conn,current,resolved)
                row.update(canonical_url=resolved['canonical_url'],platform_video_id=resolved['platform_video_id'],target_id=str(current['id']),merged=merged)
                conn.execute('insert into save_source_urls(save_id,user_id,source_url) values(%s,%s,%s) on conflict do nothing',(current['id'],current['user_id'],source))
                if not merged:conn.execute('update saves set source_url=%s,source_hash=%s where id=%s',(source,save_hash(current['user_id'],source),current['id']))
                if current['status'] in RETRY:
                    conn.execute("""update saves set status='queued',attempts=0,blocked_attempts=0,retry_at=null,
                        raw_signals=raw_signals-'extraction_registry_version',error_reason=null,updated_at=now() where id=%s""",(current['id'],))
                    conn.execute("update jobs set status='queued',error_reason=null,updated_at=now() where save_id=%s",(current['id'],))
                    row['action']='queued'
                else:row['action']='identity_backfilled'
        except FetchError as error:
            with connect() as conn:
                conn.execute("update saves set status='resolve_failed',error_reason=%s,retry_at=null,diagnostics=diagnostics || %s,updated_at=now() where id=%s",
                    (message('resolve_failed'),Jsonb({'resolution_error':{'detail':error.detail,'hops':getattr(error,'trace',[])}}),saved['id']))
            row.update(action='resolve_failed',detail=error.detail)
        results.append(row)
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    results=backfill();Path(args.output).write_text(json.dumps(results,default=str,indent=2))
    print(json.dumps({'saves':len(results),'queued':sum(r['action']=='queued' for r in results),'resolve_failed':sum(r['action']=='resolve_failed' for r in results)}))
