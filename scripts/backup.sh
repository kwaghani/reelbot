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
from worker.storage import put
key, path = sys.argv[1:]
put(key, Path(path).read_bytes(), "application/gzip")
PY

# Keys are date-addressed; deleting each date older than 30 days is idempotent.
python <<'PY'
from datetime import date, timedelta
from worker.storage import delete
for days_ago in range(31, 366):
    delete(f"backups/{date.today() - timedelta(days=days_ago):%Y-%m-%d}.sql.gz")
PY
printf 'backup uploaded: %s\n' "$key"
