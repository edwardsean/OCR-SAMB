# REST API: `/api/v1`

The web app (`frontend/`, Next.js) reads and writes everything through this API. It is served by the FastAPI
service in `services/api/` (`v1.py` for the routes, `actions.py` for what a write does). Anything else can call it
too: a script or another agent.

- **Base URL:** `http://localhost:8002` (the API's own port, `API_PORT`). Through the web app it's the same path on
  its address: `http://localhost:3002/api/v1/…`.
- **Postman:** import [docs/postman/](postman/) (a collection in the order of the work, reads and writes kept apart). [docs/testing.md](testing.md) walks through it.
- **Live docs:** `http://localhost:8002/docs` lets you try every endpoint and shows every payload with its fields;
  the raw schema is `/openapi.json`. This page is the guide: the usual flow first, then the reference.

Examples below use `API=http://localhost:8002` and made-up values (scan `b-1a2b3c4d5e`, order `SOR26110200123`).

## Conventions

| | |
|---|---|
| Format | JSON in, JSON out. An upload is `multipart/form-data`. |
| Amounts | numbers in rupiah (`648399.84`). Values read from a page stay strings, as printed and normalised (`"816598.00"`). |
| Dates | ISO 8601: a date `2026-09-12`; a moment `2026-09-28T04:43:41.282906+00:00` (UTC; the screens show WIB). |
| Boxes | where something sits on a page: `[ymin, xmin, ymax, xmax]` on a 0–1000 scale of the page image. |
| Writes | answer `200 {"ok": true, "result": …}` (an upload answers `201`). The system re-checks what changed, so read again with GET for the new state. |
| Refusals | `{"error": "…why…", …}` with `400` (bad input), `404` (no such scan, page or order), `409` (conflict: the same file again, an order not ready to approve), `422` (a body field missing or of the wrong type: FastAPI's `detail` lists them). |
| Who | **There is no login yet.** Every decision carries `by`: the person's name as typed, stored with the decision. Keep the API inside the company network until authentication is added. |
| Words | the screens' Indonesian wording (document types, statuses, check names) comes from `GET /api/v1/words`. Stored values stay English (`"needs_review"`, `"(not printed)"`). |

## The usual flow, one scan from upload to Satellite

```bash
# 1. Upload a scanned PDF (any number of pages, unsorted)
curl -F "file=@scan.pdf;type=application/pdf" $API/api/v1/scans
# → 201 {"batch_id": "b-1a2b3c4d5e"}           (409 with the earlier scan if the same file was uploaded before)

# 2. Follow it: pages are split, read by the AI OCR, then grouped into orders (SOR)
curl $API/api/v1/scans/b-1a2b3c4d5e
# → scan.status (received → splitting → split → queued → reading → read), page_done / page_total,
#   orders: {"total": 4, "need": 2, "waiting": 0, "ready": 2, "published": 0, "held": 0}

# 3. Pages whose type the system couldn't decide: say what each is
curl "$API/api/v1/labels?batch=b-1a2b3c4d5e"                 # → the next unsure page (page: null when none is left)
curl -H 'Content-Type: application/json' -d '{"batch":"b-1a2b3c4d5e","page":7,"label":"TTG","labelled_by":"Edward"}' \
     $API/api/v1/labels

# 4. Documents whose number (SOR / PO) isn't sure are held out of every order: confirm the number
curl "$API/api/v1/bundles?batch=b-1a2b3c4d5e"                # → view.held[]: each with confirm.field and what was read
curl -H 'Content-Type: application/json' \
     -d '{"batch":"b-1a2b3c4d5e","page":12,"field":"purchase_order_no","value":"4505832724","by":"Edward"}' \
     $API/api/v1/bundles/confirmations

# 5. The scan's orders, the ones that need a person first
curl "$API/api/v1/orders?batch=b-1a2b3c4d5e"                 # → rows[]: sor_no, status, issues

# 6. One order: what is left to decide (open_items), then answer each item
curl "$API/api/v1/orders/SOR26110200123?batch=b-1a2b3c4d5e"
curl -H 'Content-Type: application/json' \
     -d '{"batch":"b-1a2b3c4d5e","check":"fp_po_total","input_print":"6b81bbbd67e78c60","reason":"rounding","by":"Edward"}' \
     $API/api/v1/orders/SOR26110200123/acceptances

# 7. Approve it once nothing is left (409 with what is still left otherwise)
curl -H 'Content-Type: application/json' -d '{"batch":"b-1a2b3c4d5e","by":"Edward"}' \
     $API/api/v1/orders/SOR26110200123/approval

# 8. Publish the scan's finished orders: typed rows in Satellite + one PDF per SOR
curl -H 'Content-Type: application/json' -d '{"batch":"b-1a2b3c4d5e","by":"Edward"}' $API/api/v1/publications
# → {"ok": true, "result": ["SOR26110200123", …]}

# 9. What was written, exactly as Satellite stores it
curl $API/api/v1/published/SOR26110200123
```

An order is **finished** when its status is `auto_ok` (every check passed by itself) or `reviewed` (a person
approved it). Only finished orders are published.

## Endpoints at a glance

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/session` | the top bar: batches waiting for a person, the teacher's line, today |
| GET | `/api/v1/search?q=` | find an order, a batch or a file |
| GET | `/api/v1/words` | every display word, in Indonesian |
| GET | `/api/v1/health` | services that don't answer |
| GET | `/api/v1/home` | Beranda: work waiting over every scan, recent scans |
| POST | `/api/v1/uploads` | start an upload batch (who, scan date): its number BATCH-YYYYMMDD-NN |
| GET | `/api/v1/uploads` | every upload batch with its five steps |
| GET | `/api/v1/uploads/{id}` | one batch: its files, its five steps, its failed and unsure pages, its orders' places |
| GET | `/api/v1/scans` | recent scans |
| POST | `/api/v1/scans` | upload a scanned PDF (into a batch) |
| GET | `/api/v1/scans/{batch_id}` | one scan: its pages, its orders by status, the queues |
| GET | `/api/v1/scans/{batch_id}/pages/{page_no}` | one page: its fields, where each sits, its table rows |
| POST | `/api/v1/scans/{batch_id}/pages/{page_no}/fixes` | correct a value on a page |
| POST | `/api/v1/scans/{batch_id}/pages/{page_no}/retry` | read a page that failed again (calls the AI) |
| GET | `/api/v1/scans/{batch_id}/pages/{page_no}/region` | what a region marked on the paper holds |
| GET | `/api/v1/keycheck` | does Satellite know this SOR / PO number? |
| GET | `/api/v1/lessons` | what a fix is doing now (kept as a lesson, the teacher writing a tip, …) |
| GET | `/api/v1/labels` | the next page whose type is unsure |
| POST | `/api/v1/labels` | say what a page is |
| GET | `/api/v1/orders` | a scan's orders, needs a person first |
| POST | `/api/v1/notices/seen` | mark the "needs you" notices seen |
| GET | `/api/v1/orders/{sor}` | one order's review |
| POST | `/api/v1/orders/{sor}/confirmations` | confirm or correct a value as printed |
| POST | `/api/v1/orders/{sor}/pairings` | say which SO line a customer's row is |
| POST | `/api/v1/orders/{sor}/acceptances` | accept a difference, with a reason |
| POST | `/api/v1/orders/{sor}/calibrations` | a customer's one-time calibration |
| POST | `/api/v1/orders/{sor}/approval` | approve an order |
| POST | `/api/v1/publications` | publish the scan's finished orders to Satellite |
| GET | `/api/v1/bundles` | a scan's orders with their documents; what is held and why |
| POST | `/api/v1/bundles/confirmations` | confirm a held document's number |
| GET | `/api/v1/published` | orders published to Satellite |
| GET | `/api/v1/published/{sor}` | one published order, as Satellite stores it |

Outside `/api/v1`: page images and PDFs (see [Files](#files)), and the developers' acceptance checks (`/api/status`, `/api/batches/{id}/phase2…7`, `/api/batches/{id}/vf`).

---

## Frame

### `GET /api/v1/session`
```json
{"batches_need": 3, "needs_you": 8, "unsure_left": 0, "teacher": "Guru AI: 2 pelajaran menunggu",
 "today": "Jumat, 2 Oktober 2026", "vf": true}
```
`batches_need`: upload batches with a step a person can act on now (the Batch tab's count). `needs_you`: orders in
needs-you notices nobody has seen yet. `unsure_left`: pages waiting for their type. `teacher`: null when the teacher
has nothing to do.

### `GET /api/v1/search?q=`
The top bar's search, over every batch: `{"q", "uploads": [batch rows as GET /uploads], "orders": [{"sor_no", "status",
"customer_name", "cpo_no", "batch", "uploads", "thumb"}], "files": [{"id", "file_name", "page_total", "received_at",
"upload"}]}`. Orders match by SOR, the customer's PO number (Nomor CPO) or customer name; batches by number or
uploader; files by name. Up to 20 each; fewer than 2 characters finds nothing.

### `GET /api/v1/words`
Maps from stored values to words for people: `DOC`, `DOC_SHORT` (document types), `CHECK`, `CHECK_STATUS`,
`BUNDLE` (order statuses), `BATCH` (scan statuses), `OUTCOME` (page outcomes), `FLAG` (scan conditions), `HOLD`,
`LINK`, `PUB_STATE`, `REASON`, `NONE_REASON`, `ROLE`, `BY`, `COL`, `FIELD`, `FIELD_BY_TYPE`, `DESC_BY_TYPE`, and
`LABEL_TYPES` (`[{key, name, what}]`, the choices for POST /labels).

### `GET /api/v1/health`
`{"bad": [], "n": 8}`: the services that don't answer, out of `n`. Takes a moment: it probes every service.

### `GET /api/v1/home`
```json
{"scans": [{"id": "b-1a2b3c4d5e", "file_name": "scan.pdf", "page_total": 16, "page_done": 16, "status": "read",
            "received_at": "2026-09-28T04:43:41+00:00", "unsure": 0, "waiting_ai": 0, "held": 0,
            "need": 4, "waiting": 0, "ready": 0, "published": 0, "orders": 4}],
 "todo": {"need": {"n": 4, "batch": "b-1a2b3c4d5e"}, "unsure": {"n": 0, "batch": null},
          "held": {"n": 0, "batch": null}, "ready": {"n": 0, "batch": null}},
 "busy": []}
```
`todo.*.batch` is the newest scan where that kind of work waits.

## Upload batches and their steps

One upload action is one batch, however many files: `POST /api/v1/uploads {"by", "date"?, "note"?}` → `201 {"id",
"code": "BATCH-20261005-01", "uploaded_by", "doc_date"}`, then `POST /api/v1/scans` once per file with `upload=<id>`.

A batch's work is five steps, in the order each needs the one before (`services/api/steps.py`):

| key | Step | What a person does there |
|---|---|---|
| `baca` | Dibaca AI | the system reads; a person only retries a page that failed |
| `jenis` | Jenis halaman | says what a page is when the system couldn't decide |
| `cocokkan` | Cocokkan ke order | confirms a document's SOR / PO number so it joins its order |
| `periksa` | Periksa order | decides what an order's checks couldn't settle |
| `kirim` | Kirim ke Satellite | sends the finished orders |

Each step has a `state`: `need` (a person can act now), `sys` (the system is working), `done`, `none` (nothing has
reached it), `later` (only what never blocks sending: a Faktur Pajak waiting for its number, an order waiting for a
document from another batch). `next` is the first step whose state is `need`. An order whose only open problem is a
missing document waits (`depends`) while one of steps 1–3 is still open in its batch (`blockers`), and isn't counted
as needing a person.

### `GET /api/v1/uploads?limit=100`
`{"uploads": [{"id", "code", "uploaded_by", "doc_date", "created_at", "note", "files", "pages", "read", "need",
"waiting", "ready", "published", "orders", "steps": [...], "next": "jenis", "finished": false}]}`, newest first.
A step carries its own counts:
```json
[{"key": "baca", "state": "need", "pages": 33, "read": 32, "failed": 1, "busy": 0, "waiting_ai": 0, "unscheduled": 0},
 {"key": "jenis", "state": "need", "unsure": 3, "answered": 1},
 {"key": "cocokkan", "state": "need", "block": 3, "wait": 0, "later": 6, "loose_unread": 1, "loose_unsure": 3, "loose_other": 0},
 {"key": "periksa", "state": "need", "need": 6, "depends": 1, "outside": 0, "waiting": 0, "ready": 0, "published": 0, "orders": 7},
 {"key": "kirim", "state": "none", "ready": 0, "published": 0}]
```

### `GET /api/v1/uploads/{id}`
The batch row above as `upload`, plus `files` (its scans), `steps`, `next`, `blockers` (the open steps among 1–3),
`finished`, `orders` (`{sor: need | depends | outside | waiting | ready | published}`), `failed` and `unsure` (the
pages steps 1 and 2 list: `[{"batch_id", "page_no", "file_name", "thumb", "error"}]`). `404` when there is no such batch.

## Scans and pages

### `GET /api/v1/scans?limit=20`
`{"scans": [{"id", "file_name", "page_total", "pages_rendered", "page_done", "status", "received_at"}]}`, newest
first. `limit` 1–200.

### `POST /api/v1/scans` (multipart)
| Part | |
|---|---|
| `file` | the scanned PDF |

`201 {"batch_id": "b-1a2b3c4d5e"}`. The PDF is stored and the scan recorded at once (status `received`), then put on
the `q.intake` queue: the intake worker splits it into pages and puts one ticket per page on `q.pages` (see
[Background work](#background-work)). Follow it with `GET /api/v1/scans/{batch_id}`.
Refused: `400` not a PDF, or its pages can't be read · `409 {"error", "dup": {"id", "file_name", "received_at"}}` the
same file (SHA-256) was uploaded before.

### `GET /api/v1/scans/{batch_id}`
```json
{"scan": {"id": "b-1a2b3c4d5e", "file_name": "scan.pdf", "status": "read", "page_total": 16, "pages_rendered": 16,
          "page_done": 16, "received_at": "…"},
 "pages": [{"page_no": 1, "status": "read", "doc_type": "FP", "type_status": "decided", "type_guess": "FP",
            "quality_flags": ["rotated"], "qr_text": "SOR26110200123", "thumb_upright_path": "rtm/pages/…/p001.jpg", "…": "…"}],
 "orders": {"total": 4, "need": 2, "waiting": 0, "ready": 2, "published": 0, "held": 0},
 "flags": {"type:FP": 4, "type:TTG": 4, "rotated": 2, "faint": 1},
 "depths": {"pages": 0, "group": 0, "dlq": 0}}
```
A page's image is `GET /img/<thumb_upright_path>` (see [Files](#files)). `404` when there is no such scan.

### `GET /api/v1/scans/{batch_id}/pages/{page_no}`
```json
{"scan": {"id": "b-1a2b3c4d5e", "file_name": "scan.pdf", "page_total": 16, "status": "read"},
 "page": {"page_no": 3, "status": "read", "doc_type": "TTG", "type_status": "decided", "outcome": "clear",
          "quality_flags": ["rotated"], "upright_path": "rtm/pages/…/p003.png", "…": "…"},
 "fix": {"image": "rtm/pages/…/p003.png", "type": "TTG", "customer": "TOKO CONTOH JAYA", "ready": true,
         "fields": [{"name": "purchase_order_no", "label": "Nomor PO", "value": "10101000125418",
                     "box": [119, 817, 140, 898], "verdict": "ok", "by": "text", "role": "keys",
                     "says": "menghubungkan halaman ke ordernya", "person": false}],
         "rows": [{"i": 1, "key": "02701899", "box": [357, 16, 389, 923],
                   "cells": [["item_code", "02701899", false], ["qty", "0", true]], "copied": ["…"]}],
         "units": [{"id": "w0", "text": "10101000125418", "tess": "10101000125418", "box": [125, 817, 138, 898],
                    "block": "b7", "i": 0, "s": 0, "e": 14, "match": "equal"}],
         "lines": {"b7": "10101000125418"}, "notes": []}}
```
`fix` is null until the page's type is known and its reading is finished. `verdict`: `ok` (backed by print, the QR
code, Satellite or a person), `check` (only the AI read it), `empty`. `by`: what backed it. `role`: what the value
does (`keys` links the page to its order, `page` is the invoice's own amount, `bundle` is checked against Satellite,
`support` is settled by Satellite, null = kept as read). `units` are the words a person can click on the paper.

### `POST /api/v1/scans/{batch_id}/pages/{page_no}/fixes`
| Field | Type | | |
|---|---|---|---|
| `field` | string | required | a header field (`purchase_order_no`) or a table cell `lines[<row key>].<column>` |
| `value` | string | required | the value as printed; `"(not printed)"` when the page has none |
| `by` | string | required | who |
| `region` | string | | where it was marked: `"ymin,xmin,ymax,xmax"` on 0–1000 |
| `shown` | string | | what the screen showed before |
| `row_key` | string | | a table cell's row key |

The page is fixed at once (re-checked, regrouped) and the correction is kept as an example the teacher learns from.
`400` without `by` or `value`.

### `POST /api/v1/scans/{batch_id}/pages/{page_no}/retry`
`{"by": "…"}`. A page whose reading failed for a technical reason (dead-lettered, e.g. a dropped connection) goes back
on `q.pages` under a new run: the AI is called again (its quota). `400` without `by` · `404` no such page · `409` the
page didn't fail, or another page of the same file is still queued.

### `GET /api/v1/scans/{batch_id}/pages/{page_no}/region?box=ymin,xmin,ymax,xmax`
`{"words": "…Tesseract's words there…", "blocks": [{"id", "kind", "text"}], "suggest": "…", "region": [y0, x0, y1, x1]}`.
`400` when `box` isn't four numbers with ymin < ymax and xmin < xmax.

### `GET /api/v1/keycheck?field=…&value=…`
Before a key is saved: `{"known": true, "says": "SOR26110200123 · TOKO CONTOH JAYA"}`, `{"known": false, "says": "…"}`,
or `{"known": null}` for a field it doesn't check. `field`: `sor`, `no_ref`, `purchase_order_no`, `nomor_cpo`. A
warning only, never a refusal.

### `GET /api/v1/lessons?batch=…&page=…&field=…`
After a fix: `{"headline": "Guru AI sedang menulis kiat dari perbaikan Anda…", "steps": [{"label", "state"}],
"tip": null, "final": false}`. `state`: `done`, `now`, `todo`, `skip`, `stop`. Ask again every few seconds until
`final` is true.

## Page types

### `GET /api/v1/labels?batch=&page=&after=&upload=`
All optional: without `batch`, the newest scan with a page still unsure; without `page`, its next unsure page nobody
labelled (after page `after`). With `upload` (a batch's step 2): the next unsure page over all that batch's files, after
`batch`/`after` (wrapping round), with `upload` `{id, code, uploaded_by, doc_date}` and `left` (how many are still
unsure there).
```json
{"batch": "b-1a2b3c4d5e", "page": 7, "total": 16,
 "p": {"page_no": 7, "upright_path": "…", "original_path": "…", "quality_flags": []},
 "existing": null,
 "near": [{"page_no": 6, "thumb": "…", "full": "…", "type": "FP"}],
 "types": [{"key": "FP", "name": "Faktur Penjualan", "what": "…"}],
 "customers": ["…"],
 "prog": {"unsure": 3, "unsure_done": 1, "labelled": 5, "practice": 4, "exam": 1}}
```
`page` and `p` are null when no unsure page is left. The machine's own guess is deliberately not returned (it would
bias the answer).

### `POST /api/v1/labels`
| Field | Type | | |
|---|---|---|---|
| `batch` | string | required | |
| `page` | integer | required | |
| `label` | string | required | `FP`, `TTG`, `PO`, `SJ`, `FPJ`, `PEL`, `CONTINUATION` or `OTHER` |
| `customer` | string | | |
| `note` | string | | |
| `labelled_by` | string | | |

The page resumes (it is read with its type), and a label the machine missed becomes a lesson for the teacher.
`400 {"error": "unknown label"}`.

## Orders (Periksa order)

### `GET /api/v1/orders?batch=&upload=`
Without either: every order, each once with its documents from every scan. With `batch` (a scan) or `upload` (a
batch): the orders with a document in it, still whole. With `upload`, each row also has `step`: its place in that
batch's step 4 (`need`, `depends`, `outside`, `waiting`, `ready`, `published`).
```json
{"batch": "b-1a2b3c4d5e",
 "rows": [{"sor_no": "SOR26110200123", "status": "needs_review", "customer_name": "TOKO CONTOH JAYA",
           "docs": ["Faktur", "PO", "Tanda Terima"], "thumb": "/img/…", "reviewed_by": null,
           "issues": [["Total PO ≠ order SAMB", "need"], ["Menunggu data terima barang (CGR)", "wait"]],
           "counts": {"pass": 4, "accepted": 0, "fail": 0, "unknown": 2}, "reasons": ["…"], "checks": {"…": "…"}}],
 "scans": [{"id": "b-1a2b3c4d5e", "file_name": "scan.pdf", "received_at": "…", "need": 2}],
 "fresh": [{"sor": "SOR26110200123", "batch": "b-1a2b3c4d5e", "customer": "TOKO CONTOH JAYA", "at": "…"}],
 "ready": 2}
```
Rows come needs a person first (`needs_review`, `grouping`, `reviewed`, `auto_ok`, `published`). `fresh` = orders in
needs-you notices nobody has seen (the scheduler's `notify` job writes them).

### `POST /api/v1/notices/seen`
No body; `204`. Send it when a person has actually seen the list (a script reading `/orders` never marks anything seen).

### `GET /api/v1/orders/{sor}?batch=`
Everything one order's review needs. The parts that matter:

```json
{"sor": "SOR26110200123", "batch": "b-1a2b3c4d5e",
 "bundle": {"id": 1990, "sor_no": "SOR26110200123", "status": "needs_review", "hold_reason": null,
            "reviewed_by": null, "reviewed_at": null, "published_at": null, "documents": []},
 "can_approve": false,
 "left": ["…what still holds the approval…"],
 "open_items": [
   {"kind": "check", "key": "fp_po_total", "title": "Total PO = order SAMB",
    "plain": "Total PO Rp 168.198,16 lebih besar dari order SAMB", "status": "unknown", "why": "…",
    "print": "6b81bbbd67e78c60", "accept": true, "gap": 168198.16, "allow": 5.0,
    "pair": [["Total di PO", 816598.0], ["Order SAMB (Satellite, saat dipesan)", 648399.84]],
    "fix": [{"page": 2, "type": "PO", "name": "total", "label": "Total PO", "value": "816598.00",
             "suggest": [["648399.84", "total order, termasuk pajak (Satellite)"]], "box": [659, 743, 671, 782], "approx": true}],
    "odd_rows": [{"page": 2, "i": 4, "desc": "…", "qty": "12", "uom": "PCS", "amount": 151530.0}], "ok_rows": 3},
   {"kind": "page", "page": 6, "title": "…", "fields": [{"name": "purchase_order_no", "label": "Nomor PO", "value": "…",
                                                        "suggest": [], "box": null}]},
   {"kind": "label", "page": 9, "title": "Halaman 9: jenis dokumennya belum pasti"},
   {"kind": "wait", "page": 4, "title": "Halaman 4 menunggu AI membaca ulang"}],
 "calibration": {"chain": "1100002424", "name": "…", "asks": [{"what": "receipt"}],
                 "suggest": null, "steps": []},
 "accept_reasons": ["rounding", "tolakan confirmed", "the customer's own price", "the document comes later",
                    "other (say in the note)"],
 "none_reasons": ["not in SAMB's order", "a free (bonus) item", "another product (say in the note)"],
 "lines": [{"line_no": 10, "item_code": "1000142", "description": "…", "qty_pcs": 40.0, "cgr_qty": 0.0, "…": "…"}],
 "strip": [{"page": 1, "type": "FP", "kind": "Faktur Penjualan", "img": "/img/…", "thumb": "/img/…", "flag": false}],
 "passed": ["Dokumen lengkap", "SO ada di Satellite"],
 "checks": {"fp_po_total": {"status": "unknown", "why": "…", "print": "…"}},
 "labels": {"fp_po_total": "Total PO = order SAMB"},
 "documents": [{"page": 2, "type": "PO", "kind": "Purchase Order (PO)", "pages": [2], "outcome": "clear",
                "head": [], "kept": [], "rows": []}],
 "so": {"sor_no": "SOR26110200123", "customer_name": "TOKO CONTOH JAYA", "order_total": 648399.84, "…": "…"}}
```

How to answer each open item:

| `kind` | means | answer with |
|---|---|---|
| `check` with `accept: true` | a check that doesn't pass (`key` names it) | **acceptances** (`check` = `key`, `input_print` = `print`, a `reason`), or fix a misread value from `fix[]` with **confirmations**. A receipt check (`key: received`) lists `qty_fix[]` rows: confirm a row's quantity (`field: lines[<key>].qty`) or pair an unrecognised row (**pairings**) |
| `check` with `accept: false` | waiting for the AI or for Satellite | nothing: it continues by itself |
| `page` | a page whose key or amounts aren't sure | **confirmations**, one per entry in `fields[]` |
| `label` | a page whose type isn't decided | POST /labels |
| `wait` | the AI is still reading a page | nothing |
| `calibration.asks[]` | the customer's first look (only `receipt` now) | **calibrations**: `receipt_shows` |

`404` when there is no such order.

### `POST /api/v1/orders/{sor}/confirmations`
| Field | Type | | |
|---|---|---|---|
| `batch` | string | required | |
| `page` | integer | required | |
| `field` | string | required | a header field (`total`, `purchase_order_no`, `posting_date`, …) or `lines[<row key>].<column>` |
| `value` | string | required | as printed; `"(not printed)"` when the page has none |
| `by` | string | required | |
| `row_key` | string | | a table cell's row key |
| `shown` | string | | what the screen showed |

Quantities carry their unit as written (`"2 CTN"`, `"48 PCS"`). The page is re-checked and the order regrouped.

### `POST /api/v1/orders/{sor}/pairings`
| Field | Type | | |
|---|---|---|---|
| `batch`, `page` | | required | |
| `row` | integer | required | the row's index on the page (0-based) |
| `line_no` | string | required | the SO line (`lines[].line_no`), or `"none"` |
| `by` | string | required | |
| `note` | string | | with `"none"`: why, one of `none_reasons` |

A pair is remembered for the customer: the same product matches by itself next time.

### `POST /api/v1/orders/{sor}/acceptances`
| Field | Type | | |
|---|---|---|---|
| `batch` | string | required | |
| `check` | string | required | the open item's `key` |
| `input_print` | string | required | the open item's `print`: the acceptance holds only while the check says exactly this |
| `reason` | string | required | one of `accept_reasons` |
| `by` | string | required | |
| `note` | string | | with `"other (say in the note)"` |

### `POST /api/v1/orders/{sor}/calibrations`
| Field | Type | | |
|---|---|---|---|
| `chain` | string | required | `calibration.chain` |
| `name` | string | | `calibration.name` |
| `by` | string | required | |
| `receipt_shows` | string | required | `"received"` or `"ordered"`: what its receipts print after a rejection |
| `allowance` | string | | no longer accepted (400): every customer's allowance is Rp 1,000 per document |

Asked once per customer, on its first order with a tolakan; every order of that customer, in every scan, is checked
again. `result` lists those scans.

### `POST /api/v1/orders/{sor}/approval`
`{"batch", "by"}`. `409 {"error": "not yet", "left": ["…"]}` while anything is left; then the order is `reviewed`.

### `POST /api/v1/publications`
`{"batch", "by"}` → `{"ok": true, "result": ["SOR26110200123", …]}`: the scan's finished orders, checked once more,
written to Satellite (`satellite.doc_faktur_penjualan` / `doc_po` / `doc_ttg` and their `_line` tables, plus
`satellite.sor_document`) with one PDF per SOR. An empty list when nothing was finished.

## Berkas per SOR

### `GET /api/v1/bundles?batch=&upload=`
Narrowed like `/orders`. Each held document has `group`: `block` (a person confirms its number; an order waits for
it), `wait` (the AI or SAP will settle it), `later` (never blocks sending, e.g. a Faktur Pajak).
```json
{"batch": "b-1a2b3c4d5e", "scans": [{"id", "file_name", "…": "…"}],
 "view": {"complete": 4,
          "bundles": [{"sor": "SOR26110200123", "customer": "TOKO CONTOH JAYA", "status": "needs_review", "hold": null,
                       "why": null, "folder": "rtm/bundles/SOR26110200123/",
                       "documents": [{"type": "PO", "page_from": 2, "page_to": 2, "pages": [2], "thumb": "/img/…",
                                      "joined": "lewat nomor PO 10101000125418 (tercetak)"}]}],
          "held": [{"type": "TTG", "page_from": 12, "page_to": 12, "why": "nomor PO-nya cocok dengan beberapa SO",
                    "suggested_sor": null,
                    "confirm": {"field": "purchase_order_no", "read": "4505832724", "value": "4505832724", "done": null}}],
          "unplaced": [{"page": 15, "type": null, "thumb": "/img/…", "why": "belum dibaca AI"}]}}
```

### `POST /api/v1/bundles/confirmations`
`{"batch", "page", "field", "value", "by"}`: `field` is the held document's `confirm.field`, `value` the number as
printed. The page is re-checked (no model call) and the scan regrouped.

## Published (Data terkirim)

### `GET /api/v1/published?batch=&just=&upload=`
`{"rows": [{"sor_no", "customer_name", "total", "pos", "ttgs", "page_count", "version", "source_batch",
"updated_at"}], "batches": [{"id", "name"}]}`. `just` (comma-separated SORs) puts those first.

### `GET /api/v1/published/{sor}`
```json
{"sor": "SOR26110200123", "customer": "TOKO CONTOH JAYA", "total": 1078330.01, "pages": 3, "version": 1,
 "docs": [{"type": "FP", "name": "Faktur Penjualan", "page_ref": [1], "linked_by": "lewat nomor SOR",
           "pages": [{"n": 10, "img": "/img/…"}], "backed": 7, "filled": 7, "to_check": 0,
           "fields": [{"name": "sor", "label": "Nomor SOR", "value": "SOR26110200123", "state": "print",
                       "page": 10, "box": [27, 801, 38, 971], "approx": false}],
           "cols": [["kode_material", "Kode material", "id"]],
           "rows": [{"line": 1, "cells": ["1001422", "…"], "page": 10, "box": null}]}],
 "record": {"name": "satellite.sor_document", "cols": [["sor_no", "Nomor SOR", null]], "rows": [["…"]]},
 "tables": [{"type": "FP", "name": "satellite.doc_faktur_penjualan", "cols": [], "rows": [], "lines": false}],
 "counts": {"FP": [1, 1], "PO": [1, 1], "TTG": [1, 1]}}
```
`state` of a value: `print` (backed by the print), `satellite`, `person`, `ai` (only the AI read it: worth a look),
`empty`. `tables` are the database rows exactly as stored. `404` when the order was never published.

## Files

| Method | Path | |
|---|---|---|
| GET | `/img/{key}` | a page image (`key` as given in `thumb_upright_path`, `upright_path`, …; also returned as `/img/…` URLs) |
| GET | `/crop/{batch}/{page_no}/{field}` | the line of a page where a field is printed, zoomed (PNG; 404 when it can't be found) |
| GET | `/crop/{batch}/{page_no}/row/{key}` | a table row as printed, across the page |
| GET | `/documents/{sor}.pdf` | a published order's PDF |

## Background work

Behind the API, the work runs on RabbitMQ queues and Python services, with no HTTP between them (n8n was removed on
2026-10-02: it struggled with thousands of records and several workers). Nothing here needs to be called; it's how
an upload becomes orders.

| Service | Takes | Does |
|---|---|---|
| `rtm-intake` | `q.intake` (one message per upload) | splits the scan into pages, then one ticket per page on `q.pages` |
| `rtm-worker` ×3 | `q.pages` | reads each page: prepares it, AI OCR, classifies, checks |
| `rtm-grouper` | `q.group` | groups pages into orders, checks each order against Satellite |
| `rtm-teacher` | `q.lessons` | learns from people's corrections and labels |
| `rtm-scheduler` | a clock | the periodic jobs below |

| Job | Every | Does |
|---|---|---|
| `intake` | `INTAKE_RETRY_MINUTES` (10) | a scan received but not split yet goes back on `q.intake` |
| `notify` | `NOTIFY_EVERY_MINUTES` (5) | orders that newly need a person become one notice (`needs_you`, `fresh`) |
| `sweep` | `SWEEP_EVERY_MINUTES` (180) | pages still waiting for the AI go back on `q.pages` |
| `lint` | `LINT_EVERY_MINUTES` (360) | learned knowledge a person's later correction contradicts is taken out |

Each job runs once per interval however many schedulers run (it takes its turn in `staging.job_run`); the Status
page shows when each last ran and what it did. Any step is safe to repeat, so a message delivered twice or a worker
that dies mid-way never does the work twice.

## Statuses

| | values |
|---|---|
| scan `status` | `received`, `splitting`, `split` / `rendered`, `queued`, `reading`, `read`, `grouping`, `done`, `failed` |
| page `outcome` | `clear`, `waiting_ai` (the AI must read or look again), `needs_person`, `held_unsure` (type undecided) |
| page `type_status` | `decided`, `labelled` (by a person), `unsure`, null (not read) |
| order `status` | `grouping` (waits for the AI or Satellite), `needs_review`, `auto_ok`, `reviewed`, `published` |
| check `status` | `pass`, `accepted`, `fail`, `unknown`, `waiting`, `info`, `n/a` |
| document types | `FP` Faktur Penjualan, `TTG` Tanda Terima, `PO`, `SJ` Surat Jalan, `FPJ` Faktur Pajak, `PEL` Pelunasan, `CONTINUATION`, `OTHER` |

The words people see for each are in `GET /api/v1/words`.
