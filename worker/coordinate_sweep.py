"""Independent expiry enforcement. No refresh module, provider key or network call."""
import json
import logging
import time
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from worker.db import connect

LOG = logging.getLogger(__name__)


def run(*, at=None):
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        # Never lock behind a provider request: the renewal job holds an advisory
        # lock, not the place row, while networking. One UPDATE is atomic.
        rows = conn.execute("""update places set lat=null,lng=null,coords_fetched_at=null
            where (lat is not null or lng is not null or coords_fetched_at is not null)
            and (coords_fetched_at is null or coords_fetched_at<=%s-interval '30 days'
                 or coords_fetched_at>%s) returning id""", (at, at)).fetchall()
        for row in rows:
            conn.execute("insert into retention_events(kind,place_id,detail) values('coordinates_deleted',%s,'{}')", (row['id'],))
            LOG.info('coordinates_deleted place_id=%s reason=expired_or_unknown_lease', row['id'])
        if rows:
            conn.execute("select set_config('reelbot.seed_sync','on',true)")
            conn.execute('update entries set updated_at=now() where place_id=any(%s::uuid[]) and deleted_at is null', ([str(r['id']) for r in rows],))
        # Old city-centroid cache is Google data too; runtime no longer fills it.
        centroids = conn.execute('delete from city_bias_cache returning city_key').fetchall()
        totals = {'places_nulled': len(rows), 'legacy_centroids_deleted': len(centroids)}
        conn.execute("insert into retention_runs(job,finished_at,stats) values('sweep',%s,%s)", (at, Jsonb(totals)))
    LOG.info('coordinate_deletion_sweep %s', totals)
    return totals


def next_delay():
    # Wake at the nearest lease deadline as well as the hourly heartbeat.
    # Logical reads also suppress expired coordinates if this process is delayed.
    with connect() as conn:
        row=conn.execute("select extract(epoch from min(coords_fetched_at)+interval '30 days'-clock_timestamp()) delay from places where coords_fetched_at is not null").fetchone()
    return max(.1,min(3600,float(row['delay']))) if row['delay'] is not None else 3600


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    while True:
        try: print(json.dumps(run()), flush=True)
        except Exception: LOG.exception('coordinate_deletion_sweep_failed')
        if '--daily' not in sys.argv: break
        # Enforce more often than daily: a once-daily sweep alone can retain
        # content for nearly 31 days. This process is independent of refresh.
        try: delay=next_delay()
        except Exception: delay=60
        time.sleep(delay)
