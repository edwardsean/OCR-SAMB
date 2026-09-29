"""Inspection UI. One tab per pipeline phase; a tab lights up when its phase is built."""
import difflib
import hashlib
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from common import db, health, intake, keys as keymod, queue as q, storage, verify
from common.fields import DOCS, decides

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

# vlm-first experiment (branch vlm-first): same UI code, its own database / vhost / MinIO prefix, pages cloned from v1
VF = os.environ.get("PIPELINE") == "vlm-first"
PHASE_BUILT = 7 if VF else 5                 # grouping (6) and cross-checks + Review (7) are built on vlm-first
N8N_WEBHOOK = "http://n8n:5678/webhook/intake"
N8N_VF_WEBHOOK = "http://n8n:5678/webhook/vf-intake"   # vlm-first's intake workflow (n8n/vf-intake.workflow.json)
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
    EXPECTED_TABLES += 8  # + context_version, lesson, model_call (010), field_confirmation (011), satellite.sor_item
                          # (012), line_match (013), bundle_decision (014), notice (018)

# (name, role, how to probe, console link on the host)
SERVICES = [
    ("postgres",  "Shared with v1 · database ocr_vf", "probe", None),
    ("rabbitmq",  "Shared with v1 · vhost vf",        "probe", "http://localhost:15672"),
    ("minio",     "Shared with v1 · reads v1's page renders, writes vf/", "probe", "http://localhost:9001"),
    ("n8n",       "Shared with v1 · vf's intake, sweep (3 h) and needs-you (5 min) workflows",
                  "http://n8n:5678/healthz", "http://localhost:5678"),
    ("vf-worker", "vlm-first page workers ×3: q.pages (q.pages.wait while the AI refuses)", "http://vf-worker:8080/health", None),
    ("vf-grouper", "q.group: group, check bundles, send pages back to look again", "http://vf-grouper:8080/health", None),
    ("vf-teacher", "Teacher: q.lessons, one lesson at a time", "http://vf-teacher:8080/health", None),
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
    if VF:                                  # presence only, never the values
        keys["GROQ_API_KEY (vlm-first AI OCR)"] = bool(os.environ.get("GROQ_API_KEY"))
        keys["ZAI_API_KEY (vlm-first teacher)"] = bool(os.environ.get("ZAI_API_KEY"))
    checks = [
        (f"All {len(SERVICES)} services healthy", all(r["ok"] for r in rows), f"{sum(r['ok'] for r in rows)} / {len(rows)}"),
        (f"{EXPECTED_TABLES} tables in satellite + staging", len(tables) == EXPECTED_TABLES, f"{len(tables)} found"),
    ]
    return {"services": rows, "tables": tables, "model_keys": keys, "checks": checks,
            "all_ok": all(c[1] for c in checks)}


def ctx(request, **kw):
    return {"request": request, "tabs": TABS, "built": PHASE_BUILT, "path": request.url.path,
            "needs_you": _needs_you(), **kw}


def _needs_you():
    """vlm-first: how many bundles in unseen notices still need a person (the Review tab's count)."""
    if not VF:
        return 0
    try:
        from common import notice
        with db.connect() as c:
            return len(notice.unseen(c))
    except Exception:                    # before migration 018, or the database is down: no count, never an error
        return 0


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


PREFIX = os.environ.get("STORAGE_PREFIX", "")


@app.get("/upload", response_class=HTMLResponse)
def page_upload(request: Request):
    return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(), error=None, dup=None,
                                                         vf=VF))


@app.post("/upload", response_class=HTMLResponse)
async def do_upload(request: Request, file: UploadFile = File(...)):
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
    key = f"{PREFIX}scans/{day}/{batch_id}/{file.filename}"
    storage.ensure_bucket().put_object(storage.bucket(), key, io.BytesIO(data), len(data), content_type="application/pdf")

    payload = {"batch_id": batch_id, "object_key": key, "file_name": file.filename, "sha256": sha, "scanned_day": day}
    try:                                         # n8n: split into pages, then one ticket per page (vf: its own workflow)
        r = httpx.post(N8N_VF_WEBHOOK if VF else N8N_WEBHOOK, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return templates.TemplateResponse("upload.html", ctx(request, batches=_recent_batches(), dup=None,
            error=f"Stored in MinIO, but n8n did not accept it ({type(e).__name__}: {e}). "
                  f"Is the intake workflow published? Run {'scripts/n8n-setup-vf.sh' if VF else 'scripts/n8n-setup.sh'}."),
            status_code=502)
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.post("/internal/intake/split")
def internal_split(body: dict):          # n8n's intake workflow (vf: n8n/vf-intake.workflow.json → http://vf-ui:8000)
    return intake.split(body["batch_id"], body["object_key"], body["file_name"], body["sha256"], body["scanned_day"])


@app.post("/internal/intake/enqueue")
def internal_enqueue(body: dict):
    return intake.enqueue(body["batch_id"])


@app.post("/internal/vf/sweep")
def internal_sweep():
    """n8n's "vf — sweep" schedule: pages still waiting for the AI go back on the queue (worker/vf.py sweep)."""
    if not VF:
        return JSONResponse({"error": "vlm-first only"}, status_code=404)
    from worker import vf
    return vf.sweep()


@app.post("/notices/seen")
def notices_seen():
    """Sent by /review's page after it loads in a browser: a script or a test fetching the page never marks a notice
    seen (the screen tests once did, before anyone had looked)."""
    from common import notice
    with db.connect() as c:
        notice.mark_seen(c)
    return Response(status_code=204)


@app.post("/internal/vf/notify")
def internal_notify():
    """n8n's "vf — needs you" schedule: one notice for the bundles that newly need a person (common/notice.py)."""
    if not VF:
        return JSONResponse({"error": "vlm-first only"}, status_code=404)
    from common import notice
    with db.connect() as c:
        n = notice.record(c)
    return {"new": bool(n), **({"id": n["id"], "text": n["text"]} if n else {})}


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
                                          depths=_depths(), vf=vf_checks(batch_id), p6=phase6_checks(batch_id),
                                          p7=phase7_cached(batch_id),
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
                                          depth=None, depths=_depths(), vf=vf_checks(batch_id),
                                          p6=phase6_checks(batch_id), p7=phase7_cached(batch_id)))
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
    ("TTG", "posting_date"): "posting_date", ("TTG", "vendor_number"): "vendor_number",
    ("PO", "purchase_order_no"): "purchase_order_no", ("PO", "total"): "total", ("PO", "ppn"): "ppn",
    ("PO", "vendor_code"): "vendor_code",
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
    """(page, type, field, expected) for every answer-key value the AI OCR has a field for, pages in SCOPE.
    expected None = checked by eye and not printed on the page (a Hero PO page ending before TOTAL NETTO): any
    value stored for it is wrong."""
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


def _bends(kind, value, source):
    """Every consistent one-digit misread of a value: each digit of its printed text changed (+1), and the value
    changed to match (an amount is re-read from the bent text). Cents and last digits are where tolerances and
    cut-offs fail, so every position is tried, not only the first."""
    if kind == "date":
        return [_one_digit_off(kind, value, source)]
    src, val, out = str(source or ""), str(value or ""), []
    for i, ch in enumerate(src):
        if not ch.isdigit():
            continue
        s2 = src[:i] + str((int(ch) + 1) % 10) + src[i + 1:]
        if kind == "amount":
            a = verify.amount(s2)
            if a is not None:
                out.append((f"{a:.2f}", s2))
        elif verify.flat(val) == verify.flat(src):     # an identifier stored as printed: bend the same digit
            k = sum(c.isdigit() for c in src[:i])
            j = [p for p, c in enumerate(val) if c.isdigit()]
            if k < len(j):
                out.append((val[:j[k]] + s2[i] + val[j[k] + 1:], s2))
    return out or [_one_digit_off(kind, value, source)]


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
    roles = {}
    if VF and p and p["doc_type"]:                 # what each unsettled value does (verification redesign): the ⚠
        from common.fields import DECIDES          # column says so, instead of "a person checks it" for every value
        for level, names in (DECIDES.get(p["doc_type"]) or {}).items():
            roles.update({n: level for n in names})
    return templates.TemplateResponse("page.html", ctx(request, batch_id=batch_id, b=b, p=p, page_no=page_no,
                                      ticket=ticket, words=words, size=size, pc=pc, vfp=vfp, roles=roles))


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


def resumable(key):
    """A page with a real image: v1's render (pages/…) or one uploaded here ({PREFIX}pages/…). A clone's stand-in
    image isn't: its page is never sent to a worker."""
    return bool(key) and (key.startswith("pages/") or key.startswith(f"{PREFIX}pages/"))


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
        lesson = p["pile"] == "practice" and (m.get("status") != "decided" or m.get("doc_type") != label) and p["read"]
        if lesson:
            c.execute("""INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, %s, %s)
                         ON CONFLICT (batch_id, page_no) DO UPDATE SET label=EXCLUDED.label, status='waiting',
                           answer=NULL, proposal=NULL, error=NULL""", (batch, page, label))
        resume = p["read"] and resumable(p["original_path"] or p["image_path"])
        if resume:                          # only pages with a real image (v1's render, or one uploaded here) are resumed
            c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND page_no=%s", (batch, page))
    if lesson:                              # after the commit above, so the teacher finds the lesson
        try:
            q.wake_teacher(f"page {page} labelled {label}")
        except Exception as e:              # the lesson is saved; the teacher's half-hourly look finds it anyway
            print("could not wake the teacher:", e)
    if not resume:
        return
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
    """Would the value, changed at any one digit, still come back ✅ the way the pipeline decides: print (the whole
    page, then the zoomed spot), then the 7a rules with the page's other verdicts as they are? Returns the first
    bend that passes (value, printed text), or None."""
    from common import gates
    from worker import zoom
    for val, src in _bends(kind, f["value"], f.get("source_text") or f["value"]):
        bent = {**(r["fields"] or {}), name: {"value": val, "source_text": src}}
        v = verify.header(r["doc_type"], bent, r["classical_text"], r["qr_text"], sums=False)[name]
        z = (r["zoom"] or {}).get(name) or {}
        if v["verdict"] != "ok" and z.get("texts") and zoom.confirm(src, z["texts"], z.get("band") or [])[0]:
            v = {"verdict": "ok", "by": "zoom"}
        res = {**r["checks"], "header": {**r["checks"]["header"], name: v}}
        if gates.apply(r["doc_type"], bent, res)["header"][name]["verdict"] == "ok":
            return val, src
    return None


def _vf_bend_header(rows, pages):
    """The bend test on header values: (values bent, [the ones still ✅ after a one-digit change])."""
    changed, missed = 0, []
    for n in pages:
        r = rows[n]
        if not r["checks"] or r["doc_type"] not in DOCS:
            continue
        kinds = {f["name"]: f["kind"] for f in DOCS[r["doc_type"]]["header"]}
        for name, v in r["checks"]["header"].items():
            f = (r["fields"] or {}).get(name)
            # print-backed ✅ only: a misread of a value Satellite or a person settled is replaced, not passed
            if name not in kinds:                   # a field no longer on the type's list (a receipt's total, 2026-09-28)
                continue
            if v["verdict"] == "ok" and v.get("by") not in ("satellite", "person") and f and f.get("value") is not None:
                changed += 1
                hit = _vf_bent_passes(r, name, kinds[name], f)
                if hit:
                    missed.append(f"p{n} {name} {f.get('source_text')!r}→{hit[1]!r}")
    return changed, missed


