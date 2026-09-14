#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
stamp="$(date -u +%Y-%m-%d)"
key="backups/${stamp}.sql.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if command -v pg_dump >/dev/null 2>&1; then
  pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$tmp/backup.sql.gz"
else
  # Compose is the documented local substitute; deployed cron images include
  # postgresql-client and never take this path.
  docker compose exec -T postgres pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$tmp/backup.sql.gz"
fi
python - "$key" "$tmp/backup.sql.gz" <<'PY'
from pathlib import Path
import sys
from urllib.parse import urlsplit
from config import settings, psycopg_database_url
from worker.storage import put, get_url, exists
key, path = sys.argv[1:]
put(key, Path(path).read_bytes(), "application/gzip")
local_database = urlsplit(psycopg_database_url(settings().database_url)).hostname in {"localhost", "127.0.0.1", "::1"}
if (not local_database and not get_url(key).startswith("https://")) or not exists(key):
    raise RuntimeError("Backup was not persisted to R2; refusing to report success or prune old backups")
PY

# Keys are date-addressed; deleting each date older than 30 days is idempotent.
python <<'PY'
from datetime import date, timedelta
from worker.storage import delete
for days_ago in range(31, 366):
    delete(f"backups/{date.today() - timedelta(days=days_ago):%Y-%m-%d}.sql.gz")
PY
printf 'backup uploaded: %s\n' "$key"
