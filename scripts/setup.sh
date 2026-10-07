#!/usr/bin/env bash
# First-time setup, once the servers run (docker-compose.servers.yml, or the main branch's stack):
#   1. the pipeline's database (PIPELINE_DB), with every migration in schema/ applied in order
#   2. the RabbitMQ vhost (RABBITMQ_VHOST); the services declare their own queues
# The MinIO bucket is made by the first upload. Safe to run again: a database that exists is left as it is (a new
# migration on an existing database is applied by hand: see README.md).
#
# Needs Docker only (psql runs in a throwaway postgres container on the servers' network). Settings come from the
# environment, else from .env, else the defaults in .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env yet: cp .env.example .env, and fill it in" >&2; exit 1; }

setting() {        # $1 from the environment, else from .env (the last KEY=value line), else the default $2
  local v="${!1:-}"
  [ -n "$v" ] || v="$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)"
  echo "${v:-$2}"
}
NET="$(setting DOCKER_NETWORK samb-ocr_default)"
PGHOST="$(setting POSTGRES_HOST postgres)"; PGPORT="$(setting POSTGRES_PORT 5432)"
PGUSER="$(setting POSTGRES_USER ocr)"; PGPASS="$(setting POSTGRES_PASSWORD '')"
DB="$(setting PIPELINE_DB ocr_rtm)"
RMQ_URL="$(setting RABBITMQ_CONSOLE_URL http://localhost:15672)"
RMQ_USER="$(setting RABBITMQ_USER ocr)"; RMQ_PASS="$(setting RABBITMQ_PASSWORD '')"
VHOST="$(setting RABBITMQ_VHOST rtm)"

psql() {
  docker run --rm -i --network "$NET" -e PGPASSWORD="$PGPASS" postgres:17 \
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -v ON_ERROR_STOP=1 -q "$@"
}

echo "Postgres ($PGHOST on network $NET)…"
for i in $(seq 1 30); do psql -d postgres -c "select 1" >/dev/null 2>&1 && break; sleep 2
  [ "$i" = 30 ] && { echo "Postgres doesn't answer: are the servers running?" >&2; exit 1; }; done

if [ -n "$(psql -d postgres -tAc "select 1 from pg_database where datname = '$DB'")" ]; then
  echo "  database $DB exists: left as it is"
else
  psql -d postgres -c "CREATE DATABASE \"$DB\""
  for f in schema/satellite-documents.sql schema/0*.sql; do
    echo "  $f"
    psql -d "$DB" < "$f"
  done
  echo "  database $DB: $(psql -d "$DB" -tAc "select count(*) from information_schema.tables
                                             where table_schema in ('satellite', 'staging')") tables"
fi

echo "RabbitMQ ($RMQ_URL)…"
for i in $(seq 1 30); do curl -sf -u "$RMQ_USER:$RMQ_PASS" "$RMQ_URL/api/overview" >/dev/null && break; sleep 2
  [ "$i" = 30 ] && { echo "RabbitMQ's management API doesn't answer at $RMQ_URL" >&2; exit 1; }; done
curl -sf -u "$RMQ_USER:$RMQ_PASS" -X PUT "$RMQ_URL/api/vhosts/$VHOST" >/dev/null
curl -sf -u "$RMQ_USER:$RMQ_PASS" -X PUT -H "content-type: application/json" \
     -d '{"configure": ".*", "write": ".*", "read": ".*"}' "$RMQ_URL/api/permissions/$VHOST/$RMQ_USER" >/dev/null
echo "  vhost $VHOST ready for $RMQ_USER"

echo "Done. Next: docker compose up -d --build, then ./scripts/seed.sh   (then open http://localhost:$(setting WEB_PORT 3002))"
