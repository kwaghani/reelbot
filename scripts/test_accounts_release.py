"""Run this release's contracts on a fresh disposable local Postgres database."""
import json
import os
import subprocess
import sys
from urllib.parse import quote
from uuid import uuid4
import psycopg
from psycopg import sql

values=dict(item.split('=',1) for item in json.loads(subprocess.check_output(
    ['docker','inspect','reelbot-audit-db','--format','{{json .Config.Env}}'],text=True)))
base='postgresql://postgres:'+quote(values['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55439/'
name='reelbot_accounts_release_test_'+uuid4().hex[:8]
with psycopg.connect(base+'postgres',autocommit=True) as conn:
    conn.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
try:
    env={**os.environ,'TEST_DATABASE_URL':base+name}
    code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-p','test_accounts_sync.py'],env=env)
    if not code:code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-p','test_release_contract.py'],env=env)
    if not code:code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-p','test_accounts_release_integrity.py'],env=env)
finally:
    with psycopg.connect(base+'postgres',autocommit=True) as conn:
        conn.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))
raise SystemExit(code)
