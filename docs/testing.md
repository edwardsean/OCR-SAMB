# How to test it

There are three ways to test the system, from quickest to fullest:

| | What it proves | Time | Changes data |
|---|---|---|---|
| [Automated tests](#1-the-automated-tests) | the code's rules, every endpoint answering, the queues and jobs | ~40 s | no (test rows are removed) |
| [The endpoints in Postman](#2-the-endpoints-in-postman) | what each endpoint takes and returns, on real data | minutes | only folder 2 |
| [The whole workflow by hand](#3-the-whole-workflow-from-upload-to-satellite) | an upload goes all the way to Satellite | as long as the AI takes | yes, and it uses AI quota |

All three need the stack running (see [README.md](../README.md#run-it)). The examples use the default ports: the web
app on <http://localhost:3002>, the API on <http://localhost:8002>.

## 1. The automated tests

They run inside the API container, against the running stack and its database:

```bash
docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/                        # everything (~40 s)
docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/test_api_v1.py           # one file
docker compose exec -e PYTHONPATH=/app rtm-api pytest -q tests/test_vf_schedule.py -k once   # tests whose name has "once"
```

A good result looks like `361 passed, 16 skipped`. The skipped ones belong to v1 (the retired first pipeline).

| Area | Files | What they check |
|---|---|---|
| The API | `test_api_v1.py`, `test_screens.py` | every read answers JSON; refusals say why and write nothing; every Teknis screen renders |
| Config | `test_config.py` | only `common/config.py` reads the environment; every setting is in `.env.example` |
| Intake and scheduler | `test_vf_schedule.py` | an upload is recorded and queued once; one worker splits a scan; each job runs once per interval |
| Reading a page | `test_vf_flow.py`, `test_vf_keys.py`, `test_vf_gates.py`, `test_vf_boxes.py`, … | how values are read, checked against the print, and trusted or not |
| Orders | `test_vf_grouping.py`, `test_vf_crosscheck.py`, `test_vf_totals.py`, `test_vf_review.py`, … | grouping pages into orders, the checks against Satellite, what a person is asked |
| Publishing | `test_vf_publish.py` | what is written to Satellite |
| Grading | `test_vf_phase6.py`, `test_vf_phase7.py`, `test_answer_key_isolation.py` | results against the hand-made answer key; the pipeline never reads that key |

What they need:
- **The stack running.** A test that needs something missing is skipped or fails with a clear message.
- **The 288-page sample scan** at `testdata/sample.pdf` (or `sample.pdf` in the folder `SAMPLE_DIR` names). It is real customer data and not in git; without it, the tests that read it skip.
- **The sample's pages already read** into the database (scan `b-4bab9b736d`). On a fresh database those tests fail.
  The rest still runs.
- Nothing calls an AI model, and every test removes the rows it adds.

The **browser tests** in `tests/browser/` drive the page viewer in Chrome and are run by hand. They need
`puppeteer-core`. The first lines of each file say how to run it.

## 2. The endpoints in Postman

1. In Postman, **Import** both files in [docs/postman/](postman/):
   - `SAMB-OCR.postman_collection.json`: the requests;
   - `SAMB-OCR.local.postman_environment.json`: `baseUrl` and `by`.
2. Pick the environment **SAMB OCR (local)** (top right), and set `by` to your name: every decision is signed with it.
3. Open the folder **1 · Look around (read only)** and send its requests **in order**, or click **Run**. Each request
   fills in what the next one needs, as collection variables:

   | Request | Sets |
   |---|---|
   | Recent scans | `batch` |
   | One scan | `page` |
   | Periksa order | `sor` |
   | One order | `checkKey`, `checkPrint`, `chain` |

   Look at the **Body** of each response: that is the payload the web app gets.
4. **3 · See the errors** shows what a refusal looks like: a status code and `{"error": "…"}`. Nothing is written.
5. **2 · Do the work** sends a person's decisions, in the order of the work:
   - upload;
   - label a page;
   - confirm a number;
   - correct a value;
   - accept a difference;
   - calibrate a customer;
   - approve;
   - publish.

   **These change the database.** Read each request's description before sending it, and use a test stack. Bodies
   that say `TYPE THE VALUE AS PRINTED` need the real value from the page.

What it looks like, run from the command line with Newman (the read and error folders: 26 requests, 33 checks, all
passing):

```bash
npx newman run docs/postman/SAMB-OCR.postman_collection.json -e docs/postman/SAMB-OCR.local.postman_environment.json \
    --folder "1 · Look around (read only)" --folder "3 · See the errors (nothing is written)"
```

Other ways to try the endpoints:
- **In the browser:** the API's own page at <http://localhost:8002/docs> lists every endpoint with its fields, and
  "Try it out" sends a request.
- **In Postman, from the live schema:** Import → Link → `http://localhost:8002/openapi.json` gives every endpoint,
  without the ordering and variables of the collection above.
- **On paper:** [docs/api.md](api.md) explains every endpoint and payload.

## 3. The whole workflow, from upload to Satellite

This follows one real scan through every step. It needs:
- **AI keys in `.env`:** the AI OCR's provider key for `VF_AI_OCR` (e.g. `DASHSCOPE_API_KEY`), `TYPESAFE_API_KEY`
  (Jev, which decides each page's type), and `ZAI_API_KEY` (the teacher).
- **Satellite's export loaded** (see README): without it, no order can be checked, so everything stays on hold.
- **A scan of real documents.** These are customer data: keep them out of git.

Each step lists what you do (in the web app, or with the Postman request named in brackets), what happens behind it,
and how to see it.

**1. Upload the PDF.** In Unggah batch, or with [Upload a scanned PDF].
- The scan exists at once, with status `received`.
- The upload puts one message on `q.intake`.
- See it: the screen opens the batch's page at step 1, Dibaca AI. The intake worker logs the split:
  `docker compose logs -f rtm-intake`.

**2. Split into pages.** Nothing to do.
- `rtm-intake` renders every page, then puts one ticket per page on `q.pages`.
- The status goes `splitting` → `split` → `queued`.
- See it: the scan's page shows the steps and a thumbnail per page ([One scan]: `scan.status`, `pages_rendered`).

**3. Read each page.** Nothing to do.
- The three `rtm-worker`s prepare each page and send it to the AI OCR (this uses AI quota).
- Jev decides the page's type. Tesseract checks each value against the print.
- See it: `docker compose logs -f rtm-worker`, the scan's progress bar, and [One page] for what was read and where.

**4. Say what unsure pages are.** In the batch's step 2, Jenis halaman, or [Jenis halaman] then [Say what a page is].
- A page Jev couldn't decide waits for a person's label, then continues by itself.
- See it: step 2's count on the batch's page (`steps` in `GET /api/v1/uploads/{id}`; `unsure_left` in [Session]).

**5. Group into orders.** Nothing to do, unless a number isn't sure.
- `rtm-grouper` joins pages into one order per SOR, by the numbers printed on them.
- A document whose number isn't sure is held. Confirm it in the batch's step 3, Cocokkan ke order, or with
  [Confirm a held document's number].
- See it: Berkas per SOR ([Berkas per SOR]: `view.bundles`, `view.held`).

**6. Check against Satellite.** Nothing to do.
- Each order is compared with SAMB's record: totals, received quantities, dates, the store.
- If everything matches, the order is `auto_ok` (ready to send). Otherwise it's `needs_review`.
- See it: the batch's step 4, Periksa order ([Periksa order]: each row's `status` and `issues`).

**7. Decide what doesn't add up.** In the batch's step 4, open an order. Over the API: [One order], then the requests in
folder 2.
- Each open item asks for one decision:
  - correct a value read wrong;
  - pair a row with SAMB's line;
  - accept a difference with a reason;
  - calibrate a new customer.
- See it: [One order]: `open_items` gets shorter, then `can_approve` becomes true.

**8. Approve.** The button at the bottom of the order, or [Approve an order].
- The order becomes `reviewed`.

**9. Send to Satellite.** The batch's step 5, Kirim ke Satellite, or [Publish the scan's finished orders].
- Every finished order (`auto_ok` or `reviewed`) is written to Satellite's tables, with one PDF per SOR.
- See it: the same step's "Sudah terkirim" ([One published order, as Satellite stores it]), and the PDF ([A published order's PDF]).

Two things also happen by themselves, from the scheduler:
- Orders that newly need a person become a notice (`notify`, every 5 min), marked seen when a person opens step 4.
- A scan that never got split is put back on the queue (`intake`, every 10 min).

### When something seems stuck

| What you see | Where to look | Usual reason |
|---|---|---|
| A scan stays `received` | `docker compose logs rtm-intake` | the intake worker isn't running; the scheduler resends it within 10 min once it is |
| Pages stay `queued` / "Menunggu AI" | `docker compose logs rtm-worker`; the Status page's "Model AI" table | the AI's daily limit: pages wait in `q.pages.wait` and come back by themselves |
| Every order is held | Berkas per SOR (`why`) | Satellite's export isn't loaded, or the numbers weren't read |
| No notices appear | Status page → **Jadwal** (each job's last run and result) | `rtm-scheduler` isn't running, or its job failed (the table says why) |
| Anything else | **Status page** (Teknis → Status sistem): every service, the queues, the tables | a service down |

The queues themselves (`q.intake`, `q.pages`, `q.pages.wait`, `q.group`, `q.lessons`) are in RabbitMQ's console at
<http://localhost:15672> (vhost `rtm`).

### Test data

- **Real scans and Satellite's export are customer data.** They stay on the machine and never go into git (`*.pdf`,
  `*.csv` are ignored). Ask Edward for them.
- **A made-up PDF** (any PDF) tests the upload and the split. Its pages then go to the AI, but it makes no orders,
  because Satellite has no SO for it.
- There is no button to delete a scan yet. Use a test stack (its own `PIPELINE_DB`, `RABBITMQ_VHOST` and
  `STORAGE_PREFIX`) for experiments.
