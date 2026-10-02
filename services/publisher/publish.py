"""Phase 8: publish. A finished bundle leaves the pipeline: its documents become rows in Satellite's document tables
(satellite.doc_faktur_penjualan / doc_po / doc_ttg and their _line tables) and its pages one PDF per SOR
(satellite.sor_document, the file under {PREFIX}documents/SOR….pdf). Only the publisher writes these tables.

  Finished = auto_ok (every check passed, no person needed) or reviewed (a person approved it on Review). Nothing
  held, nothing half-complete: a bundle that still has anything left is never published.

  What is written: the final values, after Satellite's and people's corrections (a person's "(not printed)" is empty),
  typed by the field list (common/fields.py: amounts, quantities, dates). Copies of one PO or one receipt (the same PO
  or receipt number) are one row, with every copy's pages. confidence = the share of the document's values that a
  witness backed (print, QR, Satellite, a person); the rest are kept as the AI read them. page_ref = where the
  document sits in the SOR's PDF; source_pages = its pages in the scan.

  One transaction per bundle: the rows, the PDF record and the bundle's status 'published' go together. A published
  bundle is never checked again, and regrouping re-attaches its pages to it instead of opening a new bundle.

  python -m publisher.publish <batch> [<SOR> …]       publish the batch's finished bundles (or these)
  python -m publisher.publish <batch> --undo <SOR> …  take a publication back (development): rows, PDF, status
"""
import io
import re
import sys
from datetime import date
from decimal import Decimal

from PIL import Image
from psycopg.types.json import Json

from common import config, db, storage, verify
from common.fields import DOCS, column, project

PREFIX = config.STORAGE_PREFIX
READY = ("auto_ok", "reviewed")
ORDER = {"FP": 0, "PO": 1, "TTG": 2}
TABLE = {t: d["table"] for t, d in DOCS.items()}
NOT_PRINTED = "(not printed)"
NUMBER_OF = {"PO": "purchase_order_no", "TTG": "document_no"}      # copies of one document share this number


def typed(kind, v):
    """A stored value as its Satellite column wants it; None for nothing read or '(not printed)'."""
    if v in (None, "", NOT_PRINTED):
        return None
    if kind in ("amount", "qty"):
        a = verify.amount(re.findall(r"\d[\d.,]*", str(v))[0]) if re.search(r"\d", str(v)) else None
        return Decimal(f"{a:.3f}") if a is not None else None
    if kind == "date":
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None
    return str(v)


def plan(sor, docs, pages):
    """Pure. docs: [{type, pages, linked_by}] (the bundle's documents in this batch); pages: {page_no: {doc_type,
    fields, fields_all, checks}}. Returns {"pdf": [page_no, …] in PDF order, "documents": [{type, table, header,
    lines, page_ref, source_pages, linked_by, confidence}]}: FP first, then POs, then receipts; copies merged."""
    merged = []
    for d in sorted(docs, key=lambda d: (ORDER.get(d["type"], 9), d["pages"][0])):
        if d["type"] not in TABLE:
            continue
        number = verify.flat(((pages[d["pages"][0]].get("fields") or {}).get(NUMBER_OF.get(d["type"], "")) or {})
                             .get("value")) if d["type"] in NUMBER_OF else ""
        same = next((m for m in merged if m["type"] == d["type"] and number and m["number"] == number), None)
        if same:
            same["copies"].append(d)
        else:
            merged.append({"type": d["type"], "number": number, "copies": [d]})
    pdf, out = [], []
    for m in merged:
        t, first = m["type"], m["copies"][0]
        own = [n for c in m["copies"] for n in c["pages"]]
        ref = [len(pdf) + i + 1 for i in range(len(own))]
        pdf += own
        p = pages[first["pages"][0]]
        fields, checks = p.get("fields") or {}, (p.get("checks") or {}).get("header") or {}
        header = {"sor_no": sor}
        for f in DOCS[t]["header"]:
            if f["name"] == "sor" or f["source"] == "check":
                continue
            header[column(f)] = typed(f["kind"], (fields.get(f["name"]) or {}).get("value"))
        read = [f["name"] for f in DOCS[t]["header"] if f["name"] != "sor" and f["source"] != "check"
                and (fields.get(f["name"]) or {}).get("value") not in (None, "", NOT_PRINTED)]
        backed = sum(1 for n in read if (checks.get(n) or {}).get("verdict") == "ok")
        lines = []
        for n in first["pages"]:                         # the first copy's rows: its own page, then continuations
            q = pages[n]
            rows = (q.get("fields") or {}).get("lines") if q.get("doc_type") == t else \
                project(q.get("fields_all") or {}, t).get("lines")
            for r in rows or []:
                lines.append({f["name"]: typed(f["kind"], r.get(f["name"])) for f in DOCS[t]["lines"]})
        out.append({"type": t, "table": TABLE[t], "header": header, "lines": lines, "page_ref": ref,
                    "source_pages": own, "linked_by": first.get("linked_by"),
                    "confidence": round(backed / len(read), 3) if read else None})
    return {"pdf": pdf, "documents": out}


