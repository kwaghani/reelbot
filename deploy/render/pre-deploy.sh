#!/usr/bin/env bash
set -euo pipefail

python -m db.migrate --dry-run
python -m db.migrate
