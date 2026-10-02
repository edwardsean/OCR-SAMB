"""What a person can do: each action the REST API (api/v1.py) runs when the web app (frontend/) sends a person's
decision. Each action checks its input, writes, re-checks what it touched, and returns plain data; a refusal raises
ActionError, which the API turns into its status and reason.

Moved here from app.py's form handlers on 2026-10-02 (the user: "lets make it using react and next js where it talks
with REST API"), unchanged in what it writes."""
from common import db, intake, verify


class ActionError(Exception):
    """A refused action: the HTTP status it maps to, a message for people, and anything else worth returning."""

    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra

    def body(self):
        return {"error": self.error, **self.extra}


def _app():
    from api import app            # late: app.py imports this module, and its helpers are needed only at call time
    return app


def _need(by, value=None, what="the value as printed"):
    if not (by or "").strip() or (value is not None and not str(value).strip()):
        raise ActionError(400, f"say who you are, and {what}" if value is not None else "say who you are")


def _confirmation(c, batch, page, field, value, by, row_key=None, shown=None):
    c.execute("""INSERT INTO staging.field_confirmation (batch_id, page_no, field, value, confirmed_by, row_key, shown)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (batch_id, page_no, field) DO UPDATE SET value=EXCLUDED.value, row_key=EXCLUDED.row_key,
                   shown=EXCLUDED.shown, confirmed_by=EXCLUDED.confirmed_by, confirmed_at=now()""",
              (batch, page, field, value.strip(), by.strip(), row_key or None, shown or None))


# ---------------------------------------------------------------------------------------------- a scan arrives

def upload(data, filename):
    """A scanned PDF: stored, recorded as 'received', and put on q.intake, where the intake worker splits it into
    pages and queues them (common/intake.py). Returns {"batch_id"}. Refused: not a PDF, or its pages can't be read
    (400); the same file again (409, with the earlier scan)."""
    if not data.startswith(b"%PDF"):
        raise ActionError(400, "File itu bukan PDF. Pilih file PDF hasil scan.")
    try:
        out = intake.receive(data, filename)
    except intake.NotReadable as e:
        raise ActionError(400, "Halaman PDF ini tidak bisa dibaca. Coba scan atau ekspor ulang filenya.", detail=str(e))
    if out["duplicate"]:
        raise ActionError(409, "File ini sudah pernah diunggah.", dup=out["earlier"])
    return {"batch_id": out["batch_id"]}


# ---------------------------------------------------------------------------------------------- a page

def save_label(batch, page, label, customer="", note="", labelled_by=""):
    """A person says what a page is. The pile (practice/exam) is drawn once, on first save; relabelling never moves a
    page between piles. vlm-first: a page v1 already put in a pile keeps that pile."""
    a = _app()
    if label not in {t[0] for t in a.LABEL_TYPES}:
        raise ActionError(400, "unknown label")
    v1_pile = a._v1_pile(batch, page) if a.VF else None
    with db.connect() as c:
        c.execute("""INSERT INTO staging.type_label (batch_id, page_no, label, customer, note, labelled_by, pile)
                     VALUES (%s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
                             COALESCE(%s, CASE WHEN random() < %s THEN 'exam' ELSE 'practice' END))
                     ON CONFLICT (batch_id, page_no) DO UPDATE SET
                       label=EXCLUDED.label, customer=EXCLUDED.customer, note=EXCLUDED.note,
                       labelled_by=EXCLUDED.labelled_by, labelled_at=now()""",
                  (batch, page, label, (customer or "").strip(), (note or "").strip(), (labelled_by or "").strip(),
                   v1_pile, a.EXAM_SHARE))
    if a.VF:
        a.vf_after_label(batch, page, label)


