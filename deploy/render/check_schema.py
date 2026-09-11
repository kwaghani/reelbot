"""Fail startup before serving an incompatible database."""
from worker.db import connect
with connect() as conn:
    ready = conn.execute("select to_regclass('public.entries') is not null and to_regclass('public.photo_jobs') is not null and to_regclass('public.image_assets') is not null as ready").fetchone()['ready']
    if not ready:
        raise SystemExit('ReelBot database migrations must be applied before starting this version.')
print('ReelBot personal-library schema ready.', flush=True)
