"""The REST API the web app (frontend/, Next.js) calls: JSON under /api/v1. The user (2026-10-02): "lets make it using
react and next js where it talks with REST API". Reads reuse app.py's view functions; a person's decisions run
actions.py. Documented for people in docs/api.md, and live at /docs (generated from this file).

Reads are GET, a person's decisions are POST with a JSON body (an upload is multipart). A refused action answers
with its status and {"error", ...}. Values are as stored (amounts are numbers, dates ISO strings); the wording for
people (bahasa.py) comes from /words, and sentences other modules produce are translated here."""
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from common import db, verify
from api import actions, bahasa

router = APIRouter(prefix="/api/v1")

FRAME, SCANS, TYPES, ORDERS, BERKAS, SENT = ("Frame", "Scans and pages", "Page types", "Orders (Periksa order)",
                                             "Berkas per SOR", "Published (Data terkirim)")
UPLOADS = "Upload batches"


# ================================================================================================ request bodies
# Every decision is signed: `by` is the person's name as typed (not verified: there is no login yet).

def _by():
    return Field(description="who decides: the person's name as typed (kept with the decision)", examples=["Edward"])


def _batch():
    return Field(description="the scan's id", examples=["b-1a2b3c4d5e"])


def _page():
    return Field(description="the page number in the scan (1-based)", examples=[3])


class Fix(BaseModel):
    field: str = Field(description="a header field (`purchase_order_no`) or a table cell (`lines[<row key>].<column>`)",
                       examples=["purchase_order_no"])
    value: str = Field(description='the value as printed on the paper; "(not printed)" when the page has none',
                       examples=["10101000125418"])
    by: str = _by()
    region: str = Field("", description="where it was marked on the paper: `ymin,xmin,ymax,xmax` on 0-1000",
                        examples=["300,500,330,700"])
    shown: str = Field("", description="the value the screen showed before the fix (what the reading said)")
    row_key: str = Field("", description="for a table cell: the row's key (as in `lines[<row key>]`)")


class Label(BaseModel):
    batch: str = _batch()
    page: int = _page()
    label: str = Field(description="the document type: FP, TTG, PO, SJ, FPJ, PEL, CONTINUATION or OTHER (GET /words "
                                   "→ LABEL_TYPES)", examples=["TTG"])
    customer: str = Field("", description="the customer, if known", examples=["Indomaret"])
    note: str = Field("", description="anything worth keeping about the page")
    labelled_by: str = Field("", description="who says so", examples=["Edward"])


class Confirm(BaseModel):
    batch: str = _batch()
    page: int = _page()
    field: str = Field(description="a header field, or a table cell `lines[<row key>].<column>`",
                       examples=["total", "lines[02701899].qty"])
    value: str = Field(description='the value as printed; "(not printed)" when the page has none', examples=["816598.00"])
    by: str = _by()
    row_key: str = Field("", description="for a table cell: the row's key")
    shown: str = Field("", description="the value the screen showed")


class Pair(BaseModel):
    batch: str = _batch()
    page: int = _page()
    row: int = Field(description="the row's index on the page (0-based)", examples=[3])
    line_no: str = Field(description='the SO line it is (its `line_no`), or "none" when it is no line of the order',
                         examples=["20", "none"])
    by: str = _by()
    note: str = Field("", description='with "none": why (e.g. "not in SAMB\'s order")')


class Accept(BaseModel):
    batch: str = _batch()
    check: str = Field(description="the check accepted (an open item's `key`)", examples=["fp_po_total"])
    input_print: str = Field(description="the check's `print` as the order showed it: the acceptance holds only while "
                                         "the check says exactly this")
    reason: str = Field(description="one of the order's `accept_reasons`", examples=["rounding"])
    by: str = _by()
    note: str = Field("", description='with "other (say in the note)": the reason in words')


class Calibrate(BaseModel):
    chain: str = Field(description="the customer chain (`calibration.chain` of the order)", examples=["1100002424"])
    name: str = Field("", description="the chain's name, kept with the answer")
    by: str = _by()
    allowance: str = Field("", description="no longer accepted: every customer's allowance is Rp 1,000 "
                                           "(a value here is refused with 400)", examples=[""])
    receipt_shows: str = Field("", description='after a rejection its receipts print "received" or the whole "ordered"')


