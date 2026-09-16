"""Fail startup before serving an incompatible database."""
from worker.db import connect
with connect() as conn:
    ready = conn.execute("select to_regclass('public.entries') is not null and to_regclass('public.photo_jobs') is not null and to_regclass('public.image_assets') is not null as ready").fetchone()['ready']
    if not ready:
        raise SystemExit('ReelBot database migrations must be applied before starting this version.')
    if not conn.execute("select 1 from schema_migrations where id='20260915000000_google_retention'").fetchone():
        raise SystemExit('Google content retention migration must finish before this version starts.')
print('ReelBot personal-library schema ready.', flush=True)
