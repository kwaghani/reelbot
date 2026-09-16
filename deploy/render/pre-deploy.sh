#!/usr/bin/env bash
set -euo pipefail

# Releases prepare and verify schema explicitly before deploying either writer.
# Never execute the ownership/retention purge merely because a service deploys.
python -m deploy.render.check_schema
