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
   - 3 orders match. They're ready to send (`auto_ok`). A difference of up to Rp 1,000 per document counts as a
     match ("selisih wajar", the same for every customer; nobody is asked).
   - 1 order's PO is Rp 36,414 more than SAMB's order. It waits for a person (`needs_review`).
6. **Decide.** In the web app, on Periksa order, a person sees that difference beside the scanned page, with the row
   that causes it (a product that isn't in SAMB's order), and accepts it with a reason or fixes a misread number.
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
| `scripts/` | `setup.sh` (first-time setup), `seed.sh` (Jev's context into the database), `load_satellite.py` (Satellite's export) |
| `services/seed/` | the data a new database starts from: Jev's context (`jev-context.json`); refresh it from a working database with `python -m common.seed export` |
| `tests/` | pytest, run inside the API container; `tests/browser/` drives the web app in Chrome |
| `docs/` | [API](docs/api.md), [testing](docs/testing.md), [Postman collection](docs/postman/) |

## Run it

### On a fresh machine

You need Docker (give it at least 4 GB of memory) and git.

```bash
git clone git@github.com:edwardsean/OCR-SAMB.git && cd OCR-SAMB && git checkout read-then-map
cp .env.example .env                                   # servers and passwords (the AI models come later, in the web app)
docker compose -f docker-compose.servers.yml up -d     # Postgres, RabbitMQ, MinIO
./scripts/setup.sh                                     # the database (every migration) and the RabbitMQ vhost
docker compose up -d --build                           # the API, the web app, the workers
./scripts/seed.sh                                      # Jev's context into the database (once)
```

Then open:
- the web app: <http://localhost:3002>
- the API's docs: <http://localhost:8002/docs>
- the Status page: Teknis → Status sistem (every service should be green)

Check it with the tests: `docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/`. On a fresh clone about
80 of them skip: they read the sample scan (`testdata/sample.pdf`) or stored orders, real customer documents that are
not in git. None should fail.

**Models and keys** are set only in the web app, on Teknis → Model & kunci API (never in `.env`):
- the **vision model**, for every call that sends a page image (including the page-type teacher);
- the **text model**, for every call that sends only text (including the knowledge teacher and the product matcher);
- the **classification model**, which decides each page's type (an instruct, non-thinking model; it replaced
  TypeSafe's Jev).

For each: enter the endpoint (any OpenAI-compatible API, e.g. Alibaba Model Studio's
`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`) and its API key, press Find models (it suggests the model
this system was measured with), Save, then Test.

Until they are set, the screens and the API work, but no page is read: pages wait and go on by themselves once they are.

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

### When something fails, or is slow

Everything the services and people do is written to the trace (`staging.trace`, [schema/028-trace.sql](schema/028-trace.sql),
[services/common/trace.py](services/common/trace.py)):
- a page's attempts and each of their stages (prepare, read, classify, knowledge, tesseract, check, look again, save);
- each time a page is sent to the queue or waits for a limit, and each split file;
- every grouping round, and every order whose status changes, with why;
- what people did (and what was refused, with why);
- the teachers' lessons and changes, and the scheduled jobs.

The AI calls stay in `staging.model_call`. Everything is said in plain words (each step's name and what it does:
[services/api/trace_words.py](services/api/trace_words.py)); the code's own name is kept small under each line, with
the raw row folded under it. Look under **Teknis**:
- **Jejak** (`/teknis/jejak`): what happened at a moment (±15 minutes), or everything about a batch, file, SOR or
  person, newest first, filtered by the workflow's step and the result (failed, waiting, running). Scheduled jobs that
  found nothing to do are hidden unless you choose them.
- **Jejak for one batch** (a batch's link in Jejak): its story in the workflow's order: 1 upload, 2 split into pages,
  3 read each page (each page's own log: every try, the queue wait before it, each stage, and the AI calls made inside
  each stage), 4 group into orders, 5 what people did, 6 what the teachers learned, 7 sent to Satellite. A summary on
  top (when it started, when every page was read, the time a page takes, AI calls, tokens, cost), a timeline under it.
- **Jejak for one page**: that page's log alone.

Every stage of a page also keeps what went in and what came out ([services/worker/trace_io.py](services/worker/trace_io.py)),
under "Input and output" on its line, for example:
- the AI OCR's copy of the page, the text model's mapping (the JSON fields) and which fields its two answers
  disagreed on;
- what the classifier saw and its probabilities;
- the type's own fields after the code's projection;
- Tesseract's text, word count, confidence and variant;
- each value's verdict, the look-again's questions and answers, the outcome and the linking numbers.

Long texts are cut at 1,500 characters, about 5 KB per read; the full text stays on the page.

Every AI call keeps its exact payload too, under "Request and response" on its line (`staging.ai_payload`), filled in
by the model adapters:
- the body sent: the model, its settings and the whole prompt (an image only as a placeholder: the page image is in
  storage; the API key never);
- the raw response that came back: its HTTP status, the model's answer, the finish reason and the usage; for the
  classifier, each option's probability.

Payloads are about 100 KB a page, so they are kept `PAYLOAD_KEEP_DAYS` (14), while the rest of the trace is kept 90.
- **Metrik** (`/teknis/metrik`): the last day, week or 30 days. How long a page and each stage take (median, 90%), the
  queue wait, the AI calls per purpose and model (latency, failures, tokens, USD), failures by cause, what the
  teachers and people did, pages read per hour, and what is running now.

The trace is kept `TRACE_KEEP_DAYS` (90). A database without `028-trace.sql` keeps working, but nothing is traced
until the migration is applied (see above). The tests never write to it.

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
