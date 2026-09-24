"""Inspection UI. One tab per pipeline phase; a tab lights up when its phase is built."""
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from common import db, health, intake, keys as keymod, queue as q, storage, verify
from common.fields import DOCS

HERE = os.path.dirname(__file__)
app = FastAPI(title="SAMB OCR — inspection UI")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def static_v(name):
    """Version stamp for a static file (its modification time), so a CSS/JS edit reaches the browser at once
    instead of the browser reusing its cached copy."""
    try:
        return int(os.path.getmtime(os.path.join(HERE, "static", name)))
    except OSError:
        return 0


templates.env.globals["static_v"] = static_v

PHASE_BUILT = 5
# vlm-first experiment (branch vlm-first): same UI code, its own database / vhost / MinIO prefix, pages cloned from v1
VF = os.environ.get("PIPELINE") == "vlm-first"
N8N_WEBHOOK = "http://n8n:5678/webhook/intake"
WIB = timezone(timedelta(hours=7))
TABS = [  # (phase, path, label)
    (0, "/", "Status"),
    (1, "/upload", "Upload"),
    (1, "/batches", "Batches"),
    (3, "/label", "Label"),
    (4, "/fields", "Field lists"),
    (6, "/bundles", "Bundles"),
    (7, "/review", "Review"),
    (8, "/sor", "SOR search"),
]
if VF:
    TABS[4:4] = [(5, "/context", "Jev context"), (5, "/compare", "Compare v1")]

EXPECTED_TABLES = 20      # 19 from the base schema + staging.type_label (006)
if VF:
    EXPECTED_TABLES += 3  # + context_version, lesson, model_call (schema/010, vlm-first only)

# (name, role, how to probe, console link on the host)
SERVICES = [
    ("postgres",  "Shared with v1 · database ocr_vf", "probe", None),
    ("rabbitmq",  "Shared with v1 · vhost vf",        "probe", "http://localhost:15672"),
    ("minio",     "Shared with v1 · reads v1's page renders, writes vf/", "probe", "http://localhost:9001"),
    ("vf-worker", "vlm-first page worker",            "http://vf-worker:8080/health", None),
    ("vf-ui",     "This UI",                          "self",  None),
] if VF else [
    ("postgres",  "Staging + Satellite schema",   "probe",  None),
    ("rabbitmq",  "q.pages · q.group",            "probe",  "http://localhost:15672"),
    ("minio",     "Temp repository (OSS stand-in)", "probe", "http://localhost:9001"),
    ("n8n",       "Intake orchestration",         "http://n8n:5678/healthz", "http://localhost:5678"),
    ("ollama",    "Local models (idle: phase 5 checks by code, no model)", "http://ollama:11434/api/tags", None),
    ("worker",    "Page worker",                  "http://worker:8080/health", None),
    ("grouper",   "Grouping worker",              "http://grouper:8080/health", None),
    ("publisher", "Publisher",                    "http://publisher:8080/health", None),
    ("ui",        "This UI",                      "self",   None),
]


def _check(svc):
    name, role, how, link = svc
    row = {"name": name, "role": role, "link": link, "ok": False, "detail": ""}
    try:
        if how == "self":
            row.update(ok=True, detail="serving")
        elif how == "probe":
            row.update(health.run_probes([name])[name])
        else:
            r = httpx.get(how, timeout=4)
            r.raise_for_status()
            if name == "ollama":
                models = [m["name"] for m in r.json().get("models", [])]
                row.update(ok=True, detail=("models: " + ", ".join(models)) if models else "up, no models pulled yet")
            elif name == "n8n":
                row.update(ok=True, detail="up")
            else:
                j = r.json()
                bad = {k: v["detail"] for k, v in j["deps"].items() if not v["ok"]}
                row.update(ok=j["ok"], detail=j.get("role", "") if j["ok"] else f"deps failing: {bad}")
    except Exception as e:
        row["detail"] = f"{type(e).__name__}: {e}"[:160]
    return row


def status():
    with ThreadPoolExecutor(len(SERVICES)) as ex:
        rows = list(ex.map(_check, SERVICES))
    tables = []
    try:
        with db.connect(connect_timeout=3) as c:
            tables = [r["t"] for r in c.execute(
                "select table_schema||'.'||table_name as t from information_schema.tables "
                "where table_schema in ('satellite','staging') order by 1")]
    except Exception:
        pass
    keys = {
        "TYPESAFE_API_KEY (Jev, phase 3)": bool(os.environ.get("TYPESAFE_API_KEY")),
        "GEMINI_API_KEY (VLM, phase 4)": bool(os.environ.get("GEMINI_API_KEY")),
    }
    checks = [
        (f"All {len(SERVICES)} services healthy", all(r["ok"] for r in rows), f"{sum(r['ok'] for r in rows)} / {len(rows)}"),
        (f"{EXPECTED_TABLES} tables in satellite + staging", len(tables) == EXPECTED_TABLES, f"{len(tables)} found"),
    ]
    return {"services": rows, "tables": tables, "model_keys": keys, "checks": checks,
            "all_ok": all(c[1] for c in checks)}


def ctx(request, **kw):
    return {"request": request, "tabs": TABS, "built": PHASE_BUILT, "path": request.url.path, **kw}


@app.get("/", response_class=HTMLResponse)
def page_status(request: Request):
    return templates.TemplateResponse("status.html", ctx(request, s=status()))


@app.get("/partials/status", response_class=HTMLResponse)
def partial_status(request: Request):
    return templates.TemplateResponse("_status_body.html", ctx(request, s=status()))


@app.get("/api/status")
def api_status():
    return JSONResponse(status())


# ---------------------------------------------------------------- phase 1: intake

def _recent_batches(limit=20):
    with db.connect() as c:
        return c.execute("""SELECT id, file_name, page_total, pages_rendered, page_done, status, received_at
                            FROM staging.scan_batch ORDER BY received_at DESC LIMIT %s""", (limit,)).fetchall()


VF_NO_INTAKE = ("Upload is switched off in the vlm-first experiment: its pages are cloned from v1 "
                "(python -m worker.clone), and n8n's intake belongs to v1.")


@app.get("/upload", response_class=HTMLResponse)
def page_upload(request: Request):
    return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(),
                                                         error=VF_NO_INTAKE if VF else None, dup=None))


@app.post("/upload", response_class=HTMLResponse)
async def do_upload(request: Request, file: UploadFile = File(...)):
    if VF:
        return JSONResponse({"error": VF_NO_INTAKE}, status_code=403)
    data = await file.read()
    if not data.startswith(b"%PDF"):
        return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(),
                                          error="That file is not a PDF.", dup=None), status_code=400)
    sha = hashlib.sha256(data).hexdigest()
    batch_id = intake.batch_id_for(sha)
    with db.connect() as c:
        dup = c.execute("SELECT id, file_name, received_at FROM staging.scan_batch WHERE sha256=%s", (sha,)).fetchone()
    if dup:
        return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(), error=None, dup=dup),
                                          status_code=409)

    day = datetime.now(WIB).date().isoformat()
    key = f"scans/{day}/{batch_id}/{file.filename}"
    storage.ensure_bucket().put_object(storage.bucket(), key, io.BytesIO(data), len(data), content_type="application/pdf")

    payload = {"batch_id": batch_id, "object_key": key, "file_name": file.filename, "sha256": sha, "scanned_day": day}
    try:
        r = httpx.post(N8N_WEBHOOK, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(), dup=None,
            error=f"Stored in MinIO, but n8n did not accept it ({type(e).__name__}: {e}). "
                  f"Is the intake workflow published? Run scripts/n8n-setup.sh."), status_code=502)
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.post("/internal/intake/split")
def internal_split(body: dict):
    if VF:
        return JSONResponse({"error": VF_NO_INTAKE}, status_code=403)
    return intake.split(body["batch_id"], body["object_key"], body["file_name"], body["sha256"], body["scanned_day"])


