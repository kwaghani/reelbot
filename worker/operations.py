"""Three bounded operational alerts. No personal content enters alert messages."""
import json
import logging
import os
import smtplib
import ssl
import time
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from psycopg.types.json import Jsonb
from worker.db import connect

LOG = logging.getLogger(__name__)
FAILURES = {'failed', 'resolve_failed', 'fetch_blocked', 'fetch_not_found', 'fetch_ok_no_content', 'extraction_empty', 'no_content_found', 'needs_source_info'}

def heartbeat():
    with connect() as conn:
        conn.execute("select pg_advisory_xact_lock(hashtextextended('worker_heartbeat',17))")
        row=conn.execute("select id from events where kind='worker_heartbeat' order by created_at desc limit 1").fetchone()
        if row: conn.execute('update events set created_at=clock_timestamp() where id=%s',(row['id'],))
        else: conn.execute("insert into events(kind) values('worker_heartbeat')")

def snapshot(at=None):
    at=at or datetime.now(timezone.utc)
    with connect() as conn:
        queue=conn.execute("select count(*) depth,min(created_at) oldest from jobs where status in ('queued','processing')").fetchone()
        beat=conn.execute("select max(created_at) at from events where kind='worker_heartbeat'").fetchone()['at']
        completed=conn.execute("select max(created_at) at from events where kind='save_finished'").fetchone()['at']
        outcomes=conn.execute("select detail->>'status' status,count(*) n from events where kind='save_finished' and created_at>=%s-interval '1 hour' group by detail->>'status'",(at,)).fetchall()
        has_sweep=conn.execute("select to_regclass('public.retention_runs') relation").fetchone()['relation']
        sweep=conn.execute("select max(finished_at) at from retention_runs where job='sweep'").fetchone()['at'] if has_sweep else None
        refresh=conn.execute("select max(finished_at) at from retention_runs where job='refresh'").fetchone()['at'] if has_sweep else None
        has_coords=conn.execute("select 1 from information_schema.columns where table_schema='public' and table_name='places' and column_name='coords_fetched_at'").fetchone()
        oldest=conn.execute('select min(coords_fetched_at) at from places').fetchone()['at'] if has_coords else None
    total=sum(row['n'] for row in outcomes); failed=sum(row['n'] for row in outcomes if row['status'] in FAILURES)
    return {'worker_heartbeat_age_seconds': max(0,(at-beat).total_seconds()) if beat else None,
            'queue_depth':queue['depth'],'oldest_queued_at':queue['oldest'],'last_job_completed_at':completed,
            'oldest_coords_fetched_at':oldest,'last_sweep_at':sweep,'last_refresh_at':refresh,'extractions_last_hour':total,'failures_last_hour':failed}

def evaluate(data, at=None):
    at=at or datetime.now(timezone.utc); alerts=[]
    progress=[value for value in (data.get('oldest_queued_at'),data.get('last_job_completed_at')) if value]
    if data['queue_depth'] and (not progress or at-max(progress)>=timedelta(minutes=30)):
        alerts.append(('worker_stalled','No job has completed for 30 minutes while the queue has pending work.'))
    if not data.get('last_sweep_at') or at-data['last_sweep_at']>=timedelta(hours=24):
        alerts.append(('retention_sweep_missed','The coordinate deletion sweep has not completed in 24 hours.'))
    if data['extractions_last_hour'] and data['failures_last_hour']/data['extractions_last_hour']>.30:
        alerts.append(('extraction_error_rate','Extraction failures exceed 30% of completed attempts in the last hour.'))
    return alerts

def message(kind, detail, *, test=False):
    prefix='[TEST — no production incident]' if test else '[ReelBot alert]'
    return {'subject':f'{prefix} {kind.replace("_"," ")}', 'body':detail+'\n\nCheck ReelBot /readyz and the worker logs. No reel URLs, notes, or credentials are included.'}

def send_email(payload):
    if not email_alerts_enabled():
        raise RuntimeError('Email alerts are disabled for this release')
    host=os.environ.get('REELBOT_SMTP_HOST'); sender=os.environ.get('REELBOT_ALERT_FROM'); recipient=os.environ.get('REELBOT_ALERT_TO')
    if not all((host,sender,recipient)): raise RuntimeError('Alert email transport is not configured')
    email=EmailMessage();email['From']=sender;email['To']=recipient;email['Subject']=payload['subject'];email.set_content(payload['body'])
    port=int(os.environ.get('REELBOT_SMTP_PORT','587'))
    # TLS is mandatory; do not downgrade authentication to plaintext.
    transport=smtplib.SMTP_SSL if port==465 else smtplib.SMTP
    with transport(host,port,timeout=10) as smtp:
        if port!=465:smtp.starttls(context=ssl.create_default_context())
        username=os.environ.get('REELBOT_SMTP_USERNAME');password=os.environ.get('REELBOT_SMTP_PASSWORD')
        if username:smtp.login(username,password or '')
        refused=smtp.send_message(email)
        if refused:raise RuntimeError('Alert recipient rejected')

def email_alerts_enabled():
    return os.environ.get('REELBOT_EMAIL_ALERTS_ENABLED', '').lower() == 'true'


def run(send=send_email, at=None):
    at=at or datetime.now(timezone.utc); data=snapshot(at); sent=[]
    if not email_alerts_enabled():
        return {'snapshot':data,'alerts_sent':sent,'email_alerts_enabled':False}
    for kind,detail in evaluate(data,at):
        # Claim a five-minute send lease, then RELEASE the DB transaction before
        # contacting SMTP. Multiple watchdogs share deduplication across restarts.
        with connect() as conn:
            conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,17))',('alert:'+kind,))
            previous=conn.execute("select created_at,detail from events where kind='operations_alert' and detail->>'alert'=%s order by created_at desc limit 1",(kind,)).fetchone()
            if previous and at-previous['created_at']<timedelta(minutes=60 if previous['detail'].get('sent') else 5):continue
            identifier=conn.execute("insert into events(kind,detail,created_at) values('operations_alert',%s,%s) returning id",(Jsonb({'alert':kind,'sent':False}),at)).fetchone()['id']
        try:
            send(message(kind,detail))
            with connect() as conn:conn.execute("update events set detail=detail || '{\"sent\":true}'::jsonb where id=%s",(identifier,))
            sent.append(kind)
        except Exception as exc:LOG.error('alert_delivery_failed alert=%s error_type=%s',kind,type(exc).__name__)
    return {'snapshot':data,'alerts_sent':sent,'email_alerts_enabled':True}

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    while True:
        try:print(json.dumps(run(),default=str),flush=True)
        except Exception as exc:LOG.error('operations_watchdog_failed error_type=%s',type(exc).__name__)
        time.sleep(60)
