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

def new_upload(by, doc_date=None, note=None):
    """A new upload batch (common/uploads.py): its generated number, who uploads, the scan date. Refused without a
    name, or with a date in the future (400)."""
    from common import uploads
    try:
        return uploads.create(by, doc_date, note)
    except ValueError as e:
        raise ActionError(400, {"say who uploads": "Tulis nama Anda dulu.",
                                "the scan date can't be in the future": "Tanggal scan tidak boleh di masa depan."}
                          .get(str(e), str(e)))


def upload(data, filename, upload_id=None):
    """A scanned PDF: stored, recorded as 'received', and put on q.intake, where the intake worker splits it into
    pages and queues them (common/intake.py). Returns {"batch_id"}. Refused: not a PDF, or its pages can't be read
    (400); the same file again (409, with the earlier scan)."""
    if not data.startswith(b"%PDF"):
        raise ActionError(400, "File itu bukan PDF. Pilih file PDF hasil scan.")
    up = None
    if upload_id is not None:
        from common import uploads
        with db.connect() as c:
            up = uploads.get(c, upload_id)
        if not up:
            raise ActionError(400, "Batch unggahan itu tidak ada. Mulai unggah lagi dari awal.")
    try:
        out = intake.receive(data, filename, up)
    except intake.NotReadable as e:
        raise ActionError(400, "Halaman PDF ini tidak bisa dibaca. Coba scan atau ekspor ulang filenya.", detail=str(e))
    if out["duplicate"]:
        raise ActionError(409, "File ini sudah pernah diunggah.", dup=out["earlier"])
    return {"batch_id": out["batch_id"]}


# ---------------------------------------------------------------------------------------------- a page

def _not_now():
    """Why no page can be tried right now (a model not set, a limit), for people, else None. Pages wait in the waiting
    room and go on by themselves, so a retry would only park them again."""
    from api import stuck
    from worker import vf
    return stuck.blocked_text(vf.blocked())


def retry_page(batch, page, by):
    """Try a stuck page again (api/stuck.py): a page whose worker died goes back on q.pages under a new run
    (intake.rerun); a page whose model call kept failing goes back on q.pages as it is (vf.again), and redoes only what
    failed. Refused (409): its order is already sent to Satellite; it is queued (being tried); it isn't stuck; a model
    isn't set or a limit holds every page; for a dead page, while another page of its file is queued (the new run
    would make that page's ticket stale)."""
    from api import stuck
    _need(by)
    with db.connect() as c:
        p = c.execute(f"""SELECT p.status::text AS status, p.extract_status, p.second_look->>'waiting' AS waits,
                                 {stuck.PUBLISHED} AS published
                            FROM staging.page p WHERE p.batch_id=%s AND p.page_no=%s""", (batch, page)).fetchone()
        if not p:
            raise ActionError(404, "Halaman itu tidak ada.")
        if p["published"]:
            raise ActionError(409, "Order halaman ini sudah dikirim ke Satellite, jadi halamannya tidak dibaca ulang.")
        if p["status"] == "queued":
            raise ActionError(409, "Halaman ini sedang dibaca atau menunggu giliran: sistem sedang mencobanya.")
        k = stuck.kind(p["status"], p["extract_status"], p["waits"])
        if not k:
            raise ActionError(409, "Halaman ini tidak gagal dibaca, jadi tidak perlu dicoba lagi.")
        busy = c.execute("""SELECT count(*) AS n FROM staging.page WHERE batch_id=%s AND page_no <> %s
                              AND status = 'queued'""", (batch, page)).fetchone()["n"]
    why = _not_now()
    if why:
        raise ActionError(409, why)
    if k == "crashed" and busy:
        raise ActionError(409, "Halaman lain di file ini masih dibaca. Coba lagi setelah selesai.")
    print(f"retry {batch} p{page} ({k}) by {by.strip()}", flush=True)
    if k == "crashed":
        intake.rerun(batch, [page])
    else:
        from worker import vf
        vf.again(batch, [page])
    return {"batch_id": batch, "page_no": page, "retried": k}


def retry_scan(batch, by):
    """A file that couldn't be split into pages: split again (nothing comes after it yet, so it is safe)."""
    from common import queue
    _need(by)
    with db.connect() as c:
        r = c.execute("""UPDATE staging.scan_batch SET status='received', error=NULL WHERE id=%s AND status='failed'
                         RETURNING id""", (batch,)).fetchone()
    if not r:
        raise ActionError(409, "File ini tidak gagal diproses, jadi tidak perlu dicoba lagi.")
    queue.send(queue.Q_INTAKE, [{"batch_id": batch}])
    print(f"retry split {batch} by {by.strip()}", flush=True)
    return {"batch_id": batch, "retried": "split"}