def ready(c, bid, sors=None):
    """The batch's finished bundles: auto_ok or reviewed, not held."""
    return [r["sor_no"] for r in c.execute(
        """SELECT DISTINCT b.sor_no FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
             JOIN staging.document d ON d.id = bd.document_id
            WHERE d.batch_id = %s AND b.status = ANY(%s) AND b.hold_reason IS NULL ORDER BY 1""", (bid, list(READY)))
        if sors is None or r["sor_no"] in sors]


def _inputs(c, bid, sor):
    docs = [{"type": d["t"], "pages": list(range(d["page_from"], d["page_to"] + 1)), "linked_by": d["linked_by"]}
            for d in c.execute("""SELECT d.page_from, d.page_to, d.doc_type::text AS t, d.linked_by::text AS linked_by
                                    FROM staging.document d JOIN staging.bundle_document bd ON bd.document_id = d.id
                                    JOIN staging.bundle b ON b.id = bd.bundle_id
                                   WHERE d.batch_id = %s AND b.sor_no = %s AND b.status = ANY(%s)""",
                                (bid, sor, list(READY)))]
    numbers = [n for d in docs for n in d["pages"]]
    pages = {r["page_no"]: dict(r) for r in c.execute(
        """SELECT page_no, doc_type::text AS doc_type, fields, fields_all, upright_path FROM staging.page
            WHERE batch_id = %s AND page_no = ANY(%s)""", (bid, numbers))}
    for n in pages:
        pages[n]["checks"] = verify.load(c, bid, n)
    return docs, pages


def pdf_bytes(paths):
    """The pages, upright, as one 1-bit PDF at 300 dpi (the scans are 1-bit; ~80 KB a page)."""
    from worker import main as v1
    images = [Image.fromarray(v1.load(p)).convert("1") for p in paths]
    b = io.BytesIO()
    images[0].save(b, "PDF", save_all=True, append_images=images[1:], resolution=300)
    return b.getvalue()


