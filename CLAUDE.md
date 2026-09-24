# SAMB OCR — Rekonsiliasi AR

OCR pipeline for **SAMB (PT Sarana Abadi Makmur Bersama)**, part of AnterAja. Finance Invoicing scans stacks of customer documents; this system turns each scan into typed rows in **Satellite** (the system in front of SAP), grouped per **SOR** (sales order), plus one PDF per SOR.

The user is an intern building this with two mentors (a human mentor and an "AI mentor"). They are learning the system as they build it: explain with a concrete end-to-end scenario first, then the general rule.

## Source of truth

- **`Problem Statement & Process Delivery - Rekonsiliasi AR.pdf`** (8 pages). Problems P1–P7, the To-Be process, **§06 the four OCR capabilities in order: Klasifikasi → Grouping → Ekstraksi → Verifikasi**, §6.1 fields per document, §6.2 linking fields, §07 open questions. An older 4-page version existed and was stale; don't rely on it.
- **`7000356304 - 7000356499.pdf`**: the real 288-page sample scan. Every test runs against it.
- **`testdata/golden_p1-32.json`**: pages 1–32 read by hand (page types, SORs, bundles, rotated/dark pages). It is the answer key for the acceptance tests.
- **`Infrastructure OCR.jpeg`**: the human mentor's original sketch.
- Phase plan: [docs/phase-plan.md](docs/phase-plan.md) (summary below). The **vlm-first** experiment lives on git branch `vlm-first`, checked out at `.claude/worktrees/vlm-first` (its own CLAUDE.md section describes it).

## THIS CHECKOUT: the vlm-first experiment (branch `vlm-first`)

The user's alternative design, built to compare with v1 on pages 1–31 **without touching v1**. Per page:
1. **Prepare the image** (`enhance.prepare`: dark bands, upright, straighten, QR). No Tesseract reading.
2. **AI OCR reads every field on ONE combined list** (`vlm.extract_all`), plus where each value is (`box`).
3. **Jev classifies from that reading only** (`vf.jev_state`), with a context generated from the registry.
4. **Decide:** Jev ≥ 0.85. An SOR QR means FP; an FP also needs the QR or the FP layout (image-only witnesses). Otherwise unsure → Label screen.
5. **Tesseract reads the page only now**, after classification.
6. **Check** (`verify.run` on the projected per-type fields), then Tesseract re-reads each unbacked value's spot zoomed in (`worker/zoom.py`).
7. **Look again** (`vlm.second_look`): blind, field names and crops only, never Tesseract's reading or Gemini's first answer. A new answer counts only if print backs it.
8. **Outcome:** `clear` (every §6.1 field ✅), `needs_person`, or `held_unsure`.

**Decided with the user (2026-09-24):**
- Teacher = GLM-4.6V-Flash (Z.ai, needs `ZAI_API_KEY`).
- The look-again is blind.
- Context changes are replayed on practice labels + anchors, then **a person approves** (Context screen).

**Two kinds of "new field":**
- **New for a type:** the field is already on the combined list. The type's list gains it as `sometimes` + a note; the combined list doesn't change.
- **Genuinely new:** the field joins the list and the type as a `clue`. Its example must be printed (Tesseract), it must not repeat another field's value, and a person confirms it.

**Pieces:**

