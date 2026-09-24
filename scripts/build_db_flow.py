"""Build diagrams/db-flow.html: every pipeline phase, every table it touches, every column.

    python3 scripts/build_db_flow.py

Columns come from parsing schema/*.sql, never from docs. The only hand-written part is
PHASES below: which phase reads or writes which columns. The script fails loudly when
  - a column named in PHASES is not in the DDL (typo, or the schema changed), or
  - a column a BUILT phase touches does not appear in that phase's source file.
Re-run it after every schema migration and republish the artifact.
"""
import html
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_FILES = ["satellite-documents.sql", "002-page-images.sql", "003-page-quality.sql",
                "004-batch-run.sql", "005-page-type.sql", "006-type-label.sql", "007-extraction.sql",
                "008-document-fields.sql", "009-verify.sql"]
OUT = os.path.join(ROOT, "diagrams", "db-flow.html")

# ---------------------------------------------------------------------------
# 1. Parse the DDL
# ---------------------------------------------------------------------------

def _cols_in_parens(s):
    m = re.search(r"\(([^)]*)\)", s)
    return [c.strip() for c in m.group(1).split(",")] if m else []


def parse_schema():
    tables, enums, comments = OrderedDict(), OrderedDict(), {}
    for fname in SCHEMA_FILES:
        tag = "base" if fname.startswith("satellite") else fname[:3]
        lines = open(os.path.join(ROOT, "schema", fname)).read().split("\n")
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            m = re.match(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\.(\w+) \(", s)
            if m:
                name = m.group(1) + "." + m.group(2)
                t = tables.setdefault(name, {"name": name, "schema": m.group(1), "table": m.group(2),
                                             "cols": OrderedDict()})
                i += 1
                while not lines[i].strip().startswith(");"):
                    _col_line(t, lines[i], tag)
                    i += 1
                i += 1
                continue
            m = re.match(r"CREATE TYPE (\w+)\s+AS ENUM \((.*)", s)
            if m:
                buf = m.group(2)
                while ");" not in lines[i]:
                    i += 1
                    buf += "\n" + lines[i]
                enums[m.group(1)] = re.findall(r"'([^']+)'", "\n".join(l.split("--")[0] for l in buf.split("\n")))
                i += 1
                continue
            m = re.match(r"ALTER TYPE (\w+) ADD VALUE (?:IF NOT EXISTS )?'(\w+)'(?: BEFORE '(\w+)')?", s)
            if m:
                vals = enums.setdefault(m.group(1), [])
                if m.group(2) not in vals:
                    pos = vals.index(m.group(3)) if m.group(3) in vals else len(vals)
                    vals.insert(pos, m.group(2))
                i += 1
                continue
            m = re.match(r"ALTER TABLE (\w+\.\w+)", s)
            if m and "ADD COLUMN" in "".join(lines[i:i + 30]).split(";")[0] + ";":
                t = tables[m.group(1)]
                while True:
                    code = lines[i].split("--")[0]
                    if "ADD COLUMN" in code:
                        _col_line(t, lines[i][lines[i].index("ADD COLUMN") + len("ADD COLUMN"):]
                                  .replace("IF NOT EXISTS", "", 1), tag)
                    if ";" in code:
                        break
                    i += 1
                i += 1
                continue
            m = re.match(r"COMMENT ON COLUMN (\w+\.\w+)\.(\w+) IS", s)
            if m:
                buf = s
                while not buf.rstrip().endswith(";"):
                    i += 1
                    buf += " " + lines[i].strip()
                txt = re.search(r"IS\s+'(.*)';", buf, re.S)
                if txt:
                    comments[(m.group(1), m.group(2))] = txt.group(1).replace("''", "'")
                i += 1
                continue
            i += 1
    for (tn, cn), txt in comments.items():
        c = tables[tn]["cols"][cn]
        c["comment"] = (c["comment"] + " · " if c["comment"] else "") + txt
    return tables, enums


def _col_line(t, line, tag):
    code, _, cmt = line.partition("--")
    code, cmt = code.strip().rstrip(",;").strip(), cmt.strip()
    if not code:
        return
    up = code.upper()
    if up.startswith("PRIMARY KEY"):
        for c in _cols_in_parens(code):
            t["cols"][c]["pk"] = True
        return
    if up.startswith("FOREIGN KEY"):
        ref = re.search(r"REFERENCES (\w+\.\w+)", code).group(1)
        for c in _cols_in_parens(code):
            t["cols"][c]["fk"] = ref
        return
    if re.match(r"(CHECK|UNIQUE)\s*\(|CONSTRAINT\s", up):
        return
    m = re.match(r"(\w+)\s+(.*)", code)
    name, rest = m.group(1), m.group(2)
    typ = re.match(r"([\w]+(?:\s*\([\d,\s]+\))?(?:\[\])?)", rest).group(1)
    ru = rest.upper()
    fk = re.search(r"REFERENCES (\w+\.\w+)", rest)
    dflt = re.search(r"DEFAULT\s+('[^']*'|\S+)", rest)
    t["cols"][name] = {"name": name, "type": typ, "pk": "PRIMARY KEY" in ru,
                       "uk": " UNIQUE" in " " + ru and "PRIMARY KEY" not in ru,
                       "notnull": "NOT NULL" in ru or "PRIMARY KEY" in ru,
                       "fk": fk.group(1) if fk else None,
                       "default": dflt.group(1) if dflt else None,
                       "comment": cmt, "origin": tag}


# ---------------------------------------------------------------------------
# 2. The touch map: which phase reads / writes which columns
# ---------------------------------------------------------------------------
ALL = "*"
SRC_UI, SRC_INTAKE, SRC_WORKER = "services/ui/app.py", "services/common/intake.py", "services/worker/main.py"


def op(label, table, verb, r=(), w=(), note="", src=None, missing=(), auto=()):
    """auto = columns filled by a DEFAULT on INSERT: written, but not named in the source code."""
    return {"label": label, "table": table, "verb": verb, "r": list(r), "w": w if w == ALL else list(w) + list(auto),
            "auto": list(auto), "note": note, "src": src, "missing": list(missing)}


P2_COLS = ["rotation", "osd_conf", "skew_angle", "black_ratio", "dark_band_ratio", "speckle_ratio", "ocr_variant",
           "variant_scores", "ocr_conf", "confident_chars", "ocr_words", "classical_text", "quality_flags", "qr_text",
           "ms_enhance_ocr"]
HOLD_MISSING = [("hold_reason", "fp_unreadable · fp_missing · no_key · keys_disagree · page_unsure"),
                ("suggested_sor", "the SOR a person is offered, never an automatic link"),
                ("suggestion_evidence", "why that suggestion (e.g. the page after an FP)"),
                ("sor_no NULL", "a held group has no SOR yet, but sor_no is NOT NULL today")]

PHASES = [
    {"key": "0", "title": "Stack and schema", "status": "built",
     "who": "docker compose · Postgres init", "when": "First start on a fresh volume",
     "does": "Postgres runs schema/*.sql from docker-entrypoint-initdb.d and creates every table on this page. "
             "The Status screen counts them and expects 20.",
     "story": "Before any paper arrives, both schemas exist and every table is empty.",
     "fx": ["20 tables created", "Status screen: table count"], "next": "A PDF is uploaded",
     "ops": []},
    {"key": "1", "title": "Intake", "status": "built",
     "who": "Upload screen → n8n → intake.split, intake.enqueue", "when": "A PDF is uploaded (stands in for the SCP drop)",
     "does": "Rejects a file seen before, renders every page at 300 dpi, writes one batch row and one row per page, "
             "then puts one ticket per page on the queue.",
     "story": "Ibu Intan's 288-page scan 7000356304 - 7000356499.pdf is uploaded. Its SHA-256 is new, so it "
              "becomes batch b-4bab9b736d with 288 page rows, and 288 tickets go onto q.pages. Uploading the same "
              "file again stops at the sha256 lookup.",
     "fx": ["MinIO · scans/<day>/<batch>/<file>.pdf", "MinIO · pages/<batch>/original, thumb", "n8n webhook /intake",
            "q.pages · 288 tickets {batch_id, run, page_no, image_key, pdf_key}"],
     "next": "288 tickets wait on q.pages",
     "ops": [
         op("Upload screen · duplicate check", "staging.scan_batch", "SELECT",
            r=["sha256", "id", "file_name", "received_at"], src=SRC_UI),
         op("split · already split?", "staging.scan_batch", "SELECT", r=["sha256", "id", "status", "page_total"],
            src=SRC_INTAKE),
         op("split · new batch", "staging.scan_batch", "INSERT",
            w=["id", "file_name", "file_path", "sha256", "scanned_day", "page_total", "status"],
            auto=["received_at", "page_done", "run"],
            note="status = 'splitting'; received_at, page_done, run come from column defaults", src=SRC_INTAKE),
         op("split · each page", "staging.page", "INSERT ×288",
            w=["batch_id", "page_no", "image_path", "original_path", "thumb_path", "status"],
            note="status = 'rendered'; image_path equals original_path until phase 2", src=SRC_INTAKE),
         op("split · progress", "staging.scan_batch", "UPDATE ×288", w=["pages_rendered"], src=SRC_INTAKE),
         op("split · finish", "staging.scan_batch", "UPDATE", w=["status", "error"],
            note="'split', or 'failed' with the error", src=SRC_INTAKE),
         op("enqueue · claim the batch", "staging.scan_batch", "UPDATE … RETURNING", w=["status"],
            r=["page_total", "file_path", "run"], note="split → queued; only the call that wins publishes",
            src=SRC_INTAKE),
         op("enqueue · list pages", "staging.page", "SELECT", r=["page_no", "image_path", "original_path"],
            src=SRC_INTAKE),
         op("enqueue · mark queued", "staging.page", "UPDATE", w=["status"], src=SRC_INTAKE),
         op("re-run button", "staging.scan_batch", "UPDATE", w=["status", "page_done", "run"],
            note="run + 1 makes every older ticket stale", src=SRC_INTAKE),
         op("re-run button", "staging.page", "UPDATE", w=["status", "error"], src=SRC_INTAKE),
     ]},
    {"key": "2", "title": "Enhance and classical OCR", "status": "built",
     "who": "Page worker ×6", "when": "It takes one ticket from q.pages",
     "does": "Drops tickets from an older run, turns the page upright, straightens it, masks dark bands, "
             "reads it with Tesseract and decodes any QR. The save is one UPDATE shared with phase 3.",
     "story": "A worker takes the ticket for page 3. The batch's run still matches, so the ticket is current. "
              "The page is already upright; Tesseract reads it and the QR decodes to SOR26110255837.",
     "fx": ["MinIO · pages/<batch>/upright, clean, thumb_upright", "Tesseract ind+eng", "QR decoder, 8 variants"],
     "next": "Same ticket continues into classify",
     "ops": [
         op("stale-ticket check", "staging.scan_batch", "SELECT", r=["run"], src=SRC_WORKER),
         op("reuse an earlier read?", "staging.page", "SELECT",
            r=["enhance_version", "upright_path", "clean_path", "thumb_upright_path"] + P2_COLS,
            note="skips enhance + OCR when enhance_version matches the code", src=SRC_WORKER),
         op("save page · phase 2 part", "staging.page", "UPDATE … RETURNING",
            w=["status", "upright_path", "clean_path", "thumb_upright_path"] + P2_COLS +
              ["enhance_version", "error", "read_at"],
            note="only if status <> 'read' and the batch run is still the ticket's run", src=SRC_WORKER),
         op("save guard (inside the UPDATE)", "staging.scan_batch", "EXISTS", r=["run"], src=SRC_WORKER),
     ]},
    {"key": "3", "title": "Classify", "status": "built",
     "who": "Page worker ×6 · Label screen (a person)", "when": "Right after phase 2, same ticket",
     "does": "Title keywords, SAMB's FP layout fingerprint, Jev and the QR vote. The page gets a decided type or "
             "'unsure'. Then the scoreboard is recounted; the worker that completes N of N rings the bell once.",
     "story": "Page 3 votes FP: the title, the layout and the QR agree. Page 4 becomes PO and page 5 TTG "
              "(Boots calls it Good Receipt). A page nobody is sure of is saved as unsure and waits on the Label "
              "screen. When the 288th page is saved, that one worker moves the batch to 'read' and rings q.group.",
     "fx": ["Jev · POST /v1/systemone (text only)", "q.group · {batch_id, run}, once", "q.pages.dlq · second failure"],
     "next": "Bell on q.group (nothing listens yet)",
     "ops": [
         op("save page · phase 3 part", "staging.page", "same UPDATE",
            w=["doc_type", "type_status", "type_guess", "type_votes", "doc_type_conf", "layout_score", "footer",
               "classify_version"], note="doc_type stays NULL when type_status = 'unsure'", src=SRC_WORKER),
         op("scoreboard recount", "staging.page", "SELECT count", r=["status"], src=SRC_WORKER),
         op("scoreboard recount", "staging.scan_batch", "UPDATE … RETURNING", w=["page_done", "status"],
            r=["page_done", "page_total", "run"], note="page_done = GREATEST(old, pages read); queued → reading",
            src=SRC_WORKER),
         op("bell: reading → read", "staging.scan_batch", "UPDATE … RETURNING", w=["status"],
            r=["page_total", "run"], note="exactly one worker wins, and it publishes to q.group", src=SRC_WORKER),
         op("failure path", "staging.page", "UPDATE", w=["error", "status"],
            note="'queued' for one retry, 'dead_letter' on the second failure", src=SRC_WORKER),
         op("Label screen · a person answers", "staging.type_label", "UPSERT",
            w=["batch_id", "page_no", "label", "customer", "note", "labelled_by", "labelled_at", "pile"],
            note="pile drawn once: 80% practice, 20% exam", src=SRC_UI),
         op("Jev description proposer", "staging.type_label", "SELECT",
            r=["batch_id", "page_no", "label", "customer", "note", "pile"],
            note="not code yet: a reasoning model, or Claude in a session; practice pile only, exam pile scores"),
     ]},
    {"key": "4", "title": "AI OCR extraction", "status": "built",
     "who": "Page worker + Gemini", "when": "Same ticket, after classify (pages 1–31 while on the free tier)",
     "does": "The decided type picks the field list (common/fields.py). Gemini reads the upright image and returns "
             "every header value with the text exactly as printed, plus line items. Unsure pages are read without a "
             "type first and Jev decides again from that transcript. A failure is recorded; the page still counts.",
     "story": "Page 4 is a PO, so Gemini gets the PO field list. It returns PO number 4505832724, total 1.126.006 "
              "and six Vaseline lines. Re-runs reuse this answer: no second call.",
     "fx": ["Gemini gemini-3.8-flash, fallbacks 3.7 / 3.5 · free tier 20 calls/model/day",
            "common/fields.py → models/schemas.py (generated)"],
     "next": "Same ticket continues into the check",
     "ops": [
         op("pick field list and image", "staging.page", "SELECT",
            r=["doc_type", "type_status", "upright_path", "classical_text", "qr_text"], src=SRC_WORKER),
         op("reuse an earlier answer?", "staging.page", "SELECT",
            r=["fields", "keys", "extract_status", "extract_error", "extract_version", "vlm_meta", "vlm_read"],
            note="same type and extract_version → no new Gemini call", src=SRC_WORKER),
         op("save page · phase 4 part", "staging.page", "same UPDATE",
            w=["fields", "keys", "extract_status", "extract_error", "extract_version", "vlm_meta", "vlm_read",
               "model_vlm", "model_ms"],
            note="extract_status done · failed · skipped; keys = {sor, po_no, document_no, billing_no} with confirmed_by",
            src=SRC_WORKER),
     ]},
    {"key": "5", "title": "Check every value", "status": "built",
     "who": "Page worker, plain code (no model)", "when": "Same ticket, after extraction; worker.reverify re-checks stored pages",
     "does": "Each value is ok only if something printed backs it: it is in Tesseract's text (same line, never inside "
             "a longer number), it is the FP's SOR and equals the QR, or the FP's DPP + PPN = Total with PPN 11% of "
             "DPP. Otherwise status check, with the reason, for a person in phase 7. No verifier model.",
     "story": "Page 3's total: Tesseract read 1.126.911, Gemini 1.126.011. Not the same, but DPP 1.014.424 + PPN "
              "111.586 = 1.126.011, so it is ok by adds_up. Page 8's SOR is backed by nothing: status check.",
     "fx": ["common/verify.py", "Pages 1–31: 92 of 118 main values ok, none of them wrong"],
     "next": "Page done · ticket acked",
     "ops": [
         op("values and what backs them", "staging.page", "SELECT", r=["fields", "classical_text", "qr_text"],
            src=SRC_WORKER),
         op("one row per value", "staging.field_check", "DELETE + INSERT per value",
            w=["batch_id", "page_no", "field_path", "vlm_value", "source_text", "classical_match", "status",
               "confirmed_by", "reason"],
            note="status ok · check · empty; adjudicated_value / adjudicator wait for a person (phase 7)",
            src="services/common/verify.py"),
         op("checked with which rules", "staging.page", "same UPDATE", w=["verify_version"], src=SRC_WORKER),
     ]},
    {"key": "6", "title": "Grouping", "status": "planned",
     "who": "Grouper", "when": "The bell on q.group {batch_id, run}",
     "does": "Reads every page of the batch in order. Pages become documents (continuation pages join), and "
             "documents join one open bundle per SOR, by printed SOR, QR, No Ref, or PO No through Satellite.",
     "story": "The bell for b-4bab9b736d wakes the grouper. Page 3 (FP) carries SOR26110255837 itself. Pages 4 "
              "and 5 carry only PO 4505832724; satellite.sor.cpo_no maps it to exactly one SOR, the same one. "
              "Three documents, one bundle. Hari Hari differs: its slip prints the SOR as No Ref, so it links by sor.",
     "fx": ["q.group consumer (grouper is idle today)", "dev: satellite.sor seeded from extracted FPs"],
     "next": "Bundles go to the cross-checks",
     "ops": [
         op("stale bell?", "staging.scan_batch", "SELECT", r=["run", "status"]),
         op("every page, in order", "staging.page", "SELECT",
            r=["page_no", "doc_type", "type_status", "type_guess", "keys", "qr_text", "is_continuation", "footer",
               "fields"], note="type_guess is only a suggestion for a person; is_continuation has no writer"),
         op("corrected keys win", "staging.field_check", "SELECT", r=["field_path", "status", "adjudicated_value"]),
         op("PO No → SOR", "satellite.sor", "SELECT", r=["sor_no", "cpo_no", "customer_code"],
            note="must return exactly one SOR"),
         op("customer rules", "satellite.customer_profile", "SELECT",
            r=["customer_code", "doc_aliases", "expected_docs", "link_key"]),
         op("pages → documents", "staging.document", "INSERT",
            w=["id", "batch_id", "doc_type", "page_from", "page_to", "customer_code", "key_sor", "key_po_no",
               "resolved_sor", "linked_by", "confidence"]),
         op("documents → SOR", "staging.bundle", "INSERT or join", r=["sor_no", "status"],
            w=["id", "sor_no", "status", "created_at"], missing=HOLD_MISSING,
            note="one open bundle per SOR; a TTG scanned next week joins the same bundle"),
         op("link", "staging.bundle_document", "INSERT", w=["bundle_id", "document_id"]),
         op("batch state", "staging.scan_batch", "UPDATE", w=["status"], note="'grouping'"),
     ]},
    {"key": "7", "title": "Cross-checks and review", "status": "planned",
     "who": "Grouper · Review screen (a person)", "when": "Right after grouping, per bundle",
     "does": "Runs the §6.3 checks per bundle. A bundle that passes is auto_ok. One that fails, or a held group, "
             "waits for a person, one SOR at a time.",
     "story": "Boots: FP total 1,126,011 vs PO total 1,126,006, a 5-rupiah gap within tolerance. Quantities "
              "match and the SOR exists, so auto_ok. Bundle SOR26110256585 has no FP; it is held as fp_missing "
              "and shows up on the Review screen.",
     "fx": ["CGR qty · existing Satellite table, not modelled here", "Review screen: confirm · choose SOR · split"],
     "next": "auto_ok or reviewed bundles go to publish",
     "ops": [
         op("load the bundle", "staging.bundle", "SELECT", r=["id", "sor_no", "status"]),
         op("load the bundle", "staging.bundle_document", "SELECT", r=["bundle_id", "document_id"]),
         op("load the bundle", "staging.document", "SELECT",
            r=["batch_id", "doc_type", "page_from", "page_to", "customer_code", "key_sor", "key_po_no",
               "resolved_sor", "linked_by", "confidence"], note="key_sor vs key_po_no disagreeing → keys_disagree"),
         op("values to compare", "staging.page", "SELECT", r=["fields"]),
         op("values to compare", "staging.field_check", "SELECT", r=["field_path", "status", "adjudicated_value"]),
         op("sor_in_satellite", "satellite.sor", "SELECT", r=["sor_no", "cpo_no", "total"]),
         op("kode_match", "satellite.product_code_map", "SELECT",
            r=["customer_code", "customer_item_code", "customer_barcode", "samb_material_code"],
            note="empty until the mentors confirm a mapping table (§07)"),
         op("full set or subset", "satellite.customer_profile", "SELECT", r=["expected_docs"]),
         op("verdict", "staging.bundle", "UPDATE", w=["checks", "status", "confidence", "json"],
            note="auto_ok or needs_review; json is the per-SOR payload publish reads"),
         op("Review screen · a person", "staging.bundle", "UPDATE", r=["checks", "json"],
            w=["json", "status", "reviewed_by", "reviewed_at"], missing=HOLD_MISSING[:3],
            note="the held list needs hold_reason and suggested_sor"),
     ]},
    {"key": "8", "title": "Publish", "status": "planned",
     "who": "Publisher", "when": "Not defined yet: no queue or trigger exists",
     "does": "Writes one typed row per document with its line items, every row pointing at one SOR, cuts the "
             "bundle's pages into one PDF, and marks the bundle published.",
     "story": "Bundle SOR26110255837 is auto_ok. The publisher writes one FP, one PO and one TTG row with six "
              "lines each, all with sor_no SOR26110255837, cuts pages 3–5 into SOR26110255837.pdf, and marks "
              "the bundle published.",
     "fx": ["MinIO · documents/SOR<no>.pdf"], "next": "Later: Faktur Pajak and Pelunasan",
     "ops": [
         op("ready bundles", "staging.bundle", "SELECT", r=["id", "sor_no", "status", "json"],
            note="status IN (auto_ok, reviewed)"),
         op("pages to cut", "staging.bundle_document", "SELECT", r=["bundle_id", "document_id"]),
         op("pages to cut", "staging.document", "SELECT",
            r=["batch_id", "doc_type", "page_from", "page_to", "linked_by", "confidence"]),
         op("source PDF", "staging.scan_batch", "SELECT", r=["id", "file_path"]),
         op("foreign key target", "satellite.sor", "FK check", r=["sor_no"]),
         op("FP header", "satellite.doc_faktur_penjualan", "INSERT", w=ALL),
         op("FP lines", "satellite.doc_faktur_penjualan_line", "INSERT", w=ALL),
         op("TTG header", "satellite.doc_ttg", "INSERT", w=ALL),
         op("TTG lines", "satellite.doc_ttg_line", "INSERT", w=ALL),
         op("PO header", "satellite.doc_po", "INSERT", w=ALL),
         op("PO lines", "satellite.doc_po_line", "INSERT", w=ALL),
         op("SJ header", "satellite.doc_surat_jalan", "INSERT", w=ALL, note="no filled Surat Jalan seen yet"),
         op("SOR PDF", "satellite.sor_document", "UPSERT",
            w=["sor_no", "pdf_path", "page_count", "version", "source_batch", "updated_at"]),
         op("done", "staging.bundle", "UPDATE", w=["status", "published_at"]),
         op("done", "staging.scan_batch", "UPDATE", w=["status"], note="'done': when every bundle is published or held?"),
     ]},
    {"key": "L", "title": "Later stages", "status": "later",
     "who": "Not designed yet", "when": "A Faktur Pajak or a payment document arrives",
     "does": "Only the row shapes exist. A Faktur Pajak waits unlinked until its Billing No resolves to an SOR, "
             "then it is appended to that SOR's PDF. Payment lines are traced back to an SOR later.",
     "story": "The Faktur Pajak for SOR26110255837 arrives next month carrying only a Billing No. Once SAP maps "
              "it to the SOR, sor_no is filled and SOR26110255837.pdf goes to version 2.",
     "fx": ["Billing No → SAP → SOR"], "next": "",
     "ops": [
         op("Faktur Pajak arrives", "satellite.doc_faktur_pajak", "INSERT", w=ALL, note="sor_no stays NULL"),
         op("Billing No → SOR", "satellite.doc_faktur_pajak", "UPDATE", r=["billing_number"], w=["sor_no"]),
         op("append to the SOR PDF", "satellite.sor_document", "UPDATE", r=["pdf_path"],
            w=["version", "page_count", "updated_at"]),
         op("payment lines", "satellite.doc_pelunasan_line", "INSERT", w=ALL),
         op("trace Ref No", "satellite.doc_pelunasan_line", "UPDATE", r=["reference_no", "reference_kind"],
            w=["sor_no", "ar_document", "reference_kind"]),
     ]},
]

# Tables whose rows come from outside this pipeline: never "read but not written" bugs.
EXTERNAL = {
    "satellite.sor": "Owned by Satellite. The pipeline only reads it; in dev it is seeded from extracted FPs.",
    "satellite.customer_profile": "Maintained by people. No screen writes it yet.",
    "satellite.product_code_map": "Open question §07: does Satellite already have this mapping? Nothing fills it yet.",
}

BLURB = {
    "staging.scan_batch": "One row per uploaded PDF. The unit of fan-out and fan-in.",
    "staging.page": "One row per page. Everything the worker learns about a page lands here.",
    "staging.field_check": "One row per extracted value: ok (printed · QR · adds up) or check (why), and later a person's correction.",
    "staging.document": "Pages that form one piece of paper, with the keys that tie it to an SOR.",
    "staging.bundle": "Documents that belong to one SOR. One open bundle per SOR across batches.",
    "staging.bundle_document": "Which documents are in which bundle.",
    "staging.type_label": "A person's answer to 'what is this page?', split into practice and exam piles.",
    "satellite.sor": "Stub of Satellite's existing sales-order table: only the columns the pipeline reads.",
    "satellite.customer_profile": "Per-customer knowledge: document names, expected set, how their TTG points back.",
    "satellite.product_code_map": "Customer item code → SAMB material code, for line-item checks.",
    "satellite.sor_document": "One PDF per SOR, cut from the scan at publish.",
    "satellite.doc_faktur_penjualan": "SAMB's own invoice. At most one per SOR.",
    "satellite.doc_faktur_penjualan_line": "FP line items.",
    "satellite.doc_ttg": "Tanda Terima: Receiving Slip, Good Receipt, Goods Receive Note.",
    "satellite.doc_ttg_line": "TTG line items, in the customer's item codes.",
    "satellite.doc_po": "The customer's purchase order.",
    "satellite.doc_po_line": "PO line items with price and discount.",
    "satellite.doc_surat_jalan": "Surat Jalan. Shape only; no filled example seen.",
    "satellite.doc_faktur_pajak": "Faktur Pajak. Next stage; linked by Billing No.",
    "satellite.doc_pelunasan_line": "Payment document lines. Later stage.",
}

PAGE_GROUPS = [
    ("Identity and state", ["batch_id", "page_no", "status", "error", "read_at"]),
    ("Images", ["image_path", "original_path", "thumb_path", "upright_path", "clean_path", "thumb_upright_path"]),
    ("Enhance", ["rotation", "osd_conf", "skew_angle", "black_ratio", "dark_band_ratio", "speckle_ratio",
                 "quality_flags", "enhance_version", "ms_enhance_ocr"]),
    ("Classical OCR", ["ocr_variant", "variant_scores", "ocr_conf", "confident_chars", "ocr_words", "classical_text",
                       "qr_text", "footer"]),
    ("Classify", ["doc_type", "doc_type_conf", "type_status", "type_guess", "type_votes", "layout_score",
                  "is_continuation", "classify_version"]),
    ("AI OCR extract", ["keys", "fields", "model_vlm", "model_ms", "model_cost_idr", "extract_status", "extract_error",
                        "extract_version", "vlm_meta", "vlm_read"]),
    ("Check", ["verify_version"]),
]

# Boots SOR26110255837, pages 3–5 of the sample scan. Example values only.
EX = {
    ("staging.scan_batch", "id"): "b-4bab9b736d", ("staging.scan_batch", "file_name"): "7000356304 - 7000356499.pdf",
    ("staging.scan_batch", "page_total"): "288", ("staging.scan_batch", "page_done"): "288",
    ("staging.scan_batch", "status"): "read", ("staging.scan_batch", "run"): "5",
    ("staging.page", "batch_id"): "b-4bab9b736d", ("staging.page", "page_no"): "3",
    ("staging.page", "qr_text"): "SOR26110255837", ("staging.page", "doc_type"): "FP",
    ("staging.page", "type_status"): "decided", ("staging.page", "rotation"): "0",
    ("staging.page", "status"): "read", ("staging.page", "keys"): '{"sor": "SOR26110255837"}',
    ("staging.page", "original_path"): "pages/b-4bab9b736d/original/p003.png",
    ("staging.field_check", "field_path"): "header.purchase_order_no",
    ("staging.field_check", "vlm_value"): "4505832724", ("staging.field_check", "classical_match"): "true",
    ("staging.field_check", "status"): "ok", ("staging.field_check", "confirmed_by"): "text",
    ("staging.field_check", "source_text"): "4505832724",
    ("staging.document", "doc_type"): "PO", ("staging.document", "page_from"): "4",
    ("staging.document", "page_to"): "4", ("staging.document", "key_po_no"): "4505832724",
    ("staging.document", "resolved_sor"): "SOR26110255837", ("staging.document", "linked_by"): "po_no",
    ("staging.bundle", "sor_no"): "SOR26110255837", ("staging.bundle", "status"): "auto_ok",
    ("satellite.sor", "sor_no"): "SOR26110255837", ("satellite.sor", "cpo_no"): "4505832724",
    ("satellite.product_code_map", "customer_item_code"): "K6N302030561",
    ("satellite.product_code_map", "samb_material_code"): "1000566",
    ("satellite.sor_document", "sor_no"): "SOR26110255837",
    ("satellite.sor_document", "pdf_path"): "documents/SOR26110255837.pdf",
    ("satellite.sor_document", "page_count"): "3", ("satellite.sor_document", "version"): "1",
    ("satellite.sor_document", "source_batch"): "b-4bab9b736d",
    ("satellite.doc_faktur_penjualan", "sor_no"): "SOR26110255837",
    ("satellite.doc_faktur_penjualan", "nomor_cpo"): "4505832724",
    ("satellite.doc_faktur_penjualan", "total"): "1126011", ("satellite.doc_faktur_penjualan", "page_ref"): "{1}",
    ("satellite.doc_faktur_penjualan", "linked_by"): "sor",
    ("satellite.doc_po", "sor_no"): "SOR26110255837", ("satellite.doc_po", "purchase_order_no"): "4505832724",
    ("satellite.doc_po", "total"): "1126006", ("satellite.doc_po", "page_ref"): "{2}",
    ("satellite.doc_po", "linked_by"): "po_no",
    ("satellite.doc_ttg", "sor_no"): "SOR26110255837", ("satellite.doc_ttg", "document_no"): "5043773365",
    ("satellite.doc_ttg", "document_no"): "5043773365", ("satellite.doc_ttg", "purchase_order_no"): "4505832724",
    ("satellite.doc_ttg", "page_ref"): "{3}", ("satellite.doc_ttg", "linked_by"): "po_no",
}

GAPS = [
    ("Hold columns are missing from staging.bundle",
     "The hold rules keep held groups in the bundle table with hold_reason, suggested_sor and suggestion_evidence, "
     "and a held group has no SOR. None of those columns exist, and sor_no is NOT NULL. Phases 6 and 7 need a "
     "migration first."),
    ("page.is_continuation has no writer",
     "Grouping reads it to join page 2 of a PO to page 1, but no phase sets it. Either phase 3 writes it when "
     "doc_type = CONTINUATION, or the column goes and grouping uses doc_type."),
    ("Corrected values have no way back yet",
     "Phase 7 will put a person's correction in field_check.adjudicated_value, while page.fields keeps Gemini's value. "
     "Grouping, the checks and publish must each prefer the correction, and worker.reverify must not wipe it."),
    ("scan_batch.status has four vocabularies",
     "The DDL default is 'received'. Migration 002 says splitting → split → queued → reading → grouping → done. "
     "The code uses splitting, split, queued, reading, read, failed. docs/database.md lists a fifth set. "
     "No CHECK or enum enforces any of them."),
    ("The page_done comment describes the old counter",
     "The DDL comment says page_done = page_done + 1. The code recounts read pages with GREATEST, which is what "
     "fixed the early bell."),
    ("Publish has no trigger",
     "There is a queue for pages and a queue for grouping, but nothing tells the publisher a bundle became "
     "auto_ok or reviewed. Polling bundle.status or a q.publish queue are the obvious options."),
    ("When is a batch 'done'?",
     "A batch can hold bundles that wait for a person and bundles that were published. Nothing defines when "
     "scan_batch.status becomes done, or whether it needs to."),
    ("docs/database.md is behind the schema",
     "It omits staging.type_label and about 60 columns, including all of migration 003 and 005. This page is "
     "generated from the SQL, so it is the complete list."),
]

# ---------------------------------------------------------------------------
# 3. Resolve, check, derive
# ---------------------------------------------------------------------------

def resolve(tables):
    errors = []
    src_text = {}
    for ph in PHASES:
        for o in ph["ops"]:
            t = tables.get(o["table"])
            if not t:
                errors.append("phase %s: unknown table %s" % (ph["key"], o["table"]))
                continue
            if o["w"] == ALL:
                o["w"] = list(t["cols"].keys())
            for c in o["r"] + o["w"]:
                if c not in t["cols"]:
                    errors.append("phase %s %s: %s.%s not in DDL" % (ph["key"], o["label"], o["table"], c))
            if ph["status"] == "built" and o["src"]:
                if o["src"] not in src_text:
                    src_text[o["src"]] = open(os.path.join(ROOT, o["src"])).read()
                for c in o["r"] + o["w"]:
                    if c not in o["auto"] and c not in src_text[o["src"]]:
                        errors.append("phase %s %s: column %s not found in %s" % (ph["key"], o["label"], c, o["src"]))
    if len(tables) != 20:
        errors.append("expected 20 tables, parsed %d" % len(tables))
    if len(tables["staging.page"]["cols"]) != 47:
        errors.append("expected 47 page columns, parsed %d" % len(tables["staging.page"]["cols"]))
    if len(tables["staging.scan_batch"]["cols"]) != 12:
        errors.append("expected 12 scan_batch columns, parsed %d" % len(tables["staging.scan_batch"]["cols"]))
    grouped = sum(len(g[1]) for g in PAGE_GROUPS)
    if grouped != 47 or set(c for g in PAGE_GROUPS for c in g[1]) != set(tables["staging.page"]["cols"]):
        errors.append("PAGE_GROUPS does not cover staging.page exactly")
    if errors:
        sys.exit("build_db_flow: FAILED\n  " + "\n  ".join(errors))


def derive(tables):
    trace = {}      # (table, col) -> OrderedDict(phase_key -> set('R','W'))
    cover = {}      # table -> OrderedDict(phase_key -> set)
    for ph in PHASES:
        for o in ph["ops"]:
            for kind, cols in (("R", o["r"]), ("W", o["w"])):
                for c in cols:
                    trace.setdefault((o["table"], c), OrderedDict()).setdefault(ph["key"], set()).add(kind)
                    cover.setdefault(o["table"], OrderedDict()).setdefault(ph["key"], set()).add(kind)
    order = [p["key"] for p in PHASES]
    never, read_only, write_only = [], [], []
    for tn, t in tables.items():
        for cn in t["cols"]:
            tr = trace.get((tn, cn))
            if not tr:
                never.append((tn, cn))
                continue
            kinds = set().union(*tr.values())
            if "W" not in kinds and tn not in EXTERNAL:
                read_only.append((tn, cn))
            col = t["cols"][cn]
            if t["schema"] == "staging" and "R" not in kinds and not col["pk"] and not col["fk"]:
                write_only.append((tn, cn))
    return trace, cover, order, never, read_only, write_only


# ---------------------------------------------------------------------------
# 4. Render
# ---------------------------------------------------------------------------
e = html.escape


def trace_html(tr, here=None):
    if not tr:
        return '<span class="trace none">never</span>'
    out = []
    for k, kinds in tr.items():
        rw = "".join(x for x in ("R", "W") if x in kinds)
        out.append('<span class="tp%s">%s%s</span>' % (" here" if k == here else "", e(k), rw))
    return '<span class="trace">' + "".join(out) + "</span>"


def vd_html(t, c="", uid=""):
    return ('<span class="vd" data-t="%s" data-c="%s">'
            '<button type="button" data-v="keep" aria-pressed="false">Keep</button>'
            '<button type="button" data-v="drop" aria-pressed="false">Drop</button>'
            '<button type="button" data-v="ask" aria-pressed="false">Ask</button>'
            '<button type="button" class="nb" aria-expanded="false" aria-controls="n-%s" title="Add a note">Note</button>'
            '</span>') % (e(t), e(c), uid)


def note_row(t, c, uid):
    return ('<div class="note-row" id="n-%s" hidden><label class="sr" for="in-%s">Note on %s</label>'
            '<input id="in-%s" class="note-in" data-t="%s" data-c="%s" placeholder="Why keep or drop it, or the '
            'question for your mentor" autocomplete="off"></div>') % (uid, uid, e((t + "." + c) if c else t), uid,
                                                                      e(t), e(c))


def col_row(tables, trace, tn, cn, uid, here=None, rset=(), wset=(), dim=False):
    col = tables[tn]["cols"][cn]
    kind = ("rw" if cn in rset and cn in wset else "w" if cn in wset else "r" if cn in rset else "")
    badge = {"rw": "RW", "w": "W", "r": "R", "": ""}[kind]
    keys = []
    if col["pk"]:
        keys.append("PK")
    if col["fk"]:
        keys.append("FK")
    if col["uk"]:
        keys.append("UK")
    ex = EX.get((tn, cn))
    detail = ('<code class="ex" title="Example: Boots SOR26110255837">%s</code>' % e(ex) if ex else "")
    cm = col["comment"]
    if col["fk"]:
        cm = ("→ " + col["fk"] + (" · " + cm if cm else ""))
    detail += '<span class="cm">%s</span>' % e(cm) if cm else ""
    meta = e(col["type"]) + ("" if col["notnull"] else '<span class="nul"> null</span>')
    return ('<div class="crow %s%s" data-key="%s">'
            '<span class="bdg">%s</span>'
            '<button type="button" class="cname" title="Highlight %s everywhere">%s</button>'
            '<span class="ctype">%s%s</span>'
            '<span class="detail">%s</span>%s%s</div>%s') % (
        kind, " dim" if dim else "", e(tn + "." + cn), badge, e(cn), e(cn), meta,
        "".join('<i class="k">%s</i>' % k for k in keys), detail, trace_html(trace.get((tn, cn)), here),
        vd_html(tn, cn, uid), note_row(tn, cn, uid))


def card_html(tables, trace, ph, tn, ops, n):
    t = tables[tn]
    rset = set(c for o in ops for c in o["r"])
    wset = set(c for o in ops for c in o["w"])
    missing = []
    for o in ops:
        for m in o["missing"]:
            if m not in missing:
                missing.append(m)
    direction = "rw" if rset and wset else "w" if wset else "r"
    touched = len(rset | wset)
    uid0 = "p%s-%s" % (ph["key"], tn.replace(".", "-"))
    opl = "".join('<li><b>%s</b><span>%s</span>%s</li>' % (
        e(o["verb"]), e(o["label"]), '<em>%s</em>' % e(o["note"]) if o["note"] else "") for o in ops)
    stub = ('<div class="stub d-%s"><ul>%s</ul><div class="arrow" aria-hidden="true"><i class="l"></i>'
            '<i class="line"></i><i class="r"></i></div></div>') % (direction, opl)
    rows = []
    groups = PAGE_GROUPS if tn == "staging.page" else [("", list(t["cols"].keys()))]
    for gname, cols in groups:
        if gname:
            rows.append('<div class="grp">%s</div>' % e(gname))
        for cn in cols:
            rows.append(col_row(tables, trace, tn, cn, "%s-%s" % (uid0, cn), ph["key"], rset, wset,
                                dim=cn not in rset and cn not in wset))
    for name, why in missing:
        rows.append('<div class="crow ghost"><span class="bdg">!</span><span class="cname">%s</span>'
                    '<span class="ctype">not in DDL</span><span class="detail"><span class="cm">%s</span></span>'
                    '</div>' % (e(name), e(why)))
    return ('<div class="link">%s<section class="card %s %s" id="%s" aria-label="%s">'
            '<header><span class="sch">%s</span><h4>%s</h4><span class="cnt">%d of %d columns touched</span>%s</header>'
            '<p class="blurb">%s%s</p>%s<div class="cols"><div class="cols-in">%s</div></div></section></div>') % (
        stub, t["schema"], ph["status"], uid0, e(tn), e(t["schema"]), e(t["table"]), touched, len(t["cols"]),
        vd_html(tn, "", uid0 + "-t"), e(BLURB.get(tn, "")),
        (' <span class="ext">%s</span>' % e(EXTERNAL[tn])) if tn in EXTERNAL else "",
        note_row(tn, "", uid0 + "-t"), "".join(rows))


def phase_html(tables, trace, ph):
    by_table = OrderedDict()
    for o in ph["ops"]:
        by_table.setdefault(o["table"], []).append(o)
    chip = {"built": "built", "planned": "planned · no code", "later": "later stage"}[ph["status"]]
    label = "Phase " + ph["key"] if ph["key"] != "L" else "After phase 8"
    cards = "".join(card_html(tables, trace, ph, tn, ops, i) for i, (tn, ops) in enumerate(by_table.items()))
    if not cards:
        cards = '<p class="nocards">No rows are read or written. This phase creates the tables.</p>'
    spine = ('<aside class="spine"><div class="node %s"><div class="eyebrow">%s <span class="chip %s">%s</span></div>'
             '<h3>%s</h3><dl><dt>Who</dt><dd>%s</dd><dt>Starts when</dt><dd>%s</dd></dl><p class="does">%s</p>'
             '<p class="story"><span class="tag">The Boots SOR</span>%s</p><ul class="fx">%s</ul></div></aside>') % (
        ph["status"], label, ph["status"], chip, e(ph["title"]), e(ph["who"]), e(ph["when"]), e(ph["does"]),
        e(ph["story"]), "".join("<li>%s</li>" % e(x) for x in ph["fx"]))
    nxt = ('<div class="handoff"><span>%s</span></div>' % e(ph["next"])) if ph["next"] else ""
    return '<section class="phase" id="phase-%s">%s<div class="links">%s</div></section>%s' % (
        ph["key"], spine, cards, nxt)


def overview_svg():
    # 9 nodes (phases 1–8 + later) in a row; side effects above, staging below, satellite above-right.
    W, x0, pitch, nw, nh, ny = 1200, 16, 131, 110, 58, 118
    cx = lambda i: x0 + i * pitch + nw / 2
    phases = PHASES[1:]
    p = ['<svg class="ov" viewBox="0 0 %d 300" role="img" aria-label="Phases 1 to 8 in order. Phase 1 writes '
         'page images to MinIO and 288 tickets to q.pages; phases 1 to 3 write staging; the phase 3 bell on '
         'q.group wakes grouping in phase 6; phases 6 and 7 read Satellite and phase 8 writes it.">' % W]
    p.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>')
    # top row boxes
    boxes = [("MinIO", "images · PDFs", x0, 110, "fxb"), ("q.pages", "1 ticket / page", x0 + pitch, 110, "fxb"),
             ("q.group", "bell, once", x0 + 3 * pitch + 10, 110, "fxb"),
             ("Satellite", "sor · profile · doc tables", x0 + 5 * pitch, 3 * pitch + nw - pitch * 0, "satb")]
    for name, sub, x, w, cls in boxes:
        p.append('<rect class="%s" x="%d" y="14" width="%d" height="44" rx="6"/>' % (cls, x, w))
        p.append('<text class="bx" x="%d" y="33" text-anchor="middle">%s</text>' % (x + w / 2, name))
        p.append('<text class="bs" x="%d" y="49" text-anchor="middle">%s</text>' % (x + w / 2, sub))
    # staging band
    p.append('<rect class="stgb" x="%d" y="226" width="%d" height="44" rx="6"/>' % (x0, 8 * pitch + nw))
    p.append('<text class="bx" x="%d" y="245" text-anchor="middle">staging</text>' % (x0 + (8 * pitch + nw) / 2))
    p.append('<text class="bs" x="%d" y="261" text-anchor="middle">scan_batch · page · type_label · field_check · '
             'document · bundle · bundle_document</text>' % (x0 + (8 * pitch + nw) / 2))
    # phase nodes + main arrows
    for i, ph in enumerate(phases):
        x = x0 + i * pitch
        p.append('<a href="#phase-%s"><rect class="pn %s" x="%d" y="%d" width="%d" height="%d" rx="6"/>'
                 '<text class="pk" x="%d" y="%d" text-anchor="middle">%s</text>'
                 '<text class="pt" x="%d" y="%d" text-anchor="middle">%s</text></a>' % (
                     ph["key"], ph["status"], x, ny, nw, nh, x + nw / 2, ny + 22,
                     ("PHASE " + ph["key"]) if ph["key"] != "L" else "LATER",
                     x + nw / 2, ny + 41, e(ph["title"].replace(" and ", " + ").replace("Enhance + classical OCR",
                                                                                     "Enhance + OCR")
                                           .replace("Cross-checks + review", "Checks + review")
                                           .replace("AI OCR extraction", "AI OCR"))))
        if i < len(phases) - 1:
            p.append('<line class="ar" x1="%d" y1="%d" x2="%d" y2="%d" marker-end="url(#ah)"/>' % (
                x + nw, ny + nh / 2, x + pitch - 2, ny + nh / 2))
    # staging / satellite verbs per phase
    stg = {"1": "W", "2": "RW", "3": "RW", "4": "RW", "5": "RW", "6": "RW", "7": "RW", "8": "RW"}
    for i, ph in enumerate(phases):
        k = ph["key"]
        if k in stg:
            x = cx(i)
            p.append('<line class="ln stg" x1="%d" y1="%d" x2="%d" y2="226" marker-end="url(#ah)"%s/>' % (
                x, ny + nh, x, ' marker-start="url(#ah)"' if "R" in stg[k] else ""))
            p.append('<text class="vl" x="%d" y="%d">%s</text>' % (x + 6, ny + nh + 30, stg[k]))
    # side-effect arrows
    p.append('<line class="ln fx" x1="%d" y1="%d" x2="%d" y2="58" marker-end="url(#ah)"/>' % (cx(0) - 20, ny, cx(0) - 20))
    p.append('<text class="vl" x="%d" y="90">writes</text>' % (cx(0) - 16))
    p.append('<path class="ln fx" d="M%d %d V78 H%d V60" marker-end="url(#ah)"/>' % (cx(0) + 30, ny, cx(1) - 20))
    p.append('<line class="ln fx" x1="%d" y1="58" x2="%d" y2="%d" marker-end="url(#ah)"/>' % (cx(1) + 20, cx(1) + 20, ny - 2))
    p.append('<text class="vl" x="%d" y="100">288</text>' % (cx(1) + 26))
    qg = x0 + 3 * pitch + 10
    p.append('<path class="ln fx" d="M%d %d V84 H%d V60" marker-end="url(#ah)"/>' % (cx(2), ny, qg + 30))
    p.append('<text class="vl" x="%d" y="80">N of N</text>' % (cx(2) + 6))
    p.append('<path class="ln fx" d="M%d 58 V96 H%d V%d" marker-end="url(#ah)"/>' % (qg + 80, cx(5) - 30, ny - 2))
    p.append('<text class="vl" x="%d" y="92">wakes grouper</text>' % (qg + 86))
    for i, verb in ((5, "R"), (6, "R"), (7, "W")):
        x = cx(i) + 18
        if verb == "W":
            p.append('<line class="ln sat" x1="%d" y1="%d" x2="%d" y2="60" marker-end="url(#ah)"/>' % (x, ny, x))
        else:
            p.append('<line class="ln sat" x1="%d" y1="58" x2="%d" y2="%d" marker-end="url(#ah)"/>' % (x, x, ny - 2))
        p.append('<text class="vl" x="%d" y="108">%s</text>' % (x + 5, verb))
    x = cx(8) + 18
    p.append('<line class="ln sat dash" x1="%d" y1="%d" x2="%d" y2="60" marker-end="url(#ah)"/>' % (x, ny, x))
    p.append('<text class="vl" x="%d" y="108">W</text>' % (x + 5))
    p.append("</svg>")
    return "".join(p)


def matrix_html(tables, cover, order):
    head = "".join('<th scope="col">%s</th>' % ("P" + k if k != "L" else "Later") for k in order[1:])
    body = []
    status = {p["key"]: p["status"] for p in PHASES}
    for tn, t in tables.items():
        cells, n = [], 0
        for k in order[1:]:
            kinds = cover.get(tn, {}).get(k)
            if kinds:
                n += 1
                rw = "".join(x for x in ("R", "W") if x in kinds)
                cells.append('<td class="c %s %s"><a href="#p%s-%s">%s</a></td>' % (
                    rw.lower(), status[k], k, tn.replace(".", "-"), rw))
            else:
                cells.append('<td class="c"></td>')
        body.append('<tr class="%s"><th scope="row"><span class="sch">%s</span>%s</th>%s'
                    '<td class="n">%d</td><td class="mv" data-t="%s"><span class="vchip">open</span></td></tr>' % (
                        t["schema"], e(t["schema"]), e(t["table"]), "".join(cells), n, e(tn)))
    return ('<div class="mx-wrap"><table class="mx"><thead><tr><th scope="col">Table</th>%s'
            '<th scope="col">Phases</th><th scope="col">Your verdict</th></tr></thead><tbody>%s</tbody></table></div>'
            ) % (head, "".join(body))


def finding_rows(tables, trace, items, prefix):
    out = []
    for tn, cn in items:
        out.append(col_row(tables, trace, tn, cn, "%s-%s-%s" % (prefix, tn.replace(".", "-"), cn)))
    return '<div class="cols"><div class="cols-in">%s</div></div>' % "".join(out)


CSS = r"""
:root{
  --bg:#F3F5F8; --surface:#FFFFFF; --surface-2:#EDF0F5; --ink:#172033; --muted:#58647B; --faint:#8791A5;
  --line:#D5DBE5; --line-2:#E5E9F0;
  --stg:#0A7A6E; --stg-soft:#DCF0EC; --sat:#9C5F0C; --sat-soft:#F6EAD6; --fx:#4B4FB8; --fx-soft:#E5E6F8;
  --flag:#B93628; --flag-soft:#F8E0DC; --ok:#237345; --ok-soft:#DDF0E3; --hl:#FFF1B8;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"IBM Plex Sans",-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --bg:#0B1220; --surface:#111A2B; --surface-2:#172238; --ink:#E2E7F0; --muted:#9BA6BB; --faint:#6D798F;
    --line:#27334B; --line-2:#1D283D;
    --stg:#43C4B3; --stg-soft:#0E2C2C; --sat:#E3A84C; --sat-soft:#2D2313; --fx:#A2A6FF; --fx-soft:#1D2046;
    --flag:#F17D6E; --flag-soft:#3A1D1B; --ok:#5FCB8D; --ok-soft:#12301F; --hl:#4A3F12;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0B1220; --surface:#111A2B; --surface-2:#172238; --ink:#E2E7F0; --muted:#9BA6BB; --faint:#6D798F;
  --line:#27334B; --line-2:#1D283D;
  --stg:#43C4B3; --stg-soft:#0E2C2C; --sat:#E3A84C; --sat-soft:#2D2313; --fx:#A2A6FF; --fx-soft:#1D2046;
  --flag:#F17D6E; --flag-soft:#3A1D1B; --ok:#5FCB8D; --ok-soft:#12301F; --hl:#4A3F12;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans);margin:0}
.wrap{max-width:1360px;margin:0 auto;padding-inline:20px;padding-block:0 64px}
code,.mono{font-family:var(--mono)}
h1,h2,h3,h4{text-wrap:balance;margin:0}
a{color:inherit}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--fx);outline-offset:2px}

/* top bar */
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:20;background:color-mix(in srgb,var(--bg) 92%,transparent);
  backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.bar-in{max-width:1360px;margin:0 auto;padding:8px 20px;display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
.bar nav{display:flex;flex-wrap:wrap;gap:4px}
.bar nav a{font:500 12px/1 var(--mono);text-decoration:none;padding:6px 8px;border-radius:5px;border:1px solid var(--line);background:var(--surface)}
.bar nav a.planned,.bar nav a.later{border-style:dashed;color:var(--muted)}
.bar nav a:hover{border-color:var(--ink)}
.sum{font:12px/1.3 var(--mono);color:var(--muted);display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.sum b{color:var(--ink);font-weight:600}
.sum .k{color:var(--ok)}.sum .d{color:var(--flag)}.sum .a{color:var(--fx)}
.save{font:12px var(--mono);padding:3px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted)}
.save.err{background:var(--flag-soft);color:var(--flag)}
.save.ok{background:var(--ok-soft);color:var(--ok)}
.tog{display:flex;gap:6px;align-items:center;font-size:13px;color:var(--muted);margin-left:auto}
.msg{font:12px var(--mono);color:var(--ink);background:var(--hl);padding:3px 8px;border-radius:4px}

/* header */
header.top{padding-block:36px 8px;display:grid;gap:14px;max-width:880px}
.eyebrow{font:600 11px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);display:flex;gap:8px;align-items:center}
header.top h1{font:600 34px/1.15 var(--sans);letter-spacing:-.01em}
.lede{font-size:17px;color:var(--muted);max-width:68ch;margin:0}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted);margin:0;padding:0;list-style:none}
.legend li{display:flex;gap:6px;align-items:center}
.sw{display:inline-block;width:22px;height:14px;border-radius:3px;border:1.5px solid}
.sw.stg{border-color:var(--stg);background:var(--stg-soft)}.sw.sat{border-color:var(--sat);background:var(--sat-soft)}
.sw.fx{border-color:var(--fx);background:var(--fx-soft)}.sw.pl{border-style:dashed;border-color:var(--muted)}
.sw.w{background:var(--ink);border-color:var(--ink)}.sw.r{border-color:var(--ink)}
.sw.flag{border-color:var(--flag);background:var(--flag-soft);border-style:dashed}
.how{font-size:14px;color:var(--muted);max-width:74ch;margin:0}

/* overview */
figure.ovf{margin:24px 0 8px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 14px 6px;overflow-x:auto}
svg.ov{display:block;width:100%;min-width:900px;height:auto;color:var(--muted)}
svg.ov .pn{fill:var(--surface);stroke:var(--ink);stroke-width:1.5}
svg.ov .pn.planned,svg.ov .pn.later{stroke-dasharray:5 4;stroke:var(--muted)}
svg.ov a:hover .pn{fill:var(--surface-2)}
svg.ov .pk{font:600 10px var(--mono);letter-spacing:.08em;fill:var(--muted)}
svg.ov .pt{font:600 12.5px var(--sans);fill:var(--ink)}
svg.ov .bx{font:600 13px var(--mono);fill:var(--ink)}
svg.ov .bs{font:11px var(--sans);fill:var(--muted)}
svg.ov .vl{font:600 11px var(--mono);fill:var(--muted)}
svg.ov .fxb{fill:var(--fx-soft);stroke:var(--fx);stroke-width:1.5}
svg.ov .satb{fill:var(--sat-soft);stroke:var(--sat);stroke-width:1.5}
svg.ov .stgb{fill:var(--stg-soft);stroke:var(--stg);stroke-width:1.5}
svg.ov .ar{stroke:var(--ink);stroke-width:1.5;color:var(--ink)}
svg.ov .ln{fill:none;stroke-width:1.4}
svg.ov .ln.fx{stroke:var(--fx);color:var(--fx)}
svg.ov .ln.stg{stroke:var(--stg);color:var(--stg)}
svg.ov .ln.sat{stroke:var(--sat);color:var(--sat)}
svg.ov .dash{stroke-dasharray:4 3}
figcaption{font-size:13px;color:var(--muted);padding:6px 2px 4px;max-width:90ch}

h2.sec{font:600 22px/1.2 var(--sans);margin:48px 0 6px}
p.sec-lede{color:var(--muted);max-width:74ch;margin:0 0 18px}

/* ledger */
.phase{display:grid;grid-template-columns:300px 1fr;gap:0 0;margin-top:18px;scroll-margin-top:70px}
.spine{padding-right:18px}
.node{position:sticky;top:66px;background:var(--surface);border:1.5px solid var(--ink);border-radius:10px;padding:16px;display:grid;gap:10px}
.node.planned,.node.later{border-style:dashed;border-color:var(--muted)}
.node h3{font:600 20px/1.2 var(--sans)}
.chip{font:600 10px/1 var(--mono);letter-spacing:.06em;padding:4px 6px;border-radius:4px;text-transform:uppercase}
.chip.built{background:var(--ok-soft);color:var(--ok)}
.chip.planned,.chip.later{background:var(--surface-2);color:var(--muted);border:1px dashed var(--muted)}
.node dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:13px}
.node dt{color:var(--muted)}.node dd{margin:0}
.does{margin:0;font-size:14px}
.story{margin:0;font-size:13.5px;background:var(--surface-2);border-radius:6px;padding:10px}
.story .tag{display:block;font:600 10px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.fx{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:4px}
.fx li{font:11.5px/1.3 var(--mono);color:var(--fx);background:var(--fx-soft);border-radius:4px;padding:3px 6px}
.links{border-left:2px solid var(--ink);display:grid;gap:14px;padding-block:12px;min-width:0}
.phase:has(.node.planned) .links,.phase:has(.node.later) .links{border-left-style:dashed;border-left-color:var(--muted)}
.nocards{margin:0 0 0 24px;color:var(--muted);font-size:14px}
.handoff{margin-left:300px;border-left:2px solid var(--line);padding:10px 0 10px 18px}
.handoff span{font:12px var(--mono);color:var(--muted);background:var(--surface-2);padding:4px 8px;border-radius:4px}

.link{display:grid;grid-template-columns:170px minmax(0,1fr);align-items:start}
.stub{padding-top:10px;min-width:0}
.stub ul{list-style:none;margin:0 10px 6px 12px;padding:0;display:grid;gap:5px}
.stub li{font-size:11.5px;line-height:1.3;color:var(--muted)}
.stub li b{display:block;font:600 11px var(--mono);color:var(--ink)}
.stub li em{display:block;font-style:normal;color:var(--faint);font-size:11px}
.arrow{display:flex;align-items:center;height:12px;color:var(--ink)}
.arrow .line{flex:1;height:2px;background:currentColor}
.arrow .l,.arrow .r{width:0;height:0;border-top:6px solid transparent;border-bottom:6px solid transparent;visibility:hidden}
.arrow .l{border-right:9px solid currentColor}.arrow .r{border-left:9px solid currentColor}
.d-w .r,.d-rw .r,.d-r .l,.d-rw .l{visibility:visible}
.stub.d-r .arrow .line{background:repeating-linear-gradient(90deg,currentColor 0 6px,transparent 6px 10px)}

.card{background:var(--surface);border:1.5px solid var(--stg);border-radius:10px;min-width:0;scroll-margin-top:70px}
.card.satellite{border-color:var(--sat)}
.card.planned,.card.later{border-style:dashed}
.card header{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line-2)}
.sch{font:600 10px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 6px;border-radius:4px;background:var(--stg-soft);color:var(--stg)}
.satellite .sch,tr.satellite .sch{background:var(--sat-soft);color:var(--sat)}
.card h4{font:600 15px var(--mono)}
.cnt{font:12px var(--mono);color:var(--muted)}
.card header .vd{margin-left:auto}
.blurb{margin:0;padding:8px 12px 0;font-size:13px;color:var(--muted)}
.ext{color:var(--sat)}
.card>.note-row{padding:6px 12px 0}
.cols{overflow-x:auto;padding:8px 0 10px}
.cols-in{min-width:760px}
.grp{font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);padding:10px 12px 3px}
.crow{display:grid;grid-template-columns:30px 176px 128px minmax(160px,1fr) 118px 196px;gap:8px;align-items:start;
  padding:4px 12px;border-top:1px solid var(--line-2);font-size:12.5px}
.crow.dim{opacity:.55}
.crow.dim:hover{opacity:1}
.crow.hl{background:var(--hl);opacity:1}
.bdg{font:700 10.5px var(--mono);text-align:center;border-radius:3px;padding:2px 0;align-self:center}
.crow.w .bdg{background:var(--ink);color:var(--surface)}
.crow.r .bdg{border:1.5px solid var(--ink)}
.crow.rw .bdg{background:color-mix(in srgb,var(--ink) 22%,transparent);border:1.5px solid var(--ink);color:var(--ink)}
.cname{font:600 12.5px var(--mono);text-align:left;background:none;border:0;padding:0;cursor:pointer;overflow-wrap:anywhere}
.crow.dim .cname{font-weight:400}
.cname:hover{text-decoration:underline}
.ctype{font:11.5px var(--mono);color:var(--muted);overflow-wrap:anywhere}
.nul{color:var(--faint)}
.k{font:600 9.5px var(--mono);font-style:normal;margin-left:5px;padding:1px 3px;border-radius:3px;background:var(--surface-2);color:var(--muted)}
.detail{display:grid;gap:1px;min-width:0}
.ex{font:600 11.5px var(--mono);color:var(--stg);overflow-wrap:anywhere}
.satellite .ex{color:var(--sat)}
.cm{color:var(--muted);font-size:12px;overflow-wrap:anywhere}
.trace{display:flex;flex-wrap:wrap;gap:3px;font:10.5px var(--mono);color:var(--muted);align-self:center}
.trace .tp{padding:1px 3px;border-radius:3px;background:var(--surface-2)}
.trace .tp.here{background:var(--ink);color:var(--surface)}
.trace.none{color:var(--flag);font-weight:600}
.crow.ghost{border:1.5px dashed var(--flag);background:var(--flag-soft);margin:4px 12px;border-radius:6px;grid-template-columns:30px 176px 128px 1fr}
.crow.ghost .bdg{color:var(--flag)}.crow.ghost .ctype{color:var(--flag);font-weight:600}
.vd{display:inline-flex;gap:2px;align-self:center}
.vd button{font:500 11px/1 var(--sans);padding:5px 7px;border:1px solid var(--line);background:var(--surface);border-radius:4px;cursor:pointer;color:var(--muted)}
.vd button:hover{border-color:var(--ink);color:var(--ink)}
.vd button[data-v="keep"][aria-pressed="true"]{background:var(--ok);border-color:var(--ok);color:var(--surface)}
.vd button[data-v="drop"][aria-pressed="true"]{background:var(--flag);border-color:var(--flag);color:var(--surface)}
.vd button[data-v="ask"][aria-pressed="true"]{background:var(--fx);border-color:var(--fx);color:var(--surface)}
.vd .nb.has{color:var(--fx);border-color:var(--fx)}
.note-row{padding:2px 12px 6px 226px}
.note-in{width:100%;font:13px var(--sans);padding:6px 8px;border:1px solid var(--line);border-radius:5px;background:var(--surface-2);color:var(--ink)}
body.only .crow.dim,body.only .crow.dim+.note-row{display:none}

/* matrix */
.mx-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table.mx{border-collapse:collapse;width:100%;min-width:760px;font-size:13px;font-variant-numeric:tabular-nums}
.mx th,.mx td{border-bottom:1px solid var(--line-2);padding:6px 8px;text-align:center}
.mx thead th{font:600 11px var(--mono);color:var(--muted);letter-spacing:.04em}
.mx tbody th{text-align:left;font:500 12.5px var(--mono);white-space:nowrap}
.mx tbody th .sch{margin-right:8px}
.mx td.c{width:52px;font:600 11px var(--mono)}
.mx td.c a{display:block;text-decoration:none;border-radius:4px;padding:4px 0}
.mx td.c.w a{background:var(--ink);color:var(--surface)}
.mx td.c.r a{border:1.5px solid var(--ink)}
.mx td.c.rw a{border:1.5px solid var(--ink);background:linear-gradient(90deg,transparent 50%,color-mix(in srgb,var(--ink) 22%,transparent) 50%)}
.mx td.c.planned a,.mx td.c.later a{border-style:dashed;opacity:.8}
.mx td.c.w.planned a,.mx td.c.w.later a{background:repeating-linear-gradient(135deg,var(--ink) 0 3px,transparent 3px 6px);color:var(--ink);border:1.5px dashed var(--ink)}
.mx td.n{font:12px var(--mono);color:var(--muted)}
.vchip{font:600 10.5px var(--mono);padding:3px 7px;border-radius:99px;background:var(--surface-2);color:var(--muted)}
.vchip.keep{background:var(--ok-soft);color:var(--ok)}.vchip.drop{background:var(--flag-soft);color:var(--flag)}
.vchip.ask{background:var(--fx-soft);color:var(--fx)}

/* findings */
.find{display:grid;gap:18px}
.fbox{background:var(--surface);border:1px solid var(--line);border-radius:10px}
.fbox>h3{font:600 16px var(--sans);padding:14px 14px 2px}
.fbox>p{margin:0;padding:0 14px 4px;color:var(--muted);font-size:13.5px;max-width:80ch}
.gaps{list-style:none;padding:0;margin:0;display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.gaps li{background:var(--surface);border:1.5px dashed var(--flag);border-radius:10px;padding:12px 14px}
.gaps b{display:block;font-size:14.5px;margin-bottom:4px}
.gaps span{font-size:13.5px;color:var(--muted)}
footer.src{margin-top:48px;font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}
footer.src code{font-size:12px}

@media (max-width:900px){
  .phase{grid-template-columns:1fr}
  .spine{padding:0 0 10px}
  .node{position:static}
  .links{border-left:0;padding:0}
  .link{grid-template-columns:1fr}
  .stub{padding:0 0 6px}
  .stub ul{margin:0 0 4px}
  .arrow{display:none}
  .handoff{margin-left:0}
  header.top h1{font-size:27px}
  .tog{margin-left:0}
}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
"""

JS = r"""
(function(){
  var META = JSON.parse(document.getElementById('meta').textContent);
  var state = {}, db = null, readOnly = false, dirty = {}, busy = {}, again = {}, timers = {};
  var saveEl = document.getElementById('save');
  function setSave(t, cls){ saveEl.textContent = t; saveEl.className = 'save' + (cls ? ' ' + cls : ''); }
  function S(t){ return state[t] || (state[t] = {table:t, verdict:'', note:'', columns:{}}); }
  function cell(t, c){ var s = S(t); if (!c) return s; return s.columns[c] || (s.columns[c] = {verdict:'', note:''}); }
  function sel(t, c, extra){ return extra + '[data-t="' + CSS.escape(t) + '"][data-c="' + CSS.escape(c) + '"]'; }

  function paint(t){
    var s = S(t);
    document.querySelectorAll('.vd[data-t="' + CSS.escape(t) + '"]').forEach(function(el){
      var c = el.getAttribute('data-c'), v = c ? (s.columns[c] || {}) : s;
      el.querySelectorAll('button[data-v]').forEach(function(b){
        b.setAttribute('aria-pressed', String(b.getAttribute('data-v') === (v.verdict || '')));
      });
      var nb = el.querySelector('.nb'); if (nb) nb.classList.toggle('has', !!(v.note));
    });
    document.querySelectorAll('.note-in[data-t="' + CSS.escape(t) + '"]').forEach(function(inp){
      if (inp === document.activeElement) return;
      var c = inp.getAttribute('data-c'), v = c ? (s.columns[c] || {}) : s;
      inp.value = v.note || '';
      if (v.note) inp.parentElement.hidden = false;
    });
    var chip = document.querySelector('.mv[data-t="' + CSS.escape(t) + '"] .vchip');
    if (chip){ chip.className = 'vchip ' + (s.verdict || ''); chip.textContent = {keep:'keep', drop:'drop', ask:'ask mentor'}[s.verdict] || 'open'; }
  }
  function summary(){
    var tc = {keep:0, drop:0, ask:0}, cc = {keep:0, drop:0, ask:0}, nt = 0, nc = 0;
    Object.keys(META.tables).forEach(function(t){
      nt++; var s = state[t]; if (s && tc[s.verdict] !== undefined) tc[s.verdict]++;
      META.tables[t].forEach(function(c){ nc++; var v = s && s.columns[c]; if (v && cc[v.verdict] !== undefined) cc[v.verdict]++; });
    });
    function f(o, n){ return '<span class="k">' + o.keep + ' keep</span> · <span class="d">' + o.drop + ' drop</span> · <span class="a">' + o.ask + ' ask</span> · ' + (n - o.keep - o.drop - o.ask) + ' open'; }
    document.getElementById('sum').innerHTML = '<span><b>Tables</b> ' + f(tc, nt) + '</span><span><b>Columns</b> ' + f(cc, nc) + '</span>';
  }
  function paintAll(){ Object.keys(META.tables).forEach(paint); summary(); }

  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  async function write(t){
    if (!db || readOnly) return;
    if (busy[t]){ again[t] = true; return; }
    busy[t] = true; dirty[t] = false; setSave('Saving…');
    var body = JSON.parse(JSON.stringify(S(t))); body.updatedAt = new Date().toISOString();
    var ref = db.collection('verdicts').doc(t), ok = false;
    for (var attempt = 0; attempt < 2 && !ok; attempt++){
      try { await ref.set(body); ok = true; }
      catch (e){
        var code = e && e.code;
        if (code === 'unavailable' && attempt === 0){ await sleep(400 + Math.random() * 800); continue; }
        if (code === 'invalid_argument' || code === 'not_granted'){ readOnly = true; setSave('Read-only: you can view verdicts but not record them', 'err'); }
        else if (code === 'quota_exceeded'){ setSave('Storage is full: verdicts are not being saved', 'err'); readOnly = true; }
        else if (code === 'revoked'){ setSave('Access changed: reload to continue', 'err'); readOnly = true; }
        else setSave('Not saved: ' + (code || 'error') + '. Try the button again.', 'err');
        break;
      }
    }
    busy[t] = false;
    if (again[t]){ again[t] = false; return write(t); }
    if (ok) setSave('Saved', 'ok');
  }
  function changed(t, delay){
    dirty[t] = true; paint(t); summary();
    clearTimeout(timers[t]);
    if (delay) timers[t] = setTimeout(function(){ write(t); }, delay); else write(t);
  }

  document.addEventListener('click', function(ev){
    var b = ev.target.closest('.vd button[data-v]');
    if (b){
      var vd = b.parentElement, t = vd.getAttribute('data-t'), c = vd.getAttribute('data-c'), v = b.getAttribute('data-v');
      var tgt = cell(t, c); tgt.verdict = tgt.verdict === v ? '' : v; changed(t, 0); return;
    }
    var nb = ev.target.closest('.vd .nb');
    if (nb){
      var row = document.getElementById(nb.getAttribute('aria-controls'));
      if (row){ row.hidden = !row.hidden; nb.setAttribute('aria-expanded', String(!row.hidden)); if (!row.hidden) row.querySelector('input').focus(); }
      return;
    }
    var cn = ev.target.closest('.cname');
    if (cn && cn.tagName === 'BUTTON'){
      var key = cn.closest('.crow').getAttribute('data-key');
      var on = !cn.closest('.crow').classList.contains('hl');
      document.querySelectorAll('.crow.hl').forEach(function(r){ r.classList.remove('hl'); });
      var msg = document.getElementById('msg');
      if (on){
        var rows = document.querySelectorAll('.crow[data-key="' + CSS.escape(key) + '"]');
        rows.forEach(function(r){ r.classList.add('hl'); });
        var tr = META.trace[key] || 'never touched';
        msg.textContent = key + ' · ' + tr + ' · highlighted in ' + rows.length + ' places'; msg.hidden = false;
      } else msg.hidden = true;
    }
  });
  document.addEventListener('input', function(ev){
    var inp = ev.target.closest('.note-in'); if (!inp) return;
    var t = inp.getAttribute('data-t'), c = inp.getAttribute('data-c');
    cell(t, c).note = inp.value; changed(t, 600);
  });
  var only = document.getElementById('only');
  only.addEventListener('change', function(){ document.body.classList.toggle('only', only.checked); });

  paintAll();
  setSave('Connecting…');
  var use = window.claude && window.claude.use ? window.claude.use('db') : Promise.resolve(null);
  Promise.resolve(use).then(function(ns){
    if (!ns){ setSave('Not saved here: open this page in claude.ai to record verdicts', 'err'); return; }
    db = ns;
    setSave('Ready');
    db.collection('verdicts').onSnapshot(function(snap){
      snap.docs.forEach(function(d){
        var t = d.id; if (!META.tables[t] || dirty[t] || busy[t]) return;
        var x = d.data() || {};
        state[t] = {table:t, verdict:x.verdict || '', note:x.note || '', columns: JSON.parse(JSON.stringify(x.columns || {}))};
      });
      paintAll();
    }, function(e){ setSave('Live updates stopped (' + (e && e.code) + '): reload to see others\' verdicts', 'err'); });
  }, function(){ setSave('Not saved here: open this page in claude.ai to record verdicts', 'err'); });
})();
"""


def build():
    tables, enums = parse_schema()
    resolve(tables)
    trace, cover, order, never, read_only, write_only = derive(tables)

    nav = "".join('<a class="%s" href="#phase-%s">%s</a>' % (p["status"], p["key"], p["key"] if p["key"] != "L" else "Later")
                  for p in PHASES)
    nav += '<a href="#matrix">Matrix</a><a href="#findings">Findings</a>'
    meta = {"tables": {tn: list(t["cols"].keys()) for tn, t in tables.items()},
            "trace": {"%s.%s" % k: " ".join(p + "".join(x for x in ("R", "W") if x in s) for p, s in v.items())
                      for k, v in trace.items()}}
    ncols = sum(len(t["cols"]) for t in tables.values())
    ledger = "".join(phase_html(tables, trace, p) for p in PHASES)
    enum_rows = "".join("<li><code>%s</code> %s</li>" % (e(k), e(" · ".join(v))) for k, v in enums.items())

    doc = []
    doc.append('<title>Rekonsiliasi AR Data Flow</title>\n<meta name="description" content="Every OCR pipeline phase, '
               'every table it reads or writes, every column, with a verdict for each.">\n')
    doc.append('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
               '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600;700&display=swap">\n')
    doc.append("<style>" + CSS + "</style>\n")
    doc.append('<div class="bar"><div class="bar-in"><nav aria-label="Phases">%s</nav>'
               '<div class="sum" id="sum"></div><span class="save" id="save" role="status">Loading</span>'
               '<span class="msg" id="msg" hidden></span>'
               '<label class="tog"><input type="checkbox" id="only"> Only touched columns</label></div></div>' % nav)
    doc.append('<div class="wrap"><header class="top"><div class="eyebrow">SAMB · Rekonsiliasi AR · OCR ingestion</div>'
               '<h1>Rekonsiliasi AR data flow</h1>'
               '<p class="lede">What each phase of the pipeline reads and writes, table by table and column by column, '
               'following the Boots SOR26110255837 (pages 3 to 5 of the sample scan) from upload to Satellite.</p>'
               '<ul class="legend">'
               '<li><span class="sw stg"></span>staging table</li><li><span class="sw sat"></span>satellite table</li>'
               '<li><span class="sw fx"></span>queue, file store or model call</li>'
               '<li><span class="sw w"></span>W written</li><li><span class="sw r"></span>R read</li>'
               '<li><span class="sw pl"></span>planned, no code yet</li><li><span class="sw flag"></span>gap or missing column</li>'
               '</ul>'
               '<p class="how">Each phase is a card on the left. The arrows to the right go to every table it touches, labelled '
               'with the statement it runs; an arrow into a table is a write, out of it a read. Each table lists all its '
               'columns: lit ones are touched in that phase, faded ones are not. The trace on each column '
               '(e.g. <code>1W 6R</code>) shows every phase that touches it, so a column with one write and no read is '
               'easy to spot. Click a column name to highlight it everywhere. Mark tables and columns Keep, Drop or '
               'Ask; your verdicts are saved with this page.</p></header>')
    doc.append('<figure class="ovf">%s<figcaption>The map. Phases 1 to 3 are built; 4 to 8 exist only in the schema and the '
               'plan. Click a phase to jump to its tables. q.group is rung today, but nothing listens to it until phase 6.'
               '</figcaption></figure>' % overview_svg())
    doc.append('<h2 class="sec" id="steps">Step by step</h2><p class="sec-lede">%d tables, %d columns, parsed from '
               '<code>schema/*.sql</code>. Only what a phase touches is listed under it; the matrix below shows the rest.</p>'
               % (len(tables), ncols))
    doc.append(ledger)
    doc.append('<h2 class="sec" id="matrix">Which phase touches which table</h2><p class="sec-lede">Click a cell to jump '
               'to that table in that phase. Dashed cells are planned. A table with few cells is the first place to ask '
               '"do we need this yet?".</p>')
    doc.append(matrix_html(tables, cover, order))
    doc.append('<h2 class="sec" id="findings">Findings: what to decide</h2><p class="sec-lede">Computed from the map above. '
               'Each column has the same Keep, Drop and Ask controls as in the ledger.</p><div class="find">')
    doc.append('<div class="fbox"><h3>No phase reads or writes these (%d)</h3><p>Candidates to drop, or a phase is '
               'missing a step.</p>%s</div>' % (len(never), finding_rows(tables, trace, never, "nv")))
    doc.append('<div class="fbox"><h3>Read, but nothing in the pipeline writes them (%d)</h3><p>A phase depends on a value '
               'nobody sets. Tables owned outside the pipeline (satellite.sor, customer_profile, product_code_map) are '
               'left out.</p>%s</div>' % (len(read_only), finding_rows(tables, trace, read_only, "ro")))
    doc.append('<div class="fbox"><h3>Staging columns written, never read by a later step (%d)</h3><p>Diagnostics and '
               'provenance. The inspection UI shows most of them to people; no pipeline step uses them. Keep them if a '
               'person needs to see them, otherwise drop.</p>%s</div>' % (len(write_only),
                                                                          finding_rows(tables, trace, write_only, "wo")))
    doc.append('</div><h2 class="sec" id="gaps">Where the design and the schema disagree</h2>'
               '<p class="sec-lede">Found while building this page. Each needs a decision before the phase it belongs to.</p>'
               '<ul class="gaps">%s</ul>' % "".join("<li><b>%s</b><span>%s</span></li>" % (e(a), e(b)) for a, b in GAPS))
    doc.append('<h2 class="sec">Vocabulary</h2><ul class="how" style="padding-left:18px">%s</ul>' % enum_rows)
    doc.append('<footer class="src">Generated %s by <code>scripts/build_db_flow.py</code> from <code>schema/satellite-documents.sql</code> '
               'and migrations 002 to 006. Built reads and writes were checked against <code>services/common/intake.py</code>, '
               '<code>services/worker/main.py</code> and <code>services/ui/app.py</code>. Planned phases follow '
               '<code>docs/database.md</code>, <code>CLAUDE.md</code> and the phase plan. Examples come from '
               '<code>testdata/golden_p1-32.json</code>.</footer></div>' % date.today().isoformat())
    doc.append('<script type="application/json" id="meta">%s</script>' % json.dumps(meta).replace("</", "<\\/"))
    doc.append("<script>" + JS + "</script>")
    open(OUT, "w").write("".join(doc))
    print("wrote %s: %d tables, %d columns, %d never touched, %d read-not-written, %d write-only (staging), %d KB" % (
        os.path.relpath(OUT, ROOT), len(tables), ncols, len(never), len(read_only), len(write_only),
        os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    build()