def publish_one(bid, sor):
    """One bundle: the PDF first (a stored file with no rows is harmless; rows without their PDF are not), then the
    rows, the PDF record and the status in one transaction. Returns what was written."""
    with db.connect() as c:
        docs, pages = _inputs(c, bid, sor)
    if not docs:
        return None
    p = plan(sor, docs, pages)
    key = f"{PREFIX}documents/{sor}.pdf"
    data = pdf_bytes([pages[n]["upright_path"] for n in p["pdf"]])
    storage.client().put_object(storage.bucket(), key, io.BytesIO(data), len(data), content_type="application/pdf")
    with db.connect() as c:                               # one transaction: all of it, or none of it
        b = c.execute("SELECT id, status::text AS status FROM staging.bundle WHERE sor_no=%s AND status = ANY(%s) "
                      "FOR UPDATE", (sor, list(READY))).fetchone()
        if not b:
            return None                                   # it changed since: not finished any more
        for d in p["documents"]:
            row = {**d["header"], "page_ref": d["page_ref"], "linked_by": d["linked_by"], "confidence": d["confidence"],
                   "source_batch": bid, "source_pages": d["source_pages"]}
            cols = list(row)
            doc_id = c.execute(f"INSERT INTO satellite.{d['table']} ({', '.join(cols)}) VALUES "
                               f"({', '.join(['%s'] * len(cols))}) RETURNING id", [row[k] for k in cols]).fetchone()["id"]
            for i, line in enumerate(d["lines"], 1):
                lc = list(line)
                c.execute(f"INSERT INTO satellite.{d['table']}_line (doc_id, line_no, {', '.join(lc)}) VALUES "
                          f"(%s, %s, {', '.join(['%s'] * len(lc))})", [doc_id, i] + [line[k] for k in lc])
        c.execute("""INSERT INTO satellite.sor_document (sor_no, pdf_path, page_count, source_batch)
                     VALUES (%s, %s, %s, %s)
                     ON CONFLICT (sor_no) DO UPDATE SET pdf_path=EXCLUDED.pdf_path, page_count=EXCLUDED.page_count,
                       version=satellite.sor_document.version + 1, source_batch=EXCLUDED.source_batch, updated_at=now()""",
                  (sor, key, len(p["pdf"]), bid))
        c.execute("""UPDATE staging.bundle SET status='published', published_at=now(),
                       json = coalesce(json, '{}'::jsonb) || %s::jsonb WHERE id=%s""",
                  (Json({"published_from": b["status"], "published_batch": bid, "pdf": key,
                         "documents": [{"type": d["type"], "table": d["table"], "source_pages": d["source_pages"],
                                        "page_ref": d["page_ref"], "confidence": d["confidence"],
                                        "lines": len(d["lines"])} for d in p["documents"]]}), b["id"]))
    return {"sor": sor, "pdf": key, "pages": len(p["pdf"]), "bytes": len(data),
            "documents": [(d["type"], d["source_pages"], len(d["lines"]), d["confidence"]) for d in p["documents"]]}


def publish(bid, sors=None):
    """Every finished bundle of the batch (or those named), checked once more first: grouping and the checks run
    again, so only what is finished now is published."""
    from grouper import group
    group.run(bid)
    with db.connect() as c:
        todo = ready(c, bid, sors)
    return [r for r in (publish_one(bid, s) for s in todo) if r]


def undo(sor):
    """Take a publication back (development): the SOR's rows, its PDF record and file; the bundle goes back to be
    checked (its next grouping gives it its status again)."""
    with db.connect() as c:
        for t in TABLE.values():
            c.execute(f"DELETE FROM satellite.{t} WHERE sor_no=%s", (sor,))   # lines go with their document (cascade)
        r = c.execute("DELETE FROM satellite.sor_document WHERE sor_no=%s RETURNING pdf_path", (sor,)).fetchone()
        c.execute("""UPDATE staging.bundle SET status='needs_review', published_at=NULL,
                       json = coalesce(json, '{}'::jsonb) - 'published_from' - 'published_batch' - 'pdf' - 'documents'
                     WHERE sor_no=%s AND status='published'""", (sor,))
    if r:
        try:
            storage.client().remove_object(storage.bucket(), r["pdf_path"])
        except Exception:
            pass
    return bool(r)


if __name__ == "__main__":
    batch, rest = sys.argv[1], sys.argv[2:]
    if rest and rest[0] == "--undo":
        from grouper import group
        for s in rest[1:]:
            print(s, "taken back" if undo(s) else "was not published")
        group.run(batch)
    else:
        for r in publish(batch, rest or None):
            print(f"{r['sor']}: {r['pages']} pages → {r['pdf']} ({r['bytes'] // 1024} KB) · "
                  + " · ".join(f"{t} p{','.join(map(str, ps))} ({n} lines, backed {c})" for t, ps, n, c in r["documents"]))