| Piece | Where |
|---|---|
| Combined list | `common/fields.py` `CANON` (29 per-type fields → 18 stored + 2 clue), `TYPE_MAP`, `project()`/`lift()` |
| Jev context | `common/context.py` (versioned in `staging.context_version`; invariant: union of types' fields == list, checked by `validate()`) |
| Page flow | `worker/vf.py` |
| Teacher | `common/models/teacher.py` |
| Lessons and replay gate | `worker/lesson.py` |
| Clone | `worker/clone.py` |
| Schema | `schema/010-vlm-first.sql` (vf database only) |
| Screens | vf UI http://localhost:8001: batch box, page view section, `/context`, `/compare` |

**Runtime** (`docker-compose.yml` here):
- Project `samb-ocr-vf`: `vf-worker` ×1 and `vf-ui` on :8001, on v1's network `samb-ocr_default`.
- Database `ocr_vf`, RabbitMQ vhost `vf`, MinIO keys under `vf/`. It reads v1's page renders and, read-only, v1's DB (`MAIN_DATABASE_URL`).
- **Service names must never be `ui`/`worker`**: n8n calls `http://ui:8000`.
- Start v1 first; stop vf before `docker compose down` in v1.

**Commands (from this folder):**
```bash
docker compose up -d                                                     # vf-worker + vf-ui
docker compose exec vf-worker python -m worker.clone b-4bab9b736d 1-31,48,52,57   # identities + labels only
docker compose exec vf-worker python -m worker.vf once b-4bab9b736d 1,4,15 [--v1-reading]  # run pages now (dry run: v1's reading instead of Gemini)
docker compose exec vf-worker python -m worker.zoom report b-4bab9b736d 1-31      # the zoomed check on v1's values, read-only
docker compose exec vf-worker python -m worker.lesson backfill|run|exam …       # lessons → teacher → proposals
docker compose exec -e PYTHONPATH=/app vf-ui pytest -q tests/                   # 71 pass; test_vf_acceptance waits for the Gemini run; v1's phase tests skip
```

**Budget:**
- Gemini free tier is 20 calls/model/day, shared with v1; it resets at 14:00 WIB.
- vf caps itself at `VF_GEMINI_DAILY_CAP` (40) and counts every call in `staging.model_call`.
- About 1 read plus ≤ 1 look-again per page.

**Measured so far (2026-09-24):**
- **The zoomed check exposed a shared mistake.** Page 22 prints S10232; Gemini read `510232` and the zoomed Tesseract read also said `510232`. So a zoomed ✅ is refused when any Tesseract reading of that spot disagrees (`$10232` on the whole page).
- **Zoomed reads misread too.** p1 `3190721`, p9 `214436`: they never cancel a whole-page ✅. On v1's 26 values for a person, the zoom confirms 2.
- **Dry run with v1's readings** (19 pages; no document title in them):
  - all 7 FPs decided right; every TTG/PO unsure;
  - no wrong type, no wrong ✅, 38 of 38 one-digit changes caught.
  - The real run needs Gemini; it waits for the quota.

## Domain in one minute

- One **SOR** has one **Faktur Penjualan (FP)**, SAMB's own invoice, which prints the SOR. The customer returns supporting documents for it: **TTG** (Tanda Terima), **PO**, and sometimes **SJ** (Surat Jalan). Later a **Faktur Pajak (FPj)** arrives, and later still **Pelunasan** (payment) documents.
- **TTG is one type with many names**: Receiving Slip Order (Hari Hari), Good Receipt (Boots), Goods Receive Note (Hero/DFI).
- **How a document finds its SOR**:
  - The FP prints the SOR. **Its QR code also encodes the SOR.** The FP also prints `Nomor CPO` (the customer's PO number).
  - Some TTGs print the SOR: Hari Hari `No Ref: SOR…`; Puri Indah `DO#: 26110259371` and Indogrosir `S/Fak: 2611025…` print it **without the `SOR` prefix**.
  - Otherwise the TTG or PO carries the customer PO number, which maps to `satellite.sor.cpo_no` (= the FP's Nomor CPO).
  - **Checked by eye 2026-09-24: TTGs don't all say "PO", and it doesn't always match.** Labels vary (`No PO`, `PO No`, `ORDER NO`, `No. Pesanan`, a `PO# or OT#` column in the rows). AEON's receiving note (p272) has no PO field; its `RECEIPT NO` equals the FP's Nomor CPO. Hari Hari p2's `No PO 5201510` ≠ FP p1's Nomor CPO `5190721` (its No Ref SOR links it). So phase 6 should use both keys: the SOR (with or without prefix) first, then the FP's CPO number found anywhere on the TTG; if they point to different FPs, hold.
- The To-Be process says scanning happens **"tanpa sortir"** (unsorted), so grouping must use keys, not page adjacency.
- Scale: ~1,700 SO/day × ~4 documents ≈ **6,800 pages/day**.
- The scans are **1-bit black-and-white** (CCITT, 300 dpi). "Blur" is faint print broken into dots; software can't recover it. The user was advised to test **greyscale scanning** at the source.

## Architecture

```
upload / SCP ─▶ MinIO (temp repo) ─▶ n8n intake ─▶ q.pages (1 ticket per page)
   ─▶ page worker ×6: enhance + Tesseract + QR ─▶ [Jev classify ─▶ AI OCR extract ─▶ verify]   (phases 3–5)
   ─▶ N-of-N fan-in ─▶ q.group ─▶ grouping worker: pages → documents → SOR, cross-checks       (phases 6–7)
   ─▶ needs_review ─▶ Dashboard per SOR ─▶ publish: Satellite rows + SOR PDF                   (phases 7–8)
```

- **Queue unit = page.** A worker never sees other pages, so grouping is a separate per-batch stage.
- **Classical OCR runs first.** Its text is the independent witness. **Jev** classifies before the **AI OCR** (VLM) extracts. **Plain code** then checks every value (phase 5): ✅ only if something printed backs it, otherwise a person checks it. **No verifier model** (decided with the user 2026-09-24).
- **Two stores.** `staging` is the pipeline's memory: append-only, replayable. `satellite` is the clean record. Only the publisher writes Satellite.
- **Models**, all behind adapters so a provider swap is config:
  - Jev: TypeSafe System One, `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`. See `.claude/skills/jev/SKILL.md`.
  - Dev VLM: Gemini (`GEMINI_MODEL`, pinned `gemini-3.8-flash`, fallbacks `gemini-3.7-flash`, `gemini-3.5-flash`). **Free tier on this key = 20 requests per model per day** (measured from Google's 429, not the ~1,500/day web articles claim), so ~60 pages/day total. Free-tier inputs may be used by Google: **sample data only**.
  - Production targets: Model Studio / Ark.

Diagrams: `diagrams/ocr-pipeline.html` is the **as-built** overview (only what exists and runs). Clicking a box marked `details ›` opens its own diagram: `detail-intake.html`, `detail-enhance.html`, `detail-classify.html` (including what happens when a page is unsure), `detail-worker.html` (retries, stale runs, scoreboard, bell), `detail-extract.html` (AI OCR + the phase-5 check). They are **generated**: edit `diagrams/tools/overview.py` / `details.py` and regenerate (see `diagrams/tools/README.md`); when a phase is built, add it there. `diagrams/ocr-steps.html` still shows the *target* swim-lane flow. Database: **[docs/database.md](docs/database.md)**, with ER diagrams, the staging → Satellite bridge, and a worked example.

Data flow: `diagrams/db-flow.html` shows every phase, the tables it touches and all their columns, with Keep/Drop/Ask verdicts. It is published at https://claude.ai/artifact/1w8u1n6xxAiYc5PKV2pXAW, and the verdicts live in its `verdicts` collection. Regenerate with `python3 scripts/build_db_flow.py`, then republish after every schema migration.

## Repo layout

```
docker-compose.yml   9 services: postgres, rabbitmq, minio, n8n, ollama, worker(×6), grouper, publisher, ui
.env / .env.example  dev passwords + TYPESAFE_API_KEY, GEMINI_API_KEY (empty = fallback)
schema/              satellite-documents.sql (base), 002…007 migrations, 008-document-fields (GENERATED from services/common/fields.py)
services/common/     fields.py = THE field list per document type (§6.1 + linking); verify.py = phase 5 check; db, queue, storage, intake, keys, health;
                     models/vlm.py (AI OCR), models/schemas.py (generated from fields.py)
services/worker/     enhance.py + orient.py (2), classify.py + layout.py + fp_layout.json (3), main.py (consumer: 2→5 + fan-in),
                     reclassify.py / reverify.py (re-decide / re-check from stored data, no model calls)
services/grouper/    idle until phase 6
services/publisher/  idle until phase 8
services/ui/         FastAPI + Jinja + htmx inspection UI, http://localhost:8000
n8n/                 intake.workflow.json (webhook → split → enqueue); scripts/n8n-setup.sh imports + publishes it
tests/               test_phase0..5.py, test_fanin.py, test_answer_key_isolation.py, test_labels.py, test_screens.py; run inside the ui container
diagrams/  docs/     architecture diagrams, database guide
```

## Running it

```bash
docker compose up -d                                   # whole stack
bash scripts/n8n-setup.sh                              # first time only: import + publish the intake workflow
docker compose exec -e PYTHONPATH=/app ui pytest -q tests/     # all acceptance tests (58; test_phase4 fails until pages 1–31 are all read)
docker compose exec worker python -m worker.reverify b-4bab9b736d   # phase 5: re-check stored values with the current rules (seconds)
docker compose exec worker python -m worker.calibrate 1,8,11   # phase-2 metrics for specific sample pages
```

Consoles:
- UI: http://localhost:8000
- RabbitMQ: http://localhost:15672 (`ocr` / `ocr_dev_pw`)
- MinIO: http://localhost:9001
- n8n: http://localhost:5678
- Postgres: `localhost:55432`

The sample batch id is **`b-4bab9b736d`**.

## Build status: phased, the user verifies each phase

| Phase | What | Status |
|---|---|---|
| 0 | Stack, schema, Status screen | ✅ |
| 1 | Upload → MinIO → n8n → 288 page tickets; duplicate file rejected by SHA-256 | ✅ |
| 2 | Upright (sideways detection), deskew, dark-band mask, Tesseract, QR, N-of-N bell | ✅ run 4: 288/288, 81 QR = SOR |
| 3 | Classify: Jev leads; QR / FP layout / FP title as second witness for FP; fixed title words → decided or `unsure` | ✅ v2: pages 1–32 no wrong type; whole batch 52 unsure |
| 4 | AI OCR extraction per type schema, each value with `source_text` | built · pages 1–31: 19 read, 12 wait for the free-tier quota |
| 5 | Check every value by code: printed in Tesseract's text / = QR / FP amounts add up; else a person | ✅ pages 1–31 (19 read): 92 of 118 main values ✅, 26 to a person, no wrong value ✅; 92/92 one-digit changes caught |
| 6 | Grouping worker: pages → documents → SOR (QR / printed SOR / PO No → `sor.cpo_no`) | |
| 7 | Cross-checks (§6.3) + Review screen | |
| 8 | Publish: Satellite rows + `SOR<no>.pdf` | |

## Rules decided with the user. Don't relax these.

- **"Don't know" is acceptable; a confident wrong answer never is.** Every phase is graded that way: None, `unsure`, or held all pass; a wrong type or wrong SOR fails.
- **No cheating.** `testdata/golden_p1-32.json` is visible only to the UI and tests, never to pipeline code (`worker`, `grouper`, `publisher`, `common`). The answer key keeps what's *printed on the paper*, even where the pipeline can't read it. In production there is no answer key. Correctness comes from:
  - Satellite checks (the SO, `cpo_no` and CGR already exist there);
  - agreement between independent sources;
  - people's corrections;
  - random spot checks of `auto_ok` bundles.
- **Hold rules.** An FP whose SOR is unknown is **never** linked to the previous SOR:
  1. No SOR by position alone. "The page after an FP" is only a *suggestion* for a person.
  2. An unsure or unreadable page breaks the chain. Later pages can't inherit the previous SOR; pages with their own key (SOR, `No Ref`, PO No → `sor.cpo_no`) still resolve by that key.
  3. The unknown FP plus the following keyless pages is held as one group with no SOR.
  4. A bundle missing its FP is held too (`fp_missing`). Nothing half-complete is published.
  5. Only that group waits; the rest of the batch continues.
  6. A person confirms, chooses, or splits → `reviewed` → publish.

  Holds live in `staging.bundle` (`needs_review`, `hold_reason`, `suggested_sor`), not in RabbitMQ's dead-letter queue. `q.pages.dlq` is only for technical failures.

- **Learning from corrections (in progress).** People label unsure pages on the **Label** screen (`/label`). Each label goes into `staging.type_label` with a pile drawn once at random: **practice** (80%) or **exam** (20%). Whoever proposes better Jev descriptions (a reasoning model, or Claude in a session until there's a key) may read **practice labels only, never exam labels**, not even to peek. A change is kept only if, re-run on all labels, it makes **zero new wrong answers** and fewer unsure, stays within a length budget, and a person approves it. The exam pile is scored afterwards to confirm. Fixed facts (a customer's name for a document) go into `customer_profile.doc_aliases`, not Jev prose. The machine's guess is hidden on the Label screen so it can't bias the labeller.

**Working agreement:** build one phase, run its acceptance test and look at its UI screen, then **stop and report**. Start the next phase only when the user says "go". Each phase adds:
- a `phaseN_checks()` in `services/ui/app.py`, served at `/api/batches/{id}/phaseN`;
- a matching `tests/test_phaseN.py`;
- an acceptance box on the batch page.

## Gotchas learned the hard way

- **Restart Python services after editing code.** The code is bind-mounted, but processes load it at start. A stale UI process once ran old intake code.
- **Changed `requirements.txt`?** Rebuild every Python service (`docker compose build ui worker grouper publisher`). Each keeps its own image.
- **Fan-in.** `page_done` is recounted from `page.status='read'` (monotonic via `GREATEST`), never `+1`. Tickets carry `run`, and stale-run tickets are dropped. Only the worker that wins the `reading → read` transition rings the bell. A `+1` counter once rang the bell before page 286 was read. `tests/test_fanin.py` guards this.
- **Re-running a batch** (UI button or `intake.rerun`) bumps `run`. Don't purge `q.pages`: other batches' tickets live there too.
- **Orientation (`worker/orient.py`, enhance v2).** Sideways-or-not = text-line direction after removing ruling lines (upright ≤ 1.24, sideways ≥ 6.0 on all 288 pages); a quick read picks 90° vs 270°. Tesseract OSD and read-scores can't tell 0° from 90° (Tesseract rotates text blocks itself). The v1 ink-profile test got 12 pages wrong; they're the regression set in the answer key (`orientation_verified`). Page 69 is a phone photo of a bank-transfer confirmation (Pelunasan paperwork) mixed into the scan.
- **Cleanup variant.** Scoring cleanups on the header didn't predict the full-page winner. `close` at 2× is the default; `smooth` is tried only on weak reads.
- **Field lists come from one file.** `services/common/fields.py` lists every field per document type, from Problem Statement §6.1, plus *linking* fields §6.1 doesn't list but grouping needs (FP `nomor_cpo`, TTG `no_ref`, `customer_name`, FP `customer_code`). It generates the AI OCR schema and `schema/008-document-fields.sql` (`python -m common.fields ddl`); the UI shows it at `/fields`. Change fields there, never in the SQL or the schema directly. Decided with the user 2026-09-24: FP qty split into `qty_crt` / `qty_pcs`; Surat Jalan has no list (not observed) and Pelunasan waits for its stage.
- **The handwritten number on an FP (356304…) is the SAP billing number** written by Invoicing after posting (P7), not a bundle counter. The sample's filename `7000356304 - 7000356499` is a billing-number range. It is not extracted; SAP has it.
- **Gemini free tier = 20 requests per model per day** on this key (measured). It resets at **midnight Pacific = 14:00 WIB**; the quota breaker in `vlm.py` uses that day. Phase 4 extraction on the whole batch needs billing or another provider. Until then AI OCR work is scoped to **pages 1–31** (re-run with pages `1-31`, never the whole batch: a whole-batch re-run retries every failed page and burns the day's quota). Pages 1–31 end on a complete bundle; page 32's FP continues past the answer key.
- **Classification (`worker/classify.py`, v2 since 2026-09-24).** Jev leads. Rules: SOR QR → FP; an FP needs Jev **plus** a second witness (QR, FP layout ≥ 0.70, or the printed FAKTUR PENJUALAN title), because Jev said FP at 0.88 on SAMB's handwritten SALES ORDER form (p68); title + Jev agree → that type; Jev ≥ 0.85 with no contradicting title → that type; else unsure. Title words: short lines in the top 35%, lines containing ':' are field labels (not titles), aliases include GOODS RECEIPT, BUKTI PENERIMAAN BARANG, RECEIVING NOTE, RECEIVED NOTE, PRODUCT RECEIPT. Measured on all 288 pages before adopting: unsure 73 → 54, 0 wrong on pages 1–32; 'Jev alone' was tested and rejected (saved 3, lost 7). `python -m worker.reclassify <batch>` re-decides from stored votes without calling Jev.
- **The QR sits top-centre-right** on the FP, not top-left. The decoder tries 8 detector/variant combinations.
- **Docker has ~4 GB RAM.** Six Tesseract workers fit. Local Ollama VLMs will not; use native Ollama via `OLLAMA_URL=http://host.docker.internal:11434` or raise Docker's memory.
- **Postgres migrations** live in `schema/00N-*.sql`, mounted into `docker-entrypoint-initdb.d` (fresh volumes only). Apply them to the running DB by hand: `docker compose exec -T postgres psql -U ocr -d ocr -f - < schema/00N-….sql`.
- `services/tests/` and `services/testdata/` are empty mount-point folders Docker created. Ignore them.

- **Phase 5 check (`common/verify.py`, v1).** Rows in `staging.field_check` (status ok · check · empty, `confirmed_by` text · qr · adds_up, `reason`). "Printed" = letters+digits of the AI's `source_text` on one Tesseract line (or a line + the next), and **never inside a longer number** (Tesseract `11.126.006` does not confirm `1.126.006`). The stored value must agree with its own `source_text`. **Never "close enough"**: p22 is printed S10232, Tesseract read `$10232`, Gemini `510232`. Adds-up = FP DPP + PPN = Total (±1) and PPN = 11% (or 12%) of DPP. Line items count only on their own row; Tesseract can't read most table rows on these 1-bit scans, so line values are mostly "check" and phase 7's FP/TTG/PO comparison is where they get confirmed. The acceptance changes every ✅ value by one digit and re-checks: all must fail. The right edge of some FP scans is cut off (p3 `1.014.424,3`, p10 `1.078.330,`): the AI reads what's visible.

## Open questions for the mentors

- Is there a customer product-code → SAMB material-code table? (§07; needed for line-item cross-checks.)
- Surat Jalan fields (no filled example yet).
- Does "28k documents" mean pages or bundles?
- What should happen to a bundle split across two feeder runs?
- Is there API access to customer web portals?
