#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 backups/<key>.sql.gz <target-database-url>" >&2
  exit 64
fi
key="$1"
target_database="$2"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
url="$(python - "$key" <<'PY'
import sys
from worker.storage import get_url
print(get_url(sys.argv[1]))
PY
)"
if command -v psql >/dev/null 2>&1; then
  curl --fail --silent --show-error --location "$url" | gzip -dc | psql "$target_database" -v ON_ERROR_STOP=1
else
  curl --fail --silent --show-error --location "$url" | gzip -dc | docker compose exec -T postgres psql "$target_database" -v ON_ERROR_STOP=1
fi
printf 'restore completed: %s\n' "$key"
