# SAMB OCR — Rekonsiliasi AR

OCR pipeline for **SAMB (PT Sarana Abadi Makmur Bersama)**, part of AnterAja. Finance Invoicing scans stacks of customer documents; this system turns each scan into typed rows in **Satellite** (the system in front of SAP), grouped per **SOR** (sales order), plus one PDF per SOR.

The user is an intern building this with two mentors (a human mentor and an "AI mentor"). They are learning the system as they build it: explain with a concrete end-to-end scenario first, then the general rule.

## Source of truth

- **`Problem Statement & Process Delivery - Rekonsiliasi AR.pdf`** (8 pages). Problems P1–P7, the To-Be process, **§06 the four OCR capabilities in order: Klasifikasi → Grouping → Ekstraksi → Verifikasi**, §6.1 fields per document, §6.2 linking fields, §07 open questions. An older 4-page version existed and was stale; don't rely on it.
- **`7000356304 - 7000356499.pdf`**: the real 288-page sample scan. Every test runs against it.
- **`testdata/golden_p1-32.json`**: pages 1–32 read by hand (page types, SORs, bundles, rotated/dark pages). It is the answer key for the acceptance tests.
- **`Infrastructure OCR.jpeg`**: the human mentor's original sketch.
- Phase plan: [docs/phase-plan.md](docs/phase-plan.md) (summary below).

**Since 2026-09-29, `main` IS the vlm-first pipeline** (the user: "make this version main, main's version should be gone"). `main` was fast-forwarded to branch `vlm-first`, and one project runs everything from this folder (`docker-compose.yml`, project `samb-ocr`). v1's own services (6 workers, its UI on :8000, grouper, publisher, ollama) are retired. v1's code stays because vlm-first reuses its modules (image preparation, orientation, Tesseract, QR, Jev), and v1's database `ocr` and page renders stay because batch 1 was cloned from them. The "v1" parts below describe that history. The old worktree `.claude/worktrees/vlm-first` (branch `vlm-first`) is no longer used.

## THIS WORKTREE: read, then map (branch `read-then-map`, 2026-09-29/30)

The mentor's change to step 2: **the AI OCR copies everything on the page with no field list** (printed text, tables cell by cell, handwriting, stamps, marks: a sketch on an invoice may be the only sign of a rejection), then **a text model maps the copy onto the field list and writes notes** (`staging.page.notes`, the AI's description, never checked). The user's addition: a Nanonets-style page viewer where a person corrects a field by marking it on the paper, and knowledge learned per document type and customer (an LLM wiki, Karpathy's pattern, behind a replay gate). The plan is in the session's plan file; the user chose to finish this design (not the "one image call maps too" variant) and to test only on `b-c80bbbde4d`.

- **Runtime:** project `samb-ocr-rtm` on main's servers, own database `ocr_rtm` (a copy of `ocr_vf`, migration 019 applied only here), vhost `rtm`, storage prefix `rtm/`, services `rtm-*` (UI :8002), `VF_READER=two_step`, `VF_AI_MAP=dashscope:qwen-plus`. main is untouched.
- **Stage 0:** the AI budget per **model** (`vf.reserve`/`ai_left`/`refused_for`/`blocked` keyed on the model; each Model Studio model has its own quota), a finishing reserve for pages already started, `openai_vlm._image` reports the size actually sent, per-model calls and tokens on the Status page.
- **Stage 1a (trial):** `common/transcript.py` (pure) + `openai_vlm.transcribe`/`map_text` + prompts `vlm.TRANSCRIBE`/`MAP` (a prompt change must bump its `_V`: `PROMPT_SHA`), `python -m worker.vf trial <batch> <pages>` (→ `staging.reading_trial`), `reground` (re-applies grounding to stored answers, no model call), screen `/trial/<batch>` (`?only=N`), shared partial `_read_steps.html` (what the AI OCR copied, boxes on the page; what the text model mapped, kept/moved/dropped).
  - **Grounding:** a mapped value is kept only if it is on the page: its characters as whole tokens (never part of a longer number), across neighbouring tokens, an amount by value, a date by meaning (`verify.dates`); found in the block named, else the block that holds it (`moved`); a value its source doesn't contain is dropped. A table header line is never an item row; a numbers-only line under a row is that row wrapped (`row_blocks`).
  - **Boxes:** the value's share of its line (`narrow`), then its ink with table ruling, flat dashes and short thin dashes removed (`zoom.ink_rect`: page 9's cell borders made the zoom read nothing), then Tesseract's own words after step 5 (`snap_boxes`).
  - **Map twice, merge** (`transcript.merge`): agreements and single finds kept, disagreements left empty. qwen-plus differed between two runs on 10 of 16 pages.