@app.post("/internal/intake/enqueue")
def internal_enqueue(body: dict):
    if VF:
        return JSONResponse({"error": VF_NO_INTAKE}, status_code=403)
    return intake.enqueue(body["batch_id"])


def _batch(batch_id):
    with db.connect() as c:
        b = c.execute("SELECT * FROM staging.scan_batch WHERE id=%s", (batch_id,)).fetchone()
        pages = c.execute("""SELECT page_no, status, thumb_path, thumb_upright_path, rotation, quality_flags,
                                    qr_text, ocr_conf, error, doc_type, type_status, type_guess
                             FROM staging.page WHERE batch_id=%s ORDER BY page_no""", (batch_id,)).fetchall() if b else []
    return b, pages


def _flag_counts(pages):
    out = {}
    for p in pages:
        for f in p["quality_flags"] or []:
            out[f] = out.get(f, 0) + 1
        t = p.get("doc_type") or ("unsure" if p.get("type_status") == "unsure" else None)
        if t:
            out["type:" + t] = out.get("type:" + t, 0) + 1
    return out


def _safe_depth(name=q.Q_PAGES):
    try:
        return intake.queue_depth(name)
    except Exception:
        return None


_DEPTH_CACHE = {"at": 0.0, "val": None}


def _depths():
    """All three queue depths over one RabbitMQ connection, cached 2 s: the batch page polls this constantly."""
    import time as _t
    if _t.time() - _DEPTH_CACHE["at"] < 2 and _DEPTH_CACHE["val"]:
        return _DEPTH_CACHE["val"]
    try:
        conn = q.connect()
        try:
            ch = conn.channel(); q.declare(ch)
            val = {k: ch.queue_declare(queue=name, passive=True).method.message_count
                   for k, name in (("pages", q.Q_PAGES), ("group", q.Q_GROUP), ("dlq", q.Q_PAGES_DLQ))}
        finally:
            conn.close()
    except Exception:
        val = {"pages": None, "group": None, "dlq": None}
    _DEPTH_CACHE.update(at=_t.time(), val=val)
    return val


GOLDEN_FILE = "7000356304 - 7000356499.pdf"
SOR_RE = re.compile(r"SOR2611\d{7}")


def _golden():
    try:
        return json.load(open("/app/testdata/golden_p1-32.json"))
    except Exception:
        return None


def phase2_checks(batch_id):
    """Acceptance for phase 2, computed from the database. Same code feeds the UI box and the test."""
    b, pages = _batch(batch_id)
    if not b:
        return None
    with db.connect() as c:
        detail = {r["page_no"]: r for r in c.execute(
            """SELECT page_no, rotation, quality_flags, classical_text, qr_text FROM staging.page
               WHERE batch_id=%s AND page_no <= 32""", (batch_id,))}
    total = b["page_total"]
    read = sum(p["status"] == "read" for p in pages)
    dead = [p["page_no"] for p in pages if p["status"] == "dead_letter"]
    checks = [
        ("Every page read", read == total, f"{read} of {total}"),
        ("Scoreboard reached N of N", b["page_done"] == total, f"{b['page_done']} of {total}"),
        ("No page in the dead-letter queue", not dead, f"{len(dead)} failed" + (f": {dead[:10]}" if dead else "")),
    ]
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    fp = []
    if golden and read == total:
        rot_found = sorted(n for n, r in detail.items() if r["rotation"])
        rot_want = golden["rotated_pages"]
        checks.append(("Sideways pages turned upright (pages 1–32)", rot_found == rot_want,
                       f"found {rot_found}" + ("" if rot_found == rot_want else f" · expected {rot_want}")))
        ov = golden.get("orientation_verified")
        if ov:
            with db.connect() as c:
                rot = {r["page_no"]: r["rotation"] for r in c.execute(
                    "SELECT page_no, rotation FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)",
                    (batch_id, ov["as_scanned_upright"] + ov["as_scanned_sideways"]))}
            bad = [n for n in ov["as_scanned_upright"] if rot.get(n)] + [n for n in ov["as_scanned_sideways"] if not rot.get(n)]
            checks.append(("Orientation regression set (12 pages Phase 2 v1 got wrong)", not bad,
                           "all 12 right" if not bad else f"still wrong: {bad}"))
        dark_found = sorted(n for n, r in detail.items() if "dark_band" in (r["quality_flags"] or []))
        dark_want = sorted(golden["dark_pages"] + golden["dark_pages_unconfirmed"])
        checks.append(("Dark pages flagged (pages 1–32)", set(dark_want) <= set(dark_found), f"found {dark_found}"))
        want = {bd["pages"][0]: bd["sor"] for bd in golden["bundles"]}
        for n, sor in sorted(want.items()):
            r = detail[n]
            flat = re.sub(r"[^A-Z0-9]", "", (r["classical_text"] or "").upper())
            m = SOR_RE.search(flat)
            qr = r["qr_text"] if r["qr_text"] and re.fullmatch(r"SOR\d{11}", r["qr_text"]) else None
            found = qr or (m.group(0) if m else None)            # what the PIPELINE has, not the answer key
            how = "QR" if qr else ("text" if m else None)
            fp.append({"page": n, "key": sor, "found": found, "how": how, "title": "FAKTURPENJUALAN" in flat,
                       "faint": "faint" in (r["quality_flags"] or []), "wrong": bool(found and found != sor)})
        wrong = [x["page"] for x in fp if x["wrong"]]
        missing = [x["page"] for x in fp if not x["found"]]
        checks.append(("No wrong SOR on any Faktur Penjualan (\"don't know\" is allowed, wrong is not)", not wrong,
                       f"pipeline found {len(fp) - len(missing)} of {len(fp)}" +
                       (f" · not found: {missing}" if missing else "") + (f" · WRONG: {wrong}" if wrong else "")))
    return {"checks": checks, "fp": fp, "all_ok": all(ok for _, ok, _ in checks), "golden": bool(golden)}