def _bent_cells(value):
    """A line value as printed, changed at each digit (+1) in turn; a value with no digit gets one letter changed."""
    s = str(value)
    out = [s[:i] + str((int(ch) + 1) % 10) + s[i + 1:] for i, ch in enumerate(s) if ch.isdigit()]
    return out or [_one_digit_off("text", s, s)[0]]


def _vf_bend_lines(rows, pages):
    """The bend test on line items: each print-backed ✅ cell changed at every digit, its row re-checked the same way
    (its own row in Tesseract's text, then the 7a column rule). Before 7a a short value like "2" passed whenever that
    word was anywhere on the row."""
    from common import gates
    changed, missed = 0, []
    for n in pages:
        r = rows[n]
        if not r["checks"] or r["doc_type"] not in DOCS or not DOCS[r["doc_type"]]["lines"]:
            continue
        stored = (r["fields"] or {}).get("lines") or []
        for i, verdicts in enumerate(r["checks"]["lines"]):
            for col, v in verdicts.items():
                if v["verdict"] != "ok" or v.get("by") != "text" or i >= len(stored):
                    continue
                changed += 1
                for bent in _bent_cells(stored[i].get(col)):
                    rows_ = [dict(x) for x in stored]
                    rows_[i][col] = bent
                    v = verify.lines(r["doc_type"], rows_, r["classical_text"])[i][col]
                    if gates.columns([{col: v}])[0][col]["verdict"] == "ok":
                        missed.append(f"p{n} row {i + 1} {col} {stored[i].get(col)!r}→{bent!r}")
                        break
    return changed, missed


def _beyond(u, value):
    """Is a bent value further from what its check compared it with than the check allows? An amount: more than
    `allow` rupiah from `ref`. A date: outside the order's window (the SO's date to the scan day)."""
    if u["kind"] == "date":
        return str(value) < u["lo"] or bool(u.get("hi")) and str(value) > u["hi"]
    try:
        return abs(float(value) - float(u["ref"])) > float(u["allow"]) + 0.005
    except (TypeError, ValueError):
        return True


def _date_bends(value):
    """An ISO date changed at each digit (+1), where the result is still a date: a receipt date misread by one digit."""
    s, out = str(value), []
    for i, ch in enumerate(s):
        if ch.isdigit():
            t = s[:i] + str((int(ch) + 1) % 10) + s[i + 1:]
            try:
                datetime.strptime(t, "%Y-%m-%d")
                out.append((t, t))
            except ValueError:
                pass
    return out