class Approve(BaseModel):
    batch: str = _batch()
    by: str = _by()


class NewUpload(BaseModel):
    by: str = Field(description="who uploads: the person's name as typed", examples=["Edward"])
    date: str | None = Field(None, description="the day the papers were scanned (ISO); default today (WIB)",
                             examples=["2026-10-05"])
    note: str | None = Field(None, description="anything worth knowing about this batch", examples=["kiriman Senin"])


class Publish(BaseModel):
    batch: str | None = Field(None, description="the scan whose finished orders to publish; none: every finished order",
                              examples=["b-1a2b3c4d5e"])
    upload: int | None = Field(None, description="the upload batch whose finished orders to publish", examples=[3])
    by: str = _by()


class ConfirmKey(BaseModel):
    batch: str = _batch()
    page: int = _page()
    field: str = Field(description="the held document's key field (`confirm.field` on GET /bundles)",
                       examples=["purchase_order_no"])
    value: str = Field(description="the key as printed", examples=["4505832724"])
    by: str = _by()


def _a():
    from api import app          # late: app.py includes this router while it is still being imported
    return app


def _ok(data):
    return JSONResponse(jsonable_encoder(data))


def _run(fn, *args, **kw):
    """An action: its result as JSON, or its refusal with its own status."""
    try:
        return _ok({"ok": True, "result": fn(*args, **kw)})
    except actions.ActionError as e:
        return JSONResponse(jsonable_encoder(e.body()), status_code=e.status)


def _missing(what="not found"):
    return JSONResponse({"error": what}, status_code=404)


# ================================================================================================ the frame

@router.get("/session", tags=[FRAME], summary="Top bar: counts, the teacher's line, today")
def session():
    """The top bar: how many batches have a step a person can act on now (and the older counts after Periksa order
    and Jenis halaman), the teacher's one line, today's date."""
    A = _a()
    line = None
    if A.VF:
        try:
            with db.connect() as c:
                line = A.teacher_now(c)
        except Exception:                # before migration 023, or the database is down: nothing to show
            line = None
    return _ok({"needs_you": A._needs_you(), "unsure_left": A._unsure_left(), "batches_need": A._batches_need(),
                "teacher": bahasa.teacher(line), "today": bahasa.hari_ini(), "vf": A.VF})


@router.get("/words", tags=[FRAME], summary="Every display word (Indonesian)")
def words():
    """Every display word the screens use (bahasa.py): document types, checks, statuses, field names, reasons."""
    from common.fields import DOCS
    fields = {t: {f["name"]: bahasa.field(f["name"], t) for f in d["header"]} for t, d in DOCS.items()}
    descs = {t: {f["name"]: bahasa.desc(f["name"], t) for f in d["header"] if bahasa.desc(f["name"], t)}
             for t, d in DOCS.items()}
    return _ok({"DOC": bahasa.DOC, "DOC_SHORT": bahasa.DOC_SHORT, "CHECK": bahasa.CHECK,
                "CHECK_STATUS": bahasa.CHECK_STATUS, "BUNDLE": bahasa.BUNDLE, "BATCH": bahasa.BATCH,
                "OUTCOME": bahasa.OUTCOME, "FLAG": bahasa.FLAG, "HOLD": bahasa.HOLD, "LINK": bahasa.LINK,
                "PUB_STATE": bahasa.PUB_STATE, "REASON": bahasa.REASON, "NONE_REASON": bahasa.NONE_REASON,
                "ROLE": {k or "none": v for k, v in bahasa.ROLE.items()}, "BY": bahasa.BY, "COL": bahasa.COL,
                "FIELD": bahasa.FIELD, "FIELD_BY_TYPE": fields, "DESC_BY_TYPE": descs,
                "LABEL_TYPES": [{"key": k, "name": n, "what": w} for k, n, w in bahasa.LABEL_TYPES]})


@router.get("/search", tags=[FRAME], summary="Find a batch, an order or a file")
def search(q: str = Query("", description="part of a batch number, SOR, customer PO number, customer or file name")):
    """The top bar's search: batches by number, orders by SOR, Nomor CPO or customer, files by name (each up to 20),
    with the batch each came in."""
    return _ok(_a().search_view(q))