def phase3_checks(batch_id):
    """Acceptance for phase 3. A decided type must be right; 'unsure' is allowed; a Faktur Penjualan must never
    be given another type (a missed FP is the one mistake that would break grouping)."""
    b, pages = _batch(batch_id)
    if not b:
        return None
    with db.connect() as c:
        rows = c.execute("""SELECT page_no, doc_type, type_status, type_guess, type_votes, layout_score, qr_text,
                                   classify_version, quality_flags
                            FROM staging.page WHERE batch_id=%s ORDER BY page_no""", (batch_id,)).fetchall()
    total = b["page_total"]
    done = [r for r in rows if r["classify_version"]]
    dist, unsure_all = {}, []
    for r in done:
        if r["type_status"] == "decided":
            dist[r["doc_type"]] = dist.get(r["doc_type"], 0) + 1
        else:
            unsure_all.append(r["page_no"])
    jev_errors = [r["page_no"] for r in done if (r["type_votes"] or {}).get("jev", {}).get("error")]
    jev_skipped = any((r["type_votes"] or {}).get("jev", {}).get("skipped") for r in done)
    checks = [
        ("Every page classified (decided or unsure)", len(done) == total, f"{len(done)} of {total}"),
        ("Jev answered every page", not jev_errors and not jev_skipped,
         "no key, keyword + layout only" if jev_skipped else f"{len(jev_errors)} errors" + (f": {jev_errors[:10]}" if jev_errors else "")),
    ]
    # Consistency across the WHOLE batch, no answer key needed.
    qr_not_fp = [r["page_no"] for r in done if r["qr_text"] and SOR_RE.fullmatch(r["qr_text"] or "") and r["doc_type"] != "FP"]
    checks.append(("Every page with an SOR QR code is decided FP (whole batch, no answer key)", not qr_not_fp,
                   f"{sum(1 for r in done if r['qr_text'])} QR pages" + (f" · not FP: {qr_not_fp}" if qr_not_fp else "")))

    table, cmp = [], {}
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    if golden and len(done) == total:
        alts = golden.get("type_alternatives", {})
        by = {r["page_no"]: r for r in rows}
        wrong, fp_bad, stats = [], [], {"keyword": [0, 0, 0], "jev": [0, 0, 0], "final": [0, 0, 0]}   # right, abstain, wrong
        for n_s, key in sorted(golden["page_types"].items(), key=lambda kv: int(kv[0])):
            n = int(n_s); r = by[n]; ok = set(alts.get(n_s, [key]))
            v = r["type_votes"] or {}; jv = v.get("jev", {})
            kw = v.get("keyword"); kw = None if (kw or "").startswith("conflict") else kw
            final = r["doc_type"] if r["type_status"] == "decided" else None
            for name, val in (("keyword", kw), ("jev", jv.get("choice")), ("final", final)):
                stats[name][0 if val in ok else (1 if val is None else 2)] += 1
            if final and final not in ok:
                wrong.append(n)
            if key == "FP" and final not in (None, "FP"):
                fp_bad.append(n)
            table.append({"page": n, "key": "/".join(sorted(ok)), "keyword": v.get("keyword"), "jev": jv.get("choice"),
                          "jev_conf": jv.get("confidence"), "layout": r["layout_score"], "qr": bool(r["qr_text"]),
                          "final": final, "guess": r["type_guess"], "status": r["type_status"], "reason": v.get("reason"),
                          "flags": r["quality_flags"] or [], "right": final in ok if final else None})
        unsure = [t["page"] for t in table if t["status"] == "unsure"]
        fp_pages = [t for t in table if t["key"] == "FP"]
        checks.append(("No page given a wrong type (pages 1–32; unsure is allowed)", not wrong,
                       f"{32 - len(unsure)} decided, {len(unsure)} unsure {unsure}" + (f" · WRONG: {wrong}" if wrong else "")))
        checks.append(("Every Faktur Penjualan is FP or unsure — never another type", not fp_bad,
                       f"{sum(1 for t in fp_pages if t['final'] == 'FP')} of {len(fp_pages)} decided FP" +
                       (f" · given another type: {fp_bad}" if fp_bad else "")))
        f6 = {t["page"]: (t["final"] or f"unsure (guess {t['guess']})") for t in table if t["page"] in (6, 8)}
        checks.append(("Faint FP pages 6 and 8 are not given a wrong type", all(v in ("FP",) or v.startswith("unsure") for v in f6.values()),
                       f"page 6: {f6.get(6)} · page 8: {f6.get(8)}"))
        cmp = {k: {"right": v[0], "abstain": v[1], "wrong": v[2]} for k, v in stats.items()}
    return {"checks": checks, "all_ok": all(ok for _, ok, _ in checks), "table": table, "cmp": cmp,
            "dist": dict(sorted(dist.items(), key=lambda kv: -kv[1])), "unsure": unsure_all, "golden": bool(golden)}


# ---------------------------------------------------------------- labelling (learning from corrections)

LABEL_TYPES = [("FP", "Faktur Penjualan", "SAMB's own sales invoice"),
               ("TTG", "Tanda Terima", "customer's receipt of goods, any name"),
               ("PO", "Purchase Order", "customer's order to SAMB, incl. Surat Pesanan"),
               ("SJ", "Surat Jalan", "delivery note"),
               ("FPJ", "Faktur Pajak", "tax invoice"),
               ("PEL", "Pelunasan", "payment / remittance"),
               ("CONTINUATION", "Continuation", "later page of the document before it, no title of its own"),
               ("OTHER", "Other", "none of these, or unreadable")]
EXAM_SHARE = 0.2


def _latest_batch():
    with db.connect() as c:
        r = c.execute("SELECT id FROM staging.scan_batch ORDER BY received_at DESC LIMIT 1").fetchone()
    return r and r["id"]


def _label_progress(batch_id):
    with db.connect() as c:
        return c.execute("""
            SELECT count(*) FILTER (WHERE p.type_status='unsure')                       AS unsure,
                   count(*) FILTER (WHERE p.type_status='unsure' AND l.page_no IS NOT NULL) AS unsure_done,
                   (SELECT count(*) FROM staging.type_label WHERE batch_id=%s)          AS labelled,
                   (SELECT count(*) FROM staging.type_label WHERE batch_id=%s AND pile='practice') AS practice,
                   (SELECT count(*) FROM staging.type_label WHERE batch_id=%s AND pile='exam')     AS exam
            FROM staging.page p LEFT JOIN staging.type_label l USING (batch_id, page_no)
            WHERE p.batch_id=%s""", (batch_id,) * 4).fetchone()


def _customers():
    with db.connect() as c:
        seen = [r["customer"] for r in c.execute(
            "SELECT DISTINCT customer FROM staging.type_label WHERE customer IS NOT NULL AND customer <> '' ORDER BY 1")]
    starters = ["Hari Hari", "Boots", "Hero / DFI Retail", "Indomaret", "MM2", "TIP TOP", "Ranch Market", "Farmers Market"]
    return seen + [x for x in starters if x not in seen]


@app.get("/label", response_class=HTMLResponse)
def page_label(request: Request, batch: str | None = None, page: int | None = None, saved: int | None = None,
               after: int = 0):
    batch = batch or _latest_batch()
    if not batch:
        return templates.TemplateResponse("label.html", ctx(request, batch=None))
    with db.connect() as c:
        if page is None:   # next unsure page nobody has labelled yet
            r = c.execute("""SELECT p.page_no FROM staging.page p
                             LEFT JOIN staging.type_label l USING (batch_id, page_no)
                             WHERE p.batch_id=%s AND p.type_status='unsure' AND l.page_no IS NULL AND p.page_no > %s
                             ORDER BY p.page_no LIMIT 1""", (batch, after)).fetchone()
            page = r and r["page_no"]
        p = c.execute("""SELECT page_no, upright_path, original_path, quality_flags FROM staging.page
                         WHERE batch_id=%s AND page_no=%s""", (batch, page)).fetchone() if page else None
        total = c.execute("SELECT page_total FROM staging.scan_batch WHERE id=%s", (batch,)).fetchone()["page_total"]
        existing = c.execute("SELECT * FROM staging.type_label WHERE batch_id=%s AND page_no=%s",
                             (batch, page)).fetchone() if page else None
        near = c.execute("""SELECT page_no, thumb_upright_path, thumb_path, upright_path, original_path,
                                   doc_type, type_status FROM staging.page
                            WHERE batch_id=%s AND page_no BETWEEN %s AND %s ORDER BY page_no""",
                         (batch, (page or 1) - 2, (page or 1) + 2)).fetchall() if page else []
    near_json = json.dumps([{"page_no": n["page_no"], "full": n["upright_path"] or n["original_path"],
                             "type": n["doc_type"] or ("unsure" if n["type_status"] == "unsure" else None),
                             "cur": n["page_no"] == page} for n in near]).replace("</", "<\\/")
    return templates.TemplateResponse("label.html", ctx(
        request, batch=batch, p=p, page=page, total=total, existing=existing, near=near, near_json=near_json,
        types=LABEL_TYPES,
        customers=_customers(), prog=_label_progress(batch), saved=saved))


