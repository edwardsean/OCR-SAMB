#!/usr/bin/env bash
# vlm-first's n8n workflows, into v1's running n8n (one n8n serves both; v1's own files are untouched):
#   intake   webhook /webhook/vf-intake → split into pages → one ticket per page   (vf-ui's upload calls it)
#   sweep    every 3 hours: pages still waiting for the AI go back on the queue
#   needs-you every 5 minutes: bundles that newly need a person become a notice (the Review tab's count)
#   lint     every 6 hours: a knowledge claim a person's later correction contradicts is taken out
# n8n mounts only v1's n8n/ folder, so the files are copied in. Re-running replaces them. n8n restarts at the end so
# the webhook and the schedules register (v1's intake is down for those seconds).
set -euo pipefail
cd "$(dirname "$0")/.."
N8N=samb-ocr-n8n-1
for w in vf-intake vf-sweep vf-notify vf-lint; do
  docker cp "n8n/$w.workflow.json" "$N8N:/tmp/$w.workflow.json"
  docker exec "$N8N" n8n import:workflow --input="/tmp/$w.workflow.json"
done
for id in sambOcrVfIntake1 sambOcrVfSweep01 sambOcrVfNotify1 sambOcrVfLint001; do
  docker exec "$N8N" n8n publish:workflow --id="$id"
done
docker restart "$N8N" >/dev/null
echo "Waiting for n8n…"
for i in $(seq 1 30); do
  if docker compose exec -T vf-ui python -c "import httpx,sys; sys.exit(0 if httpx.get('http://n8n:5678/healthz',timeout=2).status_code==200 else 1)" 2>/dev/null; then
    echo "n8n up. Webhook: http://n8n:5678/webhook/vf-intake · schedules: sweep every 3 h, needs-you every 5 min, lint every 6 h"; exit 0; fi
  sleep 2
done
echo "n8n did not come back" >&2; exit 1