@router.get("/health", tags=[FRAME], summary="Services that don't answer")
def health():
    """Beranda's one line about the system: the services that don't answer (details on the Status page)."""
    s = _a().status()
    return _ok({"bad": [r["name"] for r in s["services"] if not r["ok"]], "n": len(s["services"])})


@router.get("/home", tags=[FRAME], summary="Beranda: work waiting, recent scans")
def home():
    return _ok(_a().home_view())


# ================================================================================================ scans and pages

@router.get("/scans", tags=[SCANS], summary="Recent scans")
def scans(limit: int = Query(20, ge=1, le=200)):
    return _ok({"scans": _a()._recent_batches(limit)})


@router.post("/uploads", tags=[UPLOADS], summary="Start an upload batch")
def new_upload(body: NewUpload):
    """One upload action, however many files: a generated batch number (BATCH-YYYYMMDD-NN), who uploads, and the
    day the papers were scanned. Then POST /scans once per file with its id. 201 {id, code, uploaded_by, doc_date};
    400 without a name, or with a date in the future."""
    try:
        out = actions.new_upload(body.by, body.date, body.note)
    except actions.ActionError as e:
        return JSONResponse(jsonable_encoder(e.body()), status_code=e.status)
    return JSONResponse(jsonable_encoder(out), status_code=201)


@router.get("/uploads", tags=[UPLOADS], summary="Every upload batch, newest first")
def list_uploads(limit: int = Query(100, ge=1, le=500)):
    """Each batch: its number, who uploaded it, the scan date, its files and pages, how far reading has come, and
    its orders by status."""
    return _ok({"uploads": _a().uploads_view(limit)})


@router.get("/uploads/{upload_id}", tags=[UPLOADS], summary="One upload batch: its files and orders")
def one_upload(upload_id: int):
    v = _a().upload_view(upload_id)
    return _ok(v) if v else _missing("no such batch")


@router.post("/scans", tags=[SCANS], summary="Upload a scanned PDF")
async def upload(file: UploadFile = File(...), upload: int | None = Form(None, description="the batch it belongs to "
                                                                                          "(POST /uploads first)")):
    """A scanned PDF: stored, recorded as 'received', and put on q.intake (the intake worker splits it into pages and
    queues them). With `upload`, it joins that batch and takes its scan date. 201 {batch_id}; 400 when it isn't a
    readable PDF or the batch doesn't exist; 409 with the earlier scan when it's the same file."""
    try:
        out = actions.upload(await file.read(), file.filename, upload)
    except actions.ActionError as e:
        return JSONResponse(jsonable_encoder(e.body()), status_code=e.status)
    return JSONResponse(out, status_code=201)


@router.get("/scans/{batch_id}", tags=[SCANS], summary="One scan: pages, orders, queues")
def scan(batch_id: str):
    """One scan: its record, every page (type, flags, thumbnail), its orders by status and the queues."""
    A = _a()
    b, pages = A._batch(batch_id)
    if not b:
        return _missing("no such scan")
    return _ok({"scan": b, "pages": pages, "orders": A._batch_orders(batch_id), "flags": A._flag_counts(pages),
                "depths": A._depths(), "upload": A._upload_of(batch_id)})


@router.get("/scans/{batch_id}/pages/{page_no}", tags=[SCANS], summary="One page, with its fields and where each sits")
def page(batch_id: str, page_no: int):
    """One page for the page viewer: what it is, its image, and (once read) its fields with where each sits on the
    paper, its table rows, and the words a person can click to correct a value (app._fix_view)."""
    A = _a()
    with db.connect() as c:
        b = c.execute("SELECT id, file_name, page_total, status FROM staging.scan_batch WHERE id=%s",
                      (batch_id,)).fetchone()
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (batch_id, page_no)).fetchone()
        if not b or not p:
            return _missing("no such page")
        pc = verify.load(c, batch_id, page_no)
        fixv = A._fix_view(c, p, pc) if A.VF else None
    keep = ("page_no", "status", "doc_type", "type_status", "outcome", "quality_flags", "qr_text", "upright_path",
            "original_path", "thumb_upright_path", "thumb_path", "error")
    return _ok({"scan": b, "page": {k: p.get(k) for k in keep}, "fix": fixv, "upload": A._upload_of(batch_id)})