@app.post("/label")
def save_label(batch: str = Form(...), page: int = Form(...), label: str = Form(...), customer: str = Form(""),
               note: str = Form(""), labelled_by: str = Form(""), then: str = Form("next")):
    if label not in {t[0] for t in LABEL_TYPES}:
        return JSONResponse({"error": "unknown label"}, status_code=400)
    v1_pile = _v1_pile(batch, page) if VF else None
    with db.connect() as c:
        # The pile is drawn once, on first save; relabelling never moves a page between practice and exam.
        # vlm-first: a page v1 already put in a pile keeps that pile, so a page is never practice in one and exam in the other.
        c.execute("""INSERT INTO staging.type_label (batch_id, page_no, label, customer, note, labelled_by, pile)
                     VALUES (%s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
                             COALESCE(%s, CASE WHEN random() < %s THEN 'exam' ELSE 'practice' END))
                     ON CONFLICT (batch_id, page_no) DO UPDATE SET
                       label=EXCLUDED.label, customer=EXCLUDED.customer, note=EXCLUDED.note,
                       labelled_by=EXCLUDED.labelled_by, labelled_at=now()""",
                  (batch, page, label, customer.strip(), note.strip(), labelled_by.strip(), v1_pile, EXAM_SHARE))
    if VF:
        vf_after_label(batch, page, label)
    nxt = f"/label?batch={batch}&saved={page}" if then == "next" else f"/label?batch={batch}&page={page}&saved={page}"
    return RedirectResponse(nxt, status_code=303)


@app.get("/labels", response_class=HTMLResponse)
def page_labels(request: Request, batch: str | None = None):
    batch = batch or _latest_batch()
    with db.connect() as c:
        rows = c.execute("""SELECT l.*, p.type_status, p.doc_type, p.type_guess, p.thumb_upright_path
                            FROM staging.type_label l JOIN staging.page p USING (batch_id, page_no)
                            WHERE l.batch_id=%s ORDER BY l.page_no""", (batch,)).fetchall() if batch else []
    by_type = {}
    for r in rows:
        by_type[r["label"]] = by_type.get(r["label"], 0) + 1
    return templates.TemplateResponse("labels.html", ctx(request, batch=batch, rows=rows, by_type=by_type,
                                                         prog=_label_progress(batch) if batch else None))


@app.get("/fields", response_class=HTMLResponse)
def page_fields(request: Request):
    from common import fields as F
    return templates.TemplateResponse("fields.html", ctx(request, docs=F.DOCS, not_yet=F.NOT_YET, sql=F.SQL, column=F.column))


@app.get("/batches", response_class=HTMLResponse)
def page_batches(request: Request):
    return templates.TemplateResponse("batches.html", ctx(request, batches=_recent_batches(100)))


@app.get("/batches/{batch_id}", response_class=HTMLResponse)
def page_batch(request: Request, batch_id: str):
    b, pages = _batch(batch_id)
    if VF:
        return templates.TemplateResponse("batch.html", ctx(request, batch_id=batch_id, b=b, pages=pages, depth=None,
                                          depths=_depths(), vf=vf_checks(batch_id),
                                          flt=request.query_params.get("flag"), fc=_flag_counts(pages)))
    return templates.TemplateResponse("batch.html", ctx(request, batch_id=batch_id, b=b, pages=pages, depth=None,
                                      depths=_depths(), p2=phase2_checks(batch_id), p3=phase3_checks(batch_id),
                                      p4=phase4_checks(batch_id), p5=phase5_checks(batch_id),
                                      flt=request.query_params.get("flag"), fc=_flag_counts(pages)))


@app.get("/partials/batch/{batch_id}/stats", response_class=HTMLResponse)
def partial_batch_stats(request: Request, batch_id: str):
    b, pages = _batch(batch_id)
    if VF:
        return templates.TemplateResponse("_batch_stats.html", ctx(request, batch_id=batch_id, b=b, pages=pages,
                                          depth=None, depths=_depths(), vf=vf_checks(batch_id)))
    return templates.TemplateResponse("_batch_stats.html", ctx(request, batch_id=batch_id, b=b, pages=pages, depth=None,
                                      depths=_depths(), p2=phase2_checks(batch_id), p3=phase3_checks(batch_id),
                                      p4=phase4_checks(batch_id), p5=phase5_checks(batch_id)))


@app.get("/partials/batch/{batch_id}/grid", response_class=HTMLResponse)
def partial_batch_grid(request: Request, batch_id: str):
    b, pages = _batch(batch_id)
    return templates.TemplateResponse("_batch_grid.html", ctx(request, batch_id=batch_id, b=b, pages=pages,
                                      flt=request.query_params.get("flag"), fc=_flag_counts(pages)))


@app.post("/batches/{batch_id}/rerun")
def rerun(batch_id: str, pages: str = Form("")):
    """pages: empty = all, or a range like '1-32' / '1,3,8'."""
    sel = []
    for part in [x.strip() for x in pages.split(",") if x.strip()]:
        a, _, z = part.partition("-")
        sel += list(range(int(a), int(z or a) + 1))
    intake.rerun(batch_id, sel or None)
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


GOLDEN_FIELD_MAP = {  # answer-key field -> (doc type, extracted field)
    ("FP", "sor"): "sor", ("FP", "nomor_cpo"): "nomor_cpo",
    ("TTG", "no_ref"): "no_ref", ("TTG", "no_receive"): "document_no", ("TTG", "document_no"): "document_no",
    ("TTG", "purchase_order_no"): "purchase_order_no",
    ("PO", "purchase_order_no"): "purchase_order_no", ("PO", "total"): "total",
}


def _same(expected, got):
    try:
        return abs(float(expected) - float(got)) < 0.01
    except (TypeError, ValueError):
        return keymod.flat(expected) == keymod.flat(got)


def _confirmed(value, source, text):
    """Does Tesseract's independent reading contain this value (letters+digits only)? Amounts: digits only."""
    if not value:
        return False
    return keymod.in_text(value, text) or keymod.in_text(source, text) or \
        (re.sub(r"\D", "", source or "") and re.sub(r"\D", "", source or "") in re.sub(r"\D", "", text or ""))


SCOPE = range(1, 32)   # AI OCR work is on pages 1–31 (decided 2026-09-24): the free tier is ~60 calls a day, and
                       # page 31 ends a bundle while page 32's FP continues past the answer key