def retry_upload(upload_id, by):
    """Everything stuck in one upload batch, tried again at once: its files that couldn't be split, then its stuck
    pages, except those of orders already sent to Satellite. Returns what was tried and what was left, and why."""
    from api import stuck
    _need(by)
    why = _not_now()
    if why:
        raise ActionError(409, why)
    with db.connect() as c:
        files = [r["id"] for r in c.execute("SELECT id FROM staging.scan_batch WHERE upload_id=%s AND status='failed'",
                                            (upload_id,))]
        pages = [dict(r) for r in c.execute(
            f"""SELECT p.batch_id, p.page_no, p.status::text AS status, p.extract_status,
                       p.second_look->>'waiting' AS waits, {stuck.PUBLISHED} AS published,
                       EXISTS (SELECT 1 FROM staging.page q WHERE q.batch_id = p.batch_id AND q.status = 'queued') AS busy
                  FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
                 WHERE s.upload_id = %s AND {stuck.STUCK} ORDER BY p.batch_id, p.page_no""", (upload_id,))]
    tried, left = {"files": 0, "pages": 0}, []
    for b in files:
        retry_scan(b, by)
        tried["files"] += 1
    crashed, failed = {}, {}
    for p in pages:
        k = stuck.kind(p["status"], p["extract_status"], p["waits"])
        if p["published"]:
            left.append({"batch_id": p["batch_id"], "page_no": p["page_no"], "why": "sudah dikirim ke Satellite"})
        elif k == "crashed" and p["busy"]:
            left.append({"batch_id": p["batch_id"], "page_no": p["page_no"], "why": "halaman lain di filenya masih dibaca"})
        else:
            (crashed if k == "crashed" else failed).setdefault(p["batch_id"], []).append(p["page_no"])
    from worker import vf
    for b, ns in crashed.items():
        intake.rerun(b, ns)
        tried["pages"] += len(ns)
    for b, ns in failed.items():
        tried["pages"] += len(vf.again(b, ns))
    print(f"retry upload {upload_id} by {by.strip()}: {tried}, left {len(left)}", flush=True)
    return {**tried, "left": left}


def save_label(batch, page, label, customer="", note="", labelled_by=""):
    """A person says what a page is: a page the system was unsure of (Jenis halaman), or one it decided wrong (Ubah
    jenis on the page; api/relabel.py). The pile (practice/exam) is drawn once, on first save; relabelling never moves
    a page between piles. vlm-first: a page v1 already put in a pile keeps that pile. Refused (409) for a page of an
    order already sent to Satellite, and while the page is queued."""
    from api import relabel, stuck
    a = _app()
    if label not in {t[0] for t in a.LABEL_TYPES}:
        raise ActionError(400, "unknown label")
    with db.connect() as c:
        p = c.execute(f"""SELECT p.status::text AS status, {stuck.PUBLISHED} AS published FROM staging.page p
                          WHERE p.batch_id=%s AND p.page_no=%s""", (batch, page)).fetchone()
    if not p:
        raise ActionError(404, "no such page")
    why = relabel.refusal(p["status"], p["published"])
    if why:
        raise ActionError(409, why)
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
    """A customer's once-only calibration: what its receipts print after a rejection. Every bundle of that customer,
    in every batch, is checked again. Returns those batches. Its allowance isn't set any more: every customer's is
    Rp 1,000 (crosscheck.ROUNDING; the mentor, 2026-10-08)."""
    from grouper import crosscheck
    a = _app()
    _need(by)
    if str(allowance or "").strip():
        raise ActionError(400, f"every customer's allowance is Rp {crosscheck.ROUNDING:,.0f}: there is none to set")
    if receipt_shows not in ("received", "ordered"):
        raise ActionError(400, "say what the receipts print: 'received' or 'ordered'")
    batches = list(crosscheck.calibrate(chain, name, by.strip(), receipt_shows))
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


def publish(batch, by, upload=None):
    """Phase 8: publish the batch's finished bundles (auto_ok or reviewed): Satellite's document rows and one PDF per
    SOR, checked once more first. Returns the SORs written."""
    from publisher import publish as pub
    _need(by)
    if batch:
        return [d["sor"] for d in pub.publish(batch)]
    with db.connect() as c:                            # an upload batch's scans, or every scan with a finished order
        scans = [r["batch_id"] for r in c.execute(
            """SELECT DISTINCT d.batch_id FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                 JOIN staging.document d ON d.id = bd.document_id JOIN staging.scan_batch s ON s.id = d.batch_id
                WHERE b.status = ANY(%s) AND b.hold_reason IS NULL AND (%s::int IS NULL OR s.upload_id = %s)
                ORDER BY 1""", (list(pub.READY), upload, upload))]
    done = []
    for bid in scans:
        done += [d["sor"] for d in pub.publish(bid) if d["sor"] not in done]
    return done