- **Measured on b-c80bbbde4d (16 pages), before → after:** pages clear 14 → 16; values that decide backed 32 → 37, none lost; the same page types (p2's title fix: `document_title` is the document's name, never a letterhead, MAP_V 3); bundle open items 12 → 10, and both false "fail" receipt checks from misread quantities (pack sizes on AEON p3, the price on Indomaret p12) became "don't know". 87 notes. Cost: ~6.3K image tokens a page (as before) + ~10K text tokens a page (two mappings).
- **Stage 1b (adopted here):** `vf.read_then_map` in `_handle` (transcript reused from the page or a trial, saved at once; mapping reused from a trial with the same version; `carry_over` keeps a look-again whose first answer is unchanged), `vf.read_fields` for the teacher's new-field trial (re-maps the transcript), notes and both steps on the page view.
- **Stage 2a (built 2026-09-30): the page viewer and examples** (no separate training screen, the user's choice).
  - **The viewer** (`_fix_page.html` at the top of the page view; `app._fix_view`): the paper with a box on every value read (green backed, amber not confirmed, blue a person's, grey dashed rows) and the type's fields beside it, those that decide first, each with its meaning (`desc`), status and what it does.
  - **Fix = click the value on the paper** (the user, 2026-09-30: "why do I need to drag a box?"). `knowledge.pick_units`: **boxes sit on Tesseract's words** (exact places) and **their text is the AI OCR's copy**, aligned word by word within each copied line (difflib), so a word Tesseract misread gets the copy's characters; short or unsure Tesseract-only words are dropped as specks; small boxes are drawn on top. A copy word with no Tesseract word under it gets **no box**. Two approaches failed a visual check and were removed: placing words by their characters' share of the line (boxes on the wrong columns: the user hovered COST and got DISCOUNT %), and cells from the table's ruling lines (p3 row 1 slipped a column: the row number and a dashed line cancelled out in the count). **Table rows** (Tesseract reads little in them on these scans): Fix on a row cell zooms the paper to that row and lists the row's cells as the copy has them, left to right, as buttons; the person clicks the one they see in the column (matching by eye, no geometry). **Zoom:** −/+/Fit, Ctrl+scroll; Fix zooms to 250% on the field's (or row's) box. Tested in Chrome (puppeteer, `tests/browser/pick_all.mjs` and `row_test.mjs`, run by hand): pages 3, 12, 5, 1: 503 of 505 boxes hover right, 0 wrong values on click. **Open (handed to another session, 2026-09-30):** most table-row text and some header text has no box at all, because Tesseract read nothing there; the test only checks boxes that exist. Hovering shows a box, clicking takes its text, shift-click adds the next word of the same line. The panel shows the value large, whether Tesseract reads the same (by token), and for a key whether Satellite knows it (`/api/keycheck`: warns "no order in Satellite has the SOR …" before a PO number is saved as No Ref). Dragging a box (`/api/region`) is only the fallback for text the copy missed. Save → `/page/fix`. Row cells too (`lines[<row key>].<col>`). "Not printed on this page" works as on Review. Checked by clicking through in Chrome (puppeteer).
  - **Review links to it:** a "Pick it on the page ›" button beside Save on the fix forms and receipt rows opens the viewer on that field (`?fix=…&back=…`) and returns to the Review card. Receipt rows show their printed row (`/crop/<batch>/<page>/row/<key>`, from the copy's row boxes) and a labelled, wide input.
  - **The first real correction (Edward, 2026-09-30, page 3):** a PO number saved as No Ref (the SOR reference). Its example was captured right (marked region, "RECEIPT NO" beside it, printed) but would teach the wrong thing; hence the field meanings and the Satellite check in the panel.
  - **A correction fixes the page at once** (`field_confirmation`, recheck, regroup: as a Review answer) **and is kept as an example** (`staging.extract_example`, `schema/020-extract-example.sql`, `common/knowledge.py save_example`): the region, Tesseract's words and the copy's blocks there, what is printed beside it (`anchor_of`: the label left of it, the table header and its cell above), the block kind under it (handwriting, stamp…), whether Tesseract backs the value, the customer when the page's order is known, a pile drawn once (practice 80% / exam 20%), older examples of that field superseded. A typed Review answer becomes an example too, placed where the copy prints the value (not found: no example). Deleting a confirmation deletes its example (cascade). Nothing is learned yet.
  - Tests: `tests/test_vf_examples.py` (anchors, suggestions, a real round trip through `/page/fix`, undone).
- **Next:** 2b (which customer, from the page, in shadow), 2c (the knowledge wiki, its gate, pass B), then Stage 3 (the teacher writes the wiki).

## The vlm-first pipeline

The user's alternative design, built to compare with v1 on pages 1–31 **without touching v1**. Per page:
1. **Prepare the image** (`enhance.prepare`: dark bands, upright, straighten, QR). No Tesseract reading.
2. **AI OCR reads every field on ONE combined list** (`vlm.extract_all`), plus where each value is (`box`).
3. **Jev classifies from that reading only** (`vf.jev_state`), with a context generated from the registry.
4. **Decide:** Jev ≥ 0.85. An SOR QR means FP; an FP also needs an image-only witness: the QR, the FP layout (≥ 0.70), or the printed title FAKTUR PENJUALAN (Tesseract on the top 35%, looked for only when it can decide; `vf.fp_title`, column `fp_title`, `schema/017-fp-title.sql`; 14 of 15 FPs and 0 of 32 other pages on 47 read pages). Otherwise unsure → Label screen.
5. **Tesseract reads the page only now**, after classification.
6. **Check** (`verify.run` on the projected per-type fields), then Tesseract re-reads each unbacked value's spot zoomed in (`worker/zoom.py`). After every witness, the 7a rules (`common/gates.py`) take away any ✅ that can't be trusted.
7. **Look again** (`second_look`, the same AI OCR): blind, field names and crops only, never Tesseract's reading or the model's first answer. A new answer counts only if print backs it.
   - **7b, the store question (S4):** only when a key only the AI read still can't link because an SO one character away ships to another store. One blind call asks which store the goods go to (`vf.store_step`, stored in `staging.page.ship_to`).
8. **Outcome:** `clear` (every §6.1 field ✅), `waiting_ai` (the AI OCR still has to read the page or look again), `needs_person` (still not backed after the look-again), or `held_unsure` (Jev unsure: Label screen).

**Nothing goes to a person before the AI OCR has read the page and looked again** (the user, 2026-09-25). When a call can't run (`--no-second-look`, the daily limit, a failed call), the page waits (`waiting_ai`):
- a failed read leaves the page **unclassified** (`type_status` NULL), never "unsure": the Label screen is only for pages Jev couldn't decide;
- a look-again not run stores `second_look = {"waiting": why}`;
- `python -m worker.vf again` runs what's waiting.

v1 has no look-again at all: its ⚠ goes straight to a person (phase 5, code only).

**Decided with the user (2026-09-24):**
- Teacher = GLM-4.6V-Flash (Z.ai, needs `ZAI_API_KEY`).
- The look-again is blind.
- Context changes are replayed on practice labels + anchors, then **a person approves** (Context screen).

**Two kinds of "new field":**
- **New for a type:** the field is already on the combined list. The type's list gains it as `sometimes` + a note; the combined list doesn't change.
- **Genuinely new:** the field joins the list and the type as a `clue`. Its example must be printed (Tesseract), it must not repeat another field's value, and a person confirms it.

**Teacher loop rules (2026-09-25, after its first real run):**
- **A change must help.** The replay must show more labelled pages right, none newly wrong, no anchor flips, and unsure not up. That's the learning rule ("fewer unsure"). GLM's first try on page 2 ("TTG usually has `document_title`") changed nothing and was refused.
- **One proposal at a time.** `lesson run` stops at the first proposal. A proposal built on an older context can't be approved: its content is that older context plus one change, so approving it would undo whatever was approved since.
- **Changes that tell Jev nothing are refused in code:** a common field (`document_title`, `page_marker`) added to one type, or anything the context already says.
- **GLM gets a second try**, told why its first change wasn't kept.
- **A lesson a newer approved context already gets right** is closed without asking GLM.
- **The teacher runs by itself** (the user, 2026-09-25): service **`vf-teacher`** (`python -m worker.lesson serve`) consumes **`q.lessons`** (vhost `vf`).
  - Wake-ups come from a label that makes a lesson, and from approving or rejecting a context. Every 30 quiet minutes it also retries lessons a model couldn't be reached for (up to 3 times).
  - Messages are only wake-ups: `staging.lesson` is the state, so a lost or duplicate message is harmless.
  - **One consumer, never parallel teachers.** Lessons build on the active context, so parallel ones would conflict. Pages 2, 7 and 9 were one problem: context #3 from page 2 closed 7 and 9 with no teacher call. Parallelism lives inside a lesson: the replay asks Jev 6 at a time.
  - Its own container holds `ZAI_API_KEY`; restarting it never touches `vf-worker` or a running `again`.
- **Naming:** Jev's context versions are **"context #N"** (the seed is #1), never "vN": v1 is the old pipeline.

**Pieces:**

| Piece | Where |
|---|---|
| Combined list | `common/fields.py` `CANON` (29 per-type fields → 18 stored + 2 clue), `TYPE_MAP`, `project()`/`lift()` |
| Jev context | `common/context.py` (versioned in `staging.context_version`; invariant: union of types' fields == list, checked by `validate()`) |
| Page flow | `worker/vf.py` |
| Teacher | `common/models/teacher.py` |
| Lessons and replay gate | `worker/lesson.py` (`serve` = the `vf-teacher` service on `q.lessons`) |
| Grouping (phase 6) | `grouper/group.py` (`plan` is pure), `common/satellite.py` (Satellite + a person as witnesses), `schema/011-grouping.sql`, screen `/bundles`, tests `test_vf_grouping.py` + `test_vf_phase6.py` |
| Cross-checks (phase 7) | `scripts/load_satellite.py` + `schema/012-crosschecks.sql` + `013-bundle-checks.sql` (SO amounts as ordered and invoiced, billing, CGR; `satellite.sor_item`; `staging.line_match`), the 7a rules `common/gates.py`, 7b `satellite.settle_record`/`pair_lines`/`resolve_fp`/`paper` + `common/confusions.py`, 7c `grouper/crosscheck.py` + `grouper/matching.py`, 7d the Review screen (`/review`, `review_view`, `review.html`, `review_sor.html`, `schema/014-review.sql`), grading `phase7_grade`/`phase7_checks` in `ui/app.py`, tests `test_vf_gates.py`, `test_vf_satellite_fp.py`, `test_vf_confusions.py`, `test_vf_resolve.py`, `test_vf_matching.py`, `test_vf_crosscheck.py`, `test_vf_review.py`, `test_vf_phase7.py` |
| Clone | `worker/clone.py` |
| Schema | `schema/010-vlm-first.sql` (vf database only) |
| Screens | vf UI http://localhost:8001: batch box, page view section, `/context`, `/compare` |
| Diagrams | `diagrams/vlm-first.html` (overview; links back to v1's), `detail-vf-page.html` (one page), `detail-vf-teacher.html` (a label teaching Jev), generated by `diagrams/tools/vf.py` |
| Data-flow artifact | https://claude.ai/artifact/1w8u1n6xxAiYc5PKV2pXAW (v4) shows **both pipelines with a switch** (vlm-first steps F0–F11 plus the planned phases 6–8; v1 unchanged). Build it from THIS branch's `scripts/build_db_flow.py`: `main`'s older generator is v1-only and would drop the vlm-first view |

**Runtime** (`docker-compose.yml`, project `samb-ocr`, since 2026-09-29):
- Servers: `postgres` (databases `ocr_vf` and v1's `ocr`, read-only here), `rabbitmq` (vhost `vf`), `minio`, `n8n`. Their volumes (`samb-ocr_pgdata`, …) are the ones v1 created; nothing was moved.
- Services: `vf-worker` ×3, `vf-grouper` ×1, `vf-teacher` ×1, `vf-ui` on :8001. They keep the `vf-` names: n8n's workflows call `http://vf-ui:8000`. The images `samb-ocr-worker` / `samb-ocr-ui` build from `services/Dockerfile` (`docker compose build` after changing `requirements.txt`).
- On a fresh machine, the database `ocr_vf`, the vhost `vf` and the migrations 010+ are not created automatically. The Postgres init scripts only cover v1's 001–009. Not needed yet; write that setup when it is.
- **On the queue (Stage 1, 2026-09-29; the user: "we should be using n8n, RabbitMQ queues and multiple workers").** Vhost `vf`:
  - `q.pages` → **3 page workers** (`worker/main.py` → `vf.handle`). A page is run by one worker at a time (an advisory lock per page; a second ticket finds it read and does nothing). The AI's daily cap is **reserved per call** in one transaction (`vf.reserve`: a 'pending' ledger row), so workers can't overspend it (tested with 8 threads at a cap of 3).
  - After every page the worker wakes **`vf-grouper`** on `q.group` (`grouper/serve.py`): it takes the wake-ups waiting together as one round, groups each batch once (`group.run`), then sends back to `q.pages` the pages a bundle asked to look again (`crosscheck.ASK_WAIT`, only pages that are 'read': a page with a ticket keeps its one). The page worker no longer groups; `vf.once` still does, for runs outside the queue.
  - **A call the daily limit or cap stopped** → the worker parks the page on **`q.pages.wait`** (queue TTL `VF_WAIT_MINUTES`, 15; RabbitMQ dead-letters it back to `q.pages`). A parked page comes back and is parked again at the cost of one query while the AI is still refused (`vf.blocked`); never counted as a failure. A refusal naming no time (Model Studio's free quota) waits `NO_TIME_WAIT` (1 h). **A failed call** (bad answer, timeout) is tried 3 times (`MAX_TRIES`), then the page waits for `again` or Stage 2's sweep. A failed call's page waits under "the call failed", so vf-grouper never re-sends it (no bouncing).
  - Invariant: `status='queued'` ⇔ the page has a ticket (on `q.pages` or in the waiting room).
  - `worker.vf again` now only puts waiting pages back on `q.pages`.
  - First run (2026-09-29): vf-grouper sent pages 12 and 14 back by itself; both look-agains ran with no command. Page 12's first call came back empty (JSONDecodeError); the retry through the queue worked.
- **On n8n (Stage 2, 2026-09-29).** vf's workflows live in `n8n/` and are loaded by **`scripts/n8n-setup-vf.sh`** (copies them in, imports, publishes, restarts n8n). n8n directs the flow; RabbitMQ and the workers do the work (per-page work in n8n would be 6,800 executions a day).
  - **`vf-intake`** (webhook `/webhook/vf-intake`): vf-ui's upload posts to it (no background thread any more; if n8n refuses, the upload says to run the setup script) → `http://vf-ui:8000/internal/intake/split` → `/internal/intake/enqueue` → `q.pages`. Checked end to end with a synthetic one-page PDF: n8n → split → vf-worker-3 read it → vf-grouper grouped it 2 s later (then the test batch was removed).
  - **`vf-sweep`** (every 3 h): `/internal/vf/sweep` → `vf.sweep`: pages still waiting for the AI with no ticket go back on `q.pages` (a page whose calls failed gets one more try per sweep), and batches with a bundle in `grouping` regroup. Nothing is sent while the AI is refused.
  - **`vf-notify`** (every 5 min): `/internal/vf/notify` → `common/notice.py record`: bundles that newly need a person (`needs_review`, a sor + fingerprint no earlier notice had) become one row in **`staging.notice`** (`schema/018-notice.sql`). The UI shows them only in the UI for now (the user's choice): a red count on the Review tab (`_needs_you` in `ctx`) and "New since you last looked" on /review. They're marked seen only when a browser shows that page (it posts `/notices/seen` after loading), never by a fetch: the screen tests once marked n8n's first notice seen before anyone had looked. A channel (Telegram, email) is one more n8n node. First notice by n8n itself: 15:10 WIB, "8 bundles need you".
  - The Status page lists n8n and vf-grouper (8 services).
- `docker compose restart` keeps a container's old environment. After changing `.env`, recreate only the service that needs it: `docker compose up -d --no-deps --force-recreate vf-teacher`. Recreate the page workers with `--force-recreate vf-worker` (a plain `up -d` can leave the first replica on old code). A worker stopped mid-page leaves its ticket unacknowledged: RabbitMQ gives it to another worker.
- Database `ocr_vf`, RabbitMQ vhost `vf`, MinIO keys under `vf/`. It reads v1's page renders and, read-only, v1's DB (`MAIN_DATABASE_URL`).
- **Upload works here too (the user, 2026-09-25: "i want a fresh start").** http://localhost:8001/upload stores the PDF under `vf/scans/` and hands it to n8n's `vf-intake` workflow (Stage 2; before, a background thread in vf-ui split it). Renders go under `vf/pages/<batch>/` (`intake.PREFIX`), and tickets go on vf's own `q.pages`. A label on such a page resumes it too (`app.resumable`: `pages/…` or `vf/pages/…`; until 2026-09-29 only `pages/…` did, so page 12 of 7000363700-03 stayed unsure after its label). The answer key grades only the file `7000356304 - 7000356499.pdf` (`GOLDEN_FILE`), so a new batch is never graded against batch 1's key.
- v1's intake workflow (`sambOcrIntake001`, webhook `/webhook/intake` → `http://ui:8000`) is still in n8n but has no UI to call. It's harmless and can be unpublished.

**Commands (from this folder):**
```bash
docker compose up -d                                                     # vf-worker + vf-ui
docker compose exec vf-worker python -m worker.clone b-4bab9b736d 1-31,48,52,57   # identities + labels only
docker compose exec vf-worker python -m worker.vf once b-4bab9b736d 1,4,15 [--v1-reading] [--no-second-look]  # run pages now (dry run: v1's reading instead of Gemini)
docker compose exec vf-worker python -m worker.vf again b-4bab9b736d [pages]      # put what waits for the AI OCR back on q.pages (usually not needed: vf-grouper and the waiting room do it)
docker compose logs -f vf-worker vf-grouper                                       # the queue at work
./scripts/n8n-setup-vf.sh                                                         # load/replace vf's n8n workflows (intake, sweep, needs-you)
docker compose exec vf-worker python -m worker.zoom report b-4bab9b736d 1-31      # the zoomed check on v1's values, read-only
docker compose logs -f vf-teacher                                                 # the teacher at work (it runs by itself)
docker compose exec vf-worker python -m grouper.group b-4bab9b736d [--recheck]   # group now (it also runs after every page); --recheck after Satellite data changes
docker compose run --rm -T -v <folder with the 3 CSVs>:/data/so:ro -v ./scripts:/scripts:ro vf-ui python /scripts/load_satellite.py /data/so   # Satellite's export (11 s)
docker compose exec vf-teacher python -m worker.lesson backfill|run|exam …      # by hand: lessons for old labels, a wake-up, exam score
docker compose exec vf-worker python -m worker.vf shadow b-4bab9b736d 1-31      # what a rule change would change; writes nothing
docker compose exec vf-teacher python -m grouper.matching propose b-4bab9b736d  # AI proposals for product lines nothing else matched (vf-teacher holds the Z.ai key)
docker compose exec vf-worker python -m grouper.crosscheck shadow b-4bab9b736d   # what a bundle-rule change would change (status, checks, items left); writes nothing
docker compose exec vf-worker python -m publisher.publish b-4bab9b736d          # phase 8: publish the finished bundles (--undo <SOR> takes one back)
docker exec -i samb-ocr-postgres-1 psql -U ocr -d ocr_vf -v ON_ERROR_STOP=1 < schema/017-fp-title.sql   # a migration, by hand (vf has no postgres of its own)
docker compose exec -e PYTHONPATH=/app vf-ui pytest -q tests/                   # 249 pass; test_vf_acceptance fails while pages wait for a look-again; v1's phase tests skip
```

**Which model reads: `VF_AI_OCR`** (in this folder's `docker-compose.yml`)
- `gemini` → `common/models/vlm.py`.
- `provider:model` → `common/models/openai_vlm.py`, one adapter for any OpenAI-compatible vision model. Providers: groq, openrouter, zai, mistral, dashscope, ollama. Keys: `GROQ_API_KEY` etc. in `.env`.
- Current: **`dashscope:qwen3-vl-plus`** (Alibaba Model Studio, Singapore/International; from 2026-09-28, set as `VF_AI_OCR` in `.env`). Before: `groq:qwen/qwen3.8-27b` (2026-09-24 to 09-28; Groq's 200K tokens/day ≈ 30 pages).
  - **Free quota:** 1M tokens per model for 90 days, Singapore only, and only once the account information is complete. Turn on **Free Quota Only** (console → Free Quota) so nothing is ever billed: the quota's end then comes as 403 `AllocationQuota.FreeTierOnly`, which `openai_vlm` raises as `DailyLimit` (the page waits; `again` stops).
  - Measured on `7000363700 - 7000363703.pdf` (16 pages): ~6.2K tokens a read, ~3.3K a look-again; the whole file used ~130K.
  - qwen3-vl-plus copies an answer example literally: the look-again prompt's example must be valid JSON. It also garbles a `box` now and then; `openai_vlm._json` drops a garbled box and keeps the answer (`tests/test_vf_keys.py`).
  - It leaves some FP fields unread (page 4's customer code); `satellite.settle` fills them from the SO record (a `None` field once crashed the page: `test_satellite_fills_an_fp_value_the_ai_did_not_read`).
- A reading is tied to the model that made it: `fields_version` = context hash + `@` + the model. Switching model re-reads; it never reuses another model's reading.

**Two amount rules**, found with Qwen and now in `common/verify.py` (they apply to v1's check too):
- **One meaning per printed amount** (`verify.amount`, rupiah conventions):
  - `111.586` = 111586;
  - with both marks, the last one is the decimal;
  - a single mark followed by exactly 3 digits groups thousands.

  vf also rewrites every amount's value from its printed text (`vf.normalise_amounts`, keeping the model's `ai_value`). Why: Qwen stored `111.586` as one hundred eleven.
- **Cut-off amounts never get ✅.** An amount ending in a mark plus 0–1 digits (`1.014.424,5`, `1.078.330,`) was cut by the scan's right edge. On page 3, Tesseract and Qwen both read the half-printed `,3x` as `,5`.

**Phase 6: grouping** (decided with the user 2026-09-25; `grouper/group.py`, `common/satellite.py`, `schema/011-grouping.sql`)
- **Only resolved keys link.** A value is resolved when it's backed by print (Tesseract's reading, the QR code, the zoomed spot, a look-again that print backs), by Satellite's SO record, by the store printed on the page (S4), or by a person. A value only the AI read waits. A key waiting for its look-again holds its page, and every page that would link through it.
- **"Read the same twice" is not a confirmation.** Page 6: the AI read the CPO (pen stroke through the 5), the customer code and the customer name the same way twice, and all three were wrong.
- **The FP is the hub** (SOR + Nomor CPO):
  - a TTG joins by the SOR it prints (No Ref; DO#/S/Fak without the letters) or by its PO number = an SO's Nomor CPO;
  - a PO joins by its PO number;
  - a Faktur Pajak by its billing number once the SO is posted in SAP (held until then);
  - Pelunasan row by row (later stage).
- **Page order is used only for continuations:** a continuation belongs to the page before it.
- **Satellite as a witness** (`satellite.settle`, run before and after the look-again so it never asks what Satellite settles):
  - an FP whose SOR is known (QR code, print, a person) is confirmed or corrected by its SO record (CPO, customer code), because SAMB prints the FP from it; if print and the record disagree, a person decides;
  - an FP's SOR comes from Satellite only as a pair (SOR and CPO both match one SO; page 8's misread SOR is another real SO);
  - a customer's PO number counts only on an exact match with no other SO's CPO one character away (Hero's run in sequence).
  - The AI's reading is kept as `ai_value`; `field_check` stores it with the new value in `adjudicated_value`.
- **Held, never guessed:**
  - not read or unsure;
  - no resolved key;
  - keys naming different SOs;
  - a PO number matching several SOs;
  - two FPs with one SOR;
  - an FP whose SOR isn't resolved: a suggestion (an SO with documents here but no FP), never a link;
  - `fp_missing`.
- **A person's confirmation:** on `/bundles`, a held document shows its blocking key (the line of the page, zoomed) and a form. It's saved in `staging.field_confirmation` (survives re-checks), and then `vf.recheck` (no model call) and a regroup run.
- **Folders:** `vf/bundles/<SOR>/<batch>-p001-FP.png …` plus a manifest; held ones under `vf/bundles/_held/<SOR>/` and `_held/unplaced/`.
- **The `/bundles` screen** (the user's request):
  - complete bundles are a **Finder-style window** listing the real storage folders: a sidebar with a customer tag per colour, SOR folders → files → a preview (page thumbnail, kind, how the page joined, the manifest's JSON), a path bar, and arrow keys;
  - the held part (bundles without their FP, documents waiting for a key with the confirm form, pages not grouped) keeps the plain layout.
- **When it runs:** after every page (`vf.handle` → `regroup`), after a confirmation, or by hand. It's serialised per batch with an advisory lock.
- **`satellite.sor` holds Satellite's real export** (26 Aug–25 Sep 2026: 37,970 SOs; their 177,218 lines in `satellite.sor_item`), loaded by `scripts/load_satellite.py` into the vf database only. The CSVs are real customer data: local, git-ignored, never committed. With 38K SOs every sample PO number has another SO's CPO one character away, so page 4's PO number lost its Satellite ✅ and page 4 is held (`no_resolved_key`) until composite resolution (7b). The by-eye seed (`testdata/satellite_sor_seed.sql`) is only a test fixture now. After a reload, run `grouper.group <batch> --recheck`.

**Phase 7: cross-checks and Review** (in progress, one stage at a time: 7.0, 7a–7e; the plan is in the session's plan file)
- **Two meanings of "truth"** (the user, 2026-09-25):
  - what the FP says: Satellite, header and lines, because SAMB prints the FP from it;
  - whether the FP is right: FP ↔ PO must be **exact** (totals, each line's price and discount), else Review, because the FP can carry a price-input error;
  - FP/PO ↔ TTG compares **quantities only**; a shortfall is a **tolakan** (the customer rejected goods), not an OCR error;
  - PO and TTG values are never overwritten from Satellite: comparing them with SAMB's record is the point.
- **Product codes** between PO/TTG and the FP: the AI proposes a matching, code checks the numbers, a person confirms each new pair once, and `product_code_map` grows from that (7c). Satellite's `customer_item_code` is just SAMB's code, not a mapping.
- **The FP prints QTY as cartons / leftover pieces** from the pieces and pieces per carton (72 at 24 a carton → "3 / 0"). Satellite's `ordered_qty` is how it was ordered, not what's printed.
- **Satellite's CGR (the goods receipt)** is recorded when the driver comes back from a delivery and hands over to the warehouse (the user, 2026-09-25).
  - Per SO: CGR number and date, on 34,001 of 37,970 SOs. Per line: received `cgr_qty`, rejected `rejected_qty`, `reject_reason`.
  - 2,122 lines on 1,554 SOs have a rejection. On every one of them, ordered = received + rejected, and **invoiced = received**.
  - Reasons: "TOLAK TOKO (…)", where the store refused (not as ordered, overstock, freezer full, wrong barcode, short loading, damaged packaging, not in the order), and "BATAL GUDANG (…)", cancelled by the warehouse.
  - **The paper FP goes out with the goods, before the CGR** (page 10 printed 7 Sep; its CGR is 9 Sep), and **SAMB never prints a new invoice** after a tolakan (the user, 2026-09-25). So the FP always shows the SO **as ordered**, while Satellite's invoice amounts shrink to what was received.
    - 7b therefore uses the SO as ordered (`satellite.paper`): quantities as ordered, and amounts = the lines' amounts and VAT summed. That equals the invoice exactly on all 31,512 unchanged SOs.
    - 6,049 of the 6,134 SOs changed after printing can be rebuilt this way. The other 85, whose lines carry no amount (free goods), have no amount corrected.
    - Composite resolution looks up the printed total too. No sample SO has a rejection.
    - For 7c, both FP and PO are as ordered, and the TTG is as received.
- **7.0 (built):**
  - `vf.page_verdicts` is the one place verdicts are made (witnesses → settle → the 7a rules); `vf.recompute` makes them from stored data, `recheck` stores them, `shadow` prints what would change;
  - the answer key is extended by eye: PO/TTG header values, every PO/TTG row, the printed amounts with their labels, and the FP amounts the scan's right edge cut;
  - grading lives in the UI (`phase7_grade`): PO/TTG against the key, FP header and lines against Satellite. It's served at `/api/batches/{id}/phase7`, with a box on the batch page (cached: recomputed when verdicts change, or after a minute);
  - the bend test changes every digit, including line items.
- **What 7.0 found:** 16 wrong ✅ (pack-size reads counted wrong, the user's decision) and 14 one-digit bends that passed:
  - Hero POs 18/24/27 took TOTAL NET PURCHASE as the total (their TOTAL NETTO fell on a page not in the scan);
  - FP amounts cut at the edge (p10 DPP/PPN, p26 DPP) or faint (p8 total, via the look-again);
  - **p29's DPP and total:** the AI read both cut last digits wrong *consistently*, so they add up exactly. Adds-up over three AI values is not an independent witness;
  - table quantities from the wrong column: p7/p9 the pack size, p12 the cartons ordered, p21 "1 x 48";
  - bends: 12 FP amounts passed through the adds-up rule's Rp 1 tolerance, and 2 lone "0"s on p17's rows.
- **7a (built and adopted 2026-09-25): `common/gates.py`, rules on a page's verdicts after every witness.** They only take ✅ away, but for one arithmetic fill-in. No labels, no AI, no person, nothing about any customer: the user rejected per-customer formats in code, and a reviewer agent showed a learned label list can't work yet (the customer isn't known when a page is checked; column headers are unreadable on about half the formats; one wrong confirmation would spread).
  - **complete:** an FP amount counts only printed in full, the way SAMB prints it (`1.126.011,00`);
  - **sums:** DPP + PPN = Total confirms one FP amount only from the two others backed by print, QR, Satellite or a person, exactly (±0.005), PPN 11%/12% of DPP (±Rp 1). v1 keeps its looser rule (`verify.header(sums=True)`); vf checks with `sums=False`;
  - **identity:** three whole FP amounts that don't add up keep no print-only ✅. Nothing is removed on the PPN rate: it misses 11% of DPP by more than a sen on 1,608 of 33,159 SOs;
  - **with_tax:** a PO total includes PPN: total − PPN, taxed at 11%/12%, must give the page's backed PPN (±Rp 1);
  - **columns:** a table quantity or unit never gets ✅ from its row's text alone (the row prints the other columns too);
  - **dates:** none after the scan day.
  - Measured with `worker.vf shadow` before adopting, then `grouper.group --recheck`: **0 wrong ✅, every bend caught, grouping unchanged, no key changed.** It cost 33 right ✅: 7 FP amounts (p6, p17, p29; 7b's Satellite settles them) and 26 table quantities and units (a person, until a later stage). 137 right ✅ remain.
  - The look-agains it adds (p6, p10, p17, p18, p24, p29; 26–28 already waited) **run after 7b**, when Satellite has settled the FP amounts (the user, 2026-09-25). No look-again for table cells.
- **7b (built and adopted 2026-09-25): Satellite for the FP, after the 7a rules** (`satellite.settle_record`, run last in `vf.page_verdicts`).
  - An FP whose SOR is resolved takes DPP, PPN, Total and its item lines (code, name, cartons / pieces) from its SO record: confirmed when they match, corrected when they don't, the AI's reading kept (`ai_value`; a line's `ai_values`, stored as `vlm_value` beside `adjudicated_value`). A value print backs in full that still differs goes to a person (the SO may have changed since printing).
  - **Rows pair with SO lines by name** (`satellite.pair_lines`): SAMB prints the SO line's name, and a code the AI read never outweighs it (page 1 read 1000566 on two rows; row 5's 1000564 is another line's code). Only when names tie (Boots' LUMINOUS / SMOOTH / YOUTHFUL variants) does a code decide, and only one print backs. An SO line no row matched is a row the reading missed (page 3's line 40).
  - **A TTG's receipt date** equal to Satellite's CGR date gets ✅ by satellite; more than 7 days apart loses a print ✅.
  - **Composite resolution** (`satellite.resolve_fp`, with `common/confusions.py`: 5/6/8, 0/6/8, 1/7, 3/8, S/5/$, R/5, a digit dropped or doubled, the decimal mark, one change at a time) for an FP whose SOR can't be read. Among the SOs whose total the printed total could be, exactly one must also match something else read on the page (customer code, Nomor CPO, customer's name, every item code); what matches every candidate tells none apart.
    - **SAMB ships one order to many stores:** eight Boots SOs of 1,126,011.00 on 4 Sep 2026; page 8's 754,022.80 is also Hari Hari Ciledug's, same two items. So a total never decides alone, and page 8 (store, code and CPO misread) stays with the person who confirmed it.
    - Measured with each FP's SOR set aside: pages 1, 3, 6, 17, 29 resolve right (page 3's total fits 20 Boots SOs; only one has its customer code and name), 0 wrong, also with the total bent at every digit (`tests/test_vf_resolve.py`).
    - **Not for PO/TTG:** the Boots POs 4505832720–29 are the same order to ten stores, so neither total nor items can separate them; only the exact PO number or the store can.
  - Measured (`shadow`, then adopted): **143 ✅ gained, 0 lost**, every FP clear, keys and grouping unchanged, 0 wrong ✅. Right ✅ went from 137 to 280. The look-agains left: pages 18, 24, 27, 28 (about 16K tokens), run by the `again` job restarted after 7b.
- **7c (built 2026-09-25): the bundle's documents checked against each other and Satellite** (`grouper/crosscheck.py`, run after every grouping under its lock; `schema/013-bundle-checks.sql`).
  - **Checks:** SO in Satellite; documents complete (`customer_profile.expected_docs`, default FP + TTG); PO addressed to SAMB; **FP ↔ PO total and PPN equal up to Rp 5** (`crosscheck.ROUNDING`; the user, 2026-09-25: "accept differences under a few rupiah". Hero rounds per carton line, 0.02–2.72 apart; Boots per piece with PPN, 5.00); FP ↔ PO lines (quantity as ordered, price up to the customer's rounding, discounts); **received vs Satellite's CGR**, where a shortfall is a **tolakan**, named with Satellite's reason; dates in order; FP ↔ Faktur Pajak (n/a).
  - A check passes or fails only on resolved values; otherwise it's unknown and says whether the reading agrees. **A row's quantity is never resolved by its text** (the 7a column rule), so quantity checks read "agrees with Satellite (column not verified)" until a person confirms, or 7f learns columns.
  - **Status:** grouping while a page waits for the AI; auto_ok when every page is clear and every check passes; else needs_review with its reasons. A person's reviewed is undone when the bundle's fingerprint changes.
  - **Product matching** (`grouper/matching.py`, in this order):
    1. a person's decision;
    2. `satellite.product_code_map`, keyed by chain (`ship_to_parent_customer_id`, since Satellite's customer group is empty) or by a barcode with a valid check digit (Satellite has no barcodes);
    3. the SO's only line;
    4. a PO row's numbers: pieces, and a price that fits per piece or per carton, with or without PPN, to within Rp 1;
    5. a TTG row by its PO row's code;
    6. an AI proposal: `python -m grouper.matching propose <batch>` in **vf-teacher** (GLM-4.7-Flash, text; vf-worker has no Z.ai key). It's checked on the numbers, and a person confirms it once (7d); that confirmation fills the map.
  - **Measured on the sample:** 10 bundles need review and 1 waits for the AI OCR.
    - FP ↔ PO totals: 5 of the 8 bundles with a PO are within Rp 5 of rounding. The other 3 (pages 18/24/27) print no total with tax, so they go to Review.
    - Hero PO rows match by their numbers, and their GRN rows through the PO row.
    - The AI's proposals: **10 of 10 right**. That's all 6 Boots rows (including the three 425ML variants with the same quantity and price), page 7's two rows, and two of page 2's. It said "not sure" for page 2's OCEAN FISH (ADULT or KITTEN 1.4KG?), as asked.
    - **Z.ai's free tier refused a 4th call within about 2 minutes** (1302 "Rate limit reached"). Two matcher runs at once made it worse. The matcher now waits 30 s between calls (`MATCH_PAUSE`) and stops at the first refusal; re-run it later.
    - No tolakan in the sample.
- **7d (built 2026-09-25): the Review screen**, `/review` (a list per bundle) and `/review/<SOR>` (`review_view` in `ui/app.py`; `schema/014-review.sql`; `PHASE_BUILT = 7`).
  - **Shows:** what is left before approval; every check with its reason; each document's unsettled header values, with their line of the page zoomed; and each PO/TTG row: its pairing with an SO line and its unconfirmed cells (quantity; for a PO also price and discount).
  - **Suggestions are buttons, never pre-filled:** Satellite's CGR quantity or date, the SO's quantity, price or discounts, the FP's total, the AI's reading, the AI's proposed pair.
  - **Actions:**
    - confirm or correct a value (`/review/confirm`). A line cell's field is `lines[<row key>].<column>`, keyed by the row's own code (`satellite.row_keys`), not its position, so it survives a re-read that reorders rows (`satellite.person_lines`);
    - pair a row with an SO line, or with none (`/review/pair`). That fills `satellite.product_code_map` for the chain, with a `customer_profile` row;
    - accept a difference with a reason (`/review/accept`, `staging.bundle_decision`). It holds only while the check says exactly the same (its print);
    - approve (`/review/approve`), refused (409) until `crosscheck.can_approve`.
  - **Checked end to end on bundle 10–12**, then undone:
    - 5 confirmations (both quantities, the discount, both vendor codes) made every check pass, so the bundle turned auto_ok;
    - approving made it reviewed;
    - removing the confirmations demoted it to needs_review by itself, because its fingerprint changed.
  - **All 6 phase-7 gates pass,** including "no ⚠ value reaches Review before its look-again": 27 ⚠ header values on Review, all asked first.
- **Deferred to a stage after 7d (called 7f):** a memory of what a column header means for line quantities, keyed by document type + header words (not customer), a person answering blind, replayed on stored pages before use, demoted when contradicted. 7c pairs a customer's amounts with the FP's by arithmetic, not by family rules in code.

**Verification redesign (decided with the user 2026-09-26/27; the plan file has the details; stages S0–S5, one at a time)**
- **Why:** on batch 1, 271 values waited for a person (27 header, 244 line cells), about 25 per bundle, which is ~40,000 a day at 1,700 SOs. Most were right:
  - Tesseract misread the print (`S10232` → `510232`);
  - a table row doesn't show which column a number came from;
  - PO/TTG values could only be proven by print or a person.
- **The user's rule:** a person only for unsure classification, or an anomaly in the AI OCR's result. Tesseract failing to read something is never, by itself, a reason for a person.
- **Two sides, each with its own reference.** Documents are never evidence for each other.
  - **Order side:** PO ↔ Satellite SO as ordered (`order_total/order_dpp/order_ppn`). The FP is the paper copy of that order.
  - **Delivery side:** TTG ↔ Satellite CGR/received. The invoice (`inv_…` = `sor.dpp/ppn/total`, line `invoice_nett_amount` = `sor_item.invoice_amount`) is built from the CGR: invoice qty = CGR qty on all 155,647 lines. It is 0 before billing. The FP is never the reference for a receipt.
- **Totals decide, rows explain.**
  - When a usable total exists (read and complete), it decides, and rows only explain a mismatch.
  - When none exists, the printed row amounts decide.
  - Row quantities are never evidence: on Hero receipts the AI copies the *ordered* column (`JUMLAH DIPESAN`), not `JUMLAH DITERIMA`.
- **An AI-read value passes without Tesseract** when it's within the customer's allowance of its Satellite reference. The allowance is confirmed once per customer, Rp 5 until then.
- **Other decisions:**
  - a new customer gets two looks (its first bundle; its first bundle with a rejection);
  - record-only fields never block and are never re-read;
  - a receipt date passes when it's in order;
  - a customer whose receipts print the ordered value passes unless Satellite records a rejection;
  - a key read by the AI links when the store printed on the page matches that SO's ship-to.
- **An unsettled decision value** goes to a person only once its reading is confirmed: print backs it → a person now; AI-only → one blind second look first. Rows and cut totals skip the second look.
- **S0 (built 2026-09-27):**
  - `crosscheck.inputs`/`evaluate` (pure) and `shadow` (writes nothing; batch 1: 11 bundles unchanged, 33 items left);
  - passing checks list the values they `used`, and `_vf_bend_bundles` bends each (19 bent, none passed beyond its allowance);
  - `phase7_values` grades decision values: 29 of 29 PO/TTG totals the AI read are printed on their page; 50 of 53 row amounts are in the AI's row text;
  - the FP is graded as ordered (`FP_SATELLITE` → `order_*`);
  - tests: `tests/test_vf_shadow.py`.
- **S1 (built and adopted 2026-09-27): what decides, and what the page decides.**
  - `common/fields.py` `DECIDES`, kept outside the AI's field list, so no page is re-read (`test_no_reading_is_redone`):
    - keys (FP `sor`; TTG `no_ref`/`purchase_order_no`; PO `purchase_order_no`);
    - page (the FP's amounts);
    - support (the FP's CPO and customer code: they block only when print contradicts Satellite);
    - bundle (PO total/PPN, TTG date: judged by the bundle, S2).
    Everything else is kept as read.
  - `vf.outcome` waits only on `page_settled`: a key, the FP's own amounts, no support conflict.
  - `vf.second_look_asks`/`to_ask` ask only those. Never a value flagged `conflict` (Satellite's `settle`/`settle_record`) or `cut` (verify); never a bundle value; never a value kept as read.
  - `vendor_is_samb` is `info` (it fails only when print names another vendor).
  - Review folds values kept as read.
  - Gate 4: no auto_ok bundle rests on a wrong *decision* value. Gate 6 applies the page's own look-again rule to stored verdicts.
  - **Measured on batch 1:** 0 ✅ lost or gained, 0 new look-again calls. 15 PO/TTG pages went from needs_person to clear, and all 31 are clear. Review items went 33 → 23: receipt vs CGR 11, FP ↔ PO lines 8, FP ↔ PO total 4, which is S2's job.
  - Tests: `tests/test_vf_decides.py`; `test_vf_flow` moved onto the page's own values.
- **S2 (built and adopted 2026-09-27): totals decide, on two sides** (`grouper/crosscheck.py`: `_order_side`, `_delivery_side`, `_dates`, `_settle` = item 11, `_rows_settle`):
  - **The order side** compares the PO's total with `satellite.paper()` (the SO as ordered; the FP page is never read): with tax against `order_total`, before tax against `order_dpp`, and a printed PPN against `order_ppn`. Hero's PO "DPP" is 11/12 of the price (the 12% rule), so a PO's DPP is never compared.
  - **The delivery side** compares the receipt's `total`/`dpp` with `satellite.received()`, by the SO's `status`:
    - invoiced: `sor.dpp/total`;
    - a CGR recorded but not billed: reconstructed;
    - no complete CGR: the check is `waiting` and the bundle is `grouping`, never a person;
    - SOF or cancelled: unknown.
  - **Every total a receipt prints must fit** (one fitting never covers another). Several POs or receipts are summed.
  - **With no usable total** (read and not cut), the printed row amounts decide, each line once. Otherwise rows are information (`fp_po_lines` is `info`; the receipt's row notes sit in `received.rows`).
  - **Item 11 at the bundle:** a value only the AI read, beyond its reference, gets `ask` → `crosscheck.ask_again` stores `second_look.bundle_asks`, the page waits, `again` runs it (`vf.second_look_asks(requested=…)`), and its answer counts within the allowance.
  - **The TTG projection gained `total`/`dpp`** (source `check`: `ddl()` skips them, and the reading's hash is unchanged). They now get print verdicts: 9 new ✅ on batch 1.
  - **Receipt date:** Satellite's CGR date, or in order.
  - **Review** suggests Satellite's order values for a PO, and the received value for a receipt.
  - **The bend test** covers values from continuation pages and row amounts too, without a stored look-again answer.
  - **Measured on batch 1:** 11 needs_review → **8 auto_ok** and 3 needs_review. Review items went 23 → 4, all printed differences of a few rupiah (Hari Hari's and Hero's rounding: S3's calibration):
    - p9's receipt is 14.36 off;
    - p18's PO is 9.36 off, and p24's 10.73;
    - p19 prints no total: its look-again found none, and its rows are about 20 off, from Hero's rounded carton prices.
  - A matched PO total records its `meaning` (with tax / before tax) for publishing. Gate 4 judges a PO's or receipt's amounts by whether the number is printed (`phase7_values`), not by the key's label: p27 prints only its total before tax, and it equals the order's DPP.
  - All phase-7 gates pass: 0 wrong ✅; 39 bundle values bent, none passed.
  - Tests: `tests/test_vf_totals.py` (including the user's rejection example: FP 10, CGR 5 + 5, TTG 5 passes on the received value, and the FP is never read); `test_vf_crosscheck.py` rewritten.
- **S3 (built and adopted 2026-09-27): each customer gets two looks, once each** (decisions 4, 5, 8). `schema/015-customer-calibration.sql` adds to `satellite.customer_profile`, keyed by chain (`satellite.chain_of`: `customer_parent`, else the store):
  - `rounding_allowance`, `allowance_by/at`;
  - `receipt_shows` (`received`/`ordered`), `receipt_by/at`.
  - **The `calibration` check** (`crosscheck._calibration`) holds every bundle of a customer on Review until its allowance is confirmed; Rp 5 applies until then. It also holds the customer's first bundle with a tolakan until a person says what its receipts print. It can't be accepted away.
  - **Both sides use the customer's allowance** (per document; per line for rows) and record the `gap` they saw and the `allow` they used.
  - **`receipt_shows='ordered'`:** a bundle with a tolakan goes to a person; one without passes.
  - **Review's calibration box** (`_calibration_view`, `/review/calibrate` → `crosscheck.calibrate`) lists the customer's gaps on every bundle and suggests the smallest step that covers them (`allowance_for`: 5, 10, 15, 20, 25, 30, 50, 100; beyond 100 it isn't rounding). One answer regroups every batch holding that customer.
  - Gate 5 judges a PO against its customer's allowance.
  - **Measured on batch 1:**
    - uncalibrated: 11 needs_review, three customers to look at (Hero, Hari Hari, Boots);
    - calibrated through the real Review form (Hero 20, Hari Hari 15, Boots 5; then undone): **11 of 11 auto_ok**, every gate passing (47 values bent, none passed).

    The real calibration is the user's.
  - Tests: `tests/test_vf_calibration.py`.
  - After the session: Edward calibrated Hero at Rp 20 (2026-09-27), so batch 1 shows 7 auto_ok, 4 needs_review (Hari Hari, Boots still to calibrate).
- **After S4 (2026-09-28), on `7000363700-03`:**
  - **An FP's decoded SOR QR code is its SOR** (`satellite.settle` step 1b): the AI's reading is kept as `ai_value`, the verdict is ✅ by qr, and Satellite then settles the rest. Page 13 read SOR26110254669 twice; its QR said 264669.
  - **Copies count once** (`crosscheck.distinct`): POs by their PO number, receipts by their receipt number. Four copies of PO.2026.09.32029 had summed to 775,397 × 4.
  - Where values go (the user asked): keys → /bundles when unresolved; FP amounts, CPO, customer code → Satellite, Review only on a print/Satellite conflict; PO total/PPN, receipt total/date → the bundle's checks against Satellite → Review beyond the allowance; everything else is kept as read and never blocks.
  - (Closed by the rows witness below: a correct AI-read key of a single-store customer settles when its rows fit that SO clearly better than every neighbour.)
- **S4 (built and adopted 2026-09-27): a key the AI alone read links by the store printed on the page** (`common/satellite.py`: `near_keys`, `store_can_tell`, `by_store`, `_store_decides` in `settle` step 3; `worker/vf.py`: `store_pending`, `store_step`, step 7b; `schema/016-ship-to.sql`).
  - **The case:** page 4 (Boots PO). The AI read `4505832724`, Tesseract `20583272`. `4505832724` is one SO's Nomor CPO, but 11 SOs are one character away (Boots' one order to ten stores is 4505832720–29), so it was held until a person confirmed it.
  - **When it asks:** a PO number or a TTG's SOR reference (with or without its letters) only the AI read, matching exactly one SO, with SOs one character away, **after** its look-again, and only if every such neighbour ships to a store with a word this SO's store lacks (`store_can_tell`). One store's runs of numbers never ask: AEON EASTVARA's POs 10101000125411–48, Duta Buah BSD's …28/…29, Hero's DC, Hari Hari BINTARO's 5213310/5213350. Until answered the page is `waiting_ai` (`again` runs it), never a person.
  - **The question is blind:** the page image and "which store do the goods go to", never Satellite's store names or the key (`vlm.STORE`, `openai_vlm.store`/`vlm.store`). It's outside the AI's field list: a field there changes `fields_version` and re-reads every page.
  - **It decides only when both hold** (`by_store`, words of 2+ letters/digits):
    - every store word on the page that fits a neighbour's store fits this SO's store too (a misread …23 → BINTARO XCHANGE 2: the page's HARAPAN INDAH fits …24: held);
    - this SO's store shares more words with the page's than **any other store in Satellite** (a number misread by two characters lands outside the neighbours; 'HARAPAN INDAH' alone ties with Hari Hari's DUTA HARAPAN INDAH: held; 'BOOTS' alone: held).
    Then the key is ✅ `by: ship_to`. An unsure or empty answer decides nothing; it's asked once per page.
  - **Measured:** Boots' ten POs × ten stores: 10 of 10 link to their own store, 0 of 90 wrong pairs. Of every store name in Satellite, only BOOTS HARAPAN INDAH AVENUE links 4505832724. The real blind question on page 4 (Qwen on Groq, one call) answered `BOOTS HARAPAN INDAH BEKASI`; with it, page 4's PO number goes ⚠ → ✅ ship_to (the link Edward confirmed by hand). Shadow on both batches: 0 ✅ lost or gained, no outcome changed, no question asked (page 4 is confirmed by a person; batch 2's held pages 2, 6, 7 are one-store runs, and page 3 has no key). All phase-7 gates pass.
  - **Worth:** it frees multi-store chains' pages (Boots-style), not single-store or DC customers. The `sor_no` neighbour list is dense (39 SOs one character from SOR26110255837), which is why the whole-Satellite comparison is required.
  - Tests: `tests/test_vf_keys.py`.
- **S5 (built 2026-09-28): Review for anomalies only** (`ui/app.py` `_open_items`, `_rows_against_order`; `review_sor.html`). The user: "this is an RPA, it's supposed to make the user faster".
  - **"What is left" is one card per open item**, each with the one action it needs:
    - a check that doesn't pass: both amounts, the gap and the customer's allowance; for the order side, the PO's rows against SAMB's order lines with their printed amounts (a PO row with no line next to an SO line with no row is either one product not paired yet or the product only one side has); one-click accept (reason buttons; the reviewer's name is remembered in the browser); the values it used, folded, to correct a misread;
    - a customer's first look (S3);
    - a page that holds the bundle: only its key, an FP's own amounts, or a support value in conflict with Satellite;
    - a missing document: a link to /bundles, or accept "the document comes later";
    - waiting for the AI or Satellite: information, no form.
  - **Everything else is under one closed "All values" fold, never required:** every check, every value read, row confirmations, product pairing.
  - Also: a bundle never checked (held, no checks) can't be approved; quantity suggestions carry their unit (`48 PCS`: a bare 48 on a carton row was 48 cartons); "none of these" on a row pairing takes a stated reason (`NONE_REASONS`, stored in `line_match.reason`).
  - **Measured:** forms before the fold per bundle 1–7 (the Duta Buah bundle: 73 → 7, of which 4 need a click: 2 calibration questions, 2 accepts; the other 3 are folded corrections). Tests: `tests/test_vf_review.py` (S5 section).
- **After S5 (2026-09-28):**
  - **Rows pair by their printed amount** (`matching.amount_fits`, rule `amount` after `numbers`): a PO row takes the only unpaired SO line whose amount (net, or with PPN) its printed amount fits to 0.03%; two rows at one amount are told apart by name, clearly, or not at all. Duta Buah's PO counts 48 PCS where SAMB's line says 2, so `numbers` couldn't pair it. On both batches: 6 new pairs, all right; ALPENLIEBE KARAMEL (the product SAMB's order lacks) stays unpaired. Pairs by amount never enter the product map.
  - **A bundle check: the page's store is the order's** (`crosscheck._store_named`, label "The page's store is the order's"): a second net for a wrong-order link, where totals can't help (Boots' one order to ten stores).
    - A store is named at a score of 1: its own words (only it has them among the customer's stores) weighted by how rare they are in Satellite's 4,184 store names (`satellite.store_df`: ≤ 12 names → 1, ≤ 25 → ½, more → 0; INDONESIA 62, HARAPAN 11), or its initials (Duta Buah's "CABANG : BSD").
    - fail → Review when a page names another store of the customer and not this order's; pass when it names this one; info (never blocks) when it names none (a head-office PO). The page's own issuer is never a competing store (Satellite lists PT. AEON INDONESIA among AEON's ship-tos).
    - Measured: Boots pages 4–5 against the ten Boots orders: the right one passes, all 9 others fail. Real bundles: 12 pass, 4 info, 0 false alarms. An address word can still name a store (Duta Buah's head office is on Jl. Jalur SUTRA; ALAM SUTRA is a store): a false alarm costs a Review click, never a link.
  - **A key settles by its rows** (`matching.rows_tell`, `satellite._rows_decide`, `by: rows`), for a PO number or SOR reference only the AI read that names exactly one SO while others are one character away (single-store customers number their POs in sequence: AEON Eastvara, Duta Buah BSD, Hero's DC). The page's rows must fit that SO's lines (`row_fits`: printed amount, pieces and price, or two shared words of 4+ letters) at least twice (once on a one-row page, by amount or quantity), and every neighbour fewer than half as often. Amounts and quantities are tried first (the order's own: a repeat order of the same products has other quantities), then product words. It runs before the store question (no model call).
    - Measured on both batches, keys taken as true: 22 of 28 settle (7000363700-03 p2, p6, p7, p11, p14, p15, p16; Hero, Hari Hari). Held: Boots p4/p5 (one order to ten stores: the store decides), Duta Buah's terms pages, p5, Hero p21.
    - The bend: each correct key replaced by each neighbour's real number (164 misreads): 0 settle. A misread's true order is among its neighbours, and the rows fit it at least as well.
  - **"Not printed on this page"** (`satellite.NOT_PRINTED`, a suggestion button in every Review confirm form): a person's answer that the page prints no such value (7000363700-03 p5: the AI took the receipt's quantity total 190.00 for its money total). The value becomes "(not printed)", ✅ by person, the AI's reading kept; `crosscheck._read` then never falls back to the AI's reading, and a "not printed" PO number is no key.
  - **The page view's check column says what a value does** (`roles` from `DECIDES`): holds the page (its key, an FP amount), judged by the bundle against Satellite, Satellite settles it (support), or kept as read, never blocks. It no longer says "a person checks it" for every unbacked value.
- **Receipts are compared in quantities, not money** (the mentors, 2026-09-28: "TTG doesn't need amount, only qty: compare the TTG's qty with the CGR"). `crosscheck._delivery_side`: each receipt row paired with an SO line counts in pieces (the line's pieces per carton; `matching.pieces` reads the unit's first word, so Indomaret's "CRT / PCS" counts cartons; CARTON is a carton), summed over the bundle's receipts, and must equal Satellite's `cgr_qty` on that line (a customer whose receipts print the whole order: `qty_pcs`, and a tolakan goes to a person). Every line Satellite received must be on a receipt row. A product whose unit differs between SAMB and the customer (Duta Buah's GUMFILLED: 1 for SAMB, 30 PCS for the customer) takes its size from the PO row that pairs by amount (`_unit_sizes`).
  - The TTG field list lost total/DPP (`fields.py`; not in the Jev context, so nothing is re-read); `DECIDES` TTG bundle = posting_date; the line column "qty" now tells the AI "the quantity RECEIVED, not ordered, not the pack size" (row wording isn't in `fields_version`: new reads only).
  - Rows pair by product words too (`matching`, rule `words`, repeated while a pair frees the next; `_same_word`: GUMFILLE/GUMFILLED, CHUP/CHUPS, STRAWBERRY/STRAW).
  - Measured: Indogrosir p11 (100 CRT = 1,000 received), Duta Buah p5 (5 lines) and Hari Hari p9 pass. AEON p3/p14 and Hari Hari p7 go to Review: the AI read the pack size or the ordered column. Re-reading AEON p3/p14 with the new wording didn't help: their received column sits under the scan's black band. A wrong column costs a Review, never a pass. The bend test bends receipt quantities too (kind `qty`).
  - **A pack size read as a quantity is named** (row `pack`: the number read equals the paired line's pieces per carton and doesn't fit; AEON p3 read 20 / 12 / 10 on its 20X200GR / 12X250GR / 10X320GR lines, where the receipt prints 0 / 2 / 1 carton received = Satellite's CGR exactly). Blank rows the AI returns are never counted. A row a person paired with "none: not in SAMB's order" is settled.
  - **Review's receipt card is fixable in place:** one box per questionable row (what was read → pieces, what Satellite received in pieces and cartons, "= its pack size"), with a quantity form (a unit button with Satellite's cartons, "0") for a paired row, and a "This row is SAMB's line: [choose…]" pairing form for an unpaired one. Tested through the real forms on AEON's SOR26110264129 (0 CTN, 1 CTN, pair + 2 CTN, "not in SAMB's order"): the receipt check passed, showing the tolakan; then undone (the answers are the user's).
- **A receipt whose number is the PO** (`satellite._receipt_is_po`): when a receipt's PO number isn't settled and its receipt number exactly equals one SO's Nomor CPO (trusted by print or a person, else Satellite or the rows; and one of its rows fits that SO), that is its PO number (`by: receipt_no`). AEON's receiving note has no PO field; its RECEIPT NO is the PO (p3). On both batches only AEON's two receipts match an SO; 12 others match none.
- **Review, redesigned for looking, not reading (2026-09-28; the user: "too much words, scattered, I don't know what to review")** (`review_sor.html`, `_plain`, `strip`, `passed` in `review_view`):
  - a header with the customer, one status pill ("Needs you · N decisions" / "Waiting" / "Ready to approve" / "Published") and a strip of the bundle's page thumbnails (a red "!" on a page with a problem);
  - one numbered card per decision, in plain words ("The PO asks Rp 36,414.70 more than SAMB's order"): the two amounts as big numbers with the difference in a red badge; only the rows that cause it ("ALPENLIEBE KARAMEL · not in SAMB's order", "✓ 6 other rows match"); reason buttons that accept in one click; "A number was read wrong? Fix it" opens the crop and the form. When one amount is under 5% or over 20× the other it says the AI probably read the wrong number and opens the crop (the Duta Buah receipt's 190.00 is its quantity total). The customer's one-time questions come after the differences;
  - "Already verified" as green chips; the name is typed once at the top (kept in the browser) and signs every form; a bar at the bottom: "N decisions left" or Approve;
  - everything else under "Show every value of this bundle (not needed to finish)". The forms post to the same endpoints as before.
- **Phase 8, publish (built 2026-09-28)** (`services/publisher/publish.py`; `/review/publish`, `/documents/<SOR>.pdf`):
  - **Finished = auto_ok or reviewed**, not held. `publish(batch)` regroups and re-checks first, then each finished bundle in one transaction: its rows in `satellite.doc_faktur_penjualan` / `doc_po` / `doc_ttg` (+ `_line`), its PDF record in `satellite.sor_document` (version bumps on a re-publish), and the bundle's status `published` with a manifest in `bundle.json` (`published_from`, documents, page_ref, confidence). The PDF (`{PREFIX}documents/SOR….pdf`, the upright pages, 1-bit at 300 dpi, ~80 KB a page) is stored first: a file without rows is harmless, rows without their file are not.
  - **What is written** (`plan`, pure): the final values (after Satellite's and people's corrections; "(not printed)" → NULL), typed by the field list; FP, then POs, then receipts; copies of one PO (one PO number) or receipt (one receipt number) are one row with every copy's pages; a continuation page's rows follow its document; `page_ref` = where the document sits in the PDF, `source_pages` = its pages in the scan; `confidence` = the share of the document's values a witness backed (the rest are kept as the AI read them). A receipt's total/DPP (`source: check`) is read for the checks, not stored.
  - **A published bundle is never checked again** (`crosscheck.inputs` skips it), and regrouping re-attaches its pages to it instead of opening a new bundle (the unique index allows a second bundle per SOR once one is published). The gates count a bundle published from auto_ok as auto_ok.
  - **Trigger:** a person, for the pilot: the Publish button on /review (or `python -m publisher.publish <batch> [SOR…]`); `--undo <SOR>` takes a publication back (development). Making it automatic after every regroup is one call.
  - **Measured on the sample:** the 7 auto_ok Hero bundles published (21 documents, 48 lines, 7 PDFs of 205–275 KB); the FP values equal Satellite's order, receipt dates the CGR dates; a regroup keeps them published, no duplicate bundle; all phase-7 gates pass. Tests: `tests/test_vf_publish.py`.
  - Open: a document for an already published SOR that arrives later (the Faktur Pajak stage) attaches to the published bundle but isn't published: a later stage bumps `sor_document.version`.
- **Still open, for the mentors:** is the CGR counted from returned goods or typed from the signed TTG? What does an SOF (free goods?) order's FP print? Its as-ordered amounts are 0 on all 409 SOF orders.

**Budget:**
- **Gemini** free tier: 20 calls/model/day, shared with v1; resets at 14:00 WIB.
- **Groq** free tier for `qwen/qwen3.8-27b`:
  - 30 requests/min, 1,000 requests/day;
  - **8K tokens/min, 200K tokens/day**. The daily budget **refills continuously** (~139 tokens a minute, ~8.3K an hour), with no reset time: Groq's own "try again in 17m22s" matched that rate exactly;
  - measured ≈ 5.1–5.5K tokens per page read and ≈ 4.4K per second look, so ~30 pages a day with reads only.
- vf caps itself at `VF_AI_OCR_DAILY_CAP` (40 for Gemini, 150 otherwise) and counts every call in `staging.model_call`.
- **While Groq's last answer is a daily-limit refusal, no call is made** (`vf.refused_for`, in `ai_call`). The page waits, and its error carries what's left of Groq's "try again in", which `again` sleeps on. Without this, an upload's pages would each fail in turn, and every failure counts against the cap: 113 calls were left when the 166-page file was about to be uploaded. Tested in `tests/test_vf_budget.py`.
- `python -m worker.vf once … --no-second-look` reads without the look-again, to save tokens. Those pages wait (`waiting_ai`), and `again` finishes them later.
- **Groq's daily limit came early:** on 2026-09-24 it stopped after 29 calls and 136K *reported* tokens, not 200K. It seems to count each request's whole answer allowance (`max_tokens`: 4,096 per read), so the look-again now asks for 1,024. `DailyLimit` errors now carry Groq's own numbers (limit, used, requested, try again in).

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
docker-compose.yml   project samb-ocr: postgres, rabbitmq, minio, n8n + vf-worker(×3), vf-grouper, vf-teacher, vf-ui (:8001)
.env / .env.example  dev passwords + TYPESAFE_API_KEY, GEMINI_API_KEY (empty = fallback)
schema/              satellite-documents.sql (base), 002…007 migrations, 008-document-fields (GENERATED from services/common/fields.py)
services/common/     fields.py = THE field list per document type (§6.1 + linking); verify.py = phase 5 check; db, queue, storage, intake, keys, health;
                     models/vlm.py (AI OCR), models/schemas.py (generated from fields.py)
services/worker/     enhance.py + orient.py (2), classify.py + layout.py + fp_layout.json (3), main.py (consumer: 2→5 + fan-in),
                     reclassify.py / reverify.py (re-decide / re-check from stored data, no model calls)
services/grouper/    idle until phase 6
services/publisher/  idle until phase 8
services/ui/         FastAPI + Jinja + htmx inspection UI, http://localhost:8001
n8n/                 vf-intake / vf-sweep / vf-notify (scripts/n8n-setup-vf.sh); intake.workflow.json is v1's (retired)
tests/               test_phase0..5.py, test_fanin.py, test_answer_key_isolation.py, test_labels.py, test_screens.py; run inside the ui container
diagrams/  docs/     architecture diagrams, database guide
```

## Running it

```bash
docker compose up -d                                   # whole stack (the vlm-first commands above are the day-to-day ones)
./scripts/n8n-setup-vf.sh                              # first time: import + publish vf's n8n workflows
docker compose exec -e PYTHONPATH=/app vf-ui pytest -q tests/  # all tests (v1's phase tests skip)
```

Consoles:
- UI: http://localhost:8001
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
| 6 | Grouping: pages → documents → SOR, by resolved keys only; one storage folder per SOR | ✅ on branch `vlm-first` (2026-09-25): pages 1–31 → 11 bundles, all 31 pages placed, 0 in a wrong bundle; 2 needed one person confirmation each (FPs 6 and 8, confirmed by the user) |
| 7 | Cross-checks (§6.3) + Review screen | in progress on `vlm-first` (2026-09-25): 7.0 built (Satellite's export, one verdict path, grading against the extended answer key and Satellite); 7a built and adopted (`common/gates.py`): 16 wrong ✅ → 0, every bend caught; 7b built and adopted (Satellite for the FP's amounts and lines, composite resolution): 143 ✅ gained, every FP clear, still 0 wrong ✅; 7c built (bundle cross-checks, statuses, product matching with AI proposals; FP ↔ PO equal up to Rp 5); 7d built (Review screen: confirm, pair, accept, approve); all 6 phase-7 gates pass. Left: 7e (amount in words), 7f (learning columns) |
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

- Is there a customer product-code → SAMB material-code table? (§07; needed for line-item cross-checks.) Not for now (the user, 2026-09-25): phase 7c builds one from AI proposals a person confirms once.
- Surat Jalan fields (no filled example yet).
- Does "28k documents" mean pages or bundles?
- What should happen to a bundle split across two feeder runs?
- Is there API access to customer web portals?