class Retry(BaseModel):
    by: str = _by()


@router.post("/scans/{batch_id}/pages/{page_no}/retry", tags=[SCANS], summary="Try a stuck page again")
def retry(batch_id: str, page_no: int, body: Retry):
    """A stuck page (its worker died, or a model call kept failing after the automatic tries) goes back on q.pages and
    redoes only what failed: it calls the AI again (its quota). 409 when its order is already sent to Satellite, when
    it is queued (being tried), when it isn't stuck, while a model isn't set or a limit holds every page, or (a dead
    page) while another page of its file is still being read."""
    return _run(actions.retry_page, batch_id, page_no, body.by)


@router.post("/scans/{batch_id}/retry", tags=[SCANS], summary="Split a failed file again")
def retry_file(batch_id: str, body: Retry):
    """A file that couldn't be split into pages is split again (status back to received, on q.intake). 409 when it
    didn't fail."""
    return _run(actions.retry_scan, batch_id, body.by)


@router.post("/uploads/{upload_id}/retries", tags=[UPLOADS], summary="Try everything stuck in a batch again")
def retry_all(upload_id: int, body: Retry):
    """Every file of the batch that couldn't be split, and every stuck page, except pages of orders already sent to
    Satellite (listed in `left` with why). 409 while a model isn't set or a limit holds every page."""
    return _run(actions.retry_upload, upload_id, body.by)


@router.post("/scans/{batch_id}/pages/{page_no}/fixes", tags=[SCANS], summary="Correct a value on a page")
def page_fix(batch_id: str, page_no: int, body: Fix):
    """A correction made on the page viewer: the value as printed, and where it was marked (0-1000)."""
    return _run(actions.fix_page, batch_id, page_no, body.field, body.value, body.by, body.region, body.shown,
                body.row_key)


@router.get("/scans/{batch_id}/pages/{page_no}/region", tags=[SCANS], summary="What a marked region of the paper holds")
def region(batch_id: str, page_no: int, box: str):
    """What a region marked on the paper holds (box = ymin,xmin,ymax,xmax on 0-1000)."""
    r = _a().api_region(batch_id, page_no, box)
    return r if isinstance(r, Response) else _ok(r)


@router.get("/keycheck", tags=[SCANS], summary="Does Satellite know this key?")
def keycheck(field: str, value: str):
    """Before a key is saved: does Satellite know it? A warning, never a refusal."""
    return _ok(_a().api_keycheck(field, value))


@router.get("/lessons", tags=[SCANS], summary="What a fix is doing now (the status bar)")
def lesson(batch: str, page: int, field: str):
    """The status bar after a fix: what the fix is doing now (kept as a lesson, the teacher writing a tip, …)."""
    A = _a()
    try:
        with db.connect() as c:
            lp = A.lesson_status(c, batch, page, field)
    except Exception as e:               # before migration 023, or the database is down: say so, never an error
        lp = {"steps": [], "headline": f"Saved. (The lesson's progress can't be shown: {type(e).__name__}.)",
              "tip": None, "final": True}
    lp = bahasa.lesson(lp)
    return _ok({**lp, "steps": [{"label": label, "state": state} for label, state in lp["steps"]]})


# ================================================================================================ page types (Label)

@router.get("/labels", tags=[TYPES], summary="The next page whose type is unsure")
def labels(batch: str | None = None, page: int | None = None, after: int = 0, upload: int | None = None):
    """The Label screen: the next page whose type the system couldn't decide (or the one named), its neighbours,
    an earlier answer, the progress, the choices. With `upload`: over that batch's files (its step 2), the next after
    `batch`/`after`, with `left` = how many are still unsure there."""
    from common import uploads
    d = _a().label_data(batch, page, after, upload)
    with db.connect() as c:
        u = uploads.get(c, upload) if upload else None
    up = u and {k: u[k] for k in ("id", "code", "uploaded_by", "doc_date")}
    if not d["batch"]:
        return _ok({"batch": None, "upload": up, "left": d.get("left")})
    p = d["p"]
    return _ok({**d, "upload": up, "p": p and {k: p[k] for k in ("page_no", "upright_path", "original_path", "quality_flags")},
                "types": [{"key": k, "name": n, "what": w} for k, n, w in d["types"]],
                "near": [{"page_no": n["page_no"], "thumb": n["thumb_upright_path"] or n["thumb_path"],
                          "full": n["upright_path"] or n["original_path"],
                          "type": n["doc_type"] or ("unsure" if n["type_status"] == "unsure" else None)}
                         for n in d["near"]]})


