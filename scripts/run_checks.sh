#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
REELBOT_PYTHON="${REELBOT_PYTHON:-.venv/bin/python}"
: "${TEST_DATABASE_URL:?Set a disposable loopback database with test in its name}"
"$REELBOT_PYTHON" -m unittest discover -s tests -v
npm --prefix app test
npm --prefix app run typecheck
"$REELBOT_PYTHON" -m compileall -q api worker tests db evals extract.py
