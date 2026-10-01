# The flow, as Eraser diagrams (read-then-map, 2026-09-30)

Eraser diagram-as-code, one file per part of the flow. Paste a file's content into an Eraser diagram (Diagram as
code), or push them with the Eraser MCP.

| File | What it shows |
|---|---|
| `01-page-flow.eraser` | One page: prepare, the AI OCR's copy, pass A, Jev, the customer, the knowledge (pass B, visual claims), Tesseract, the checks, the look-again, the outcome, the waiting room |
| `02-bundle-and-review.eraser` | Grouping (resolved keys only, holds), the cross-checks (order side, delivery side, calibration), the statuses, what reaches Review and what never does, how to review, publish |
| `03-fix-and-learn.eraser` | How to fix a value (page viewer, Review), what a fix does, the example, the teacher's lesson, the code's checks, the replay gate (truth, leave-one-out), activation, apply, the lint |
| `04-jev-context-teaching.eraser` | How a label on an unsure page changes Jev's context: lesson, GLM's one change, the code's refusals, the replay, a person's approval |
| `06-step6-fields.eraser` | Step 6 in detail: each value against print, then a person, Satellite, the 7a rules and the FP's record; per document (FP, PO, TTG, Faktur Pajak, the rest) which fields are keys, the page's, support, the bundle's, filled by Satellite or kept as read; the look-again, the bundle's checks after linking, and the two known gaps for a misread key |
| `05-services.eraser` | Who runs what: n8n workflows, RabbitMQ queues, workers, stores, models |

Node names avoid `-` and `.` (Eraser reads them as connection syntax); the labels carry the real names.

**Written for non-technical readers** (Finance, the mentors; the user, 2026-10-01): labels are short plain sentences
with everyday names ("the order number (SOR)", "Satellite, SAMB's order records", "just stored"), an example where it
helps, and a "How to read the colours" box; no field, table, function or service names in labels. Each file's header
comment lists the code behind it, for maintainers. Keep this style when editing, and push the edited file to its
diagram in the Eraser file OCR SAMB.
