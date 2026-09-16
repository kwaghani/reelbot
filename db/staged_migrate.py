"""Explicit release steps; never called implicitly by service startup.

Schema expansion does not enable triggers, rewrite personal records or drop
columns. The existing all-in-one migration is unsuitable for the live overlap.
"""
import argparse
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from config import settings, psycopg_database_url

ROOT = Path(__file__).parent / 'migrations'
ACCOUNT_VERSION = '20260914150000_accounts_sync'
ACCOUNT_EXPAND = ACCOUNT_VERSION + '_expand'
RETENTION_VERSION = '20260915000000_google_retention'
RETENTION_EXPAND = RETENTION_VERSION + '_expand'
# The additive expansion records its own marker; the runtime that reads those
# columns requires the release marker, so activation is a separate step.
RETENTION_COLUMNS = ('coords_fetched_at', 'coords_retry_at', 'extracted_name', 'venue_kind_source')


def apply(conn, phase):
    conn.execute("set local lock_timeout='5s'")
    conn.execute("set local statement_timeout='30s'")
    conn.execute('select pg_advisory_xact_lock(735016)')
    versions = {row['id'] for row in conn.execute('select id from schema_migrations')}
    if '20260911150000_shared_image_cache' not in versions:
        raise RuntimeError('Expected the verified production baseline; stop and review the migration ledger')
    marker = {'accounts-expand': ACCOUNT_EXPAND, 'accounts-activate': ACCOUNT_VERSION,
              'retention-expand': RETENTION_EXPAND, 'retention-activate': RETENTION_VERSION}[phase]
    if marker in versions:
        return {'phase': phase, 'already_applied': True}
    if phase == 'accounts-expand':
        source = (ROOT / (ACCOUNT_VERSION + '.sql')).read_text()
        boundary = '-- DELETE statements from old clients/workers become tombstones as well.'
        if source.count(boundary) != 1:
            raise RuntimeError('Accounts migration boundary changed; review before release')
        conn.execute(source.split(boundary)[0], prepare=False)
        # Protect unused tables without installing activation triggers.
        for table in ('account_sessions', 'sync_changes', 'sync_mutations', 'account_deletions'):
            conn.execute(f'alter table {table} enable row level security')
            conn.execute(f'revoke all on {table} from public')
            for role in ('anon', 'authenticated'):
                if conn.execute('select 1 from pg_roles where rolname=%s', (role,)).fetchone():
                    conn.execute(f'revoke all on {table} from {role}')
    elif phase == 'accounts-activate':
        if ACCOUNT_EXPAND not in versions:
            raise RuntimeError('Ship and verify accounts expansion before activation')
        conn.execute((ROOT / (ACCOUNT_VERSION + '.sql')).read_text(), prepare=False)
        return {'phase': phase, 'applied': True}
    elif phase == 'retention-expand':
        if ACCOUNT_VERSION not in versions:
            raise RuntimeError('Verify the accounts release before retention expansion')
        conn.execute((ROOT / '20260915000000_google_retention.sql').read_text(), prepare=False)
    elif phase == 'retention-activate':
        # Records the release marker only. It scrubs nothing and drops nothing;
        # contraction stays in db.retention_migrate and is a later release.
        if RETENTION_EXPAND not in versions:
            raise RuntimeError('Ship and verify retention expansion before activation')
        present = {row['column_name'] for row in conn.execute(
            """select column_name from information_schema.columns
               where table_schema='public' and table_name='places'""").fetchall()}
        missing = [name for name in RETENTION_COLUMNS if name not in present]
        if missing:
            raise RuntimeError('Retention expansion is incomplete; missing ' + ', '.join(missing))
        for table in ('retention_runs', 'retention_events', 'retention_object_purge'):
            if not conn.execute('select to_regclass(%s) is not null ok', ('public.' + table,)).fetchone()['ok']:
                raise RuntimeError('Retention expansion is incomplete; missing table ' + table)
        if conn.execute("select attnotnull from pg_attribute where attrelid='places'::regclass and attname='lat'").fetchone()['attnotnull']:
            raise RuntimeError('Coordinates must be nullable before the lease runtime starts')
        return {'phase': phase, 'applied': True, 'recorded': RETENTION_VERSION} if conn.execute(
            'insert into schema_migrations(id,report) values(%s,%s) returning id',
            (RETENTION_VERSION, Jsonb({'phase': phase, 'expansion_only': True}))).fetchone() else {}
    conn.execute('insert into schema_migrations(id,report) values(%s,%s)',
                 (marker, Jsonb({'phase': phase, 'expansion_only': True})))
    return {'phase': phase, 'applied': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('accounts-expand', 'accounts-activate', 'retention-expand', 'retention-activate'))
    parser.add_argument('--apply', action='store_true', help='Without this flag, execute and roll back a rehearsal')
    args = parser.parse_args()
    with psycopg.connect(psycopg_database_url(settings().database_url), row_factory=dict_row) as conn:
        result = apply(conn, args.phase)
        if not args.apply:
            conn.rollback()
        print(json.dumps({**result, 'committed': args.apply}))


if __name__ == '__main__':
    main()