def _vf_bend_bundles(batch_id):
    """The bend test on the bundle checks (verification redesign, S0): every value a passing check relied on
    (its `used`) changed at each digit of its print, and the bundle checked again from the same inputs
    (crosscheck.evaluate, nothing written). A bend within what the check allows may pass (that is its rounding);
    one beyond must not. Values Satellite or a person settled are replaced, not passed, so they're left out, as in
    the page-level test. (values bent, [the bends that still passed])."""
    from grouper import crosscheck
    with db.connect() as c:
        xs = crosscheck.inputs(c, batch_id)
    changed, missed = 0, []

    def still_passes(x, name, n, page, first=None):
        pages = {**x["pages"], n: page}
        if first in pages:            # without what a look-again read: a misread alone must never pass
            pages[first] = {**pages[first], "second_look": None}
        return crosscheck.evaluate({**x, "pages": pages})["checks"][name]["status"] == "pass"
    for x in xs:
        for name, chk in crosscheck.evaluate(x)["checks"].items():
            if chk["status"] != "pass":
                continue
            for u in chk.get("used") or []:
                page = x["pages"].get(u["page"]) or {}
                store = u.get("src") or "fields"            # the page's own field, or its whole reading
                if u["kind"] == "qty":                      # a receipt row's quantity the delivery side relied on
                    lines = (page.get(store) or {}).get("lines") or []
                    row = lines[u["row"]]
                    changed += 1
                    for k, ch in enumerate(u["text"]):
                        if not ch.isdigit():
                            continue
                        t = u["text"][:k] + str((int(ch) + 1) % 10) + u["text"][k + 1:]
                        bent = [*lines[:u["row"]], {**row, "qty": t}, *lines[u["row"] + 1:]]
                        if still_passes(x, name, u["page"], {**page, store: {**page[store], "lines": bent}}):
                            missed.append(f"{x['sor']} {name}: p{u['page']} row {u['row'] + 1} qty {u['text']!r}→{t!r}")
                            break
                    continue
                if u["kind"] == "row":                      # a printed row amount the row fallback relied on
                    lines = (page.get(store) or {}).get("lines") or []
                    row = lines[u["row"]]
                    changed += 1
                    for k, ch in enumerate(u["text"]):
                        t = u["text"][:k] + str((int(ch) + 1) % 10) + u["text"][k + 1:] if ch.isdigit() else None
                        a = t and verify.amount(t)
                        if a is None or abs(a - u["ref"]) <= u["allow"] + 0.005:
                            continue
                        bent_lines = [*lines[:u["row"]], {**row, "row_text": row["row_text"].replace(u["text"], t, 1)},
                                      *lines[u["row"] + 1:]]
                        if still_passes(x, name, u["page"], {**page, store: {**page[store], "lines": bent_lines}}):
                            missed.append(f"{x['sor']} {name}: p{u['page']} row {u['row'] + 1} {u['text']!r}→{t!r}")
                            break
                    continue
                f = (page.get(store) or {}).get(u["field"]) or {}
                v = ((page.get("checks") or {}).get("header") or {}).get(u["field"]) or {} if store == "fields" else {}
                if f.get("value") in (None, "") or v.get("by") in ("satellite", "person"):
                    continue
                changed += 1
                bends = _date_bends(f["value"]) if u["kind"] == "date" else \
                    _bends(u["kind"], f["value"], f.get("source_text") or str(f["value"]))
                for val, src in bends:
                    if not _beyond(u, val):
                        continue
                    bent = {**page, store: {**page[store], u["field"]: {**f, "value": val, "source_text": src}}}
                    if still_passes(x, name, u["page"], bent, u.get("first")):
                        missed.append(f"{x['sor']} {name}: p{u['page']} {u['field']} {f['value']!r}→{val!r}")
                        break
    return changed, missed


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
    unread = [n for n in done if rows[n]["extract_status"] == "failed"]      # waiting for the AI OCR to read it
    waits = [n for n in done if (rows[n]["second_look"] or {}).get("waiting")]
    machine = {n: ((rows[n]["type_votes"] or {}).get("machine") or {}) for n in done}
    checks = [("Every page 1–31 read and checked: nothing waiting for the AI OCR (to read it or look again)",
               len(done) == len(scope) and not unread and not waits,
               f"{len(done) - len(unread)} of {len(scope)} read" +
               (f" · not read yet: {unread} ({(rows[unread[0]]['extract_error'] or '')[:90]})" if unread else "") +
               (f" · waiting for the look-again: {waits}" if waits else ""))]
    qr_not_fp = [n for n in done if rows[n]["qr_text"] and SOR_RE.fullmatch(rows[n]["qr_text"])
                 and machine[n].get("status") == "decided" and machine[n].get("doc_type") != "FP"]
    checks.append(("Every page with an SOR QR code that the machine decided is FP", not qr_not_fp,
                   f"{sum(1 for n in done if rows[n]['qr_text'])} QR pages" + (f" · NOT FP: {qr_not_fp}" if qr_not_fp else "")))
    tally = {"decided": 0, "unsure": 0, "labelled": sum(1 for n in done if rows[n]["type_status"] == "labelled")}
    for n in done:
        if n not in unread:                          # never read: nothing was classified, so not "unsure"
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
                          "outcome": rows[n]["outcome"], "unread": n in unread})
        checks.append(("No page given a wrong type by the machine (unsure is allowed)", not wrong_types,
                       f"{tally['decided']} decided · {tally['unsure']} unsure · {tally['labelled']} labelled by a person"
                       + (f" · {len(unread)} not read yet" if unread else "")
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
    changed, missed = _vf_bend_header(rows, done)
    checks.append(("A ✅ value misread at any one digit never passes (whole page → zoomed spot → second look)",
                   not missed, f"{changed - len(missed)} of {changed} values caught at every digit"
                   + (f" · passed anyway: {missed[:6]}{' …' if len(missed) > 6 else ''}" if missed else "")))
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
    try:
        with db.connect() as c:
            context.activate(c, version, by.strip())
    except ValueError as e:                # not a proposal, invalid, or built on an older context
        return JSONResponse({"error": str(e)}, status_code=409)
    try:                                   # the exam pile, which the teacher never saw: for the report only
        from worker import lesson
        lesson.score_exam(version)
    except Exception as e:
        print("exam scoring failed:", e)
    _wake_teacher(f"context #{version} approved")
    return RedirectResponse("/context", status_code=303)


@app.post("/context/{version}/reject")
def reject_context(version: int, by: str = Form("")):
    from common import context
    with db.connect() as c:
        context.reject(c, version, by.strip() or None)
    _wake_teacher(f"context #{version} rejected")
    return RedirectResponse("/context", status_code=303)


def _wake_teacher(reason):
    """The next lesson waits for a decision on the pending proposal; after one, wake the teacher."""
    try:
        q.wake_teacher(reason)
    except Exception as e:                 # nothing is lost: the teacher also looks every half hour
        print("could not wake the teacher:", e)


@app.get("/compare", response_class=HTMLResponse)
def page_compare(request: Request, batch: str | None = None):
    batch = batch or _latest_batch()
    data = vf_compare(batch) if batch else None
    return templates.TemplateResponse("compare.html", ctx(request, batch=batch, d=data))


def vf_compare(batch):
    """v1 (its own database, read-only) vs vlm-first, on pages 1–31 that both read. Graded by the answer key."""
    q_ = """SELECT page_no, status::text AS status, doc_type::text AS doc_type, type_status, type_votes, extract_status,
                   fields, vlm_meta{} FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)"""
    with db.connect() as c:
        vf = {r["page_no"]: r for r in c.execute(q_.format(", second_look"), (batch, list(SCOPE)))}
        for n in vf:
            vf[n]["checks"] = verify.load(c, batch, n)
        vf_calls = c.execute("""SELECT provider, count(*) AS n FROM staging.model_call WHERE batch_id=%s
                                GROUP BY 1""", (batch,)).fetchall()
    with _main_db() as m:                            # v1 has no look-again, so no second_look column
        v1 = {r["page_no"]: r for r in m.execute(q_.format(""), (batch, list(SCOPE)))}
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

    def values(r):                               # an unbacked value waiting for the look-again isn't a person's yet
        out = {"ok": 0, "check": 0, "empty": 0, "waiting": 0}
        waits = bool((r.get("second_look") or {}).get("waiting"))
        for v in ((r.get("checks") or {}).get("header") or {}).values():
            out["waiting" if waits and v["verdict"] == "check" else v["verdict"]] += 1
        return out

    rows, sums = [], {s: {"right": 0, "unsure": 0, "wrong": 0, "not read": 0, "ok": 0, "check": 0, "waiting": 0}
                      for s in ("v1", "vf")}
    for n in sorted(set(vf) & set(v1)):
        if vf[n]["status"] != "read":        # not processed by vlm-first yet: nothing to compare
            continue
        key = (golden or {}).get("page_types", {}).get(str(n))
        ok_types = set(alts.get(str(n), [key]))
        row = {"page": n, "key": "/".join(sorted(ok_types)) if key else "?"}
        for side, r in (("v1", v1[n]), ("vf", vf[n])):
            t = machine(r, side == "vf")
            verdict = "unsure" if t is None else ("right" if t in ok_types else "wrong") if key else "?"
            if side == "vf" and r["extract_status"] == "failed":
                verdict = "not read"                 # the AI OCR never read it (limit reached): nothing to classify
            vals = values(r)
            row[side] = {"type": t, "verdict": verdict, "read": r["extract_status"] == "done", **vals}
            if verdict in ("right", "unsure", "wrong", "not read"):
                sums[side][verdict] += 1
            sums[side]["ok"] += vals["ok"]
            sums[side]["check"] += vals["check"]
            sums[side]["waiting"] += vals["waiting"]
        rows.append(row)
    v1_calls = {"gemini (stored readings)": sum(len(r["vlm_meta"] or {}) for r in v1.values())}
    read_by = {}
    for r in vf.values():
        if r["status"] == "read":
            model = ((r["vlm_meta"] or {}).get("read") or {}).get("model") or "not read by the AI OCR"
            read_by[model] = read_by.get(model, 0) + 1
    return {"rows": rows, "sums": sums, "vf_calls": vf_calls, "v1_calls": v1_calls, "pages": len(rows),
            "read_by": read_by}


# ---------------------------------------------------------------------------------------------- phase 6: bundles

CONFIRM_FIELD = {"FP": "sor", "TTG": "purchase_order_no", "PO": "purchase_order_no"}   # the key a person can settle


def bundles_view(batch):
    """What grouping stored for one batch: bundles per SOR (across batches, as stored), what is held and why, and
    pages not grouped yet."""
    from grouper import group
    with db.connect() as c:
        docs = c.execute("""SELECT d.*, d.doc_type::text AS type, b.sor_no, b.hold_reason AS bundle_hold, b.folder,
                                   b.status::text AS bundle_status
                              FROM staging.document d
                              LEFT JOIN staging.bundle_document bd ON bd.document_id = d.id
                              LEFT JOIN staging.bundle b ON b.id = bd.bundle_id
                             WHERE d.batch_id=%s ORDER BY d.page_from""", (batch,)).fetchall()
        pages = {r["page_no"]: r for r in c.execute("""SELECT page_no, doc_type::text AS doc_type, type_status,
                                                              outcome, keys, fields FROM staging.page
                                                        WHERE batch_id=%s""", (batch,))}
        sos = {r["sor_no"]: r for r in c.execute("SELECT * FROM satellite.sor")}
        confirmed = {(r["page_no"], r["field"]): r for r in c.execute(
            "SELECT * FROM staging.field_confirmation WHERE batch_id=%s", (batch,))}
    bundles, held = {}, []
    for d in docs:
        d["pages"] = list(range(d["page_from"], d["page_to"] + 1))
        if d["sor_no"]:
            b = bundles.setdefault(d["sor_no"], {"sor": d["sor_no"], "hold": d["bundle_hold"], "folder": d["folder"],
                                                 "why": group.WHY.get(d["bundle_hold"]), "documents": [],
                                                 "customer": (sos.get(d["sor_no"]) or {}).get("customer_name")})
            b["documents"].append(d)
        else:
            d["why"] = group.WHY.get(d["hold_reason"], d["hold_reason"])
            field = CONFIRM_FIELD.get(d["type"])
            f = ((pages.get(d["page_from"]) or {}).get("fields") or {}).get(field) or {}
            d["confirm"] = {"field": field, "read": f.get("value"),
                            "value": d["suggested_sor"] if field == "sor" and d["suggested_sor"] else f.get("value"),
                            "done": confirmed.get((d["page_from"], field))} if field else None
            held.append(d)
    grouped = {n for d in docs for n in d["pages"]}
    unplaced = [{"page": n, "type": p["doc_type"], "why": group.WHY["not_read" if p["type_status"] is None else "type_unknown"]}
                for n, p in sorted(pages.items()) if n not in grouped]
    ordered = sorted(bundles.values(), key=lambda b: (bool(b["hold"]), min(n for d in b["documents"] for n in d["pages"])))
    return {"bundles": ordered, "held": held, "unplaced": unplaced,
            "complete": sum(1 for b in ordered if not b["hold"]), "prefix": os.environ.get("STORAGE_PREFIX", "")}


KIND = {"FP": "Faktur Penjualan", "TTG": "Tanda Terima", "PO": "Purchase Order", "CONTINUATION": "Continuation page",
        "FPJ": "Faktur Pajak", "PEL": "Pelunasan", "SJ": "Surat Jalan", "OTHER": "Other document"}
TAG_COLORS = ["#ff5f57", "#febc2e", "#28c840", "#0a84ff", "#bf5af2", "#ff9f0a", "#8e8e93"]   # Finder's tag colours
PAGE_FILE = re.compile(r"^(?P<batch>.+)-p(?P<n>\d{3})-(?P<type>[A-Z]+|unread)\.png$")


def finder_view(batch, v):
    """The complete bundles' storage folders as they really are (listed from storage), with what the Finder window
    needs to preview each file. Held bundles are not here: they keep the held section."""
    c, bucket = storage.client(), storage.bucket()
    root = f"{v['prefix']}bundles"
    with db.connect() as conn:
        img = {r["page_no"]: r for r in conn.execute("SELECT page_no, thumb_upright_path, upright_path "
                                                     "FROM staging.page WHERE batch_id=%s", (batch,))}
    objs = {}
    for o in c.list_objects(bucket, prefix=root + "/", recursive=True):
        if "/_held/" not in o.object_name:
            objs.setdefault(o.object_name.rsplit("/", 1)[0], []).append(o)
    customers = sorted({b["customer"] or "no customer" for b in v["bundles"] if not b["hold"]})
    tags = [{"name": name, "color": TAG_COLORS[i % len(TAG_COLORS)]} for i, name in enumerate(customers)]
    color = {t["name"]: t["color"] for t in tags}
    folders = []
    for b in sorted((b for b in v["bundles"] if not b["hold"]), key=lambda b: b["sor"]):
        path = f"{root}/{b['sor']}"
        joined = {n: d for d in b["documents"] for n in d["pages"]}
        files, latest = [], None
        for o in sorted(objs.get(path, []), key=lambda o: o.object_name):
            latest = max(latest, o.last_modified) if latest else o.last_modified
            name = o.object_name.rsplit("/", 1)[1]
            f = {"name": name, "size": o.size, "modified": o.last_modified.astimezone(WIB).strftime("%-d %b %Y %H:%M")}
            m = PAGE_FILE.match(name)
            if m:
                n, t = int(m["n"]), m["type"]
                d, p = joined.get(n) if m["batch"] == batch else None, img.get(n) if m["batch"] == batch else None
                f.update(kind="page", type=t, kind_name=KIND.get(t, t), page=n, batch=m["batch"],
                         evidence=(d or {}).get("evidence") or [], page_url=f"/batches/{m['batch']}/pages/{n}",
                         thumb=f"/img/{p['thumb_upright_path']}" if p and p["thumb_upright_path"] else None,
                         image=f"/img/{p['upright_path']}" if p and p["upright_path"] else None)
            elif name.endswith(".json"):
                try:
                    r = c.get_object(bucket, o.object_name)
                    f.update(kind="manifest", content=json.loads(r.read()))
                    r.close(); r.release_conn()
                except Exception as e:
                    f.update(kind="manifest", content={"error": str(e)})
            files.append(f)
        types = [d["type"] for d in sorted(b["documents"], key=lambda d: d["pages"][0])]
        folders.append({"name": b["sor"], "customer": b["customer"] or "no customer",
                        "tag": color[b["customer"] or "no customer"], "path": path, "files": files,
                        "summary": " + ".join(types),
                        "modified": latest.astimezone(WIB).strftime("%-d %b %Y %H:%M") if latest else ""})
    return {"root": root, "folders": folders, "tags": tags}


@app.get("/bundles", response_class=HTMLResponse)
def page_bundles(request: Request, batch: str | None = None):
    batch = batch or _latest_batch()
    d = bundles_view(batch) if batch else None
    try:
        finder = finder_view(batch, d) if d else None
    except Exception as e:                       # storage down: the held section still works
        finder = {"root": "bundles", "folders": [], "tags": [], "error": str(e)}
    return templates.TemplateResponse("bundles.html", ctx(request, batch=batch, d=d, finder=finder,
                                                          p6=phase6_checks(batch) if batch else None))


@app.post("/bundles/confirm")
def confirm_key(batch: str = Form(...), page: int = Form(...), field: str = Form(...), value: str = Form(...),
                by: str = Form(...)):
    """A person confirms (or corrects) the key that holds a page back. The page is re-checked from stored data (no
    model call), so Satellite's record settles the rest, then the batch is regrouped."""
    if not by.strip() or not value.strip():
        return JSONResponse({"error": "say who you are, and the value as printed"}, status_code=400)
    with db.connect() as c:
        c.execute("""INSERT INTO staging.field_confirmation (batch_id, page_no, field, value, confirmed_by)
                     VALUES (%s, %s, %s, %s, %s)
                     ON CONFLICT (batch_id, page_no, field) DO UPDATE SET value=EXCLUDED.value,
                       confirmed_by=EXCLUDED.confirmed_by, confirmed_at=now()""",
                  (batch, page, field, value.strip(), by.strip()))
    from grouper import group
    from worker import vf
    vf.recheck(batch, page)
    group.run(batch)
    return RedirectResponse(f"/bundles?batch={batch}", status_code=303)


# ---------------------------------------------------------------------------------------------- phase 7d: Review

REVIEW_STATUSES = ("needs_review", "grouping", "reviewed", "auto_ok")
ACCEPT_REASONS = ["rounding", "tolakan confirmed", "the customer's own price", "the document comes later",
                  "other (say in the note)"]
NONE_REASONS = ["not in SAMB's order", "a free (bonus) item", "another product (say in the note)"]


def _bundle_pages(c, batch, sor):
    """[(first page, type, pages)] of the bundle's documents in this batch."""
    return [(d["page_from"], d["t"], list(range(d["page_from"], d["page_to"] + 1))) for d in c.execute(
        """SELECT d.page_from, d.page_to, d.doc_type::text AS t FROM staging.document d
             JOIN staging.bundle_document bd ON bd.document_id = d.id JOIN staging.bundle b ON b.id = bd.bundle_id
            WHERE d.batch_id = %s AND b.sor_no = %s ORDER BY d.page_from""", (batch, sor))]


def review_list(batch):
    """The batch's bundles with their status and what is left, needs_review first."""
    with db.connect() as c:
        rows = c.execute("""SELECT DISTINCT b.sor_no, b.status::text AS status, b.checks, b.hold_reason, b.reviewed_by,
                                   b.reviewed_at, s.customer_name
                              FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                              JOIN staging.document d ON d.id = bd.document_id
                              LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no
                             WHERE d.batch_id = %s""", (batch,)).fetchall()
        docs = {}
        for d in c.execute("""SELECT b.sor_no, d.doc_type::text AS t, d.page_from, p.thumb_upright_path AS thumb
                                FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                                JOIN staging.document d ON d.id = bd.document_id
                                LEFT JOIN staging.page p ON p.batch_id = d.batch_id AND p.page_no = d.page_from
                               WHERE d.batch_id = %s ORDER BY d.page_from""", (batch,)):
            docs.setdefault(d["sor_no"], []).append(d)
    order = {"needs_review": 0, "grouping": 1, "reviewed": 2, "auto_ok": 3, "published": 4}
    out = []
    for r in rows:
        checks = (r["checks"] or {}).get("checks") or {}
        ds = docs.get(r["sor_no"]) or []
        kinds = []
        for d in ds:                                   # Invoice · PO ×4 · Receipt
            name = {"FP": "Invoice", "PO": "PO", "TTG": "Receipt"}.get(d["t"], KIND.get(d["t"], d["t"]))
            kinds.append(name)
        chips = [f"{k} ×{kinds.count(k)}" if kinds.count(k) > 1 else k for k in dict.fromkeys(kinds)]
        fp = next((d for d in ds if d["t"] == "FP"), ds[0] if ds else None)
        out.append({**r, "reasons": (r["checks"] or {}).get("reasons") or [],
                    "counts": {s: sum(1 for x in checks.values() if x["status"] == s)
                               for s in ("pass", "accepted", "fail", "unknown")},
                    "docs": chips, "thumb": f"/img/{fp['thumb']}" if fp and fp.get("thumb") else None,
                    "issues": _issues(checks, (r["checks"] or {}).get("reasons") or [], r.get("customer_name"))})
    return sorted(out, key=lambda r: (order.get(r["status"], 9), r["sor_no"]))


def _issues(checks, reasons, customer):
    """A bundle's open problems as short chips for the Review list: (label, 'need' | 'wait')."""
    out = []
    short = {"fp_po_total": "PO total ≠ SAMB's order", "dates": "Receipt date", "docs_complete": "A document is missing",
             "store_named": "Another store is named", "sor_in_satellite": "Not in Satellite"}
    for k, c in checks.items():
        st = c.get("status")
        if st not in ("fail", "unknown", "waiting"):
            continue
        if k == "received":
            out.append(("Waiting for the goods receipt", "wait") if st == "waiting" else
                       ("Receipt quantities" if st == "fail" else "Receipt rows to pair", "need"))
        elif k == "calibration":
            out.append((f"First look: {' '.join(str(customer or 'customer').split()[:2])}", "need"))
        elif k in short:
            out.append((short[k], "wait" if st == "waiting" or c.get("ask") else "need"))
    for x in reasons:
        if "wait for the AI OCR" in x:
            out.append(("Waiting for the AI", "wait"))
        elif "wait for a person" in x:
            out.append(("A page to confirm", "need"))
    return out


def review_view(batch, sor):
    """Everything one bundle's review needs: what is left, each check, each document's unsettled values (with the
    spot on the page and suggestions as buttons, never pre-filled), each row's product pairing, and whether it can be
    approved."""
    from common import satellite as sat
    from common.fields import TYPE_MAP
    from grouper import crosscheck, matching
    from worker import vf
    with db.connect() as c:
        b = c.execute("SELECT * FROM staging.bundle WHERE sor_no=%s ORDER BY id DESC LIMIT 1", (sor,)).fetchone()
        if not b:
            return None
        docs = _bundle_pages(c, batch, sor)
        numbers = [n for _, _, ps in docs for n in ps]
        pages = {r["page_no"]: dict(r) for r in c.execute(
            """SELECT page_no, doc_type::text AS doc_type, fields, outcome, second_look, thumb_upright_path
                 FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)""", (batch, numbers))}
        for n in pages:
            pages[n]["checks"] = verify.load(c, batch, n)
        so = sat.load(c, [sor]).get(verify.flat(sor))
        lines = sat.items(c, sor)
        decisions = {(r["page_no"], r["row_index"]): r for r in c.execute(
            "SELECT * FROM staging.line_match WHERE batch_id=%s", (batch,))}
        pmap = matching.load_map(c, so and so.get("customer_parent"))
    checks = (b["checks"] or {}).get("checks") or {}
    order = sat.paper(so, lines) if so and not sat.free_goods(so) else None
    got = sat.received(so, lines) if so else None
    rows_in = [(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t, _ in docs if t in ("PO", "TTG")]
    pairs = matching.match(rows_in, lines, pmap, decisions)

    def suggest(t, name):
        out = []
        if t == "TTG" and name == "posting_date" and so and so.get("cgr_date"):
            out.append((str(so["cgr_date"]), "Satellite's goods receipt date"))
        if name == "purchase_order_no" and so and so.get("cpo_no"):
            out.append((so["cpo_no"], "the SO's Nomor CPO"))
        if t == "TTG" and name == "no_ref":
            out.append((sor, "this bundle's SOR"))
        # the order side's reference is the SO as ordered (what the FP printed), never the FP page's reading; the
        # delivery side's is what Satellite received (verification redesign, S2)
        refs = []
        if t == "PO" and order:
            refs = [(order.get("total"), "the order's total, with tax (Satellite)"),
                    (order.get("dpp"), "the order's DPP: a total before tax (Satellite)")] if name == "total" else \
                [(order.get("ppn"), "the order's PPN (Satellite)")] if name == "ppn" else []

        return out + [(f"{v:.2f}", why) for v, why in refs if v is not None]

    def entry(n, t, name):
        """One header value as a confirm form needs it: its label, what was read, why it isn't settled."""
        p = pages.get(n) or {}
        f = next((x for x in DOCS.get(t, {}).get("header", []) if x["name"] == name), {"label": name})
        v = ((p.get("checks") or {}).get("header") or {}).get(name) or {}
        canon = {b: a for a, b in TYPE_MAP.get(t, {}).items()}
        return {"name": name, "label": f["label"], "value": ((p.get("fields") or {}).get(name) or {}).get("value"),
                "why": v.get("why") or ("backed" if v.get("verdict") == "ok" else "not read"), "ok": v.get("verdict") == "ok",
                "asked": canon.get(name) in vf.asked_of(p.get("second_look")), "suggest": suggest(t, name)}

    documents = []
    for n, t, ps in docs:
        p = pages.get(n) or {}
        head, kept = [], []
        for f in DOCS.get(t, {}).get("header", []):
            v = ((p.get("checks") or {}).get("header") or {}).get(f["name"]) or {}
            if v.get("verdict") == "ok" or (v.get("verdict") == "empty" and f["source"] != "6.1"):
                continue
            # a value no check or grouping uses is kept as read: shown folded, never blocking (verification redesign)
            (head if f["name"] in decides(t) else kept).append(entry(n, t, f["name"]))
        rows = []
        if t in ("PO", "TTG", "FP"):
            stored = (p.get("fields") or {}).get("lines") or []
            keys = sat.row_keys(t, stored)
            cells = (p.get("checks") or {}).get("lines") or []
            parsed = matching.rows_of(t, p.get("fields")) if t in ("PO", "TTG") else []
            code_line = {verify.flat(s["item_code"]): s for s in lines}
            for i, r in enumerate(stored):
                m = pairs.get((n, i)) or {}
                s = lines[m["line"]] if m.get("line") is not None else None
                if t == "FP":                            # SAMB's own invoice prints the SO's lines (7b checked them)
                    s = code_line.get(verify.flat(r.get("kode_material")))
                    m = {"status": "matched" if s else "none", "how": "satellite",
                         "why": "SAMB's invoice prints the SO's own lines; checked against the record"}
                def ok_cell(col):
                    return i < len(cells) and (cells[i].get(col) or {}).get("verdict") == "ok"
                hints = {"qty": [], "unit_price": [], "discount": []}   # a quantity carries its unit: a bare 48 on a
                if s is not None and t == "TTG" and s.get("cgr_qty") is not None:   # carton row was 48 cartons (S5)
                    hints["qty"].append((f"{float(s['cgr_qty']):g} PCS", "pieces received, Satellite's CGR"))
                if s is not None and t == "PO":
                    hints["qty"].append((f"{float(s['qty_pcs']):g} PCS", "pieces ordered, the SO"))
                    hints["unit_price"] += [(f"{float(s['price_uom'] or 0):,.2f}", "the SO's price a carton"),
                                            (f"{float(s['price_pcs'] or 0):,.2f}", "the SO's price a piece")]
                    pct = [f"{float(x['value']):.2f}%" for x in (s.get("discounts") or {}).values()
                           if x.get("type") == "percentage" and x.get("value")]
                    hints["discount"].append((" / ".join(sorted(pct)) or "0", "the SO's discounts"))
                label = {"qty": "received" if t == "TTG" else "ordered", "unit_price": "unit price",
                         "discount": "discounts"}
                bonus = parsed[i]["bonus"] if i < len(parsed) else False
                todo = [] if t == "FP" or bonus else [
                    {"col": col, "label": label[col], "read": r.get(col), "hints": hints[col]}
                    for col in (("qty",) if t == "TTG" else ("qty", "unit_price", "discount"))
                    if r.get(col) not in (None, "") and not ok_cell(col)]
                rows.append({"i": i, "key": keys[i], "row": r, "match": m, "line": s, "todo": todo,
                             "qty_ok": ok_cell("qty") or t == "FP", "bonus": bonus,
                             "open": t != "FP" and m.get("status") in ("none", "proposed"),
                             "ai": (decisions.get((n, i)) or {}).get("so_line_no")
                             if (decisions.get((n, i)) or {}).get("how") == "ai" else None})
        documents.append({"page": n, "type": t, "kind": KIND.get(t, t), "pages": ps, "outcome": p.get("outcome"),
                          "head": head, "kept": kept, "rows": rows})
    ok, left = crosscheck.can_approve(checks, {n: pages[n] for n in pages})
    items = _open_items(batch, sor, docs, pages, checks, lines, pairs, entry, {"received": got, "order": order})
    flagged = {f["page"] for i in items for f in i.get("fix") or []} | {i["page"] for i in items if i.get("page")}
    strip = [{"page": n, "type": t, "kind": KIND.get(t, t), "first": n == ps[0],
              "thumb": f"/img/{pages[n]['thumb_upright_path']}" if (pages.get(n) or {}).get("thumb_upright_path") else None,
              "flag": n in flagged} for n0, t, ps in docs for n in ps]
    passed = [crosscheck.LABEL.get(k, k) for k, c in checks.items() if c["status"] in ("pass", "accepted")]
    return {"bundle": b, "sor": sor, "so": so, "lines": lines, "checks": checks, "labels": crosscheck.LABEL,
            "reasons": (b["checks"] or {}).get("reasons") or [], "documents": documents, "can_approve": ok,
            "left": left, "accept_reasons": ACCEPT_REASONS, "none_reasons": NONE_REASONS, "open_items": items,
            "strip": strip, "passed": passed,
            "calibration": _calibration_view(so, checks)}


OPEN = ("fail", "unknown", "waiting")
FIX_FIELDS = {"fp_po_total": ("PO", ("total", "ppn")), "dates": ("TTG", ("posting_date",))}   # a receipt's
# quantities are row cells: its card lists them, each with its own fix


def _money(x):
    return f"Rp {x:,.2f}" if isinstance(x, (int, float)) else "—"


def _plain(k, c, refs=None):
    """A check that doesn't pass, said in one plain line (the Review card's title)."""
    gap = c.get("gap")
    if k == "fp_po_total":
        if gap is None:
            return "The PO's total can't be compared with SAMB's order yet"
        more = (c.get("po") or 0) > (c.get("fp") or 0)
        return f"The PO asks {_money(gap)} {'more' if more else 'less'} than SAMB's order"
    if k == "received":
        if c["status"] == "fail":
            return "The receipt's quantities don't match what Satellite recorded as received"
        return "The receipt's rows aren't all paired with SAMB's lines yet"
    if k == "dates":
        return "The receipt's date doesn't fit Satellite's goods-receipt date"
    if k == "docs_complete":
        return "A document is missing: " + (c.get("why") or "").replace("no ", "the ").replace(" in the bundle", "") \
            .replace("TTG", "receipt (Tanda Terima)")
    if k == "store_named":
        return "A page names a different store of this customer"
    if k == "sor_in_satellite":
        return "This order isn't in Satellite"
    return None


def _open_items(batch, sor, docs, pages, checks, lines, pairs, entry, refs=None):
    """Review for anomalies only (verification redesign S5): one card per thing that holds the bundle, each with the
    one action it needs. A failed check shows both amounts and the gap, the rows lined up against SAMB's order lines
    as the explanation, the values it used (to correct a misread), and a one-click accept. A page shows only the
    values that hold it (its key, an FP's own amounts, a conflict with Satellite). Everything else stays folded:
    nothing a bundle doesn't need ever asks a person for input."""
    from common.fields import DECIDES, decides as dec
    from grouper import crosscheck, matching
    items = []

    def ok(v):
        return (v or {}).get("verdict") == "ok"
    for n, t, ps in docs:                                             # the pages that hold the bundle
        p = pages.get(n) or {}
        if p.get("outcome") == "held_unsure":
            items.append({"kind": "label", "title": f"Page {n}: its type waits for a label", "page": n})
        elif p.get("outcome") == "waiting_ai":
            items.append({"kind": "wait", "title": f"Page {n} waits for the AI OCR (its look-again)", "page": n})
        elif p.get("outcome") == "needs_person":
            h = (p.get("checks") or {}).get("header") or {}
            keys = DECIDES.get(t, {}).get("keys", ())
            hold = ([k for k in keys if (p.get("fields") or {}).get(k)] or list(keys)[:1]) \
                if keys and not any(ok(h.get(k)) for k in keys) else []
            hold += [f for f in dec(t, "page") if not ok(h.get(f))]
            hold += [f for f in dec(t, "support") if (h.get(f) or {}).get("conflict")]
            items.append({"kind": "page", "title": f"Page {n} · {KIND.get(t, t)}: " + ", ".join(
                entry(n, t, f)["label"] for f in hold) + " not settled", "page": n, "fields": [entry(n, t, f) for f in hold]})
    for k, c in checks.items():                                       # the bundle's checks that don't pass
        if c["status"] not in OPEN or k == "calibration":
            continue
        item = {"kind": "check", "key": k, "title": crosscheck.LABEL.get(k, k), "plain": _plain(k, c, refs),
                "status": c["status"], "why": c["why"],
                "print": c.get("print"), "accept": c["status"] != "waiting" and not c.get("ask"),
                "gap": c.get("gap"), "allow": c.get("allow"), "tolakan": c.get("tolakan") or [], "notes": [], "fix": []}
        if k == "fp_po_total" and c.get("po") is not None:
            item["pair"] = [("the PO", c["po"]), ("SAMB's order (Satellite, as ordered)", c.get("fp"))]
        if k == "received" and c.get("lines"):          # quantities, line by line (the mentors, 2026-09-28)
            item["qty_lines"] = c["lines"]
            item["qty_bad"] = [x for x in c["lines"] if x["receipt"] is None or abs(x["receipt"] - x["satellite"]) >= 0.001]
            keys = {n: sat_row_keys(pages, n) for n, dt, _ in docs if dt == "TTG"}
            item["qty_fix"] = [{**x, "key": keys.get(x["page"], [])[x["i"]] if x["i"] < len(keys.get(x["page"], [])) else None}
                               for x in c.get("qty_rows") or []
                               if x["line"] in {y["line_no"] for y in item["qty_bad"]} or x["line"] is None or x["pieces"] is None]
            got_rows = {x["line"] for x in c.get("qty_rows") or [] if x["line"] is not None}
            item["qty_missing"] = [y for y in item["qty_bad"] if y["line_no"] not in got_rows]   # not on the receipt
            item["qty_pack"] = any(x.get("pack") for x in item["qty_fix"])
        ours, ref = ((item.get("pair") or [(None, None), (None, None)])[0][1], (item.get("pair") or [(None, None), (None, None)])[1][1])
        item["suspect"] = bool(ours and ref and (ours < 0.05 * ref or ours > 20 * ref))   # not a difference: a misread
        if k == "docs_complete":
            item["held_link"] = f"/bundles?batch={batch}"
        t, names = FIX_FIELDS.get(k, (None, ()))
        firsts = crosscheck.distinct(pages, [n for n, dt, _ in docs if dt == t],
                                     "purchase_order_no" if t == "PO" else "document_no") if t else []
        item["fix"] = [{"page": n, "type": t, **entry(n, t, f)} for n in firsts for f in names
                       if (pages[n].get("fields") or {}).get(f) or f == "total"]
        if k == "fp_po_total":                                        # the rows explain where a gap comes from
            item["rows"], item["unmatched"], item["missing_lines"] = _rows_against_order(pages, firsts, lines, pairs)
            item["odd_rows"] = [x for x in item["rows"] if x["line"] is None and not x["bonus"]]
            item["ok_rows"] = sum(1 for x in item["rows"] if x["line"] is not None)
        elif c.get("rows"):
            item["notes"] = c["rows"]
        items.append(item)
    order = {"check": 0, "page": 1, "label": 2, "wait": 3}
    return sorted(items, key=lambda i: order[i["kind"]])


def sat_row_keys(pages, n):
    """The keys a person's row confirmation uses on this page (satellite.row_keys)."""
    from common import satellite as sat
    return sat.row_keys("TTG", ((pages.get(n) or {}).get("fields") or {}).get("lines") or [])


def _rows_against_order(pages, firsts, lines, pairs):
    """Each PO row next to the SO line it is (or none), with its printed amount (the last amount in its row); the
    printed amounts of the rows SAMB's order doesn't have, summed; the SO lines no row is."""
    from grouper import matching
    rows, used = [], set()
    for n in firsts:
        for r in matching.rows_of("PO", pages[n].get("fields")):
            m = pairs.get((n, r["i"])) or {}
            s = lines[m["line"]] if m.get("line") is not None else None
            amounts = _amounts_in(((pages[n].get("fields") or {}).get("lines") or [{}])[r["i"]].get("row_text"))
            rows.append({"page": n, "i": r["i"], "desc": r["desc"], "qty": r["qty"], "uom": r["uom"],
                         "amount": amounts[-1] if amounts else None, "line": s, "status": m.get("status") or "none",
                         "bonus": r["bonus"]})
            if s is not None:
                used.add(s["line_no"])
    unmatched = sum(x["amount"] or 0 for x in rows if x["line"] is None and not x["bonus"])
    return rows, round(unmatched, 2), [s for s in lines if s["line_no"] not in used]


def _calibration_view(so, checks):
    """What the calibration box on a bundle's Review page needs (verification redesign S3): the customer, what is
    still to be confirmed for it, and how far each of its bundles' amounts were from Satellite's (every batch)."""
    from common import satellite as sat
    from grouper import crosscheck
    asks = (checks.get("calibration") or {}).get("calibrate") or []
    chain = sat.chain_of(so)
    if not asks or not chain:
        return None
    gaps = []
    with db.connect() as c:
        for r in c.execute("""SELECT b.sor_no, b.checks FROM staging.bundle b JOIN satellite.sor s ON s.sor_no = b.sor_no
                               WHERE coalesce(s.customer_parent, s.customer_code) = %s ORDER BY b.sor_no""", (chain,)):
            for k in ("fp_po_total", "received"):
                g = ((((r["checks"] or {}).get("checks") or {}).get(k)) or {}).get("gap")
                if g is not None:
                    gaps.append({"sor": r["sor_no"], "check": crosscheck.LABEL[k], "gap": g})
    worst = max((g["gap"] for g in gaps), default=None)
    return {"chain": chain, "name": sat.chain_name(so), "asks": asks, "gaps": sorted(gaps, key=lambda g: -g["gap"]),
            "suggest": crosscheck.allowance_for(worst), "worst": worst, "steps": crosscheck.STEPS}


@app.get("/review", response_class=HTMLResponse)
def page_review(request: Request, batch: str | None = None, published: int | None = None):
    batch = batch or _latest_batch()
    rows = review_list(batch) if batch else []
    with db.connect() as c:
        batches = c.execute("""SELECT DISTINCT s.id, s.file_name, s.received_at FROM staging.scan_batch s
                                 JOIN staging.document d ON d.batch_id = s.id ORDER BY s.received_at DESC""").fetchall()
        fresh = []
        if VF:                           # n8n's notices; the page marks them seen once a browser shows it (/notices/seen)
            from common import notice
            fresh = notice.unseen(c)
    return templates.TemplateResponse("review.html", ctx(request, batch=batch, rows=rows, just_published=published,
                                                         batches=batches, fresh=fresh,
                                                         ready=sum(1 for r in rows if r["status"] in ("auto_ok", "reviewed"))))


@app.get("/review/{sor}", response_class=HTMLResponse)
def page_review_sor(request: Request, sor: str, batch: str | None = None):
    batch = batch or _latest_batch()
    v = review_view(batch, sor) if batch else None
    if not v:
        return HTMLResponse("no such bundle", status_code=404)
    return templates.TemplateResponse("review_sor.html", ctx(request, batch=batch, v=v))


def _regroup(batch):
    from grouper import group
    group.run(batch)


@app.post("/review/confirm")
def review_confirm(batch: str = Form(...), sor: str = Form(...), page: int = Form(...), field: str = Form(...),
                   value: str = Form(...), by: str = Form(...), row_key: str = Form(""), shown: str = Form("")):
    """A person confirms or corrects a value as printed: a header field, or a line cell ('lines[<row key>].<column>').
    The page is re-checked from stored data (no model call) and the batch regrouped and cross-checked."""
    if not by.strip() or not value.strip():
        return JSONResponse({"error": "say who you are, and the value as printed"}, status_code=400)
    with db.connect() as c:
        c.execute("""INSERT INTO staging.field_confirmation (batch_id, page_no, field, value, confirmed_by, row_key, shown)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)
                     ON CONFLICT (batch_id, page_no, field) DO UPDATE SET value=EXCLUDED.value, row_key=EXCLUDED.row_key,
                       shown=EXCLUDED.shown, confirmed_by=EXCLUDED.confirmed_by, confirmed_at=now()""",
                  (batch, page, field, value.strip(), by.strip(), row_key or None, shown or None))
    from worker import vf
    vf.recheck(batch, page)
    _regroup(batch)
    return RedirectResponse(f"/review/{sor}?batch={batch}#p{page}", status_code=303)


@app.post("/review/pair")
def review_pair(batch: str = Form(...), sor: str = Form(...), page: int = Form(...), row: int = Form(...),
                line_no: str = Form(...), by: str = Form(...), note: str = Form("")):
    """A person says which SO line a customer row is (or that none is, and why: NONE_REASONS). A pair fills the
    product map for the chain, so the same product matches with no AI and no person next time."""
    from common import satellite as sat
    from grouper import matching
    if not by.strip():
        return JSONResponse({"error": "say who you are"}, status_code=400)
    with db.connect() as c:
        p = c.execute("SELECT doc_type::text AS t, fields FROM staging.page WHERE batch_id=%s AND page_no=%s",
                      (batch, page)).fetchone()
        so = sat.load(c, [sor]).get(verify.flat(sor))
        lines = {s["line_no"]: s for s in sat.items(c, sor)}
        r = matching.rows_of(p["t"], p["fields"])[row]
        s = lines.get(int(line_no)) if line_no.isdigit() else None
        barcode = min(r["barcodes"]) if r["barcodes"] else None
        c.execute("""INSERT INTO staging.line_match (batch_id, page_no, row_index, sor_no, so_line_no, how, status,
                       reason, customer_code, ean)
                     VALUES (%s, %s, %s, %s, %s, 'person', %s, %s, %s, %s)
                     ON CONFLICT (batch_id, page_no, row_index) DO UPDATE SET so_line_no=EXCLUDED.so_line_no,
                       how='person', status=EXCLUDED.status, reason=EXCLUDED.reason, proposed_at=now()""",
                  (batch, page, row, sor, s and s["line_no"], "matched" if s else "refused",
                   f"{'paired' if s else 'no SO line' + (': ' + note.strip() if note.strip() else '')}, by {by.strip()}",
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
    _regroup(batch)
    return RedirectResponse(f"/review/{sor}?batch={batch}#p{page}", status_code=303)


@app.post("/review/accept")
def review_accept(batch: str = Form(...), sor: str = Form(...), check: str = Form(...), input_print: str = Form(...),
                  reason: str = Form(...), note: str = Form(""), by: str = Form(...)):
    """A person accepts a difference with a reason. It holds while the check says exactly what it said."""
    if not by.strip() or not reason.strip():
        return JSONResponse({"error": "say who you are, and why"}, status_code=400)
    with db.connect() as c:
        c.execute("""INSERT INTO staging.bundle_decision (sor_no, check_name, input_print, reason, note, decided_by)
                     VALUES (%s, %s, %s, %s, %s, %s)
                     ON CONFLICT (sor_no, check_name) DO UPDATE SET input_print=EXCLUDED.input_print,
                       reason=EXCLUDED.reason, note=EXCLUDED.note, decided_by=EXCLUDED.decided_by, decided_at=now()""",
                  (sor, check, input_print, reason.strip(), note.strip() or None, by.strip()))
    _regroup(batch)
    return RedirectResponse(f"/review/{sor}?batch={batch}#checks", status_code=303)


@app.post("/review/calibrate")
def review_calibrate(batch: str = Form(...), sor: str = Form(...), chain: str = Form(...), name: str = Form(""),
                     allowance: str = Form(""), receipt_shows: str = Form(""), by: str = Form(...)):
    """A customer's once-only calibration (verification redesign S3): how far its amounts may be from Satellite's,
    or what its receipts print after a rejection. Every bundle of that customer, in every batch, is checked again."""
    from grouper import crosscheck
    if not by.strip():
        return JSONResponse({"error": "say who you are"}, status_code=400)
    try:
        value = float(allowance.strip().replace(",", ".")) if allowance.strip() else None
    except ValueError:
        return JSONResponse({"error": f"an allowance is a number of rupiah, not {allowance!r}"}, status_code=400)
    if value is not None and not 0 <= value <= crosscheck.STEPS[-1]:
        return JSONResponse({"error": f"an allowance is rounding: 0 to {crosscheck.STEPS[-1]} rupiah"}, status_code=400)
    if value is None and receipt_shows not in ("received", "ordered"):
        return JSONResponse({"error": "give an allowance, or say what the receipts print"}, status_code=400)
    for bid in crosscheck.calibrate(chain, name, by.strip(), value, receipt_shows or None):
        _regroup(bid)
    return RedirectResponse(f"/review/{sor}?batch={batch}#calibration", status_code=303)


@app.post("/review/publish")
def review_publish(batch: str = Form(...), by: str = Form(...)):
    """Phase 8: publish the batch's finished bundles (auto_ok or reviewed): Satellite's document rows and one PDF per
    SOR. Checked once more first; a bundle with anything left is never published."""
    if not by.strip():
        return JSONResponse({"error": "say who you are"}, status_code=400)
    from publisher import publish
    done = publish.publish(batch)
    return RedirectResponse(f"/review?batch={batch}&published={len(done)}", status_code=303)


@app.get("/documents/{sor}.pdf")
def sor_pdf(sor: str):
    """A published SOR's PDF (phase 8), from Satellite's document record."""
    with db.connect() as c:
        r = c.execute("SELECT pdf_path FROM satellite.sor_document WHERE sor_no=%s", (sor,)).fetchone()
    if not r:
        return Response(status_code=404)
    try:
        obj = storage.client().get_object(storage.bucket(), r["pdf_path"])
        data = obj.read(); obj.close(); obj.release_conn()
    except Exception:
        return Response(status_code=404)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{sor}.pdf"'})


@app.post("/review/approve")
def review_approve(batch: str = Form(...), sor: str = Form(...), by: str = Form(...)):
    """Approve a bundle: only when nothing is left (crosscheck.can_approve), checked again now."""
    if not by.strip():
        return JSONResponse({"error": "say who you are"}, status_code=400)
    _regroup(batch)
    v = review_view(batch, sor)
    if not v or not v["can_approve"]:
        return JSONResponse({"error": "not yet", "left": (v or {}).get("left")}, status_code=409)
    with db.connect() as c:
        c.execute("""UPDATE staging.bundle SET status='reviewed', reviewed_by=%s, reviewed_at=now()
                     WHERE sor_no=%s AND status <> 'published'""", (by.strip(), sor))
    return RedirectResponse(f"/review/{sor}?batch={batch}", status_code=303)


@app.get("/crop/{batch}/{page_no}/{field}")
def field_crop(batch: str, page_no: int, field: str):
    """The line of the page where a field is printed, zoomed: what a person looks at to confirm it. Found by the value
    in Tesseract's words, else by its printed label (the whole line), else by the AI OCR's box (often off)."""
    import cv2
    from common.fields import CANON, TYPE_MAP
    from worker import main as v1, zoom
    with db.connect() as c:
        p = c.execute("SELECT doc_type::text AS t, upright_path, fields_all, ocr_words FROM staging.page "
                      "WHERE batch_id=%s AND page_no=%s", (batch, page_no)).fetchone()
    canon = {v: k for k, v in TYPE_MAP.get((p or {}).get("t") or "", {}).items()}.get(field)
    if not p or not canon or not p["upright_path"]:
        return Response(status_code=404)
    f, words, up = (p["fields_all"] or {}).get(canon) or {}, p["ocr_words"] or [], v1.load(p["upright_path"])
    h, w = up.shape[:2]
    rect = zoom.by_words(words, f.get("source_text")) or zoom.by_label(words, CANON[canon]["printed_as"], w)
    if not rect:                     # the AI's box: on these forms it tends to sit left of the value, so show the line
        box = zoom.by_ai_box(f.get("box"), up.shape)
        rect = (box[0], box[1], w, box[3]) if box else None
    if not rect:
        return Response(status_code=404)
    x0, y0, x1, y1 = rect
    crop = up[max(0, y0 - 45):min(h, y1 + 45), max(0, x0 - 80):min(w, x1 + 80)]
    ok, png = cv2.imencode(".png", cv2.resize(crop, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC))
    return Response(png.tobytes(), media_type="image/png") if ok else Response(status_code=404)


def phase6_checks(batch_id):
    """Grouping acceptance. Gates: nothing in a wrong bundle; nothing linked by page order (a continuation belongs to
    the page before it); one FP per bundle; the folders hold what the bundles say. Held is always allowed."""
    if not VF:
        return None
    b, _ = _batch(batch_id)
    if not b:
        return None
    from grouper import group
    v = bundles_view(batch_id)
    placed = {n: bd["sor"] for bd in v["bundles"] for d in bd["documents"] for n in d["pages"]}
    checks = []
    golden = _golden() if b["file_name"] == GOLDEN_FILE else None
    if golden:
        truth = {n: g["sor"] for g in golden["bundles"] for n in g["pages"]}
        wrong = [f"p{n} in {s} (key {truth[n]})" for n, s in sorted(placed.items()) if n in truth and truth[n] != s]
        whole = [g["sor"] for g in golden["bundles"] if all(placed.get(n) == g["sor"] for n in g["pages"])
                 and any(bd["sor"] == g["sor"] and not bd["hold"] for bd in v["bundles"])]
        checks.append(("No page in a wrong bundle (answer key)", not wrong,
                       f"{len(placed)} pages placed · {len(whole)} of {len(golden['bundles'])} answer-key bundles "
                       "complete" + (f" · WRONG: {wrong}" if wrong else "")))
    by_order = [f"p{d['page_from']}" for bd in v["bundles"] for d in bd["documents"] if d["linked_by"] not in ("sor", "po_no")]
    checks.append(("Nothing linked by page order (a continuation belongs to the page before it)", not by_order,
                   "every document linked by its SOR or its PO number" if not by_order else f"by order: {by_order}"))
    many = [bd["sor"] for bd in v["bundles"] if sum(d["type"] == "FP" for d in bd["documents"]) > 1]
    checks.append(("At most one FP per bundle", not many, "ok" if not many else f"several FPs: {many}"))
    try:
        c = storage.client()
        have = {o.object_name for o in c.list_objects(storage.bucket(), prefix=f"{v['prefix']}bundles/", recursive=True)}
    except Exception as e:
        have = None
        detail = f"storage unreachable: {e}"
    missing = []
    if have is not None:
        for bd in v["bundles"]:
            for d in bd["documents"]:
                for n in d["pages"]:
                    name = group.folder(bd["sor"], bd["hold"]) + group.file_name(
                        batch_id, n, d["type"] if n == d["pages"][0] else "CONTINUATION")
                    if name not in have:
                        missing.append(name)
        detail = (f"{sum(len(d['pages']) for bd in v['bundles'] for d in bd['documents'])} page files in "
                  f"{len(v['bundles'])} SOR folders" + (f" · MISSING: {missing[:3]}" if missing else ""))
    checks.append(("Each bundle's pages are in its SOR's folder", have is not None and not missing, detail))
    return {"checks": checks, "all_ok": all(ok for _, ok, _ in checks), "complete": v["complete"],
            "held_bundles": sum(1 for bd in v["bundles"] if bd["hold"]), "held": len(v["held"]),
            "unplaced": len(v["unplaced"])}


@app.get("/api/batches/{batch_id}/phase6")
def api_phase6(batch_id: str):
    r = phase6_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


# ---- Phase 7 grading. Customer documents (PO, TTG) against the answer key read by eye; the FP against Satellite's SO
# record, because SAMB prints the FP from it. Grading only: the pipeline never sees either as an answer. ----
CARTON_UNITS = {"KTN", "CT", "CTN", "CAR", "CRT", "KRT", "KARTON", "CASE", "CS", "DUS"}
LINE_COLS = {"TTG": ("item_code", "material_description"), "PO": ("product_code", "product_description")}
# The FP is the order side: it prints the SO as ordered (order_*), never the invoice (dpp/ppn/total = what was received,
# which a tolakan lowers). Grading it against the invoice would call a right full-order FP wrong on a rejected SO.
FP_SATELLITE = {"nomor_cpo": "cpo_no", "customer_code": "customer_code", "dpp": "order_dpp", "ppn": "order_ppn",
                "total": "order_total"}


def _pieces(value, uom, pack):
    """A stored quantity in pieces, with the answer key's pieces per carton: '3 KTN' → 144 at 48 a carton, '40.00' PC
    → 40. None when it isn't a quantity at all: '1 x 48' is a pack size."""
    s = str(value or "").upper()
    nums = re.findall(r"\d[\d.,]*", s)
    if re.search(r"\d\s*[X×]\s*\d", s) or len(nums) != 1:
        return None
    n = verify.amount(nums[0])
    unit = re.sub(r"[^A-Z]", "", s) or verify.flat(uom)
    return (n * pack if pack else None) if unit in CARTON_UNITS else n


def _qty_parts(value, uom):
    """(number, 'carton' | 'piece' | None) of a quantity as printed: '3 KTN' → (3, carton), '1,248' with uom EA →
    (1248, piece). (None, None) when it isn't a quantity ('1 x 48' is a pack size)."""
    s = str(value or "").upper()
    nums = re.findall(r"\d[\d.,]*", s)
    if re.search(r"\d\s*[X×]\s*\d", s) or len(nums) != 1:
        return None, None
    unit = re.sub(r"[^A-Z]", "", s) or verify.flat(uom)
    return verify.amount(nums[0]), ("carton" if unit in CARTON_UNITS else "piece" if unit else None)


def _codes(k):
    """What a row's code may be stored as: the customer's code, its barcode, or both as printed ('3078035(899…)')."""
    c, e = verify.flat(k.get("code")), verify.flat(k.get("ean"))
    return {x for x in (c, e, c + e if c and e else "") if x}


def _pair(stored, keyrows, code_of, desc_of, qty_ok, code_weight=2):
    """Which answer-key (or SO) row each stored row reads, by its code and its description; among rows alike (Hari
    Hari prints a bonus row with the same code) the quantity decides. None = no row it could be. On the FP the
    description leads (code_weight below 1): a misread code there is often another line's code (p1 read 1000566
    twice)."""
    used, out = set(), []
    for row in stored:
        f, d = verify.flat(code_of(row)), verify.flat(desc_of(row))
        best, at = 0.0, None
        for i, k in enumerate(keyrows):
            if i in used:
                continue
            like = difflib.SequenceMatcher(None, d, verify.flat(k["description"])).ratio() if d else 0.0
            s = (code_weight if f and f in k["_codes"] else 0) + (like if like >= 0.6 else 0)
            if s and qty_ok(row, k):
                s += 0.5
            if s > best:
                best, at = s, i
        if at is not None:
            used.add(at)
        out.append(at)
    return out


def _grade_customer_cell(col, v, row, k):
    """(right / WRONG / None when the key can't say, what the key's row says) for one PO/TTG line value."""
    def verdict(ok):
        return "right" if ok else "WRONG"
    if col in ("item_code", "product_code"):
        return (verdict(verify.flat(v) in k["_codes"]) if k.get("code") else None), k.get("code")
    if col in ("material_description", "product_description"):
        return verdict(verify.flat(v) == verify.flat(k["description"])), k["description"]
    if col == "qty":     # as printed in a quantity column: Hari Hari's '40.00 PC' is the pack size, even when one
        n, unit = _qty_parts(v, row.get("uom"))              # carton happens to hold as many (decided 2026-09-25)
        forms = [k["qty"], *k.get("also", [])]
        return (verdict(n is not None and any(n == fn and None in (unit, fu) or (n, unit) == (fn, fu)
                                              for fn, fu in (_qty_parts(s, k["uom"] if s == k["qty"] else None)
                                                             for s in forms))),
                " or ".join(forms))
    if col == "uom":
        return verdict(verify.flat(v) in {verify.flat(k["uom"]), verify.flat(k.get("pack_unit"))}), k["uom"]
    if col == "unit_price":
        a = verify.amount(v)
        return (verdict(a is not None and any(abs(a - verify.amount(p)) < 0.005 for p in k["prices"])),
                " or ".join(k["prices"]))
    if col == "discount":
        got = [round(float(x), 2) for x in re.findall(r"\d+(?:\.\d+)?", str(v)) if float(x)]
        want = [round(float(x.rstrip("%")), 2) for x in k.get("discounts") or [] if float(x.rstrip("%"))]
        return verdict(got == want), " / ".join(k.get("discounts") or []) or "none"
    return None, None


def _num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _grade_fp_cell(col, v, s):
    """One FP line value against its SO line. The FP prints QTY as cartons / pieces left over, from the pieces
    ORDERED (it is printed once, with the goods; a tolakan changes only the invoice) and the pieces per carton:
    72 pieces at 24 a carton print '3 / 0'."""
    per = int(s["pcs_per_uom"] or 1)
    pcs = int(s["qty_pcs"] or 0)
    if col == "kode_material":
        return ("right" if verify.flat(v) == verify.flat(s["item_code"]) else "WRONG"), s["item_code"]
    if col == "nama_produk":
        return ("right" if verify.flat(v) == verify.flat(s["description"]) else "WRONG"), s["description"]
    if col == "kemasan":    # '48X85GR', '36 BOTOL X 30 ML', '100x36 BOTOL': right when it holds the pieces per carton;
        t = str(v).upper()  # '75GR' alone names no count, so it can't be graded
        if per in {int(x) for x in re.findall(r"\d+", t)}:
            return "right", f"{per} per carton"
        return ("WRONG" if re.search(r"\d+\s*(?:[X×]\s*\d|BOTOL|BTL|PCS?\b)", t) else None), f"{per} per carton"
    if col in ("qty_crt", "qty_pcs"):
        want = pcs // per if col == "qty_crt" else pcs % per
        return ("right" if _num_or_none(v) == want else "WRONG"), f"{want} ({pcs} pcs at {per} a carton)"
    return None, None


def phase7_grade(batch_id, rows, golden):
    """Every stored value the answer key or Satellite can grade: [{page, path, got, want, verdict, by, grade, key}].
    grade: right / WRONG. verdict: what the pipeline said (ok = ✅). key: 'answer key' or 'Satellite'."""
    out = []

    def add(n, path, got, want, v, grade, key):
        out.append({"page": n, "path": path, "got": got, "want": want, "verdict": (v or {}).get("verdict"),
                    "by": (v or {}).get("by"), "grade": grade, "key": key})
    for pg, t, fname, expected in _answer_key_values(golden):
        r = rows.get(pg)
        if not r or r["doc_type"] != t or not r["checks"]:
            continue
        got = ((r["fields"] or {}).get(fname) or {}).get("value")
        if got not in (None, ""):
            add(pg, f"header.{fname}", got, expected, r["checks"]["header"].get(fname),
                "right" if _same(expected, got) else "WRONG", "answer key")
    sor_of = {n: g["sor"] for g in golden["bundles"] for n in g["pages"]}
    fps = [n for n in SCOPE if n in rows and rows[n]["doc_type"] == "FP" and rows[n]["checks"]]
    with db.connect() as c:
        sos = {s["sor_no"]: s for s in c.execute("SELECT * FROM satellite.sor WHERE sor_no = ANY(%s)",
                                                 ([sor_of[n] for n in fps if n in sor_of],))}
        items = {}
        for s in c.execute("SELECT * FROM satellite.sor_item WHERE sor_no = ANY(%s) ORDER BY sor_no, line_no",
                           (list(sos),)):
            items.setdefault(s["sor_no"], []).append({**s, "_codes": {verify.flat(s["item_code"])}})
    for n in fps:
        r, so = rows[n], sos.get(sor_of.get(n))
        if not so:
            continue
        keyed = {g["path"] for g in out if g["page"] == n}      # the answer key's own FP values (SOR, CPO) first
        for fname, col in FP_SATELLITE.items():
            got = ((r["fields"] or {}).get(fname) or {}).get("value")
            if got not in (None, "") and so[col] is not None and f"header.{fname}" not in keyed:
                want = str(so[col])
                add(n, f"header.{fname}", got, want, r["checks"]["header"].get(fname),
                    "right" if _same(want, got) else "WRONG", "Satellite")
        stored = (r["fields"] or {}).get("lines") or []
        lines_ = items.get(so["sor_no"]) or []
        pairs = _pair(stored, lines_, lambda x: x.get("kode_material"), lambda x: x.get("nama_produk"),
                      lambda row, s: False, code_weight=0.5)
        for i, (row, at) in enumerate(zip(stored, pairs)):
            verdicts = r["checks"]["lines"][i] if i < len(r["checks"]["lines"]) else {}
            for col, v in verdicts.items():
                if at is None or row.get(col) in (None, ""):
                    continue
                g, want = _grade_fp_cell(col, row.get(col), lines_[at])
                if g:
                    add(n, f"lines[{i}].{col}", row.get(col), want, v, g, f"Satellite, SO line {lines_[at]['line_no']}")
    for pg, keyrows in (golden.get("lines") or {}).items():
        n, r = int(pg), rows.get(int(pg))
        t = golden["page_types"].get(pg)
        if n not in SCOPE or not r or r["doc_type"] != t or not r["checks"] or t not in LINE_COLS:
            continue
        code_c, desc_c = LINE_COLS[t]
        keyrows = [{**k, "_codes": _codes(k)} for k in keyrows]
        stored = (r["fields"] or {}).get("lines") or []
        pairs = _pair(stored, keyrows, lambda x: x.get(code_c), lambda x: x.get(desc_c),
                      lambda row, k: _pieces(row.get("qty"), row.get("uom"), k.get("pack")) == k["pcs"])
        for i, (row, at) in enumerate(zip(stored, pairs)):
            verdicts = r["checks"]["lines"][i] if i < len(r["checks"]["lines"]) else {}
            for col, v in verdicts.items():
                if at is None or row.get(col) in (None, ""):
                    continue
                g, want = _grade_customer_cell(col, row.get(col), row, keyrows[at])
                if g:
                    add(n, f"lines[{i}].{col}", row.get(col), want, v, g, f"answer key, row {at + 1}")
    return out


def _amounts_in(text):
    """Every amount written in a text (the AI's text of a table row): '770,270.18', '518,919.00', '1.014.420'."""
    return [a for a in (verify.amount(x) for x in re.findall(r"\d[\d.,]*\d", str(text or ""))) if a is not None]


def phase7_values(batch_id, golden):
    """The values the bundle checks decide on (verification redesign), graded by the answer key, whatever their
    verdict: each PO/TTG total the AI read (`total`, `dpp`, `ppn`, taken from the whole reading, since a receipt's
    projection drops them) against the amounts the key says are printed on that page; and each key row's printed
    amount (line_total) against the amounts in the AI's text of the row it pairs with.
    Returns ([{page, field, got, grade}], rows whose amount is in the AI's row text, key rows with an amount)."""
    with db.connect() as c:
        pages = {r["page_no"]: r for r in c.execute(
            """SELECT page_no, doc_type::text AS doc_type, fields, fields_all FROM staging.page
                WHERE batch_id=%s AND page_no = ANY(%s)""", (batch_id, list(SCOPE)))}
    totals = []
    for pg, printed in (golden.get("amounts") or {}).items():
        p = pages.get(int(pg))
        if not p or p["doc_type"] != "PO":          # a receipt's amounts aren't read any more (2026-09-28)
            continue
        nums = [x for x in (verify.amount(a) for _, a in printed) if x is not None]
        for k in ("total", "dpp", "ppn"):
            got = ((p["fields_all"] or {}).get(k) or {}).get("value")
            g = verify.amount(str(got)) if got not in (None, "") else None
            if g is not None:
                totals.append({"page": int(pg), "field": k, "got": got,
                               "grade": "right" if any(abs(x - g) < 0.01 for x in nums) else "WRONG"})
    found = counted = 0
    for pg, keyrows in (golden.get("lines") or {}).items():
        n, t = int(pg), golden["page_types"].get(pg)
        p = pages.get(n)
        if n not in SCOPE or not p or p["doc_type"] != t or t not in LINE_COLS:
            continue
        code_c, desc_c = LINE_COLS[t]
        stored = (p["fields"] or {}).get("lines") or []
        pairs = _pair(stored, [{**k, "_codes": _codes(k)} for k in keyrows], lambda x: x.get(code_c),
                      lambda x: x.get(desc_c),
                      lambda row, k: _pieces(row.get("qty"), row.get("uom"), k.get("pack")) == k["pcs"])
        row_of = {at: i for i, at in enumerate(pairs) if at is not None}
        for j, k in enumerate(keyrows):
            want = verify.amount(k.get("line_total") or "")
            if not want:                                   # none printed, or a bonus row's 0
                continue
            counted += 1
            i = row_of.get(j)
            found += i is not None and any(abs(a - want) < 0.01 for a in _amounts_in(stored[i].get("row_text")))
    return totals, found, counted


def phase7_checks(batch_id):
    """Phase 7 acceptance, stage by stage. Built: 7.0 (the grading) and 7a (the rules that take ✅ away,
    common/gates.py). The cross-check, bundle status and Review gates show as waiting until their stage is built."""
    if not VF:
        return None
    b, _ = _batch(batch_id)
    golden = _golden() if b and b["file_name"] == GOLDEN_FILE else None
    if not b or not golden:
        return None
    with db.connect() as c:
        rows = {r["page_no"]: r for r in c.execute("""
            SELECT page_no, status::text AS status, doc_type::text AS doc_type, fields, qr_text, classical_text, zoom
              FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)""", (batch_id, list(SCOPE)))}
        for n, r in rows.items():
            r["checks"] = verify.load(c, batch_id, n)
    graded = phase7_grade(batch_id, rows, golden)
    wrong = [g for g in graded if g["grade"] == "WRONG" and g["verdict"] == "ok"]
    caught = sum(1 for g in graded if g["grade"] == "WRONG" and g["verdict"] != "ok")
    right = {k: sum(1 for g in graded if g["grade"] == "right" and g["verdict"] == k) for k in ("ok", "check")}
    head = [g for g in graded if g["path"].startswith("header.")]
    checks = [("No wrong value gets ✅, header or line (answer key for PO/TTG, Satellite for the FP)", not wrong,
               f"{len(graded)} values graded ({len(head)} header, {len(graded) - len(head)} line) · right: "
               f"{right['ok']} ✅, {right['check']} to a person · wrong: {caught} caught"
               + (f" · WRONG ✅: {len(wrong)}" if wrong else ""))]
    done = [n for n in SCOPE if n in rows and rows[n]["status"] == "read"]
    hc, hm = _vf_bend_header(rows, done)
    lc, lm = _vf_bend_lines(rows, done)
    checks.append(("A ✅ value misread at any one digit never passes: header and line items", not hm and not lm,
                   f"header {hc - len(hm)} of {hc} caught · lines {lc - len(lm)} of {lc} caught"
                   + (f" · passed anyway: {(hm + lm)[:5]}{' …' if len(hm) + len(lm) > 5 else ''}" if hm or lm else "")))
    bc, bm = _vf_bend_bundles(batch_id)
    checks.append(("A misread beyond what a bundle check allows never passes it (each value a passing check relied "
                   "on, bent at every digit)", not bm,
                   f"{bc} values bent · none passed" if not bm
                   else f"passed anyway: {bm[:5]}{' …' if len(bm) > 5 else ''}"))
    totals, found, counted = phase7_values(batch_id, golden)
    right_t = [t for t in totals if t["grade"] == "right"]
    not_printed = [f"p{t['page']} {t['field']} {t['got']}" for t in totals if t["grade"] != "right"]
    checks.append(("Decision values as the AI read them (measured, not a gate): document totals and row amounts",
                   True, f"PO/TTG totals: {len(right_t)} of {len(totals)} printed on their page"
                   + (f" (not: {not_printed[:6]})" if not_printed else "")
                   + f" · row amounts: {found} of {counted} in the AI's row text"))
    p6 = phase6_checks(batch_id)
    checks.append(("Grouping still right: no page in a wrong bundle", p6["checks"][0][1],
                   p6["checks"][0][2]))
    with db.connect() as c:                               # 7c: each bundle's checks and status (grouper/crosscheck.py)
        bundles = c.execute("""SELECT DISTINCT b.sor_no, b.status::text AS status, b.checks, b.hold_reason, b.json
                                 FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                                 JOIN staging.document d ON d.id = bd.document_id
                                WHERE d.batch_id = %s ORDER BY 1""", (batch_id,)).fetchall()
    pages_of = {g["sor"]: set(g["pages"]) for g in golden["bundles"]}
    # an auto_ok bundle may keep a wrong value kept as read (labelled "not verified", it decides nothing), never a
    # wrong value it decides on: a key, the FP's own values (their graded field), a PO's or receipt's amounts (the
    # number must be printed on its page: phase7_values). An amount's label isn't its decision: page 27 prints only
    # its total before tax, graded "not the total with tax" by the key, and the check compares it with the order's DPP
    amounts = {"PO": {"total", "ppn"}}                # a receipt has no amounts to decide on (2026-09-28)
    wrong_pages = {g["page"] for g in graded if g["grade"] == "WRONG" and g["path"].startswith("header.")
                   and g["path"].split(".", 1)[1] in decides(rows[g["page"]]["doc_type"])
                   and g["path"].split(".", 1)[1] not in amounts.get(rows[g["page"]]["doc_type"], ())} | \
        {t["page"] for t in totals if t["grade"] != "right"}
    unexplained = [b["sor_no"] for b in bundles if not b["hold_reason"] and not b["checks"]]
    def auto(b):                      # auto_ok, or published from auto_ok (phase 8): no person approved it
        return b["status"] == "auto_ok" or (b["status"] == "published" and (b.get("json") or {}).get("published_from") == "auto_ok")
    bad_ok = [f"{b['sor_no']} (pages {sorted(pages_of.get(b['sor_no'], set()) & wrong_pages)})" for b in bundles
              if auto(b) and pages_of.get(b["sor_no"], set()) & wrong_pages]
    tally = {}
    for b in bundles:
        tally[b["status"]] = tally.get(b["status"], 0) + 1
    checks.append(("Every bundle has a status and its reasons; no auto_ok bundle rests on a wrong decision value",
                   bool(bundles) and not unexplained and not bad_ok,
                   " · ".join(f"{n} {s}" for s, n in sorted(tally.items()))
                   + (f" · not checked: {unexplained}" if unexplained else "")
                   + (f" · auto_ok with a wrong value: {bad_ok}" if bad_ok else "")))
    from grouper.crosscheck import ROUNDING
    differ, rounding, passed = [], [], []
    for b in bundles:                 # each against its customer's allowance (S3; Rp 5 until a person confirms one)
        t = ((b["checks"] or {}).get("checks") or {}).get("fp_po_total") or {}
        allow = t.get("allow", ROUNDING)
        if t.get("fp") is not None and t.get("po") is not None and abs(t["fp"] - t["po"]) >= 0.005:
            (rounding if abs(t["fp"] - t["po"]) <= allow + 0.005 else differ).append(b["sor_no"])
            if abs(t["fp"] - t["po"]) > allow + 0.005 and (t.get("status") == "pass" or auto(b)):
                passed.append(b["sor_no"])
    tolak = [x for b in bundles for x in (((b["checks"] or {}).get("checks") or {}).get("received") or {})
             .get("tolakan") or []]
    checks.append((f"A PO's total equals the order's in Satellite (as ordered, what the FP printed) up to its customer's "
                   f"allowance (Rp {ROUNDING:g} until a person confirms one), any larger difference to Review; a tolakan "
                   "Satellite records is named", not passed,
                   f"{len(rounding)} bundles within rounding · {len(differ)} further apart, all to Review"
                   + (f" · PASSED ANYWAY: {passed}" if passed else "")
                   + (f" · tolakan: {tolak}" if tolak else " · no tolakan in this batch")))
    from common.fields import TYPE_MAP
    from worker import vf
    with db.connect() as c:
        on_review = {r["page_no"]: r["second_look"] for r in c.execute(
            """SELECT DISTINCT p.page_no, p.second_look FROM staging.page p
                 JOIN staging.document d ON d.batch_id = p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                 JOIN staging.bundle_document bd ON bd.document_id = d.id JOIN staging.bundle b ON b.id = bd.bundle_id
                WHERE p.batch_id = %s AND b.status IN ('needs_review', 'reviewed')""", (batch_id,))}
    unasked, shown = [], 0
    for n, second in sorted(on_review.items()):
        r = rows.get(n)
        if not r or not r["checks"] or r["doc_type"] not in DOCS:
            continue
        required = {f["name"] for f in DOCS[r["doc_type"]]["header"] if f["source"] == "6.1"}
        shown += sum(1 for name, v in r["checks"]["header"].items() if name in decides(r["doc_type"])
                     and (v["verdict"] == "check" or (v["verdict"] == "empty" and name in required)))
        # the page's own look-again rule (vf.to_ask) on its stored verdicts: stored verdicts don't keep the
        # conflict/cut marks, so they're read back from the reasons those rules write
        header = {k: {**v, "conflict": "but Satellite's SO record says" in (v.get("why") or ""),
                      "cut": "looks cut off at the edge" in (v.get("why") or "")}
                  for k, v in r["checks"]["header"].items()}
        unasked += [f"p{n} {name}" for name in vf.to_ask(r["doc_type"], header, vf.asked_of(second))]
    # a bundle's own values (S2, item 11): a check still owed a look-again keeps its bundle waiting, off Review
    unasked += [f"{b['sor_no']} {k}" for b in bundles if b["status"] in ("needs_review", "reviewed")
                for k, c in ((b["checks"] or {}).get("checks") or {}).items() if c.get("ask") and c["status"] == "unknown"]
    checks.append(("No ⚠ decision value reaches Review before its look-again: a page's own (its rule) and a bundle's "
                   "(item 11); values kept as read are never asked", not unasked,
                   f"{shown} ⚠ decision values on Review, none still owed a look-again" if not unasked
                   else f"reached Review unasked: {unasked[:8]}{' …' if len(unasked) > 8 else ''}"))
    edge = sum(len(v) for v in (golden.get("fp_amounts_edge") or {}).values())
    view = [{"sor": b["sor_no"], "status": b["status"], "hold": b["hold_reason"],
             "reasons": ((b["checks"] or {}).get("reasons") or [])[:4],
             "counts": {s: sum(1 for c in ((b["checks"] or {}).get("checks") or {}).values() if c["status"] == s)
                        for s in ("pass", "fail", "unknown")}} for b in bundles]
    return {"checks": checks, "all_ok": all(ok is True for _, ok, _ in checks), "wrong": wrong,
            "bend_missed": hm + lm + bm, "values": {"totals": totals, "rows_found": found, "rows": counted},
            "graded": len(graded), "right": right, "edge": edge, "bundles": view,
            "edge_pages": sorted(int(p) for p in golden.get("fp_amounts_edge", {}))}


@app.get("/api/batches/{batch_id}/phase7")
def api_phase7(batch_id: str):
    r = phase7_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


_P7 = {}    # batch -> (verdicts fingerprint, when, result). The box takes ~2.5 s and the batch page polls every 4 s


def phase7_cached(batch_id):
    """phase7_checks, recomputed when the page verdicts changed or after a minute (grouping can change alone)."""
    if not VF:
        return None
    with db.connect() as c:
        fp = tuple(c.execute("SELECT count(*) AS n, max(id) AS m FROM staging.field_check WHERE batch_id=%s",
                             (batch_id,)).fetchone().values())
    hit = _P7.get(batch_id)
    if hit and hit[0] == fp and time.time() - hit[1] < 60:
        return hit[2]
    r = phase7_checks(batch_id)
    _P7[batch_id] = (fp, time.time(), r)
    return r


@app.get("/{rest:path}", response_class=HTMLResponse)
def not_built(request: Request, rest: str):
    tab = next((t for t in TABS if t[1] == "/" + rest), None)
    return templates.TemplateResponse("not_built.html", ctx(request, tab=tab), status_code=404 if not tab else 200)
