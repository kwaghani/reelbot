#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
REELBOT_PYTHON="${REELBOT_PYTHON:-.venv/bin/python}"
if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  echo 'Set TEST_DATABASE_URL to a disposable loopback database whose name contains audit.' >&2
  exit 1
fi
"$REELBOT_PYTHON" -m unittest discover -s tests -v
"$REELBOT_PYTHON" api/smoke_test.py
npm --prefix listener test
npm --prefix app test
npm --prefix app run typecheck
"$REELBOT_PYTHON" -m compileall -q api worker tests evals extract.py
node --check listener/index.js
