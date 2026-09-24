# OCR pipeline (v1) — phased build with an inspection UI

Moved here on 2026-09-24 from the session plan file, unchanged in substance. The user checks each phase in the UI and
says "go" before the next one. The **vlm-first** experiment (branch `vlm-first`) is a separate design; see its own CLAUDE.md section.

## Context

The design is settled (diagrams in `diagrams/`, DDL in `schema/satellite-documents.sql`). It is built **one phase at a time**, and the user checks each phase's output in a UI before the next phase starts. Full stack (docker compose) from day one. No paid model keys yet: dev runs on free tiers with every model behind a swappable adapter, so moving to Model Studio / Ark later is config, not code.

Input for every phase: the real sample `7000356304 - 7000356499.pdf` (288 pages). Ground truth for acceptance comes from pages 1–32 read by hand (`testdata/golden_p1-32.json`).

## Model choices (free, dev only)

| Role | Dev choice | Why | Swap target |
|---|---|---|---|
| Classical OCR | **Tesseract 5** (`ind+eng`) | Free, local; gives text and word boxes | — |
| Classifier | **Jev** (`jev-latest`, TypeSafe `POST https://api.typesafe.ai/v1/systemone`, Choice primitive, text state) | User's chosen model; returns choice + probabilities + confidence | — |
| VLM ("AI OCR") | **Gemini Flash**, Google AI Studio free tier (measured 2026-09-24: 20 requests per model per day) | Free, strong on documents | Qwen-VL (Model Studio) / Doubao (Ark) |
| Verifier | **None: plain code** (decided with the user 2026-09-24) | A value is ✅ only if something printed backs it (Tesseract text, QR, FP sums); else a person checks it | — |
| Private fallback VLM | **qwen3-vl via Ollama** | Free and nothing leaves the machine; slower | — |

Google may use free-tier inputs to improve its models. These are real invoices with NPWP numbers: use free Gemini on the sample only; switch to paid or a private model before real volume. 6,800 pages/day does not fit a free tier.

## Phases — each ends with a stop for the user to check

| # | Build | UI screen (input → output) | Acceptance on the sample |
|---|---|---|---|
| **0** | Compose stack up; schema loaded; health checks | **Status**: green/red per service, links to consoles | All 9 services healthy; 20 tables present |
| **1** | Intake: UI upload → MinIO → n8n webhook → split endpoint renders page PNGs → `scan_batch` + 288 `page` rows → 288 messages on `q.pages` | **Upload** the PDF; **Batch** view: thumbnails, queue depth live | `page_total=288`; 288 messages; same file twice → rejected by `sha256` |
| **2** | Enhance (upright, deskew, dark bands) + classical OCR text + QR + N-of-N bell | **Page** view: original vs upright, rotation, flags, OCR text | Orientation regression set right; QR = SOR on FPs |
| **3** | Classify: Jev leads; an FP needs a second witness (QR, FP layout, printed title) | **Batch** view becomes a colour strip of types; click → votes | No wrong type on pages 1–32 |
| **4** | AI OCR: schema per type (§6.1 fields + linking fields + line items), every value with `source_text` | **Page** view: field table with what was printed | Pages 1–31 read; Boots PO p4 = `4505832724`, total `1.126.006` |
| **5** | Check every value by code only: printed in Tesseract's text (same line, never inside a longer number) / FP SOR = QR / FP DPP + PPN = Total, PPN 11% → ✅; else a person (phase 7) | Page view: ✅ or ⚠ + reason per value; batch view: the list for a person | Pages 1–31: no wrong value ✅; every ✅ value changed by one digit is caught |
| **6** | Fan-in → `q.group` → grouping worker: pages → documents → SOR. `satellite.sor` seeded from extracted FPs until there is Satellite access | **Bundles** view: one card per SOR with its pages, `linked_by` | 12 bundles on p1–32; Hari Hari p2 `linked_by=sor`; Boots p4–5 `linked_by=po_no` |
| **7** | Cross-checks (§6.3: totals, qty, DPP/PPN) → status; **Review** screen edits fields and approves | **Review**: needs_review list, per-SOR editor, approve button | Boots FP 1.126.011 vs PO 1.126.006 passes tolerance |
| **8** | Publish: typed Satellite rows + line items; cut bundle pages → `SOR<no>.pdf` in MinIO → `sor_document` | **SOR search**: rows per doc table + the PDF | `SOR26110255837.pdf` = pages 3–5; rows in doc_faktur_penjualan / doc_po / doc_ttg |

After phase 8: run the full 288 pages once and report counts, failures, time, and model calls per page.

## Linking TTG → FP (for phase 6, checked by eye 2026-09-24)

Use two keys, in order:
1. The SOR printed on the TTG, with or without the `SOR` prefix: Hari Hari `No Ref`, Puri Indah `DO#`, Indogrosir `S/Fak`.
2. Otherwise, the FP's Nomor CPO found **anywhere** on the TTG. Labels vary: No PO, PO No, ORDER NO, No. Pesanan, a PO column in the rows; AEON prints it as RECEIPT NO.

If the two keys point to different FPs → hold (`keys_disagree`). Only ✅ values count as keys. Hari Hari p2's PO number (5201510) differs from its FP's Nomor CPO (5190721), so PO alone would not link it; its SOR does.

AI OCR scope while on the free tier: **pages 1–31**. Page 31 ends a bundle; page 32's FP continues past the answer key.

## Hold rules — decided with the user (2026-09-23)

An FP whose SOR is unknown is **never** linked to the previous SOR. It is held for a person.

1. No SOR by position alone. "The page after an FP" is at most a *suggestion* (`suggested_sor` + evidence).
2. An `unsure`/unreadable page breaks the chain. Later pages cannot inherit the previous SOR; pages with their own key (SOR / `No Ref` / PO No → `sor.cpo_no`) still resolve by that key.
3. Unassigned group = unknown FP + following keyless pages up to the next confident FP → held, no SOR, `hold_reason`.
4. A bundle missing its FP, or with a held page suggested for it, is held too (`fp_missing`). Nothing half-complete is published.
5. Only the affected group waits; the rest of the batch publishes.
6. A person confirms / chooses / splits → `reviewed` → publish.

Not RabbitMQ's DLQ: that stays for technical failures only. Holds live in `staging.bundle` (`status='needs_review'`, nullable `sor_no`, `hold_reason` ∈ fp_unreadable · fp_missing · no_key · keys_disagree · page_unsure, `suggested_sor`, `suggestion_evidence`).

Sample acceptance for phase 6: pages 6 and 8 held with the correct suggestion; bundles SOR26110256585 and SOR26110256810 held (`fp_missing`); zero pages linked by position.

**Grading rule for every phase:** "don't know" (None / unsure / held) is acceptable, a confident wrong answer never is. The answer key is only visible to the UI and tests, never to pipeline code.

## Verification per phase

For each phase: bring the stack up, run the sample through, run a small `pytest` against `golden_p1-32.json` for that phase's acceptance line, look at the UI screen, then stop and hand over the UI URL (`http://localhost:8000`) plus the pass/fail list. The user checks, then says go for the next phase.

## Assumptions

- Satellite access doesn't exist yet, so `satellite.sor` is a local seeded copy; the grouping code reads it through one function, so the real connection swaps in later.
- n8n does the intake orchestration only (webhook → split → publish); page and batch logic live in Python workers so they're testable.