def fix_page(batch, page, field, value, by, region="", shown="", row_key=""):
    """A correction made on the page viewer: the value as printed, and where (a region marked on the paper, 0-1000).
    The page is fixed at once (field_confirmation, re-checked, regrouped) and the correction is kept as an example for
    the knowledge (staging.extract_example)."""
    from common import knowledge
    from worker import vf
    a = _app()
    _need(by, value)
    try:
        reg = [int(float(v)) for v in str(region or "").split(",")] if str(region or "").strip() else None
        reg = reg if reg and len(reg) == 4 else None
    except ValueError:
        reg = None
    with db.connect() as c:
        _confirmation(c, batch, page, field, value, by, row_key, shown)
        knowledge.save_example(c, batch, page, field, value.strip(), shown, by.strip(), "marked" if reg else "typed",
                               reg, row_key or None)
    vf.recheck(batch, page)
    a._regroup(batch)
    a._wake_teacher(f"a correction on {batch} p{page}: {field}")      # Stage 3: the teacher writes the wiki from it


def confirm_key(batch, page, field, value, by):
    """A person confirms (or corrects) the key that holds a page back (Berkas per SOR). The page is re-checked from
    stored data (no model call), so Satellite's record settles the rest, then the batch is regrouped."""
    from grouper import group
    from worker import vf
    _need(by, value)
    with db.connect() as c:
        c.execute("""INSERT INTO staging.field_confirmation (batch_id, page_no, field, value, confirmed_by)
                     VALUES (%s, %s, %s, %s, %s)
                     ON CONFLICT (batch_id, page_no, field) DO UPDATE SET value=EXCLUDED.value,
                       confirmed_by=EXCLUDED.confirmed_by, confirmed_at=now()""",
                  (batch, page, field, value.strip(), by.strip()))
    vf.recheck(batch, page)
    group.run(batch)


# ---------------------------------------------------------------------------------------------- an order (Review)

def confirm(batch, sor, page, field, value, by, row_key="", shown=""):
    """A person confirms or corrects a value as printed on Review: a header field, or a line cell
    ('lines[<row key>].<column>'). The page is re-checked (no model call), the batch regrouped and cross-checked."""
    from worker import vf
    a = _app()
    _need(by, value)
    with db.connect() as c:
        _confirmation(c, batch, page, field, value, by, row_key, shown)
        if a.VF:                                     # kept as an example for the knowledge (found in the page's copy)
            from common import knowledge
            knowledge.save_example(c, batch, page, field, value.strip(), shown, by.strip(), "typed",
                                   row_key=row_key or None)
    vf.recheck(batch, page)
    a._regroup(batch)
    if a.VF:
        a._wake_teacher(f"a Review answer on {batch} p{page}: {field}")


def pair(batch, sor, page, row, line_no, by, note=""):
    """A person says which SO line a customer row is (or that none is, and why). A pair fills the product map for
    the chain, so the same product matches with no AI and no person next time."""
    from common import satellite as sat
    from grouper import matching
    a = _app()
    _need(by)
    line_no = str(line_no)
    with db.connect() as c:
        p = c.execute("SELECT doc_type::text AS t, fields FROM staging.page WHERE batch_id=%s AND page_no=%s",
                      (batch, page)).fetchone()
        if not p:
            raise ActionError(404, "no such page")
        so = sat.load(c, [sor]).get(verify.flat(sor))
        lines = {s["line_no"]: s for s in sat.items(c, sor)}
        rows = matching.rows_of(p["t"], p["fields"])
        if not 0 <= int(row) < len(rows):
            raise ActionError(400, "no such row")
        r = rows[int(row)]
        s = lines.get(int(line_no)) if line_no.isdigit() else None
        barcode = min(r["barcodes"]) if r["barcodes"] else None
        c.execute("""INSERT INTO staging.line_match (batch_id, page_no, row_index, sor_no, so_line_no, how, status,
                       reason, customer_code, ean)
                     VALUES (%s, %s, %s, %s, %s, 'person', %s, %s, %s, %s)
                     ON CONFLICT (batch_id, page_no, row_index) DO UPDATE SET so_line_no=EXCLUDED.so_line_no,
                       how='person', status=EXCLUDED.status, reason=EXCLUDED.reason, proposed_at=now()""",
                  (batch, page, int(row), sor, s and s["line_no"], "matched" if s else "refused",
                   f"{'paired' if s else 'no SO line' + (': ' + note.strip() if (note or '').strip() else '')}, by {by.strip()}",
                   r["code"] or None, barcode))
        chain = so and so.get("customer_parent")
        if s and chain and (r["code"] or barcode):
            c.execute("""INSERT INTO satellite.customer_profile (customer_code, customer_name) VALUES (%s, %s)
                         ON CONFLICT (customer_code) DO NOTHING""",
                      (chain, f"chain {chain} (first: {so.get('customer_name')})"))
            c.execute("""INSERT INTO satellite.product_code_map (customer_code, customer_item_code, customer_barcode,
                           samb_material_code, description, confirmed_by, confirmed_at)
                         VALUES (%s, %s, %s, %s, %s, %s, now())
                         ON CONFLICT (customer_code, customer_item_code) DO UPDATE SET
                           customer_barcode=coalesce(EXCLUDED.customer_barcode, satellite.product_code_map.customer_barcode),
                           samb_material_code=EXCLUDED.samb_material_code, description=EXCLUDED.description,
                           confirmed_by=EXCLUDED.confirmed_by, confirmed_at=now()""",
                      (chain, r["code"] or barcode, barcode, s["item_code"], r["desc"], by.strip()))
    a._regroup(batch)