def _answer_key_values(golden):
    """(page, type, field, expected) for every answer-key value the AI OCR has a field for, pages in SCOPE."""
    want = []
    for bd in golden["bundles"]:
        want.append((bd["pages"][0], "FP", "sor", bd["sor"]))
        for pg, fl in (bd.get("fields") or {}).items():
            for name, v in fl.items():
                t = golden["page_types"].get(pg)
                if (t, name) in GOLDEN_FIELD_MAP:
                    want.append((int(pg), t, GOLDEN_FIELD_MAP[(t, name)], v))
    return sorted(w for w in want if w[0] in SCOPE)


def phase4_checks(batch_id):
    b, pages = _batch(batch_id)
    if not b:
        return None
    with db.connect() as c:
        rows = {r["page_no"]: r for r in c.execute("""
            SELECT page_no, doc_type, type_status, type_votes, fields, keys, extract_status, extract_error, vlm_meta,
                   classical_text FROM staging.page WHERE batch_id=%s""", (batch_id,))}
    st = {}
    for r in rows.values():
        st[r["extract_status"] or "not yet"] = st.get(r["extract_status"] or "not yet", 0) + 1
    models, secs, think = {}, [], []
    for r in rows.values():
        for m in (r["vlm_meta"] or {}).values():
            models[m.get("model")] = models.get(m.get("model"), 0) + 1
            secs.append(m.get("ms", 0) / 1000); think.append(m.get("tokens_thinking") or 0)
    tried = [r for r in rows.values() if (r["type_votes"] or {}).get("second_try")]
    rescued = [r["page_no"] for r in tried if r["type_status"] == "decided"]
    checks, items = [], []
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    if golden:
        scope = [n for n in SCOPE if n in rows]
        attempted = [n for n in scope if rows[n]["extract_status"]]
        failed = [n for n in scope if rows[n]["extract_status"] == "failed"]
        checks.append(("AI OCR ran on every page 1–31 (or recorded why not)", len(attempted) == len(scope) and not failed,
                       f"{len(attempted)} of {len(scope)} · done {sum(rows[n]['extract_status']=='done' for n in scope)}"
                       + (f" · FAILED {failed}: {rows[failed[0]]['extract_error']}" if failed else "")))
        for pg, t, fname, expected in _answer_key_values(golden):
            r = rows[pg]
            fld = (r["fields"] or {}).get(fname) or {}
            got, src = fld.get("value"), fld.get("source_text")
            if r["extract_status"] != "done" or r["doc_type"] != t:
                verdict = "not extracted"
            elif not got:
                verdict = "missing"
            else:
                verdict = "right" if _same(expected, got) else "WRONG"
            conf = _confirmed(got, src, r["classical_text"])
            items.append({"page": pg, "type": t, "field": fname, "expected": expected, "got": got, "source": src,
                          "verdict": verdict, "confirmed": conf})
        n = {k: sum(i["verdict"] == k for i in items) for k in ("right", "missing", "WRONG", "not extracted")}
        slipped = [f"p{i['page']} {i['field']}={i['got']}" for i in items if i["verdict"] == "WRONG" and i["confirmed"]]
        p4 = {i["field"]: i for i in items if i["page"] == 4}
        boots_ok = all(p4.get(f, {}).get("verdict") == "right" for f in ("purchase_order_no", "total"))
        checks.append(("Boots PO page 4: PO 4505832724 and total 1,126,006", boots_ok,
                       " · ".join(f"{f} {p4.get(f, {}).get('got')}" for f in ("purchase_order_no", "total"))))
        checks.append(("Every WRONG value is flagged unconfirmed (none passes as confirmed by Tesseract)", not slipped,
                       f"{n['WRONG']} wrong, all flagged" if not slipped else f"slipped through: {slipped}"))
        checks.append(("Accuracy vs the answer key (measured, not a gate — phase 5 is the gate)", True,
                       f"right {n['right']} · missing {n['missing']} · WRONG {n['WRONG']} · not extracted {n['not extracted']}"))
    checks.append(("Unsure pages: second try with the AI OCR's reading", True,
                   f"{len(tried)} tried · {len(rescued)} now decided {rescued[:12]}{'…' if len(rescued) > 12 else ''}"))
    return {"checks": checks, "all_ok": all(ok for _, ok, _ in checks), "values": items, "status": st,
            "models": models, "avg_s": round(sum(secs) / len(secs), 1) if secs else None,
            "max_think": max(think) if think else None, "golden": bool(golden)}


def _one_digit_off(kind, value, source):
    """The same value with one character changed, in the value and in its printed text alike: a consistent misread."""
    def bump(s):
        for i, ch in enumerate(s):
            if ch.isdigit():
                return s[:i] + str((int(ch) + 1) % 10) + s[i + 1:]
        for i, ch in enumerate(s):
            if ch.isalpha():
                return s[:i] + ("C" if ch.upper() == "B" else "B") + s[i + 1:]
        return s + "1"
    if kind == "date":
        y = str(value)[:4]
        y2 = str(int(y) + 1)
        return y2 + str(value)[4:], (source.replace(y, y2) if y in source else re.sub(y[2:] + r"\b", y2[2:], source))
    return bump(str(value)), bump(str(source))


def phase5_checks(batch_id):
    """Phase 5 acceptance on pages 1–31. Gates: every read page is checked; no wrong value gets ✅; a value changed
    by one digit never gets ✅. The share of ✅ is measured, not gated: 'a person checks it' is always allowed."""
    b, _ = _batch(batch_id)
    if not b:
        return None
    with db.connect() as c:
        rows = {r["page_no"]: r for r in c.execute("""
            SELECT page_no, doc_type::text AS doc_type, fields, verify_version, extract_status, classical_text, qr_text
              FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)""", (batch_id, list(SCOPE)))}
        for n, r in rows.items():
            r["checks"] = verify.load(c, batch_id, n) if r["extract_status"] == "done" else None
    done = [n for n in sorted(rows) if rows[n]["extract_status"] == "done"]
    checked = [n for n in done if rows[n]["verify_version"] == verify.VERIFY_VERSION and rows[n]["checks"]]
    checks = [("Every page the AI OCR read (pages 1–31) is checked with the current rules", len(checked) == len(done),
               f"{len(checked)} of {len(done)} read pages checked · {len(SCOPE) - len(done)} not read yet")]
    tally = {"ok": 0, "check": 0, "empty": 0, "text": 0, "qr": 0, "adds_up": 0}
    lines = {"ok": 0, "check": 0, "empty": 0}
    todo, caught, changed, missed = [], 0, 0, []
    for n in checked:
        r = rows[n]
        kinds = {f["name"]: f["kind"] for f in DOCS[r["doc_type"]]["header"]}
        for name, v in r["checks"]["header"].items():
            tally[v["verdict"]] += 1
            if v["verdict"] == "ok":
                tally[v["by"]] += 1
                f = r["fields"][name]
                val, src = _one_digit_off(kinds[name], f["value"], f["source_text"])
                bent = {**r["fields"], name: {"value": val, "source_text": src}}
                changed += 1
                if verify.header(r["doc_type"], bent, r["classical_text"], r["qr_text"])[name]["verdict"] == "ok":
                    missed.append(f"p{n} {name}: {val}")
                else:
                    caught += 1
            elif v["verdict"] == "check":
                f = r["fields"].get(name) or {}
                todo.append({"page": n, "type": r["doc_type"], "field": name, "value": f.get("value"),
                             "source": f.get("source_text"), "why": v["why"], "key": None})
        for k, v in r["checks"]["summary"]["lines"].items():
            lines[k] += v
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    if golden:
        right_ok = right_check = 0
        wrong, slipped = [], []
        for pg, t, fname, expected in _answer_key_values(golden):
            r = rows.get(pg)
            if pg not in checked or r["doc_type"] != t:
                continue
            got = ((r["fields"] or {}).get(fname) or {}).get("value")
            v = r["checks"]["header"].get(fname, {})
            if not got:
                continue
            if _same(expected, got):
                right_ok += v.get("verdict") == "ok"
                right_check += v.get("verdict") == "check"
            else:
                wrong.append(f"p{pg} {fname}={got}")
                if v.get("verdict") == "ok":
                    slipped.append(f"p{pg} {fname}={got} (answer key {expected})")
            for i in todo:
                if (i["page"], i["field"]) == (pg, fname):
                    i["key"] = "right" if _same(expected, got) else "WRONG"
        checks.append(("No wrong value gets ✅ (checked against the answer key)", not slipped,
                       (f"{len(wrong)} wrong: {', '.join(wrong)} · all sent to a person" if wrong else "no wrong values among the read pages")
                       if not slipped else f"got ✅: {slipped}"))
        checks.append(("Right values that got ✅ (measured, not a gate)", True,
                       f"{right_ok} of {right_ok + right_check} right answer-key values got ✅ · {right_check} right but sent to a person"))
    checks.append(("A value with one digit changed never gets ✅ (every ✅ value, changed and re-checked)", not missed,
                   f"{caught} of {changed} changed values caught" if not missed else f"got ✅ anyway: {missed}"))
    checks.append(("Main fields (measured)", True,
                   f"✅ {tally['ok']} — printed {tally['text']} · QR {tally['qr']} · adds up {tally['adds_up']} · "
                   f"to a person {tally['check']} · empty {tally['empty']}"))
    checks.append(("Line items (measured)", True,
                   f"✅ {lines['ok']} · to a person {lines['check']} · empty {lines['empty']} — Tesseract can't read most table rows "
                   f"on these scans; phase 7 compares FP / TTG / PO quantities"))
    return {"checks": checks, "all_ok": all(ok for _, ok, _ in checks), "todo": todo, "golden": bool(golden)}


