# SAMB OCR: Rekonsiliasi AR

Finance Invoicing at SAMB scans stacks of customer paperwork (Faktur Penjualan, PO, Tanda Terima, unsorted). This
system reads every page, groups the pages per order (SOR), checks them against SAMB's records in Satellite, asks a
person only about what doesn't add up, and writes each finished order to Satellite with one PDF per SOR.

## What is where

| | |
|---|---|
| `frontend/` | the web app people work in (Next.js): [frontend/README.md](frontend/README.md) |
| `services/api/` | the API the web app calls: REST under `/api/v1` ([docs/api.md](docs/api.md)), page images and PDFs, and the developers' Teknis screens |
| `services/worker/`, `grouper/`, `publisher/`, `common/` | the pipeline: page workers (prepare, AI OCR, classify, check), grouping and cross-checks, publishing |
| `services/intake/`, `services/scheduler/` | the intake worker (each upload → pages) and the periodic jobs: [background work](docs/api.md#background-work), all on RabbitMQ, no n8n |
| `schema/` | the database, as SQL migrations applied in order |
| `tests/` | pytest, run inside the API container; `tests/browser/` drives the web app in Chrome |
| `docs/` | [API](docs/api.md), [database](docs/database.md), [phase plan](docs/phase-plan.md) |

## Run it

1. **Configure:** `cp .env.example .env`, then fill in the model keys you have. Every setting the code reads is in
   `.env.example` with its default; nothing machine-specific is written in the code. In the backend, one file reads
   them: [services/common/config.py](services/common/config.py) (every setting with its type and default); the rest
   of the code imports from it (`from common import config` → `config.STORAGE_PREFIX`).
2. **Servers:** Postgres, RabbitMQ and MinIO run from the `main` branch's `docker-compose.yml`. This branch's
   compose file joins their Docker network (`DOCKER_NETWORK`), so start those first.
3. **First time only** (not automated yet):
   - create the database `PIPELINE_DB` and apply `schema/satellite-documents.sql`, then `schema/0*.sql` in order:
     `docker exec -i <postgres container> psql -U $POSTGRES_USER -d $PIPELINE_DB -v ON_ERROR_STOP=1 < schema/0NN-….sql`;
   - create the RabbitMQ vhost: `rabbitmqctl add_vhost $RABBITMQ_VHOST` and
     `rabbitmqctl set_permissions -p $RABBITMQ_VHOST $RABBITMQ_USER ".*" ".*" ".*"` (the services declare their
     queues themselves).
4. **Start:** `docker compose up -d --build`. Then:
   - the web app: <http://localhost:3002> (`WEB_PORT`)
   - the API and its live docs: <http://localhost:8002/docs> (`API_PORT`)
5. **Test:** `docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/`. Some tests grade against the 288-page
   sample scan, which is real customer data and never committed. Put it at `testdata/sample.pdf` (or set
   `SAMPLE_PDF`); those tests skip or fail without it.

## Never in git

`.env` (keys and passwords), customer documents (`*.pdf`, `*.xlsx`, `*.csv`), Satellite exports, and local notes.
`.gitignore` lists them.
