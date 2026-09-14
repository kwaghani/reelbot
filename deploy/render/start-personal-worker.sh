#!/usr/bin/env bash
set -euo pipefail
python -m deploy.render.check_schema
exec python -m worker.worker