@app.get("/api/batches/{batch_id}/phase5")
def api_phase5(batch_id: str):
    r = phase5_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase4")
def api_phase4(batch_id: str):
    r = phase4_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase3")
def api_phase3(batch_id: str):
    r = phase3_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase2")
def api_phase2(batch_id: str):
    r = phase2_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/batches/{batch_id}/pages/{page_no}", response_class=HTMLResponse)
def page_page(request: Request, batch_id: str, page_no: int):
    with db.connect() as c:
        b = c.execute("SELECT id, file_name, file_path, page_total, status FROM staging.scan_batch WHERE id=%s",
                      (batch_id,)).fetchone()
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (batch_id, page_no)).fetchone()
        pc = verify.load(c, batch_id, page_no) if p else None
        vfp = _vf_page(c, p) if VF and p else None
    ticket = None
    if b and p:
        ticket = json.dumps({"batch_id": batch_id, "page_no": page_no, "image_key": p["image_path"],
                             "pdf_key": b["file_path"]}, indent=2)
    words = p["ocr_words"] if p and p["ocr_words"] else []
    size = _png_size(p["upright_path"]) if p and p["upright_path"] else None
    return templates.TemplateResponse("page.html", ctx(request, batch_id=batch_id, b=b, p=p, page_no=page_no,
                                      ticket=ticket, words=words, size=size, pc=pc, vfp=vfp))


def _png_size(key):
    """Width/height straight from the PNG header (first 24 bytes), so word boxes line up with the image."""
    try:
        o = storage.client().get_object(storage.bucket(), key, offset=0, length=24)
        head = o.read(); o.close(); o.release_conn()
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        return None


@app.get("/img/{key:path}")
def image(key: str):
    if not (key.startswith("pages/") or key.startswith("vf/pages/")):
        return Response(status_code=404)
    try:
        obj = storage.client().get_object(storage.bucket(), key)
        data = obj.read(); obj.close(); obj.release_conn()
    except Exception:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg" if key.endswith(".jpg") else "image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/batches/{batch_id}")
def api_batch(batch_id: str):
    b, pages = _batch(batch_id)
    if not b:
        return JSONResponse({"error": "not found"}, status_code=404)
    by_status = {}
    for p in pages:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    return {"batch": {k: (str(v) if not isinstance(v, (int, str, type(None))) else v) for k, v in b.items()},
            "page_rows": len(pages), "pages_by_status": by_status, "q_pages_depth": _depths()["pages"], "depths": _depths()}



# ================================================================================================= vlm-first
# Only used when PIPELINE=vlm-first (branch vlm-first). Grading uses the answer key here in the UI only.

def _main_db():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(os.environ["MAIN_DATABASE_URL"], row_factory=dict_row)      # read-only


def _v1_pile(batch, page):
    try:
        with _main_db() as m:
            r = m.execute("SELECT pile FROM staging.type_label WHERE batch_id=%s AND page_no=%s", (batch, page)).fetchone()
        return r and r["pile"]
    except Exception:
        return None


def vf_after_label(batch, page, label):
    """A person's label decides the page's type. If the AI OCR already read the page, it resumes at the Tesseract
    check. A practice-pile label on a page the machine was unsure or wrong about becomes a lesson for the teacher."""
    with db.connect() as c:
        p = c.execute("""SELECT p.page_no, p.image_path, p.original_path, p.fields_all IS NOT NULL AS read,
                                p.type_votes->'machine' AS machine, b.run, l.pile
                           FROM staging.page p JOIN staging.scan_batch b ON b.id = p.batch_id
                           JOIN staging.type_label l USING (batch_id, page_no)
                          WHERE p.batch_id=%s AND p.page_no=%s""", (batch, page)).fetchone()
        if not p:
            return
        m = p["machine"] or {}
        if p["pile"] == "practice" and (m.get("status") != "decided" or m.get("doc_type") != label) and p["read"]:
            c.execute("""INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, %s, %s)
                         ON CONFLICT (batch_id, page_no) DO UPDATE SET label=EXCLUDED.label, status='waiting',
                           answer=NULL, proposal=NULL, error=NULL""", (batch, page, label))
        if not p["read"] or not (p["original_path"] or p["image_path"] or "").startswith("pages/"):
            return                          # only pages with a real image are resumed
        c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND page_no=%s", (batch, page))
    import pika
    mq = q.connect()
    try:
        ch = mq.channel(); q.declare(ch)
        ch.basic_publish("", q.Q_PAGES, json.dumps({"batch_id": batch, "page_no": page, "run": p["run"],
                                                    "image_key": p["original_path"] or p["image_path"]}).encode(),
                         pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        mq.close()


def _vf_page(c, p):
    from common.fields import CANON, TYPE_MAP
    cv = c.execute("SELECT content FROM staging.context_version WHERE version=%s", (p.get("context_version"),)).fetchone()
    fields = (cv or {}).get("content", {}).get("fields") or {n: f for n, f in CANON.items()}
    lesson = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=%s",
                       (p["batch_id"], p["page_no"])).fetchone()
    label = c.execute("SELECT label::text AS label, pile FROM staging.type_label WHERE batch_id=%s AND page_no=%s",
                      (p["batch_id"], p["page_no"])).fetchone()
    calls = c.execute("""SELECT provider, purpose, ok, ms, model FROM staging.model_call WHERE batch_id=%s AND page_no=%s
                         ORDER BY at""", (p["batch_id"], p["page_no"])).fetchall()
    mapping = None
    if p["doc_type"] in DOCS and p["fields"]:
        t = DOCS[p["doc_type"]]
        mapping = {"table": f"satellite.{t['table']}",
                   "row": {f["name"]: ((p["fields"].get(f["name"]) or {}).get("value")) for f in t["header"]}}
    filled = sum(1 for n, f in (p.get("fields_all") or {}).items()
                 if n != "lines" and isinstance(f, dict) and f.get("value") not in (None, ""))
    return {"fields": fields, "lesson": lesson, "label": label, "calls": calls, "mapping": mapping, "filled": filled,
            "type_names": {v: k for k, v in TYPE_MAP.get(p["doc_type"], {}).items()}}


