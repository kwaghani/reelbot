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
RETENTION_EXPAND = '20260915000000_google_retention_expand'


def apply(conn, phase):
    conn.execute("set local lock_timeout='5s'")
    conn.execute("set local statement_timeout='30s'")
    conn.execute('select pg_advisory_xact_lock(735016)')
    versions = {row['id'] for row in conn.execute('select id from schema_migrations')}
    if '20260911150000_shared_image_cache' not in versions:
        raise RuntimeError('Expected the verified production baseline; stop and review the migration ledger')
    marker = {'accounts-expand': ACCOUNT_EXPAND, 'accounts-activate': ACCOUNT_VERSION,
              'retention-expand': RETENTION_EXPAND}[phase]
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
    conn.execute('insert into schema_migrations(id,report) values(%s,%s)',
                 (marker, Jsonb({'phase': phase, 'expansion_only': True})))
    return {'phase': phase, 'applied': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('accounts-expand', 'accounts-activate', 'retention-expand'))
    parser.add_argument('--apply', action='store_true', help='Without this flag, execute and roll back a rehearsal')
    args = parser.parse_args()
    with psycopg.connect(psycopg_database_url(settings().database_url), row_factory=dict_row) as conn:
        result = apply(conn, args.phase)
        if not args.apply:
            conn.rollback()
        print(json.dumps({**result, 'committed': args.apply}))


if __name__ == '__main__':
    main()
