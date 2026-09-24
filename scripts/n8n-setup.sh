#!/usr/bin/env bash
# Import and publish the intake workflow into the running n8n, then restart it so the webhook registers.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T n8n n8n import:workflow --input=/workflows/intake.workflow.json
docker compose exec -T n8n n8n publish:workflow --id=sambOcrIntake001
docker compose restart n8n
echo "Waiting for n8n…"
for i in $(seq 1 30); do
  if docker compose exec -T ui python -c "import httpx,sys; sys.exit(0 if httpx.get('http://n8n:5678/healthz',timeout=2).status_code==200 else 1)" 2>/dev/null; then
    echo "n8n up. Webhook: http://n8n:5678/webhook/intake"; exit 0; fi
  sleep 2
done
echo "n8n did not come back" >&2; exit 1