@router.post("/labels", tags=[TYPES], summary="Say what a page is")
def save_label(body: Label):
    return _run(actions.save_label, body.batch, body.page, body.label, body.customer, body.note, body.labelled_by)


# ================================================================================================ orders (Periksa)

@router.get("/orders", tags=[ORDERS], summary="Every order once (all its scans), needs a person first")
def orders(batch: str | None = None, upload: int | None = None):
    """Periksa order: the scan's orders with their status and what is left (needs a person first), the scans that
    have orders, and the needs-you notices nobody has seen yet."""
    A = _a()
    rows = A.review_list(batch or None, upload)        # nothing chosen: every order (each once, all its scans)
    with db.connect() as c:
        scans_ = A.review_scans(c)
        fresh = []
        if A.VF:
            from common import notice
            fresh = notice.unseen(c)
    return _ok({"batch": batch, "upload": upload, "uploads": A.uploads_view(100), "rows": rows, "scans": scans_,
                "fresh": fresh, "ready": sum(1 for r in rows if r["status"] in ("auto_ok", "reviewed"))})


@router.post("/notices/seen", tags=[ORDERS], summary="Mark the needs-you notices seen")
def notices_seen():
    """Sent by Periksa order once a browser shows it: a fetch never marks a notice seen."""
    from common import notice
    with db.connect() as c:
        notice.mark_seen(c)
    return Response(status_code=204)


@router.get("/orders/{sor}", tags=[ORDERS], summary="One order's review")
def order(sor: str, batch: str | None = None):
    """One order's review: what is left (one item per decision), every check, each document's unsettled values with
    where they sit on the page, its rows against SAMB's order, the customer's calibration, and whether it can be
    approved."""
    A = _a()
    batch = batch or A._latest_batch()
    v = A.review_view(batch, sor) if batch else None
    if not v:
        return _missing("no such order")
    b = v["bundle"]
    return _ok({**v, "batch": batch, "left": bahasa.left(v["left"]),
                "bundle": {**{k: b.get(k) for k in ("id", "sor_no", "status", "hold_reason", "reviewed_by",
                                                    "reviewed_at", "published_at")},
                           "documents": (b.get("json") or {}).get("documents") or []}})


@router.post("/orders/{sor}/confirmations", tags=[ORDERS], summary="Confirm or correct a value")
def confirm(sor: str, body: Confirm):
    return _run(actions.confirm, body.batch, sor, body.page, body.field, body.value, body.by, body.row_key,
                body.shown)


@router.post("/orders/{sor}/pairings", tags=[ORDERS], summary="Pair a customer row with an SO line")
def pair(sor: str, body: Pair):
    return _run(actions.pair, body.batch, sor, body.page, body.row, body.line_no, body.by, body.note)


class AskPairs(BaseModel):
    by: str = _by()


_PAIRING = {}                          # sor -> the product matcher's run for that order (this API is one process)
_PAIRING_LOCK = threading.Lock()


def _pairing(sor):
    from grouper import matching
    from common import settings
    try:
        settings.refresh()
        got = matching.propose_order(sor, show=lambda *a: print(*a, flush=True))
        state = {"state": "done", **got}
    except Exception as e:
        state = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    with _PAIRING_LOCK:
        _PAIRING[sor] = {**_PAIRING.get(sor, {}), **state, "finished": datetime.now(timezone.utc).isoformat()}