def accept(batch, sor, check, input_print, reason, by, note=""):
    """A person accepts a difference with a reason. It holds while the check says exactly what it said."""
    a = _app()
    if not (by or "").strip() or not (reason or "").strip():
        raise ActionError(400, "say who you are, and why")
    with db.connect() as c:
        c.execute("""INSERT INTO staging.bundle_decision (sor_no, check_name, input_print, reason, note, decided_by)
                     VALUES (%s, %s, %s, %s, %s, %s)
                     ON CONFLICT (sor_no, check_name) DO UPDATE SET input_print=EXCLUDED.input_print,
                       reason=EXCLUDED.reason, note=EXCLUDED.note, decided_by=EXCLUDED.decided_by, decided_at=now()""",
                  (sor, check, input_print, reason.strip(), (note or "").strip() or None, by.strip()))
    a._regroup(batch)


def calibrate(chain, name, by, allowance="", receipt_shows=""):
    """A customer's once-only calibration: how far its amounts may be from Satellite's, or what its receipts print
    after a rejection. Every bundle of that customer, in every batch, is checked again. Returns those batches."""
    from grouper import crosscheck
    a = _app()
    _need(by)
    allowance, receipt_shows = str(allowance or "").strip(), (receipt_shows or "")
    try:
        value = float(allowance.replace(",", ".")) if allowance else None
    except ValueError:
        raise ActionError(400, f"an allowance is a number of rupiah, not {allowance!r}")
    if value is not None and not 0 <= value <= crosscheck.STEPS[-1]:
        raise ActionError(400, f"an allowance is rounding: 0 to {crosscheck.STEPS[-1]} rupiah")
    if value is None and receipt_shows not in ("received", "ordered"):
        raise ActionError(400, "give an allowance, or say what the receipts print")
    batches = list(crosscheck.calibrate(chain, name, by.strip(), value, receipt_shows or None))
    for bid in batches:
        a._regroup(bid)
    return batches


def approve(batch, sor, by):
    """Approve a bundle: only when nothing is left (crosscheck.can_approve), checked again now."""
    a = _app()
    _need(by)
    a._regroup(batch)
    v = a.review_view(batch, sor)
    if not v or not v["can_approve"]:
        raise ActionError(409, "not yet", left=(v or {}).get("left"))
    with db.connect() as c:
        c.execute("""UPDATE staging.bundle SET status='reviewed', reviewed_by=%s, reviewed_at=now()
                     WHERE sor_no=%s AND status <> 'published'""", (by.strip(), sor))


def publish(batch, by):
    """Phase 8: publish the batch's finished bundles (auto_ok or reviewed): Satellite's document rows and one PDF per
    SOR, checked once more first. Returns the SORs written."""
    from publisher import publish as pub
    _need(by)
    return [d["sor"] for d in pub.publish(batch)]
