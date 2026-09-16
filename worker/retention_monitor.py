"""Independent watchdog: logs actionable alerts; also powers app diagnostics."""
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from worker.db import connect

LOG = logging.getLogger(__name__)


def snapshot(at=None):
    at = at or datetime.now(timezone.utc)
    with connect() as conn:
        counts = conn.execute("""select count(*) filter(where lat is not null and lng is not null
            and coords_fetched_at>%s-interval '30 days' and coords_fetched_at<=%s) as valid_coordinates,
            count(*) filter(where google_place_id is not null and (coords_fetched_at is null or coords_fetched_at<=%s-interval '25 days')) as awaiting_refresh,
            count(*) filter(where lat is null or lng is null) as without_coordinates,
            count(*) filter(where coords_fetched_at<=%s-interval '28 days') as overdue_28_days,
            min(coords_fetched_at) as oldest_coords_fetched_at from places""", (at, at, at, at)).fetchone()
        runs = conn.execute('select distinct on(job) job,finished_at,stats from retention_runs order by job,finished_at desc').fetchall()
        latest = {r['job']: {'at': r['finished_at'], 'stats': r['stats']} for r in runs}
        deleted = conn.execute("select count(*) n from retention_events where kind='coordinates_deleted'").fetchone()['n']
        cost = conn.execute("select coalesce(sum((detail->>'estimated_usd')::numeric),0) usd,coalesce(sum((detail->>'provider_calls')::int),0) calls from retention_events where kind='coordinate_refresh' and created_at>=%s-interval '24 hours'", (at,)).fetchone()
    missed = 'sweep' not in latest or at - latest['sweep']['at'] >= timedelta(hours=24)
    alerts = ([] if not counts['overdue_28_days'] else ['Coordinate renewal is overdue.']) + (['Coordinate deletion sweep has not completed in 24 hours.'] if missed else [])
    return {**counts, 'places_nulled_total': deleted, 'last_runs': latest, 'last_24h': {'estimated_usd': float(cost['usd']), 'provider_calls': cost['calls']}, 'alerts': alerts}


def run():
    result = snapshot()
    for alert in result['alerts']: LOG.error('retention_alert %s', alert)
    return result


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    while True:
        try: print(json.dumps(run(), default=str), flush=True)
        except Exception: LOG.exception('retention_monitor_unavailable')
        if '--watch' not in sys.argv: break
        time.sleep(60 * 60)
