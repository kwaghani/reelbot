#!/usr/bin/env bash
set -euo pipefail

exec python worker/scheduler.py --once
