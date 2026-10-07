#!/usr/bin/env bash
# The data a new database starts from: Jev's context (services/seed/jev-context.json) becomes context #1 when the
# database has none. Run it once after the first `docker compose up -d --build`; safe to run again (a database that
# already has a context is left as it is). See services/common/seed.py.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm --no-deps -T -e PYTHONPATH=/app rtm-api python -m common.seed load