def _vf_bent_passes(r, name, kind, f):
    """Would the value, changed by one digit, still be backed by print somewhere in the chain?"""
    from worker import zoom
    val, src = _one_digit_off(kind, f["value"], f["source_text"])
    bent = {**(r["fields"] or {}), name: {"value": val, "source_text": src}}
    if verify.header(r["doc_type"], bent, r["classical_text"], r["qr_text"])[name]["verdict"] == "ok":
        return True
    z = (r["zoom"] or {}).get(name) or {}
    return bool(z.get("texts")) and zoom.confirm(src, z["texts"], z.get("band") or [])[0]


def vf_checks(batch_id):
    """vlm-first acceptance on pages 1–31. Gates: nothing wrong is ever confident. "Don't know" always passes."""
    from common import context
    b, _ = _batch(batch_id)
    if not b:
        return None
    with db.connect() as c:
        rows = {r["page_no"]: r for r in c.execute("""
            SELECT page_no, status::text AS status, doc_type::text AS doc_type, type_status, type_votes, fields,
                   extract_status, extract_error, qr_text, classical_text, zoom, second_look, outcome
              FROM staging.page WHERE batch_id=%s""", (batch_id,))}
        for n, r in rows.items():
            r["checks"] = verify.load(c, batch_id, n)
        ctxs = c.execute("SELECT version, status, content FROM staging.context_version ORDER BY version").fetchall()
        exam_lessons = [r["page_no"] for r in c.execute("""SELECT l.page_no FROM staging.lesson l
            JOIN staging.type_label t USING (batch_id, page_no) WHERE l.batch_id=%s AND t.pile='exam'""", (batch_id,))]
        calls = c.execute("""SELECT provider, purpose, count(*) AS n, count(*) FILTER (WHERE ok) AS ok,
                                    round(avg(ms)) AS avg_ms FROM staging.model_call WHERE batch_id=%s
                             GROUP BY 1, 2 ORDER BY 1, 2""", (batch_id,)).fetchall()
    scope = [n for n in SCOPE if n in rows]
    done = [n for n in scope if rows[n]["status"] == "read"]
    failed = [n for n in done if rows[n]["extract_status"] == "failed"]
    machine = {n: ((rows[n]["type_votes"] or {}).get("machine") or {}) for n in done}
    checks = [("Every page 1–31 processed (or failed with a reason)", len(done) == len(scope),
               f"{len(done)} of {len(scope)} processed" +
               (f" · AI OCR failed on {failed}: {(rows[failed[0]]['extract_error'] or '')[:90]}" if failed else ""))]
    qr_not_fp = [n for n in done if rows[n]["qr_text"] and SOR_RE.fullmatch(rows[n]["qr_text"])
                 and machine[n].get("status") == "decided" and machine[n].get("doc_type") != "FP"]
    checks.append(("Every page with an SOR QR code that the machine decided is FP", not qr_not_fp,
                   f"{sum(1 for n in done if rows[n]['qr_text'])} QR pages" + (f" · NOT FP: {qr_not_fp}" if qr_not_fp else "")))
    tally = {"decided": 0, "unsure": 0, "labelled": sum(1 for n in done if rows[n]["type_status"] == "labelled")}
    for n in done:
        tally["decided" if machine[n].get("status") == "decided" else "unsure"] += 1
    table = []
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    if golden:
        alts = golden.get("type_alternatives", {})
        wrong_types = []
        for n in done:
            key = golden["page_types"].get(str(n))
            ok_types = set(alts.get(str(n), [key]))
            m = machine[n]
            if m.get("status") == "decided" and m.get("doc_type") not in ok_types:
                wrong_types.append(f"p{n} {m.get('doc_type')} (key {key})")
            table.append({"page": n, "key": "/".join(sorted(ok_types)), "machine": m.get("doc_type") if
                          m.get("status") == "decided" else None, "guess": m.get("guess"), "reason": m.get("reason"),
                          "labelled": rows[n]["doc_type"] if rows[n]["type_status"] == "labelled" else None,
                          "outcome": rows[n]["outcome"]})
        checks.append(("No page given a wrong type by the machine (unsure is allowed)", not wrong_types,
                       f"{tally['decided']} decided · {tally['unsure']} unsure · {tally['labelled']} labelled by a person"
                       + (f" · WRONG: {wrong_types}" if wrong_types else "")))
        wrong_ok, right_ok, right_check = [], 0, 0
        for pg, t, fname, expected in _answer_key_values(golden):
            r = rows.get(pg)
            if not r or r["doc_type"] != t or not r["checks"]:
                continue
            got = ((r["fields"] or {}).get(fname) or {}).get("value")
            v = r["checks"]["header"].get(fname, {})
            if not got:
                continue
            if _same(expected, got):
                right_ok += v.get("verdict") == "ok"
                right_check += v.get("verdict") == "check"
            elif v.get("verdict") == "ok":
                wrong_ok.append(f"p{pg} {fname}={got} (key {expected}, by {v.get('by')})")
        checks.append(("No wrong value gets ✅ (answer key), whatever confirmed it", not wrong_ok,
                       f"right values: {right_ok} ✅ · {right_check} to a person" +
                       (f" · WRONG ✅: {wrong_ok}" if wrong_ok else "")))
    changed, missed = 0, []
    for n in done:
        r = rows[n]
        if not r["checks"] or r["doc_type"] not in DOCS:
            continue
        kinds = {f["name"]: f["kind"] for f in DOCS[r["doc_type"]]["header"]}
        for name, v in r["checks"]["header"].items():
            f = (r["fields"] or {}).get(name)
            if v["verdict"] == "ok" and f and f.get("value") is not None:
                changed += 1
                if _vf_bent_passes(r, name, kinds[name], f):
                    missed.append(f"p{n} {name}")
    checks.append(("A ✅ value changed by one digit never passes (whole page → zoomed spot → second look)", not missed,
                   f"{changed - len(missed)} of {changed} caught" + (f" · passed anyway: {missed}" if missed else "")))
    bad = [c_["version"] for c_ in ctxs if context.validate(c_["content"])]
    active = [c_["version"] for c_ in ctxs if c_["status"] == "active"]
    checks.append(("Every Jev context is valid (combined list = union of the types' fields), exactly one active",
                   not bad and len(active) == 1,
                   f"{len(ctxs)} versions · active v{active[0] if active else '-'}" + (f" · INVALID: {bad}" if bad else "")))
    checks.append(("No lesson from an exam-pile page (the teacher never sees exam labels)", not exam_lessons,
                   "none" if not exam_lessons else f"exam pages with lessons: {exam_lessons}"))
    vals = {}
    for n in done:
        for v in ((rows[n]["checks"] or {}).get("header") or {}).values():
            k = v["verdict"] + (f" · {v['by']}" if v.get("by") else "")
            vals[k] = vals.get(k, 0) + 1
    outcomes = {}
    for n in done:
        outcomes[rows[n]["outcome"] or "not read"] = outcomes.get(rows[n]["outcome"] or "not read", 0) + 1
    return {"checks": checks, "all_ok": all(ok for _, ok, _ in checks), "table": table, "values": vals,
            "outcomes": outcomes, "calls": calls, "pages": len(done), "golden": bool(golden)}


