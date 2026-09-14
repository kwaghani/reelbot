#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 https://reelbot-api.onrender.com" >&2
  exit 64
fi
base_url="${1%/}"
failed=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2" >&2; failed=1; }

health="$(curl --fail --silent --show-error "$base_url/healthz" || true)"
if [ -n "$health" ]; then pass healthz; else fail healthz 'expected HTTP 200'; fi

ready="$(curl --silent --show-error "$base_url/readyz" || true)"
if [ -n "$ready" ] && python - "$ready" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
deps = payload.get('dependencies', {})
assert deps.get('database', {}).get('healthy') is True
assert deps.get('storage', {}).get('healthy') is True
PY
then pass readyz; else fail readyz 'database and storage must be healthy'; fi

if [ -z "${REELBOT_VERIFY_TOKEN:-}" ]; then
  fail share 'set REELBOT_VERIFY_TOKEN to a disposable device bearer token before running verification'
else
  reel_url="${REELBOT_VERIFY_REEL_URL:-https://www.instagram.com/reel/C0ffee00000/}"
  created="$(curl --silent --show-error --fail -X POST "$base_url/share" -H "Authorization: Bearer $REELBOT_VERIFY_TOKEN" -H 'Content-Type: application/json' --data "{\"url\":\"$reel_url\"}" || true)"
  job_id="$(python - "$created" <<'PY'
import json, sys
try: print(json.loads(sys.argv[1]).get('job_id', ''))
except Exception: print('')
PY
)"
  if [ -z "$job_id" ]; then
    fail share 'POST did not create a durable job'
  else
    pass share
    resolved=''
    for _ in $(seq 1 45); do
      resolved="$(curl --silent --show-error "$base_url/jobs/$job_id" -H "Authorization: Bearer $REELBOT_VERIFY_TOKEN" || true)"
      status="$(python - "$resolved" <<'PY'
import json, sys
try: print(json.loads(sys.argv[1]).get('status', ''))
except Exception: print('')
PY
)"
      case "$status" in resolved|needs_review) break ;; failed|download_failed) break ;; esac
      sleep 2
    done
    case "$status" in
      resolved|needs_review) pass worker-resolution ;;
      *) fail worker-resolution "job ended as ${status:-unknown}" ;;
    esac
    items="$(curl --silent --show-error "$base_url/items" -H "Authorization: Bearer $REELBOT_VERIFY_TOKEN" || true)"
    if python - "$items" <<'PY'
import json, sys
rows = json.loads(sys.argv[1]).get('items', [])
assert any(row.get('place_id') and row.get('lat') is not None and row.get('lng') is not None and (row.get('thumbnail') or row.get('image_url')) for row in rows)
PY
    then pass entry-coordinates-image; else fail entry-coordinates-image 'resolved entry needs coordinates and an image URL'; fi
    # Image URLs may be signed; enforce the card budget without storing an original in the grid.
    image_url="$(python - "$items" <<'PY'
import json, sys
for row in json.loads(sys.argv[1]).get('items', []):
    if row.get('thumbnail') or row.get('image_url'):
        print(row.get('thumbnail') or row.get('image_url')); break
PY
)"
    if [ -n "$image_url" ] && bytes="$(curl --silent --show-error --location "$image_url" | wc -c | tr -d ' ')" && [ "$bytes" -lt 60000 ]; then pass thumbnail-budget; else fail thumbnail-budget 'thumbnail must load and be under 60 KB'; fi
  fi
fi

if [ "$failed" -ne 0 ]; then exit 1; fi
echo 'Production verification passed.'