@router.post("/orders/{sor}/pair-proposals", tags=[ORDERS], summary="Ask the AI which SAMB line each unpaired row is")
def ask_pairs(sor: str, body: AskPairs):
    """The product matcher on this order's rows that nothing else paired (it calls the AI: a few thousand tokens per
    document), in the background: 202 at once, then GET this path for its progress. Its answers become suggestions
    on the order's page; a person's confirmation (POST …/pairings) is what pairs a row and fills the product-code map.
    409 while a run for this order is going."""
    if not body.by.strip():
        return JSONResponse({"error": "say who you are"}, status_code=400)
    with _PAIRING_LOCK:
        if (_PAIRING.get(sor) or {}).get("state") == "running":
            return JSONResponse(_PAIRING[sor], status_code=409)
        _PAIRING[sor] = {"state": "running", "by": body.by.strip(), "started": datetime.now(timezone.utc).isoformat()}
    print(f"pair proposals for {sor}, asked by {body.by.strip()}", flush=True)
    threading.Thread(target=_pairing, args=(sor,), daemon=True).start()
    return JSONResponse(_PAIRING[sor], status_code=202)


@router.get("/orders/{sor}/pair-proposals", tags=[ORDERS], summary="The product matcher's progress on this order")
def pair_progress(sor: str):
    """{state: idle | running | done | failed, rows, proposed, calls, error}."""
    with _PAIRING_LOCK:
        return _ok(_PAIRING.get(sor) or {"state": "idle"})


@router.post("/orders/{sor}/acceptances", tags=[ORDERS], summary="Accept a difference, with a reason")
def accept(sor: str, body: Accept):
    return _run(actions.accept, body.batch, sor, body.check, body.input_print, body.reason, body.by, body.note)


@router.post("/orders/{sor}/calibrations", tags=[ORDERS], summary="A customer's one-time calibration")
def calibrate(sor: str, body: Calibrate):
    return _run(actions.calibrate, body.chain, body.name, body.by, body.allowance, body.receipt_shows)


@router.post("/orders/{sor}/approval", tags=[ORDERS], summary="Approve an order")
def approve(sor: str, body: Approve):
    try:
        actions.approve(body.batch, sor, body.by)
    except actions.ActionError as e:
        out = e.body()
        if "left" in out:
            out["left"] = bahasa.left(out["left"])
        return JSONResponse(jsonable_encoder(out), status_code=e.status)
    return _ok({"ok": True})


@router.post("/publications", tags=[ORDERS], summary="Publish finished orders to Satellite")
def publish(body: Publish):
    """Phase 8: the scan's finished orders written to Satellite (rows + one PDF per SOR). {result: [SOR, …]}."""
    return _run(actions.publish, body.batch, body.by, body.upload)


# ================================================================================================ Berkas, Data terkirim

@router.get("/bundles", tags=[BERKAS], summary="Orders with all their documents (every scan); what is held")
def bundles(batch: str | None = None, upload: int | None = None):
    """Berkas per SOR: the scan's orders with their documents, what is held and why (with the key a person can
    confirm), and the pages not grouped yet."""
    A = _a()                                            # no scan chosen: every order, each whole
    return _ok({"batch": batch or None, "upload": upload, "scans": A._recent_batches(), "uploads": A.uploads_view(100),
                "view": A.bundles_screen(batch or None, upload)})


@router.post("/bundles/confirmations", tags=[BERKAS], summary="Confirm a held document's key")
def confirm_key(body: ConfirmKey):
    return _run(actions.confirm_key, body.batch, body.page, body.field, body.value, body.by)


@router.get("/published", tags=[SENT], summary="Orders published to Satellite")
def published(batch: str | None = None, just: str = "", upload: int | None = None):
    """Data terkirim: every order published to Satellite, newest first, the ones just published on top; with
    `upload`, the orders with a document in that batch."""
    with db.connect() as c:
        rows, batches = _a().published_rows(c, batch, [x for x in just.split(",") if x], upload)
    return _ok({"rows": rows, "batches": batches, "upload": upload, "uploads": _a().uploads_view(100)})


@router.get("/published/{sor}", tags=[SENT], summary="One published order, as Satellite stores it")
def published_one(sor: str):
    """One published order as Satellite holds it: each document's values beside its scan pages (where each value
    sits, what backed it), and the database rows exactly as stored."""
    with db.connect() as c:
        pv = _a().published_view(c, sor)
    return _ok(pv) if pv else _missing("this order has not been published")