@app.get("/api/batches/{batch_id}/vf")
def api_vf(batch_id: str):
    r = vf_checks(batch_id) if VF else None
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/context", response_class=HTMLResponse)
def page_context(request: Request):
    from common import context
    with db.connect() as c:
        versions = c.execute("SELECT * FROM staging.context_version ORDER BY version DESC").fetchall()
        lessons = c.execute("SELECT * FROM staging.lesson ORDER BY created_at DESC").fetchall()
    active = next((v for v in versions if v["status"] == "active"), None)
    by_v = {v["version"]: v for v in versions}
    for v in versions:
        v["problems"] = context.validate(v["content"])
        v["union_ok"] = context.union_equals_list(v["content"])
        parent = by_v.get(v["parent"]) if v["parent"] else None
        v["diff"] = _context_diff(parent["content"], v["content"]) if parent else None
        v["lesson"] = next((l for l in lessons if l["proposal"] == v["version"]), None)
    return templates.TemplateResponse("context.html", ctx(request, versions=versions, active=active, lessons=lessons))


def _context_diff(a, b):
    out = []
    for name in sorted(set(b["fields"]) - set(a["fields"])):
        out.append(f"new field on the combined list: {name} ({b['fields'][name]['meaning']})")
    for code, t in b["types"].items():
        ta = a["types"][code]
        old = {f["name"]: f for f in ta["fields"]}
        for f in t["fields"]:
            if f["name"] not in old:
                out.append(f"{code} now has {f['name']} ({f['how_often']}){': ' + f['note'] if f['note'] else ''}")
            elif (old[f["name"]]["how_often"], old[f["name"]]["note"]) != (f["how_often"], f["note"]):
                out.append(f"{code}.{f['name']}: {old[f['name']]['how_often']} → {f['how_often']}"
                           f"{': ' + f['note'] if f['note'] else ''}")
        for k in ("what", "not_for"):
            if ta.get(k) != t.get(k):
                out.append(f"{code} {k}: {t.get(k)!r}")
        added = [x for x in t["titles"] if x not in ta["titles"]]
        if added:
            out.append(f"{code} titles + {added}")
    return out


@app.post("/context/{version}/approve")
def approve_context(version: int, by: str = Form(...)):
    from common import context
    if not by.strip():
        return JSONResponse({"error": "say who approves it"}, status_code=400)
    with db.connect() as c:
        context.activate(c, version, by.strip())
    try:                                   # the exam pile, which the teacher never saw: for the report only
        from worker import lesson
        lesson.score_exam(version)
    except Exception as e:
        print("exam scoring failed:", e)
    return RedirectResponse("/context", status_code=303)


@app.post("/context/{version}/reject")
def reject_context(version: int, by: str = Form("")):
    from common import context
    with db.connect() as c:
        context.reject(c, version, by.strip() or None)
    return RedirectResponse("/context", status_code=303)


@app.get("/compare", response_class=HTMLResponse)
def page_compare(request: Request, batch: str | None = None):
    batch = batch or _latest_batch()
    data = vf_compare(batch) if batch else None
    return templates.TemplateResponse("compare.html", ctx(request, batch=batch, d=data))


def vf_compare(batch):
    """v1 (its own database, read-only) vs vlm-first, on pages 1–31 that both read. Graded by the answer key."""
    q_ = """SELECT page_no, status::text AS status, doc_type::text AS doc_type, type_status, type_votes, extract_status,
                   fields, vlm_meta FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)"""
    with db.connect() as c:
        vf = {r["page_no"]: r for r in c.execute(q_, (batch, list(SCOPE)))}
        for n in vf:
            vf[n]["checks"] = verify.load(c, batch, n)
        vf_calls = c.execute("""SELECT provider, count(*) AS n FROM staging.model_call WHERE batch_id=%s
                                GROUP BY 1""", (batch,)).fetchall()
    with _main_db() as m:
        v1 = {r["page_no"]: r for r in m.execute(q_, (batch, list(SCOPE)))}
        for n in v1:
            v1[n]["checks"] = verify.load(m, batch, n)
    b, _ = _batch(batch)
    golden = _golden() if b and b["file_name"] == GOLDEN_FILE else None
    alts = (golden or {}).get("type_alternatives", {})

    def machine(r, vf_side):
        if vf_side:
            m = (r["type_votes"] or {}).get("machine") or {}
            return m.get("doc_type") if m.get("status") == "decided" else None
        return r["doc_type"] if r["type_status"] == "decided" else None

    def values(r):
        out = {"ok": 0, "check": 0, "empty": 0}
        for v in ((r.get("checks") or {}).get("header") or {}).values():
            out[v["verdict"]] += 1
        return out

    rows, sums = [], {"v1": {"right": 0, "unsure": 0, "wrong": 0, "ok": 0, "check": 0},
                      "vf": {"right": 0, "unsure": 0, "wrong": 0, "ok": 0, "check": 0}}
    for n in sorted(set(vf) & set(v1)):
        if vf[n]["status"] != "read":        # not processed by vlm-first yet: nothing to compare
            continue
        key = (golden or {}).get("page_types", {}).get(str(n))
        ok_types = set(alts.get(str(n), [key]))
        row = {"page": n, "key": "/".join(sorted(ok_types)) if key else "?"}
        for side, r in (("v1", v1[n]), ("vf", vf[n])):
            t = machine(r, side == "vf")
            verdict = "unsure" if t is None else ("right" if t in ok_types else "wrong") if key else "?"
            vals = values(r)
            row[side] = {"type": t, "verdict": verdict, "read": r["extract_status"] == "done", **vals}
            if verdict in ("right", "unsure", "wrong"):
                sums[side][verdict] += 1
            sums[side]["ok"] += vals["ok"]
            sums[side]["check"] += vals["check"]
        rows.append(row)
    v1_calls = {"gemini (stored readings)": sum(len(r["vlm_meta"] or {}) for r in v1.values())}
    read_by = {}
    for r in vf.values():
        if r["status"] == "read":
            model = ((r["vlm_meta"] or {}).get("read") or {}).get("model") or "not read by the AI OCR"
            read_by[model] = read_by.get(model, 0) + 1
    return {"rows": rows, "sums": sums, "vf_calls": vf_calls, "v1_calls": v1_calls, "pages": len(rows),
            "read_by": read_by}


@app.get("/{rest:path}", response_class=HTMLResponse)
def not_built(request: Request, rest: str):
    tab = next((t for t in TABS if t[1] == "/" + rest), None)
    return templates.TemplateResponse("not_built.html", ctx(request, tab=tab), status_code=404 if not tab else 200)
