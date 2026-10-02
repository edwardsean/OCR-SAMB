# SAMB OCR: Rekonsiliasi AR

SAMB's Finance Invoicing team scans stacks of customer paperwork for every order: SAMB's own invoice (Faktur
Penjualan), the customer's purchase order, and the customer's goods receipt (Tanda Terima). The stacks are unsorted.
This system reads every page, sorts the pages into orders, checks each order against SAMB's records, asks a person
only about what doesn't add up, and then writes each finished order to SAMB's system (Satellite) with one PDF per
order.

## How it works, with one scan

Finance uploads `scan.pdf`, 16 pages holding the paperwork of 4 orders, in no particular order:

1. **Upload.** The web app sends the PDF to the API.
   - The API stores it, records the scan (status `received`), and puts one message on the `q.intake` queue.
2. **Split.** The intake worker renders the 16 pages and puts one message per page on `q.pages`.
3. **Read.** Three page workers take the pages, several at once. For each page:
   - an AI model reads every field (the "AI OCR");
   - a classifier (Jev) decides what kind of document it is;
   - Tesseract, a classic OCR, checks each value against the printed text.

   Values the print doesn't back are marked as not sure, never silently trusted.
4. **Group.** The grouper joins the pages into one order per SOR (SAMB's sales-order number), using the numbers printed
   on them: the SOR on the invoice, the customer's PO number on the PO and the receipt.
5. **Check.** Each order is compared with Satellite's record of that sale: totals, quantities received, dates, the
   store.
   - 3 orders match. They're ready to send (`auto_ok`).
   - 1 order's PO is Rp 9 off. It waits for a person (`needs_review`).
6. **Decide.** In the web app, on Periksa order, a person sees that one difference beside the scanned page and
   accepts it ("Pembulatan", rounding).
   - It's the customer's first order, so the system also asks once how much rounding is normal for them (say Rp 10).
   - From then on, that customer's differences up to Rp 10 pass by themselves.
7. **Send.** "Kirim ke Satellite" writes the 4 orders' documents into Satellite's tables, with one PDF per SOR.

Two more things run in the background:
- A **scheduler** every few minutes: it turns new orders that need a person into a notice, resends what got stuck,
  and removes learned knowledge that people later contradicted.
- A **teacher**, which learns from every correction people make.

## The pieces

```
 browser ──▶ rtm-web :3002 (Next.js) ──▶ rtm-api :8002 (FastAPI, REST /api/v1) ──▶ Postgres · MinIO
                                              │ upload
                                              ▼
 q.intake ──▶ rtm-intake ──▶ q.pages ──▶ rtm-worker ×3 ──▶ q.group ──▶ rtm-grouper ──▶ orders, checked
                                            │  (AI OCR, Jev, Tesseract)
                                            └─▶ q.lessons ──▶ rtm-teacher           rtm-scheduler (clock)
```

| Service | What it does |
|---|---|
| `rtm-web` (:3002) | the web app people use: [frontend/](frontend/README.md) |
| `rtm-api` (:8002) | the REST API ([docs/api.md](docs/api.md), live at `/docs`), page images and PDFs, and the developers' Teknis screens |
| `rtm-intake` | each uploaded scan → pages → one message per page (`services/intake/`) |
| `rtm-worker` ×3 | reads each page (`services/worker/`) |
| `rtm-grouper` | pages → orders, and the checks against Satellite (`services/grouper/`) |
| `rtm-teacher` | learns from people's corrections and labels (`services/worker/lesson.py`, `learn.py`) |
| `rtm-scheduler` | the periodic jobs: notices, sweep, knowledge lint, intake retry (`services/scheduler/`) |
| Postgres, RabbitMQ, MinIO | the database, the queues, the file storage ([docker-compose.servers.yml](docker-compose.servers.yml)) |

Everything between the services goes through RabbitMQ. There's no n8n: it was removed because it struggled with
thousands of records and several workers.

## Words you'll meet

| | |
|---|---|
| **SOR** | SAMB's sales-order number. One SOR = one order = one bundle of documents. |
| **FP** (Faktur Penjualan) | SAMB's invoice. It prints the SOR and a QR code with it. |
| **PO** | the customer's purchase order. Its number is on the invoice as "Nomor CPO". |
| **TTG** (Tanda Terima) | the customer's goods receipt: GRN, Receiving Note, Good Receipt, … |
| **Satellite** | SAMB's system in front of SAP: the orders, what was received (CGR), the invoices. The truth the orders are checked against, and where finished orders are written. |
| **CGR / tolakan** | goods received as recorded in Satellite / goods the store refused |
| **scan** (batch) / **order** (bundle) | one uploaded PDF / one SOR's documents, in the code's own words |
| **Jev** | TypeSafe's classifier: which kind of document a page is |
| **AI OCR** | the vision model that reads the fields (`VF_AI_OCR`) |
| **Teknis** | the developers' screens: Status, Jev's context, the knowledge the teacher learned |

## What is where

| | |
|---|---|
| `frontend/` | the web app (Next.js 16, React, TypeScript) |
| `services/api/` | the API: `v1.py` (REST routes), `actions.py` (what a person can change), `app.py` (the data behind each screen, the Teknis screens) |
| `services/common/` | shared by every service: `config.py` (every setting), the database, queues, storage, the field lists, the checks |
| `services/intake/`, `worker/`, `grouper/`, `publisher/`, `scheduler/` | the background services above |
| `schema/` | the database, as SQL files applied in order |
| `scripts/` | `setup.sh` (first-time setup), `load_satellite.py` (Satellite's export) |
| `tests/` | pytest, run inside the API container; `tests/browser/` drives the web app in Chrome |
| `docs/` | [API](docs/api.md), [testing](docs/testing.md), [Postman collection](docs/postman/) |

## Run it

### On a fresh machine

You need Docker (give it at least 4 GB of memory) and git.

```bash
git clone git@github.com:edwardsean/OCR-SAMB.git && cd OCR-SAMB && git checkout read-then-map
cp .env.example .env                                   # then fill in the AI keys you have (see below)
docker compose -f docker-compose.servers.yml up -d     # Postgres, RabbitMQ, MinIO
./scripts/setup.sh                                     # the database (every migration) and the RabbitMQ vhost
docker compose up -d --build                           # the API, the web app, the workers
```

Then open:
- the web app: <http://localhost:3002>
- the API's docs: <http://localhost:8002/docs>
- the Status page: Teknis → Status sistem (every service should be green)

**Keys** (in `.env`; `.env.example` lists them):
- the AI OCR's provider key for `VF_AI_OCR` (e.g. `DASHSCOPE_API_KEY` for `dashscope:qwen3-vl-plus`);
- `TYPESAFE_API_KEY` for Jev;
- `ZAI_API_KEY` for the teacher.

Without them the screens and the API work, but no page gets read.

**Satellite's export.** The orders are checked against it, so without it every order stays on hold. It is three CSV
files of real customer data: ask Edward, keep them out of git, and load them with:

```bash
docker compose run --rm -v <folder with the CSVs>:/data/so:ro -v ./scripts:/scripts:ro rtm-api \
    python /scripts/load_satellite.py /data/so
```

### Next to the main branch's stack (Edward's laptop)

The servers already run from the main branch's stack, so skip `docker-compose.servers.yml`. This stack joins their
network (`DOCKER_NETWORK`) and keeps its own database, vhost and storage prefix (`PIPELINE_DB`, `RABBITMQ_VHOST`,
`STORAGE_PREFIX`), so it never touches main's data.

### A new migration on an existing database

`setup.sh` only sets up a database that doesn't exist yet. Apply a new `schema/0NN-….sql` by hand:

```bash
docker exec -i <postgres container> sh -c 'psql -U "$POSTGRES_USER" -d <PIPELINE_DB> -v ON_ERROR_STOP=1' < schema/0NN-….sql
```

## Everyday commands

```bash
docker compose ps                                     # what runs
docker compose logs -f rtm-intake rtm-worker rtm-grouper   # the work, live
docker compose restart rtm-api rtm-worker            # after changing Python code: the services that use it (mounted, not copied)
docker compose up -d --build rtm-web                  # after changing the web app
docker compose up -d --force-recreate <service>       # after changing .env
docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/    # the tests
```

Consoles:
- RabbitMQ: <http://localhost:15672> (the queues, in vhost `rtm`)
- MinIO: <http://localhost:9001> (the files)

## Configuration

Every setting comes from `.env`, and [.env.example](.env.example) lists them all with their defaults: ports, server
addresses, database and queue names, model choices, daily caps, the scheduler's intervals. Nothing machine-specific
is written in the code.

In the backend, one file reads them: [services/common/config.py](services/common/config.py) gives every setting its
type and default, and the rest of the code imports from it (`from common import config` → `config.STORAGE_PREFIX`).
The web app needs only `API_URL` ([frontend/.env.example](frontend/.env.example)).

## Documentation

| | |
|---|---|
| [docs/api.md](docs/api.md) | every endpoint: what to send, what comes back, the usual flow from upload to Satellite |
| [docs/testing.md](docs/testing.md) | the automated tests, the endpoints in Postman, and the whole workflow tested by hand |
| [docs/postman/](docs/postman/) | the Postman collection and environment (import both) |
| [frontend/README.md](frontend/README.md), [frontend/DESIGN.md](frontend/DESIGN.md) | the web app, and its design rules |
| [docs/database.md](docs/database.md) | the two schemas (`staging`, `satellite`), with diagrams. Written for v1; later tables are in `schema/0*.sql` |
| [docs/phase-plan.md](docs/phase-plan.md) | how the first pipeline (v1) was built, phase by phase: history |

## Never in git

- `.env` (keys and passwords);
- customer documents and data (`*.pdf`, `*.csv`, `*.xlsx`, Satellite's export);
- local notes and tools (`CLAUDE.md`, `.claude/`, `diagrams/`).

`.gitignore` lists them all. The repository is private: it holds customer names in tests and test data.
