"""The service the web app (frontend/) talks to: the REST API under /api/v1 (v1.py), what it serves the browser
(page images, crops, the SOR PDFs), and the developers' Teknis screens (server-rendered: Status, Konteks klasifikasi,
Pengetahuan AI, the technical detail of a scan or a page). The background work runs elsewhere: the intake worker
(intake/serve.py), the page workers, the grouper, the teacher, and the scheduler (scheduler/serve.py). The view functions
here build what the API answers with; actions.py holds what a person can change."""
import difflib
import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import cv2
import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from common import config, db, health, intake, keys as keymod, queue as q, settings, storage, verify
from common.fields import DOCS, decides
from api import actions, bahasa, steps

HERE = os.path.dirname(__file__)
app = FastAPI(
    title="SAMB Rekonsiliasi AR API", version="1",
    description="The API the web app (frontend/) calls. Endpoints, payloads and the usual flows: **docs/api.md** in the "
                "repository. No login yet: every decision carries the person's name in `by`, as typed.",
    openapi_tags=[{"name": t} for t in ("Frame", "Upload batches", "Scans and pages", "Page types", "Orders (Periksa order)",
                                        "Berkas per SOR", "Published (Data terkirim)", "Files (page images, crops, PDFs)",
                                        "Teknis (status, acceptance checks)")])
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.middleware("http")
async def _settings(request, call_next):
    """The models and API keys saved on the Teknis screen (common/settings.py), read again at most every few
    seconds: an action that calls a model (a replay, publishing) uses what is saved now."""
    settings.refresh()
    return await call_next(request)
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
templates.env.filters.update(bahasa.FILTERS)       # Indonesian numbers and dates on every screen
templates.env.globals.update(bahasa.GLOBALS)


def static_v(name):
    """Version stamp for a static file (its modification time), so a CSS/JS edit reaches the browser at once
    instead of the browser reusing its cached copy."""
    try:
        return int(os.path.getmtime(os.path.join(HERE, "static", name)))
    except OSError:
        return 0


templates.env.globals["static_v"] = static_v

# vlm-first experiment (branch vlm-first): same UI code, its own database / vhost / MinIO prefix, pages cloned from v1
VF = config.VF
PHASE_BUILT = 7 if VF else 5                 # grouping (6) and cross-checks + Review (7) are built on vlm-first
WIB = timezone(timedelta(hours=7))

# Where things are (common/config.py: every setting from the environment, .env.example lists them)
WEB_URL = config.WEB_URL                     # the web app people work in (frontend/)
OLLAMA_URL = config.OLLAMA_URL
CONSOLES = {"rabbitmq": config.RABBITMQ_CONSOLE_URL, "minio": config.MINIO_CONSOLE_URL}   # links on the Status page
# The answer key (grading only): its two settings stay here with the grader, never in common/ where the pipeline
# would see them. TESTDATA_DIR: where it is (beside the code in the container, the repo's testdata/ otherwise).
TESTDATA = config.env("TESTDATA_DIR") or next(
    (d for d in (os.path.join(HERE, "..", "testdata"), os.path.join(HERE, "..", "..", "testdata"))
     if os.path.isfile(os.path.join(d, "golden_p1-32.json"))), os.path.join(HERE, "..", "testdata"))
# The Teknis screens' top bar: the web app's screens (served by the web app on the same address, which passes the
# Teknis screens on to this service), and the screens for building the system under "Teknis". (path, label, count)
NAV = [("/", "Batch", "batches_need"), ("/upload", "Unggah batch", None)]      # as the web app's top bar
ALL_BATCHES = [("/review", "Periksa order"), ("/label", "Jenis halaman"), ("/bundles", "Berkas per SOR"),
               ("/published", "Data terkirim")]                                  # the web app's screens over every batch
TECH = [("/status", "Status sistem"), ("/settings", "Model & kunci API"), ("/product-codes", "Kode produk pelanggan"),
        ("/fields", "Daftar field"), ("/labels", "Semua label")]
if VF:
    TECH[2:2] = [("/context", "Konteks klasifikasi"), ("/knowledge", "Pengetahuan AI")]

EXPECTED_TABLES = 20      # 19 from the base schema + staging.type_label (006)
if VF:
    EXPECTED_TABLES += 12  # + context_version, lesson, model_call (010), field_confirmation (011), satellite.sor_item
                          # (012), line_match (013), bundle_decision (014), notice (018), reading_trial (019), extract_example (020),
                          # knowledge_page, knowledge_map (021)
    EXPECTED_TABLES += 1   # + job_run (025): the scheduler's jobs
    EXPECTED_TABLES += 1   # + upload (026): upload batches
    EXPECTED_TABLES += 1   # + setting (027): models and API keys saved from the Teknis screen

SVC = config.SERVICE_PREFIX                  # this stack's service names: vf-* on main, rtm-* in the worktree
HEALTH_PORT = config.HEALTH_PORT             # where each worker answers /health
_DB = config.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0] or "?"
_VHOST = config.AMQP_URL.rsplit("/", 1)[-1] or "?"
_PREFIX = config.STORAGE_PREFIX
# (name, role, how to probe, console link on the host)
SERVICES = [
    ("postgres",  f"Shared with v1 · database {_DB}", "probe", None),
    ("rabbitmq",  f"Shared with v1 · vhost {_VHOST}", "probe", CONSOLES["rabbitmq"]),
    ("minio",     f"Shared with v1 · reads v1's page renders, writes {_PREFIX or '/'}", "probe", CONSOLES["minio"]),
    (f"{SVC}-intake", "q.intake: split each uploaded scan into pages, one ticket per page", f"http://{SVC}-intake:{HEALTH_PORT}/health", None),
    (f"{SVC}-worker", "vlm-first page workers: q.pages (q.pages.wait while the AI refuses)", f"http://{SVC}-worker:{HEALTH_PORT}/health", None),
    (f"{SVC}-grouper", "q.group: group, check bundles, send pages back to look again", f"http://{SVC}-grouper:{HEALTH_PORT}/health", None),
    (f"{SVC}-teacher", "Teacher: q.lessons, one lesson at a time", f"http://{SVC}-teacher:{HEALTH_PORT}/health", None),
    (f"{SVC}-scheduler", "Periodic jobs: intake retry, needs-you notice, sweep, knowledge lint", f"http://{SVC}-scheduler:{HEALTH_PORT}/health", None),
    (f"{SVC}-api",    "This API (and the Teknis screens)", "self", None),
] if VF else [
    ("postgres",  "Staging + Satellite schema",   "probe",  None),
    ("rabbitmq",  "q.pages · q.group",            "probe",  CONSOLES["rabbitmq"]),
    ("minio",     "Temp repository (OSS stand-in)", "probe", CONSOLES["minio"]),
    ("ollama",    "Local models (idle: phase 5 checks by code, no model)", f"{OLLAMA_URL}/api/tags", None),
    ("worker",    "Page worker",                  f"http://worker:{HEALTH_PORT}/health", None),
    ("grouper",   "Grouping worker",              f"http://grouper:{HEALTH_PORT}/health", None),
    ("publisher", "Publisher",                    f"http://publisher:{HEALTH_PORT}/health", None),
    ("api",       "This API",                     "self",   None),
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
    keys = {}                               # set or not, never the values (Teknis → Model & kunci API)
    for kind, (title, _, _) in settings.ROWS.items():
        row = settings.EFFECTIVE.get(kind) or {}
        keys[f"{title} ({row.get('ident') or 'not set'})"] = (
            bool(row.get("ident")) and (bool(row.get("key")) or not settings.needs_key(row.get("url"))))
    usage = _model_usage() if VF else []
    jobs = []
    if VF:                                  # the scheduler's jobs: when each last ran, and what it did
        try:
            with db.connect(connect_timeout=3) as c:
                jobs = c.execute("SELECT * FROM staging.job_run ORDER BY name").fetchall()
        except Exception:                   # before migration 025: no jobs to show, never an error
            jobs = []
    checks = [
        (f"All {len(SERVICES)} services healthy", all(r["ok"] for r in rows), f"{sum(r['ok'] for r in rows)} / {len(rows)}"),
        (f"{EXPECTED_TABLES} tables in satellite + staging", len(tables) == EXPECTED_TABLES, f"{len(tables)} found"),
    ]
    return {"services": rows, "tables": tables, "model_keys": keys, "checks": checks, "usage": usage, "jobs": jobs,
            "all_ok": all(c[1] for c in checks)}


def _model_usage():
    """Per model: calls today against its cap, and tokens used since the first call (Model Studio's free quota is
    1M tokens per model for 90 days, so the running total matters, not only today's)."""
    try:
        from worker import vf
        with db.connect(connect_timeout=3) as c:
            rows = c.execute("""SELECT model, count(*) FILTER (WHERE pacific_day = %s) AS today,
                                       coalesce(sum((tokens->>'tokens_in')::bigint), 0)
                                         + coalesce(sum((tokens->>'tokens_out')::bigint), 0) AS tokens,
                                       min(at)::date AS since
                                  FROM staging.model_call WHERE model = ANY(%s) GROUP BY model""",
                             (vf.pacific_day(), list(vf.CAPS))).fetchall()
        got = {r["model"]: r for r in rows}
        return [{"model": m, "cap": cap, "today": int((got.get(m) or {}).get("today") or 0),
                 "tokens": int((got.get(m) or {}).get("tokens") or 0),
                 "since": str((got.get(m) or {}).get("since") or "") or None}          # plain JSON for /api/status
                for m, cap in vf.CAPS.items()]
    except Exception:
        return []


def ctx(request, **kw):
    path = request.url.path
    return {"request": request, "nav": NAV, "tech": TECH, "all_batches": ALL_BATCHES, "built": PHASE_BUILT,
            "path": path,
            "tech_on": any(path == p or path.startswith(p + "/") for p, _ in TECH) or path.startswith(("/trial", "/teknis")),
            "batches_need": _batches_need(), "vf": VF, **kw}


def _unsure_left():
    """Pages whose type the machine couldn't decide and nobody has chosen yet (the Jenis halaman tab's count)."""
    try:
        with db.connect() as c:
            return c.execute("""SELECT count(*) AS n FROM staging.page p WHERE p.type_status = 'unsure'
                                   AND NOT EXISTS (SELECT 1 FROM staging.type_label l
                                                    WHERE l.batch_id = p.batch_id AND l.page_no = p.page_no)""").fetchone()["n"]
    except Exception:                    # the database is down: no count, never an error
        return 0


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


def uploads_view(limit=100, only=None):
    """Every upload batch (or `only` that one), newest first: number, who, scan date, files, pages, reading progress,
    its orders by status (an order counts in each batch it has a document in), and its five steps (api/steps.py)."""
    from common import uploads
    with db.connect() as c:
        rows = [r for r in uploads.listing(c, limit) if only is None or r["id"] == only]
        orders = {}
        for r in c.execute("""SELECT s.upload_id, b.status::text AS status, count(DISTINCT b.id) AS n
                                FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                                JOIN staging.document d ON d.id = bd.document_id
                                JOIN staging.scan_batch s ON s.id = d.batch_id
                               WHERE s.upload_id IS NOT NULL GROUP BY 1, 2"""):
            orders.setdefault(r["upload_id"], {})[r["status"]] = r["n"]
        work = steps.of_uploads(c, [u["id"] for u in rows])
    for u in rows:
        o = orders.get(u["id"], {})
        u.update(need=o.get("needs_review", 0), waiting=o.get("grouping", 0),
                 ready=o.get("auto_ok", 0) + o.get("reviewed", 0), published=o.get("published", 0),
                 orders=sum(o.values()))
        w = work.get(u["id"]) or {}
        u.update(steps=w.get("steps") or [], next=w.get("next"), finished=w.get("finished", False))
    return rows


def search_view(q):
    """The top bar's search (the Batch tab replaced the screens over every batch): batches by number or uploader,
    orders by SOR, Nomor CPO or customer, files by name; up to 20 each, each with the batch it came in."""
    from common import uploads
    q = (q or "").strip()
    if len(q) < 2:
        return {"q": q, "uploads": [], "orders": [], "files": []}
    like = f"%{q}%"
    with db.connect() as c:
        ids = {r["id"] for r in c.execute("""SELECT id FROM staging.upload WHERE code ILIKE %s OR uploaded_by ILIKE %s
                                              ORDER BY created_at DESC LIMIT 20""", (like, like))}
        ups = [u for u in uploads.listing(c, 10_000) if u["id"] in ids]
        rows = c.execute("""SELECT b.id, b.sor_no, b.status::text AS status, s.customer_name, s.cpo_no
                              FROM staging.bundle b LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no
                             WHERE b.sor_no ILIKE %s OR s.cpo_no ILIKE %s OR s.customer_name ILIKE %s
                             ORDER BY b.id DESC LIMIT 20""", (like, like, like)).fetchall()
        docs = _order_docs(c, [r["id"] for r in rows])
        files = [dict(r) for r in c.execute(
            """SELECT s.id, s.file_name, s.page_total, s.received_at, u.id AS upload_id, u.code, u.uploaded_by,
                      u.doc_date
                 FROM staging.scan_batch s LEFT JOIN staging.upload u ON u.id = s.upload_id
                WHERE s.file_name ILIKE %s ORDER BY s.received_at DESC LIMIT 20""", (like,))]
    orders = []
    for r in rows:
        ds = docs.get(r["id"]) or []
        fp = next((d for d in ds if d["t"] == "FP"), ds[0] if ds else None)
        orders.append({"sor_no": r["sor_no"], "status": r["status"], "customer_name": r["customer_name"],
                       "cpo_no": r["cpo_no"], "batch": fp and fp["batch_id"], "uploads": _uploads_of(ds),
                       "thumb": _thumb(fp) if fp else None})
    for f in files:
        f["upload"] = {"id": f.pop("upload_id"), "code": f.pop("code"), "uploaded_by": f.pop("uploaded_by"),
                       "doc_date": f.pop("doc_date")} if f.get("code") else None
    return {"q": q, "uploads": ups, "orders": orders, "files": files}


def _batches_need():
    """How many upload batches have a step a person can act on now (the Batch tab's count)."""
    try:
        with db.connect() as c:
            return sum(1 for w in steps.of_uploads(c).values() if w["next"])
    except Exception:                    # before migration 026, or the database is down: no count, never an error
        return 0


def upload_view(upload_id):
    """One upload batch: its record, its files (each a scan, with its progress), its orders by status, its five
    steps (api/steps.py), and the pages steps 1 and 2 list: those stuck (api/stuck.py, each with why and whether a
    person may try it again), those whose type is unsure; the files that couldn't be split; and why nothing can be
    tried right now (a model not set, a limit), if so."""
    from api import stuck
    from common import uploads
    from worker import vf
    with db.connect() as c:
        u = uploads.get(c, upload_id)
        if not u:
            return None
        files = [dict(r) for r in c.execute(
            """SELECT id, file_name, page_total, pages_rendered, page_done, status, received_at, error
                 FROM staging.scan_batch WHERE upload_id=%s ORDER BY file_name""", (upload_id,))]
        pages = [dict(r) for r in c.execute(
            f"""SELECT p.batch_id, p.page_no, p.status::text AS status, p.type_status, p.error, s.file_name,
                       p.extract_status, p.extract_error, p.second_look->>'waiting' AS waits,
                       p.thumb_upright_path, p.thumb_path, l.page_no IS NOT NULL AS labelled,
                       {stuck.STUCK} AS stuck, {stuck.PUBLISHED} AS published
                  FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
                  LEFT JOIN staging.type_label l ON l.batch_id = p.batch_id AND l.page_no = p.page_no
                 WHERE s.upload_id = %s AND ({stuck.STUCK} OR (p.type_status = 'unsure' AND l.page_no IS NULL))
                 ORDER BY s.file_name, p.page_no""", (upload_id,))]
        work = steps.of_uploads(c, [upload_id])[upload_id]
    summary = next(iter(uploads_view(10_000, only=upload_id)), {})
    page = lambda p: {"batch_id": p["batch_id"], "page_no": p["page_no"], "file_name": p["file_name"],
                      "thumb": _thumb(p), "error": p["error"]}
    return {"upload": {**u, **{k: summary.get(k) for k in ("files", "pages", "read", "busy", "need", "waiting",
                                                             "ready", "published", "orders")}},
            "files": files, "steps": work["steps"], "next": work["next"], "blockers": work["blockers"],
            "finished": work["finished"], "orders": work["orders"],
            "failed": [{**page(p), **stuck.view(p)} for p in pages if p["stuck"]],
            "failed_files": [{"batch_id": f["id"], "file_name": f["file_name"], "error": f["error"]}
                             for f in files if f["status"] == "failed"],
            "not_now": stuck.blocked_text(vf.blocked()),
            "unsure": [page(p) for p in pages if p["type_status"] == "unsure" and not p["labelled"]
                       and not p["stuck"]]}


def home_view():
    """Beranda: what needs a person now, over every scan, and how each recent scan is doing. One row per scan:
    pages read, pages waiting for their type, documents waiting for a number, and its orders by status."""
    with db.connect() as c:
        scans = [dict(r) for r in c.execute("""
            SELECT s.id, s.file_name, s.page_total, s.pages_rendered, s.page_done, s.status, s.received_at,
                   (SELECT count(*) FROM staging.page p WHERE p.batch_id = s.id AND p.type_status = 'unsure'
                       AND NOT EXISTS (SELECT 1 FROM staging.type_label l
                                        WHERE l.batch_id = p.batch_id AND l.page_no = p.page_no)) AS unsure,
                   (SELECT count(*) FROM staging.page p WHERE p.batch_id = s.id AND p.outcome = 'waiting_ai') AS waiting_ai,
                   (SELECT count(*) FROM staging.document d WHERE d.batch_id = s.id AND NOT EXISTS
                       (SELECT 1 FROM staging.bundle_document bd WHERE bd.document_id = d.id)) AS held
              FROM staging.scan_batch s ORDER BY s.received_at DESC LIMIT 12""")]
        orders = {}
        for r in c.execute("""SELECT d.batch_id, b.status::text AS status, count(DISTINCT b.id) AS n
                                FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                                JOIN staging.document d ON d.id = bd.document_id GROUP BY 1, 2"""):
            orders.setdefault(r["batch_id"], {})[r["status"]] = r["n"]
    for s in scans:
        o = orders.get(s["id"], {})
        s.update(need=o.get("needs_review", 0), waiting=o.get("grouping", 0),
                 ready=o.get("auto_ok", 0) + o.get("reviewed", 0), published=o.get("published", 0),
                 orders=sum(o.values()))

    def first(k):                       # the most recent scan where this kind of work waits: where its button goes
        return next((s["id"] for s in scans if s[k]), None)
    todo = {k: {"n": sum(s[k] for s in scans), "batch": first(k)} for k in ("need", "unsure", "ready", "held")}
    return {"scans": scans, "uploads": uploads_view(8), "todo": todo,
            "busy": [s for s in scans if s["status"] in ("splitting", "queued", "reading") or s["waiting_ai"] or s["waiting"]]}


# ---------------------------------------------------------------------------------------------- Kode produk pelanggan

def _product_codes(c, chain=None):
    return c.execute("""SELECT m.*, p.customer_name AS chain_name FROM satellite.product_code_map m
                          LEFT JOIN satellite.customer_profile p ON p.customer_code = m.customer_code
                         WHERE (%s::text IS NULL OR m.customer_code = %s)
                         ORDER BY p.customer_name, m.customer_code, m.customer_item_code""", (chain, chain)).fetchall()


@app.get("/product-codes", response_class=HTMLResponse, include_in_schema=False)
def page_product_codes(request: Request, chain: str | None = None, msg: str | None = None):
    """The master data the product matching builds (2026-10-06): each customer's product code paired with SAMB's
    material code, one row per pair a person confirmed on an order; and the AI's suggestions still waiting there."""
    with db.connect() as c:
        rows = _product_codes(c, chain or None)
        chains = c.execute("""SELECT m.customer_code, p.customer_name, count(*) AS n, max(m.confirmed_at) AS last
                                FROM satellite.product_code_map m
                                LEFT JOIN satellite.customer_profile p ON p.customer_code = m.customer_code
                               GROUP BY 1, 2 ORDER BY 2, 1""").fetchall()
        waiting = c.execute("""SELECT l.sor_no, min(l.batch_id) AS batch, count(*) AS n, max(l.proposed_at) AS at,
                                      s.customer_name
                                 FROM staging.line_match l LEFT JOIN satellite.sor s ON s.sor_no = l.sor_no
                                WHERE l.how = 'ai' AND l.status = 'proposed'
                                GROUP BY l.sor_no, s.customer_name ORDER BY 4 DESC""").fetchall()
    return templates.TemplateResponse("product_codes.html", ctx(request, rows=rows, chains=chains, chain=chain,
                                                                waiting=waiting, msg=msg))


@app.get("/product-codes.csv", include_in_schema=False)
def product_codes_csv(chain: str | None = None):
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["customer_chain", "customer_name", "customer_item_code", "customer_barcode", "samb_material_code",
                "description", "confirmed_by", "confirmed_at"])
    with db.connect() as c:
        for r in _product_codes(c, chain or None):
            w.writerow([r["customer_code"], r["chain_name"] or "", r["customer_item_code"], r["customer_barcode"] or "",
                        r["samb_material_code"], r["description"] or "", r["confirmed_by"] or "",
                        r["confirmed_at"].isoformat() if r["confirmed_at"] else ""])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="kode-produk-{chain or "semua"}.csv"'})


@app.post("/product-codes/delete", include_in_schema=False)
def product_codes_delete(customer_code: str = Form(...), customer_item_code: str = Form(...), by: str = Form("")):
    """A wrong pair taken out of the master data (the next order pairs that product again: by its numbers, the AI's
    suggestion and a person)."""
    from urllib.parse import quote
    if not by.strip():
        return RedirectResponse(f"/product-codes?msg={quote('Tulis nama Anda dulu.')}", status_code=303)
    with db.connect() as c:
        c.execute("DELETE FROM satellite.product_code_map WHERE customer_code=%s AND customer_item_code=%s",
                  (customer_code, customer_item_code))
    print(f"product code {customer_code}/{customer_item_code} removed by {by.strip()}", flush=True)
    return RedirectResponse(f"/product-codes?chain={quote(customer_code)}&msg={quote(f'{customer_item_code} dihapus.')}",
                            status_code=303)


# ---------------------------------------------------------------------------------------------- Model & kunci API

def _s_back(msg, name="", bad=False):
    from urllib.parse import quote
    return RedirectResponse(f"/settings?msg={quote(msg[:300])}&bad={int(bad)}#{quote(name)}", status_code=303)


@app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def page_settings(request: Request, msg: str | None = None, bad: int = 0):
    """The three models (vision, text, classification): each an endpoint, its API key and a model from that endpoint's
    list, set only here, never in .env (common/settings.py). Keys are only ever shown masked."""
    try:
        with db.connect() as c:
            v = settings.view(c)
    except Exception as e:                       # before migration 027
        v, msg, bad = None, f"Settings can't be read: {type(e).__name__} (apply schema/027-setting.sql)", 1
    return templates.TemplateResponse("settings.html", ctx(request, v=v, msg=msg, bad=bad, missing=settings.missing()))


def _row_title(kind):
    return settings.ROWS.get(kind, (kind,))[0]


@app.post("/settings/row", include_in_schema=False)
def save_model_row(kind: str = Form(...), url: str = Form(""), model: str = Form(""), key: str = Form(""),
                   by: str = Form("")):
    """A row's endpoint, model and (when typed) key."""
    try:
        with db.connect() as c:
            settings.save_row(c, kind, url, model, key, by)
    except ValueError as e:
        return _s_back(f"{_row_title(kind)} not saved: {e}", kind, bad=True)
    settings.refresh(force=True)
    print(f"settings: {kind} row = {settings.norm(url)} {model.strip()}, by {by.strip()}", flush=True)
    return _s_back(f"{_row_title(kind)} saved: every service uses {model.strip()} within seconds. Press Test to check it.",
                   kind)


@app.post("/settings/models", include_in_schema=False)
def endpoint_models(kind: str = Form(...), url: str = Form(""), key: str = Form("")):
    """For the screen's dropdown: the endpoint's model list (GET /models: no tokens), the model it suggests, and the
    row's model when the endpoint is the row's. An empty key on the row's own endpoint uses the row's key (it never
    leaves the server)."""
    settings.refresh()
    row = settings.EFFECTIVE.get(kind) or {}
    url = settings.norm(url) or row.get("url", "")
    same = url == row.get("url")
    ids, why = settings.list_models(url, key.strip() or (row.get("key", "") if same else ""))
    return JSONResponse({"url": url, "models": ids, "error": why, "recommended": settings.recommend(kind, ids),
                         "current": row.get("model") if same else None})


@app.post("/settings/test", include_in_schema=False)
def test_model_row(kind: str = Form(...)):
    """The row's model list, then one tiny call (a few tokens): the vision model reads a number from an image."""
    ok, said = settings.test_row(kind)
    return _s_back(f"{_row_title(kind)}: {said}", kind, bad=not ok)


@app.get("/status", response_class=HTMLResponse, include_in_schema=False)
def page_status(request: Request):
    return templates.TemplateResponse("status.html", ctx(request, s=status()))


@app.get("/partials/status", response_class=HTMLResponse, include_in_schema=False)
def partial_status(request: Request):
    return templates.TemplateResponse("_status_body.html", ctx(request, s=status()))


@app.get("/api/status", tags=["Teknis (status, acceptance checks)"])
def api_status():
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(jsonable_encoder(status()))          # the jobs' times are datetimes


# ---------------------------------------------------------------- phase 1: intake

def _recent_batches(limit=20):
    with db.connect() as c:
        return c.execute("""SELECT id, file_name, page_total, pages_rendered, page_done, status, received_at
                            FROM staging.scan_batch ORDER BY received_at DESC LIMIT %s""", (limit,)).fetchall()


PREFIX = config.STORAGE_PREFIX
PAGE_KEY = re.compile(r"^(?:[a-z0-9]+/)?pages/")      # page renders: v1's pages/…, vf's vf/pages/… (any prefix)


@app.get("/trial/{batch}", response_class=HTMLResponse, include_in_schema=False)
def page_trial(request: Request, batch: str, variant: str | None = None, open: int | None = None,
               only: int | None = None):
    """Read-then-map (the mentor's two steps) beside each page's own reading, through today's checks. Nothing the
    pipeline uses changes: the trial lives in staging.reading_trial (python -m worker.vf trial)."""
    from worker import vf
    with db.connect() as c:
        variants = [r["variant"] for r in c.execute(
            "SELECT DISTINCT variant FROM staging.reading_trial WHERE batch_id=%s ORDER BY 1", (batch,))]
        variant = variant or (variants[0] if variants else None)
        trials = {r["page_no"]: dict(r) for r in c.execute(
            "SELECT * FROM staging.reading_trial WHERE batch_id=%s AND variant=%s", (batch, variant))}
        pages = {r["page_no"]: dict(r) for r in c.execute(
            "SELECT * FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)", (batch, list(trials)))}
    rows = [vf.compare_trial(batch, n, trials[n], pages[n]) for n in sorted(trials) if n in pages
            and (only is None or n == only)]
    open = open or only
    tally = {"pages": len(rows), "ok_old": sum(len(r["ok"][0]) for r in rows if r.get("checkable")),
             "ok_new": sum(len(r["ok"][1]) for r in rows if r.get("checkable")),
             "notes": sum(len(r["notes"]) for r in rows), "errors": sum(1 for r in rows if r.get("error")),
             "flipped": sum(1 for r in rows if (r.get("flips") or {}).get("fields") or (r.get("flips") or {}).get("cells")),
             "vl_tokens": sum(int((r["meta"].get("transcribe") or {}).get("tokens_in") or 0)
                              + int((r["meta"].get("transcribe") or {}).get("tokens_out") or 0) for r in rows),
             "map_tokens": sum(int((r["meta"].get(k) or {}).get("tokens_in") or 0)
                               + int((r["meta"].get(k) or {}).get("tokens_out") or 0) for r in rows for k in ("map", "map2"))}
    return templates.TemplateResponse("trial.html", ctx(request, batch=batch, variant=variant, variants=variants,
                                                        rows=rows, tally=tally, open=open))


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


GOLDEN_FILE = config.env("GOLDEN_FILE", "7000356304 - 7000356499.pdf")   # the scan the answer key grades
SOR_RE = re.compile(r"SOR2611\d{7}")


def _golden():
    try:
        return json.load(open(os.path.join(TESTDATA, "golden_p1-32.json")))
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

LABEL_TYPES = bahasa.LABEL_TYPES       # (key stored, name, what it is): the names are said in Indonesian
EXAM_SHARE = 0.2


def _latest_batch():
    with db.connect() as c:
        r = c.execute("SELECT id FROM staging.scan_batch ORDER BY received_at DESC LIMIT 1").fetchone()
    return r and r["id"]


def _batch_with_unsure():
    """The most recent scan that still has a page waiting for its type: where the Label screen opens by itself."""
    with db.connect() as c:
        r = c.execute("""SELECT s.id FROM staging.scan_batch s WHERE EXISTS (
                             SELECT 1 FROM staging.page p WHERE p.batch_id = s.id AND p.type_status = 'unsure'
                                AND NOT EXISTS (SELECT 1 FROM staging.type_label l
                                                 WHERE l.batch_id = p.batch_id AND l.page_no = p.page_no))
                          ORDER BY s.received_at DESC LIMIT 1""").fetchone()
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


def _unsure_in_upload(c, upload):
    """An upload batch's pages whose type is unsure and nobody has labelled: [(scan, page)] in file order."""
    return [(r["batch_id"], r["page_no"]) for r in c.execute(
        """SELECT p.batch_id, p.page_no FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
             LEFT JOIN staging.type_label l ON l.batch_id = p.batch_id AND l.page_no = p.page_no
            WHERE s.upload_id = %s AND p.type_status = 'unsure' AND l.page_no IS NULL
            ORDER BY s.file_name, p.page_no""", (upload,))]


def label_data(batch=None, page=None, after=0, upload=None):
    """What the Label screen shows: the scan (the newest with a page still unsure, when none is named), the page (the
    next unsure one nobody labelled, when none is named), its neighbours, an earlier answer, and the progress. With an
    upload batch (its step 2): the next unsure page over all its files, after the one named; `left` = how many."""
    left = None
    if upload:
        with db.connect() as c:
            todo = _unsure_in_upload(c, upload)
            files = [r["id"] for r in c.execute(
                "SELECT id FROM staging.scan_batch WHERE upload_id=%s ORDER BY file_name", (upload,))]
        left = len(todo)
        if page is None:
            rank = {b: i for i, b in enumerate(files)}
            here = (rank.get(batch, -1), after or 0) if batch else (-1, 0)
            nxt = next((t for t in todo if (rank[t[0]], t[1]) > here), todo[0] if todo else None)
            if nxt:
                batch, page = nxt
            elif not batch and files:
                batch = files[0]
        if not batch:
            return {"batch": None, "left": 0}
    batch = batch or _batch_with_unsure() or _latest_batch()
    if not batch:
        return {"batch": None}
    with db.connect() as c:
        if page is None and upload is None:   # next unsure page nobody has labelled yet
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
    return {"batch": batch, "p": p, "page": page, "total": total, "existing": existing, "near": near,
            "types": LABEL_TYPES, "customers": _customers(), "prog": _label_progress(batch), "left": left}


@app.get("/labels", response_class=HTMLResponse, include_in_schema=False)
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


@app.get("/fields", response_class=HTMLResponse, include_in_schema=False)
def page_fields(request: Request):
    from common import fields as F
    return templates.TemplateResponse("fields.html", ctx(request, docs=F.DOCS, not_yet=F.NOT_YET, sql=F.SQL, column=F.column))


def _batch_orders(batch_id):
    """A scan's orders by status, its documents waiting for a number, and its pages waiting for their type: the scan
    page's summary in plain words."""
    with db.connect() as c:
        o = {r["status"]: r["n"] for r in c.execute("""
            SELECT b.status::text AS status, count(DISTINCT b.id) AS n FROM staging.bundle b
              JOIN staging.bundle_document bd ON bd.bundle_id = b.id JOIN staging.document d ON d.id = bd.document_id
             WHERE d.batch_id = %s GROUP BY 1""", (batch_id,))}
        held = c.execute("""SELECT count(*) AS n FROM staging.document d WHERE d.batch_id = %s AND NOT EXISTS
                               (SELECT 1 FROM staging.bundle_document bd WHERE bd.document_id = d.id)""",
                         (batch_id,)).fetchone()["n"]
    return {"total": sum(o.values()), "need": o.get("needs_review", 0), "waiting": o.get("grouping", 0),
            "ready": o.get("auto_ok", 0) + o.get("reviewed", 0), "published": o.get("published", 0), "held": held}


@app.post("/batches/{batch_id}/rerun", include_in_schema=False)
def rerun(batch_id: str, pages: str = Form("")):
    """pages: empty = all, or a range like '1-32' / '1,3,8'."""
    sel = []
    for part in [x.strip() for x in pages.split(",") if x.strip()]:
        a, _, z = part.partition("-")
        sel += list(range(int(a), int(z or a) + 1))
    intake.rerun(batch_id, sel or None)
    return RedirectResponse(f"/teknis/scan/{batch_id}", status_code=303)


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


@app.get("/api/batches/{batch_id}/phase5", tags=["Teknis (status, acceptance checks)"])
def api_phase5(batch_id: str):
    r = phase5_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase4", tags=["Teknis (status, acceptance checks)"])
def api_phase4(batch_id: str):
    r = phase4_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase3", tags=["Teknis (status, acceptance checks)"])
def api_phase3(batch_id: str):
    r = phase3_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/batches/{batch_id}/phase2", tags=["Teknis (status, acceptance checks)"])
def api_phase2(batch_id: str):
    r = phase2_checks(batch_id)
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


ROLE_ORDER = {"keys": 0, "page": 1, "bundle": 2, "support": 3, None: 4}
ROLE_SAYS = bahasa.ROLE          # what each value does, in Indonesian (keys, page, bundle, support, kept as read)


def _fix_view(c, p, pc):
    """The page viewer (read-then-map, Stage 2a): the paper with a box on every value read, and the type's fields
    beside it, those that decide first. A person corrects a field by marking where it is printed; only for a page
    whose type is known and whose reading is finished."""
    from common import knowledge, satellite
    from common.boxes import line_text
    from common.fields import DECIDES, TYPE_MAP
    t = p.get("doc_type")
    if t not in DOCS or p.get("type_status") not in ("decided", "labelled") or not p.get("fields"):
        return None
    canon = {v: k for k, v in TYPE_MAP.get(t, {}).items()}
    fa, checks = p.get("fields_all") or {}, (pc or {}).get("header") or {}
    roles = {n: lvl for lvl, names in (DECIDES.get(t) or {}).items() for n in names}
    people = {r["field"] for r in c.execute("SELECT field FROM staging.field_confirmation WHERE batch_id=%s AND "
                                            "page_no=%s", (p["batch_id"], p["page_no"]))}
    fields = []
    for f in DOCS[t]["header"]:
        if f.get("source") == "check":
            continue
        name, cur, ck = f["name"], (p["fields"].get(f["name"]) or {}), checks.get(f["name"]) or {}
        fields.append({"name": name, "label": bahasa.field(name, t), "desc": bahasa.desc(name, t) or f.get("desc"),
                       "value": cur.get("value"),
                       "box": (fa.get(canon.get(name, name)) or {}).get("box"),
                       "verdict": ck.get("verdict") or ("empty" if cur.get("value") in (None, "") else "check"),
                       "by": ck.get("by"), "role": roles.get(name), "says": ROLE_SAYS[roles.get(name)],
                       "person": name in people})
    fields.sort(key=lambda x: ROLE_ORDER[x["role"]])
    lines = p["fields"].get("lines") or []
    keys, boxes = satellite.row_keys(t, lines), (p.get("mapping") or {}).get("rows") or []
    cols = [f["name"] for f in DOCS[t]["lines"]]
    by_id = {x.get("id"): x for x in p.get("transcript") or []}

    def copied(i):                                   # the row's cells as the AI OCR copied them, in order
        w = boxes[i] if i < len(boxes) else {}
        out = []
        for bid in (w or {}).get("blocks") or [(w or {}).get("block")]:
            blk = by_id.get(bid) or {}
            out += [str(c).strip() for c in blk.get("cells") or [] if str(c).strip()] or \
                ([blk["text"]] if blk.get("text") else [])
        return out
    rows = [{"i": i + 1, "key": keys[i], "text": r.get("row_text"),
             "box": (boxes[i] or {}).get("box") if i < len(boxes) else None, "copied": copied(i),
             "cells": [(col, r.get(col), f"lines[{keys[i]}].{col}" in people) for col in cols]}
            for i, r in enumerate(lines)]
    so = None
    chain = knowledge.chain_of_page(c, p["batch_id"], p["page_no"])
    if chain:
        so = c.execute("""SELECT s.customer_name FROM staging.document d JOIN staging.bundle_document bd
                            ON bd.document_id = d.id JOIN staging.bundle b ON b.id = bd.bundle_id
                            JOIN satellite.sor s ON s.sor_no = b.sor_no
                           WHERE d.batch_id=%s AND %s BETWEEN d.page_from AND d.page_to LIMIT 1""",
                       (p["batch_id"], p["page_no"])).fetchone()
    units, copied = _pick_units(p), {}
    for b in p.get("transcript") or []:              # a pick of several words is cut from its copied line as written
        if b.get("id") and any(u["block"] == b["id"] for u in units):   # ("15:16:50", not "15 16 50")
            copied[b["id"]] = line_text(b)
    units.sort(key=lambda u: -(u["box"][2] - u["box"][0]) * (u["box"][3] - u["box"][1]))   # small ones drawn on top
    return {"image": p["upright_path"], "type": t, "fields": fields, "rows": rows, "notes": p.get("notes") or [],
            "customer": (so or {}).get("customer_name"), "chain": chain, "units": units, "lines": copied,
            "ready": p.get("outcome") != "waiting_ai" and p.get("status") == "read"}


def _pick_units(p):
    """The words a person can click: the worker's (worker/boxes.py, made with its closer reads) while they belong to
    the page's transcript; else the copy paired with Tesseract's own reading of the page (fewer boxes, never a guess)."""
    from common import boxes
    from worker import boxes as made
    if not p.get("transcript"):
        return []
    pick = p.get("pick") or {}
    if pick.get("v") == made.version(p.get("transcript_version")):
        return [dict(u) for u in pick.get("units") or []]
    return boxes.match(p["transcript"], [p.get("ocr_words") or []], _png_size(p["upright_path"]))


@app.get("/crop/{batch}/{page_no}/row/{key}", tags=["Files (page images, crops, PDFs)"])
def row_crop(batch: str, page_no: int, key: str):
    """A table row as printed: the band of the page its copy's blocks cover, across the whole width (so its columns
    show), for Review's receipt rows. 404 when the page has no copy with boxes (read in one step)."""
    from common import satellite
    from worker import main as v1
    with db.connect() as c:
        p = c.execute("SELECT doc_type::text AS t, upright_path, fields, mapping FROM staging.page WHERE batch_id=%s "
                      "AND page_no=%s", (batch, page_no)).fetchone()
    if not p or not p["fields"]:
        return Response(status_code=404)
    lines = p["fields"].get("lines") or []
    boxes = (p["mapping"] or {}).get("rows") or []
    keys = satellite.row_keys(p["t"], lines)
    box = next(((boxes[i] or {}).get("box") for i, k in enumerate(keys) if k == key and i < len(boxes)), None)
    if not box:
        return Response(status_code=404)
    img = v1.load(p["upright_path"])
    h, w = img.shape[:2]
    y0, y1 = max(0, box[0] * h // 1000 - 8), min(h, box[2] * h // 1000 + 8)
    ok, png = cv2.imencode(".png", img[y0:y1, :])
    return Response(png.tobytes(), media_type="image/png") if ok else Response(status_code=404)


def api_keycheck(field: str, value: str):
    """Before a person saves a key (the value that links a page to its order): does Satellite know it? A warning in
    the page viewer, never a refusal (the order may not be in Satellite's export yet)."""
    v = value.strip()
    if not v or v == "(not printed)":
        return {"known": None}
    with db.connect() as c:
        if field in ("sor", "no_ref"):
            sor = v if v.upper().startswith("SOR") else "SOR" + re.sub(r"\D", "", v)
            r = c.execute("SELECT sor_no, customer_name FROM satellite.sor WHERE sor_no=%s", (sor.upper(),)).fetchone()
            return {"known": bool(r), "says": f"{r['sor_no']} · {r['customer_name']}" if r else
                    f"tidak ada order di Satellite dengan SOR {sor.upper()}: isian ini nomor SOR, bukan nomor PO"}
        if field in ("purchase_order_no", "nomor_cpo"):
            rs = c.execute("SELECT sor_no, customer_name FROM satellite.sor WHERE cpo_no=%s LIMIT 3", (v,)).fetchall()
            return {"known": bool(rs), "says": (" · ".join(f"{r['sor_no']} ({r['customer_name']})" for r in rs)
                                               if rs else f"tidak ada order di Satellite dengan nomor PO {v}")}
    return {"known": None}


def api_region(batch: str, page_no: int, box: str):
    """What a region marked on the paper holds: Tesseract's words, the AI OCR's copy there, and a suggested value
    (print first). box = ymin,xmin,ymax,xmax on 0-1000."""
    from common import knowledge
    try:
        region = [max(0, min(1000, int(float(v)))) for v in box.split(",")]
        assert len(region) == 4 and region[2] > region[0] and region[3] > region[1]
    except (ValueError, AssertionError):
        return JSONResponse({"error": "box must be ymin,xmin,ymax,xmax on 0-1000"}, status_code=400)
    with db.connect() as c:
        p = c.execute("SELECT transcript, ocr_words, upright_path FROM staging.page WHERE batch_id=%s AND page_no=%s",
                      (batch, page_no)).fetchone()
    if not p:
        return JSONResponse({"error": "no such page"}, status_code=404)
    return {**knowledge.region_contents(region, p["transcript"] or [], p["ocr_words"] or [],
                                        _png_size(p["upright_path"])), "region": region}


def _png_size(key):
    """Width/height straight from the PNG header (first 24 bytes), so word boxes line up with the image."""
    try:
        o = storage.client().get_object(storage.bucket(), key, offset=0, length=24)
        head = o.read(); o.close(); o.release_conn()
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        return None


@app.get("/img/{key:path}", tags=["Files (page images, crops, PDFs)"])
def image(key: str):
    if not PAGE_KEY.match(key):
        return Response(status_code=404)
    try:
        obj = storage.client().get_object(storage.bucket(), key)
        data = obj.read(); obj.close(); obj.release_conn()
    except Exception:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg" if key.endswith(".jpg") else "image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/batches/{batch_id}", tags=["Teknis (status, acceptance checks)"])
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
    return psycopg.connect(config.required("MAIN_DATABASE_URL"), row_factory=dict_row)      # read-only


def _v1_pile(batch, page):
    try:
        with _main_db() as m:
            r = m.execute("SELECT pile FROM staging.type_label WHERE batch_id=%s AND page_no=%s", (batch, page)).fetchone()
        return r and r["pile"]
    except Exception:
        return None


def resumable(key):
    """A page with a real image: v1's render (pages/…) or an uploaded page's (vf/pages/…, read by a worktree whose own
    prefix differs). A clone's stand-in image isn't: its page is never sent to a worker."""
    return bool(key and PAGE_KEY.match(key))


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
    detail = None
    if p.get("transcript"):                          # read, then mapped: what each step produced
        from worker import vf
        detail = vf.trial_detail(p, p)
    return {"fields": fields, "lesson": lesson, "label": label, "calls": calls, "mapping": mapping, "filled": filled,
            "type_names": {v: k for k, v in TYPE_MAP.get(p["doc_type"], {}).items()}, "detail": detail,
            "notes": p.get("notes") or []}


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


@app.get("/api/batches/{batch_id}/vf", tags=["Teknis (status, acceptance checks)"])
def api_vf(batch_id: str):
    r = vf_checks(batch_id) if VF else None
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/context", response_class=HTMLResponse, include_in_schema=False)
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


@app.post("/context/{version}/approve", include_in_schema=False)
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


@app.post("/context/revert", include_in_schema=False)
def revert_context(version: int = Form(...), by: str = Form(...)):
    """A person takes the classifier's context back to an earlier version: the undo, now that a change that passes
    the replay is used at once (worker/lesson.py adopt)."""
    from common import context
    if not by.strip():
        return JSONResponse({"error": "say who takes it back"}, status_code=400)
    try:
        with db.connect() as c:
            context.revert(c, version, by.strip())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    _wake_teacher(f"context taken back to #{version}")
    return RedirectResponse("/context", status_code=303)


@app.post("/context/{version}/reject", include_in_schema=False)
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


def lesson_status(c, batch, page, field):
    """The status bar after a fix (the user, 2026-10-01): what the fix of this field on this page is doing now, from
    the example saved with it (none when a typed value wasn't found in the copy), the tip it proposed, and the
    lessons before it. Pure wording in wiki.lesson_progress."""
    from common import wiki
    ex = c.execute("""SELECT e.* FROM staging.extract_example e
                        JOIN staging.field_confirmation f ON f.batch_id=e.batch_id AND f.page_no=e.page_no
                                                         AND f.field=e.confirmation
                       WHERE e.batch_id=%s AND e.page_no=%s AND e.confirmation=%s AND e.made_at >= f.confirmed_at
                       ORDER BY e.made_at DESC LIMIT 1""", (batch, page, field)).fetchone()
    kp, ahead, pending = None, 0, False
    if ex and ex.get("lesson_doc") and ex.get("lesson_version"):
        kp = c.execute("SELECT * FROM staging.knowledge_page WHERE doc_type=%s AND version=%s",
                       (ex["lesson_doc"], ex["lesson_version"])).fetchone()
    if ex and ex.get("lesson_status") in ("waiting", "teaching"):
        ahead = c.execute("""SELECT count(*) AS n FROM staging.extract_example
                              WHERE pile='practice' AND status='active' AND lesson_status IN ('waiting', 'teaching')
                                AND made_at < %s""", (ex["made_at"],)).fetchone()["n"]
        pending = bool(c.execute("SELECT 1 FROM staging.knowledge_page WHERE doc_type=%s AND status='proposed'",
                                 (ex["doc_type"],)).fetchone())
    return wiki.lesson_progress(ex, kp, ahead, pending)


def teacher_now(c):
    """The top bar's one line about the teacher (wiki.teacher_badge): a tip being tested or applied (reported within
    the last half hour), a tip being written, tips waiting for approval, lessons waiting."""
    from common import wiki
    busy = c.execute("""SELECT doc_type, progress FROM staging.knowledge_page
                         WHERE progress->>'step' IN ('testing', 'applying')
                           AND (progress->>'at')::timestamptz > now() - interval '30 minutes'
                         ORDER BY (progress->>'at')::timestamptz DESC LIMIT 1""").fetchone()
    writing = c.execute("SELECT doc_type FROM staging.extract_example WHERE lesson_status='teaching' "
                        "ORDER BY lesson_at DESC LIMIT 1").fetchone()
    approvals = c.execute("SELECT count(*) AS n FROM staging.knowledge_page WHERE status='proposed' "
                          "AND (gate->>'passed')::boolean").fetchone()["n"]
    waiting = c.execute("SELECT count(*) AS n FROM staging.extract_example WHERE lesson_status='waiting' "
                        "AND pile='practice' AND status='active'").fetchone()["n"]
    return wiki.teacher_badge((writing or {}).get("doc_type"), (busy["doc_type"], busy["progress"]) if busy else None,
                              waiting, approvals)


@app.get("/partials/teacher", response_class=HTMLResponse, include_in_schema=False)
def partial_teacher():
    """The top bar's teacher line, asked every few seconds; empty when the teacher has nothing to do."""
    try:
        with db.connect() as c:
            line = teacher_now(c)
    except Exception:                      # before migration 023, or the database is down: nothing to show
        line = None
    if not line:
        return HTMLResponse("")
    return HTMLResponse(f'<a class="teacher-badge" href="/knowledge" title="Guru AI belajar dari perbaikan yang Anda '
                        f'buat">{html.escape(bahasa.teacher(line))}</a>')


def knowledge_types(rows=()):
    """The document types the knowledge screen shows: every type with a field list (common/fields.py), in its order,
    then any other type a page was written for. Faktur Pajak was missing while this was a fixed list (2026-10-06)."""
    from common.fields import DOCS
    order = [t for t, d in DOCS.items() if d.get("header")]
    return order + sorted({r["doc_type"] for r in rows} - set(order))


def _knowledge_view():
    """The wiki (read-then-map Stage 2c): per type its active page by customer section, the proposals with their
    replay and diff, the log; the customers it knows (index) and the examples still waiting for a second page."""
    from common import customer, satellite, wiki
    from worker import learn
    with db.connect() as c:
        rows = c.execute("SELECT * FROM staging.knowledge_page ORDER BY doc_type, version DESC").fetchall()
        ex = c.execute("""SELECT id, batch_id, page_no, doc_type, chain, field, kind, value, anchor, source, pile, made_by,
                                 lesson_status, lesson, lesson_doc, lesson_version, lesson_at
                            FROM staging.extract_example WHERE status='active' ORDER BY made_at DESC""").fetchall()
        sos = satellite.load(c)
        known = customer.learned(c, sos)
    labels = learn.labels(sos)
    types, used, chains = [], set(), set()
    for t in knowledge_types(rows):
        vs = [r for r in rows if r["doc_type"] == t]
        act = next((r for r in vs if r["status"] == "active"), None)
        by_v = {r["version"]: r for r in vs}
        parsed = wiki.parse(act["markdown"]) if act else None
        for sec in (parsed or {}).get("sections") or []:
            chains.add(sec["chain"])
            for cl in sec["claims"]:
                used |= {(b, n, cl["field"]) for b, n in cl["pages"]}
        for r in vs:
            parent = by_v.get(r["parent"])
            r["diff"] = wiki.diff(parent["markdown"] if parent else "", r["markdown"])
            r["stale"] = r["status"] == "proposed" and (act["version"] if act else None) != r["parent"]
        types.append({"type": t, "active": act, "parsed": parsed, "versions": vs,
                      "proposals": [r for r in vs if r["status"] == "proposed"]})
    index = []
    for ch in sorted({c_ for c_ in chains if c_} | set(known), key=lambda x: labels.get(x) or x):
        k = known.get(ch) or {"names": {}, "vendor": {}}
        index.append({"chain": ch, "label": labels.get(ch) or ch,
                      "names": sorted(k["names"], key=lambda n: -len(k["names"][n]))[:3],
                      "vendor": sorted(k["vendor"], key=lambda n: -len(k["vendor"][n]))[:3],
                      "bundles": len(set().union(*k["names"].values(), *k["vendor"].values()) if k["names"] or k["vendor"] else ()),
                      "sections": [t["type"] for t in types for sec in (t["parsed"] or {}).get("sections") or []
                                   if sec["chain"] == ch]})
    waiting = [e for e in ex if e["pile"] == "practice" and (e["batch_id"], e["page_no"], e["field"]) not in used]
    for e in ex:
        e["label"] = labels.get(e["chain"]) if e["chain"] else None
        les = e.get("lesson") or {}
        last = (les.get("answers") or [{}])[-1] if les.get("answers") else {}
        e["lesson_says"] = les.get("why") or last.get("why") or les.get("error") or \
            ((last.get("answer") or {}).get("why") if isinstance(last.get("answer"), dict) else None)
    return {"types": types, "index": index, "waiting": waiting, "labels": labels,
            "lessons": [e for e in ex if e.get("lesson_status")],
            "exam": sum(1 for e in ex if e["pile"] == "exam")}


@app.get("/knowledge", response_class=HTMLResponse, include_in_schema=False)
def page_knowledge(request: Request, t: str | None = None, msg: str | None = None):
    k = _knowledge_view()
    return templates.TemplateResponse("knowledge.html", ctx(request, k=k, open_type=t, msg=msg))


@app.get("/knowledge/{doc_type}.md", response_class=PlainTextResponse, include_in_schema=False)
def knowledge_md(doc_type: str, version: int | None = None):
    with db.connect() as c:
        r = c.execute("SELECT markdown FROM staging.knowledge_page WHERE doc_type=%s AND "
                      + ("version=%s" if version else "status='active'"),
                      (doc_type, version) if version else (doc_type,)).fetchone()
    if not r:
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(r["markdown"], media_type="text/markdown; charset=utf-8")


def _k_back(t, msg):
    from urllib.parse import quote
    return RedirectResponse(f"/knowledge?t={quote(t)}&msg={quote(msg[:300])}#{quote(t)}", status_code=303)


@app.post("/knowledge/{doc_type}/propose", include_in_schema=False)
def knowledge_propose(doc_type: str, markdown: str = Form(...), by: str = Form(...), note: str = Form("")):
    from worker import learn
    if not by.strip():
        return JSONResponse({"error": "say who writes it"}, status_code=400)
    try:
        v = learn.propose(doc_type, markdown.replace("\r\n", "\n"), "person", by.strip(), note.strip() or None)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return _k_back(doc_type, f"{doc_type} #{v} proposed: run its replay; it is used at once if it passes")


@app.post("/knowledge/{doc_type}/draft", include_in_schema=False)
def knowledge_draft(doc_type: str):
    from worker import learn
    v, new, waiting = learn.draft(doc_type)
    return _k_back(doc_type, f"{doc_type} #{v} drafted from examples ({len(new)} claims)" if v else
                   f"nothing to draft for {doc_type}: {len(waiting)} example(s) wait for a second page in another bundle")


@app.post("/knowledge/{doc_type}/{version}/gate", include_in_schema=False)
def knowledge_gate(doc_type: str, version: int):
    """The replay: pass B on the stored pages the proposal changes (text-model calls, kept for reuse)."""
    from worker import learn
    try:
        g = learn.gate(doc_type, version, show=lambda *a: None)
    except ValueError as e:
        return _k_back(doc_type, str(e))
    except Exception as e:                  # a limit or a failed call: nothing stored, try again later
        return _k_back(doc_type, f"the replay stopped: {type(e).__name__}: {e}")
    return _k_back(doc_type, f"{doc_type} #{version}: {'passed, and in use' if g['passed'] else 'did not pass'}: "
                             f"{g['why']}")


@app.post("/knowledge/{doc_type}/{version}/approve", include_in_schema=False)
def knowledge_approve(doc_type: str, version: int, by: str = Form(...)):
    from worker import learn
    if not by.strip():
        return JSONResponse({"error": "say who approves it"}, status_code=400)
    try:
        changed = learn.activate(doc_type, version, by.strip(), show=lambda *a: None)
    except ValueError as e:
        return _k_back(doc_type, str(e))
    _wake_teacher(f"knowledge {doc_type} #{version} approved")        # the next lesson waited for this decision
    return _k_back(doc_type, f"{doc_type} #{version} is active; pages mapped again: "
                             + (", ".join(f"{p} ({', '.join(f) or 'no value changed'})" for p, f in changed.items())
                                or "none changed"))


@app.post("/knowledge/{doc_type}/{version}/reject", include_in_schema=False)
def knowledge_reject(doc_type: str, version: int, by: str = Form("")):
    from worker import learn
    learn.reject(doc_type, version, by.strip() or None)
    _wake_teacher(f"knowledge {doc_type} #{version} rejected")
    return _k_back(doc_type, f"{doc_type} #{version} rejected")


@app.post("/knowledge/lint", include_in_schema=False)
def knowledge_lint():
    from worker import learn
    r = learn.lint(show=lambda *a: None)
    return _k_back("TTG", f"lint: {len(r['demoted'])} claim change(s) taken out"
                          + (": " + "; ".join("; ".join(d["why"]) for d in r["demoted"]) if r["demoted"] else "")
                          + f" · {len(r['unbacked'])} claim(s) with no page behind them"
                          + f" · {len(r['unrecognised'])} section(s) whose customer has nothing to be recognised by")


@app.post("/knowledge/teach", include_in_schema=False)
def knowledge_teach():
    _wake_teacher("asked on /knowledge")
    return _k_back("TTG", "the teacher was woken: it takes the waiting lessons one at a time (vf-teacher's log)")


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
                                                              outcome, keys, fields, thumb_upright_path, thumb_path
                                                         FROM staging.page WHERE batch_id=%s""", (batch,))}
        sos = {r["sor_no"]: r for r in c.execute("SELECT * FROM satellite.sor")}
        confirmed = {(r["page_no"], r["field"]): r for r in c.execute(
            "SELECT * FROM staging.field_confirmation WHERE batch_id=%s", (batch,))}
    def thumb(n):
        p = pages.get(n) or {}
        return f"/img/{p.get('thumb_upright_path') or p.get('thumb_path')}" if p.get("thumb_upright_path") or p.get("thumb_path") else None

    bundles, held = {}, []
    for d in docs:
        d["pages"] = list(range(d["page_from"], d["page_to"] + 1))
        d["thumb"], d["joined"] = thumb(d["page_from"]), _joined(d)
        if d["sor_no"]:
            b = bundles.setdefault(d["sor_no"], {"sor": d["sor_no"], "hold": d["bundle_hold"], "folder": d["folder"],
                                                 "why": _why(d["bundle_hold"]), "documents": [],
                                                 "status": d["bundle_status"],
                                                 "customer": (sos.get(d["sor_no"]) or {}).get("customer_name")})
            b["documents"].append(d)
        else:
            d["why"] = _why(d["hold_reason"])
            field = CONFIRM_FIELD.get(d["type"])
            f = ((pages.get(d["page_from"]) or {}).get("fields") or {}).get(field) or {}
            d["confirm"] = {"field": field, "read": f.get("value"),
                            "value": d["suggested_sor"] if field == "sor" and d["suggested_sor"] else f.get("value"),
                            "done": confirmed.get((d["page_from"], field))} if field else None
            held.append(d)
    grouped = {n for d in docs for n in d["pages"]}
    unplaced = [{"page": n, "type": p["doc_type"], "thumb": thumb(n),
                 "why": _why("not_read" if p["type_status"] is None else "type_unknown")}
                for n, p in sorted(pages.items()) if n not in grouped]
    ordered = sorted(bundles.values(), key=lambda b: (bool(b["hold"]), min(n for d in b["documents"] for n in d["pages"])))
    return {"bundles": ordered, "held": held, "unplaced": unplaced,
            "complete": sum(1 for b in ordered if not b["hold"]), "prefix": PREFIX}


ORDERS_SHOWN = 300            # the newest orders on Berkas per SOR / Periksa order when no scan is chosen


def _scans_in(c, batch=None, upload=None):
    """The scans a screen is narrowed to: one file, the files of one upload batch, or None (every scan)."""
    if batch:
        return [batch]
    if upload:
        from common import uploads
        return uploads.scans_of(c, upload)
    return None


def _order_scope(c, batch=None, upload=None):
    """The orders a screen shows: with a scan or an upload batch chosen, those with a document in it (whole, from
    every scan); without, every order, the most recently touched first (ORDERS_SHOWN)."""
    scans = _scans_in(c, batch, upload)
    return [r["id"] for r in c.execute(
        """SELECT b.id, max(s.received_at) AS last FROM staging.bundle b
             JOIN staging.bundle_document bd ON bd.bundle_id = b.id JOIN staging.document d ON d.id = bd.document_id
             JOIN staging.scan_batch s ON s.id = d.batch_id
            WHERE (%s::text[] IS NULL OR b.id IN (SELECT bd2.bundle_id FROM staging.bundle_document bd2
                                                    JOIN staging.document d2 ON d2.id = bd2.document_id
                                                   WHERE d2.batch_id = ANY(%s)))
            GROUP BY b.id ORDER BY last DESC, b.id DESC LIMIT %s""", (scans, scans, ORDERS_SHOWN))]


def _upload_of(batch_id):
    """The upload batch a scan belongs to: {id, code, uploaded_by, doc_date}, or None."""
    from common import uploads
    with db.connect() as c:
        return uploads.of_scans(c, [batch_id]).get(batch_id)


def _uploads_of(docs):
    """The distinct upload batches of an order's documents, in order: [{id, code, uploaded_by, doc_date}]."""
    out = {}
    for d in docs:
        if d.get("upload_code"):
            out.setdefault(d["upload_code"], {"id": d["upload_id"], "code": d["upload_code"],
                                              "uploaded_by": d["uploaded_by"], "doc_date": d["doc_date"]})
    return list(out.values())


def _order_docs(c, ids):
    """{bundle id: [document]} for these orders, from every scan, each with its scan's file and its first page's
    thumbnail, in order (the FP's scan first, then by arrival, then by page)."""
    from grouper import members
    rows = c.execute("""SELECT bd.bundle_id, d.*, d.doc_type::text AS t, d.doc_type::text AS type, d.linked_by::text AS linked_by,
                               s.file_name, s.received_at, p.thumb_upright_path, p.thumb_path,
                               u.id AS upload_id, u.code AS upload_code, u.uploaded_by, u.doc_date
                          FROM staging.bundle_document bd JOIN staging.document d ON d.id = bd.document_id
                          JOIN staging.scan_batch s ON s.id = d.batch_id
                          LEFT JOIN staging.upload u ON u.id = s.upload_id
                          LEFT JOIN staging.page p ON p.batch_id = d.batch_id AND p.page_no = d.page_from
                         WHERE bd.bundle_id = ANY(%s)""", (list(ids),)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["bundle_id"], []).append(dict(r))
    for k, ds in out.items():
        order = {b: i for i, b in enumerate(members.order_batches(ds))}
        out[k] = sorted(ds, key=lambda d: (order[d["batch_id"]], d["page_from"]))
    return out


def _thumb(r):
    t = r.get("thumb_upright_path") or r.get("thumb_path")
    return f"/img/{t}" if t else None


def bundles_screen(batch=None, upload=None):
    """Berkas per SOR (the user, 2026-10-05: "why not per SOR?"): one entry per order with ALL its documents, from
    whichever scan (file) each came in; then documents whose number isn't sure yet and pages not grouped yet. With a
    scan chosen: the orders with a document in it (still whole), and that scan's held documents and loose pages."""
    with db.connect() as c:
        ids = _order_scope(c, batch, upload)
        scans = _scans_in(c, batch, upload)
        bs = {r["id"]: r for r in c.execute(
            """SELECT b.id, b.sor_no, b.hold_reason, b.folder, b.status::text AS status, s.customer_name
                 FROM staging.bundle b LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no WHERE b.id = ANY(%s)""", (ids,))}
        docs = _order_docs(c, ids)
        held = c.execute("""SELECT d.*, d.doc_type::text AS type, s.file_name, p.thumb_upright_path, p.thumb_path,
                                   p.fields, u.code AS upload_code, u.uploaded_by, u.doc_date
                              FROM staging.document d JOIN staging.scan_batch s ON s.id = d.batch_id
                              LEFT JOIN staging.upload u ON u.id = s.upload_id
                              LEFT JOIN staging.page p ON p.batch_id = d.batch_id AND p.page_no = d.page_from
                             WHERE NOT EXISTS (SELECT 1 FROM staging.bundle_document bd WHERE bd.document_id = d.id)
                               AND (%s::text[] IS NULL OR d.batch_id = ANY(%s))
                             ORDER BY s.received_at DESC, d.page_from LIMIT 200""", (scans, scans)).fetchall()
        confirmed = {(r["batch_id"], r["page_no"], r["field"]): r for r in c.execute(
            "SELECT * FROM staging.field_confirmation WHERE batch_id = ANY(%s)", (sorted({h["batch_id"] for h in held}),))}
        loose = c.execute("""SELECT p.batch_id, p.page_no, p.doc_type::text AS doc_type, p.type_status,
                                    p.thumb_upright_path, p.thumb_path, s.file_name, u.code AS upload_code
                               FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
                               LEFT JOIN staging.upload u ON u.id = s.upload_id
                              WHERE (%s::text[] IS NULL OR p.batch_id = ANY(%s))
                                AND NOT EXISTS (SELECT 1 FROM staging.document d WHERE d.batch_id = p.batch_id
                                                AND p.page_no BETWEEN d.page_from AND d.page_to)
                              ORDER BY s.received_at DESC, p.page_no LIMIT 200""", (scans, scans)).fetchall()
    orders = []
    for i in ids:
        b, ds = bs[i], docs.get(i) or []
        many = len({d["batch_id"] for d in ds}) > 1
        orders.append({"sor": b["sor_no"], "hold": b["hold_reason"], "why": _why(b["hold_reason"]),
                       "folder": b["folder"], "status": b["status"], "customer": b["customer_name"], "many_scans": many,
                       "batch": (ds[0]["batch_id"] if ds else batch),          # the FP's scan: where Review opens
                       "uploads": _uploads_of(ds),
                       "documents": [{"type": d["type"], "batch_id": d["batch_id"], "scan": d["file_name"],
                                      "upload": d["upload_code"],
                                      "page_from": d["page_from"], "page_to": d["page_to"],
                                      "pages": list(range(d["page_from"], d["page_to"] + 1)), "thumb": _thumb(d),
                                      "joined": _joined(d), "linked_by": d["linked_by"], "evidence": d["evidence"]}
                                     for d in ds]})
    out_held = []
    for h in held:
        field = CONFIRM_FIELD.get(h["type"])
        f = (h.get("fields") or {}).get(field) or {} if field else {}
        out_held.append({"type": h["type"], "batch_id": h["batch_id"], "scan": h["file_name"],
                         "upload": h["upload_code"], "uploaded_by": h["uploaded_by"], "doc_date": h["doc_date"],
                         "page_from": h["page_from"], "page_to": h["page_to"],
                         "pages": list(range(h["page_from"], h["page_to"] + 1)), "thumb": _thumb(h), "joined": "",
                         "evidence": h["evidence"], "why": _why(h["hold_reason"]), "suggested_sor": h["suggested_sor"],
                         "group": steps.held_group(h["type"], h["hold_reason"]),
                         "confirm": {"field": field, "read": f.get("value"),
                                     "value": h["suggested_sor"] if field == "sor" and h["suggested_sor"] else f.get("value"),
                                     "done": confirmed.get((h["batch_id"], h["page_from"], field))} if field else None})
    unplaced = [{"page": p["page_no"], "batch_id": p["batch_id"], "scan": p["file_name"], "upload": p["upload_code"],
                 "type": p["doc_type"],
                 "thumb": _thumb(p), "why": _why("not_read" if p["type_status"] is None else "type_unknown")}
                for p in loose]
    orders.sort(key=lambda o: bool(o["hold"]))                        # whole orders first, newest first within
    return {"bundles": orders, "held": out_held, "unplaced": unplaced,
            "complete": sum(1 for o in orders if not o["hold"]), "prefix": PREFIX}


JOIN_HOW = {"satellite": "dicocokkan dengan Satellite", "qr": "dari kode QR", "person": "dipastikan orang",
            "ocr_text": "tercetak", "text": "tercetak", "zoom": "tercetak", "second_look": "dibaca ulang AI",
            "ship_to": "lewat nama toko", "rows": "lewat baris barang", "receipt_no": "lewat nomor tanda terima"}


def _joined(d):
    """How a document joined its order, said plainly: "lewat nomor PO 10101000125418 (tercetak)". From grouping's own
    evidence ("its PO number … (ocr_text) is …'s Nomor CPO"), else from how it was linked."""
    for e in d.get("evidence") or []:
        m = re.match(r"its (SOR|PO number) (\S+) \((\w+)\)", str(e))
        if m:
            how = JOIN_HOW.get(m[3])
            return f"lewat {m[2]}" if m[1] == "SOR" and not how else \
                f"lewat {m[2]} ({how})" if m[1] == "SOR" else f"lewat nomor PO {m[2]}" + (f" ({how})" if how else "")
    return bahasa.LINK.get(d.get("linked_by")) or ""


def _why(key):
    """Why grouping holds something, in Indonesian (grouping's own words for a reason added later)."""
    from grouper import group
    return bahasa.HOLD.get(key) or group.WHY.get(key, key)


KIND = bahasa.DOC                 # document type -> its name for people
# ---------------------------------------------------------------------------------------------- phase 7d: Review

ACCEPT_REASONS = ["rounding", "tolakan confirmed", "the customer's own price", "the document comes later",
                  "other (say in the note)"]
NONE_REASONS = ["not in SAMB's order", "a free (bonus) item", "another product (say in the note)"]


def _order_uploads(where):
    """The upload batches an order's pages came in: [{id, code, uploaded_by, doc_date}], each once."""
    from common import uploads
    with db.connect() as c:
        by_scan = uploads.of_scans(c, sorted({w["batch"] for w in where.values()}))
    out = {}
    for w in where.values():
        u = by_scan.get(w["batch"])
        if u:
            out.setdefault(u["code"], u)
    return list(out.values())


def _order_pages(c, bundle_id):
    """An order's documents and pages from every scan it has documents in (grouper/members.py), for Review: (docs
    [(first page, type, pages)], pages {page: row + checks}, where {page: {batch, page, scan}}, row decisions
    {(page, row): line_match}). Pages are numbered within the order: one scan keeps its page numbers."""
    from grouper import members
    rows = members.of_bundle(c, bundle_id)
    to_key, where = members.keys(rows)
    docs = [(to_key[(r["batch_id"], r["page_from"])], r["t"],
             [to_key[(r["batch_id"], n)] for n in range(r["page_from"], r["page_to"] + 1)]) for r in rows]
    pages = {}
    for r in rows:
        for p in c.execute("""SELECT page_no, doc_type::text AS doc_type, fields, outcome, second_look,
                                     thumb_upright_path, upright_path, fields_all, mapping, ocr_words
                                FROM staging.page WHERE batch_id=%s AND page_no BETWEEN %s AND %s""",
                           (r["batch_id"], r["page_from"], r["page_to"])):
            k = to_key[(r["batch_id"], p["page_no"])]
            pages[k] = {**dict(p), "page_no": k, "checks": verify.load(c, r["batch_id"], p["page_no"])}
    decisions = {(to_key[(d["batch_id"], d["page_no"])], d["row_index"]): d for d in c.execute(
        "SELECT * FROM staging.line_match WHERE batch_id = ANY(%s)", (sorted({r["batch_id"] for r in rows}),))
        if (d["batch_id"], d["page_no"]) in to_key}
    return docs, pages, where, decisions


def _bundle_pages(c, batch, sor):
    """[(first page, type, pages)] of the bundle's documents in this batch."""
    return [(d["page_from"], d["t"], list(range(d["page_from"], d["page_to"] + 1))) for d in c.execute(
        """SELECT d.page_from, d.page_to, d.doc_type::text AS t FROM staging.document d
             JOIN staging.bundle_document bd ON bd.document_id = d.id JOIN staging.bundle b ON b.id = bd.bundle_id
            WHERE d.batch_id = %s AND b.sor_no = %s ORDER BY d.page_from""", (batch, sor))]


def review_list(batch=None, upload=None):
    """Periksa order: every order once, with its status and what is left, needs_review first. Its documents come from
    every scan (an order can arrive as several files); with a scan chosen, the orders with a document in it. With an
    upload batch, each order also says where it stands in that batch's steps (`step`: need, depends on an earlier
    step, outside: waits for another batch's document, waiting, ready, published)."""
    with db.connect() as c:
        ids = _order_scope(c, batch, upload)
        states = (steps.of_uploads(c, [upload]).get(upload) or {}).get("orders", {}) if upload else {}
        rows = c.execute("""SELECT b.id, b.sor_no, b.status::text AS status, b.checks, b.hold_reason, b.reviewed_by,
                                   b.reviewed_at, s.customer_name
                              FROM staging.bundle b LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no
                             WHERE b.id = ANY(%s)""", (ids,)).fetchall()
        docs = _order_docs(c, ids)
    order = {"needs_review": 0, "grouping": 1, "reviewed": 2, "auto_ok": 3, "published": 4}
    out = []
    for r in rows:
        checks = (r["checks"] or {}).get("checks") or {}
        ds = docs.get(r["id"]) or []
        kinds = []
        for d in ds:                                   # Invoice · PO ×4 · Receipt
            name = bahasa.DOC_SHORT.get(d["t"], KIND.get(d["t"], d["t"]))
            kinds.append(name)
        chips = [f"{k} ×{kinds.count(k)}" if kinds.count(k) > 1 else k for k in dict.fromkeys(kinds)]
        fp = next((d for d in ds if d["t"] == "FP"), ds[0] if ds else None)
        out.append({**{k: v for k, v in r.items() if k != "id"}, "reasons": (r["checks"] or {}).get("reasons") or [],
                    "counts": {s: sum(1 for x in checks.values() if x["status"] == s)
                               for s in ("pass", "accepted", "fail", "unknown")},
                    "docs": chips, "thumb": _thumb(fp) if fp else None,
                    "batch": fp["batch_id"] if fp else batch, "scans": len({d["batch_id"] for d in ds}),
                    "uploads": _uploads_of(ds), "step": states.get(r["sor_no"]),
                    "issues": _issues(checks, (r["checks"] or {}).get("reasons") or [], r.get("customer_name"))})
    return sorted(out, key=lambda r: (order.get(r["status"], 9), r["sor_no"]))


def _issues(checks, reasons, customer):
    """A bundle's open problems as short chips for the Review list: (label, 'need' | 'wait')."""
    out = []
    short = {"fp_po_total": "Total PO ≠ order SAMB", "dates": "Tanggal terima", "docs_complete": "Ada dokumen kurang",
             "store_named": "Nama toko berbeda", "sor_in_satellite": "Tidak ada di Satellite"}
    for k, c in checks.items():
        st = c.get("status")
        if st not in ("fail", "unknown", "waiting"):
            continue
        if k == "received":
            out.append(("Menunggu data terima barang (CGR)", "wait") if st == "waiting" else
                       ("Qty tanda terima" if st == "fail" else "Baris tanda terima perlu dipasangkan", "need"))
        elif k == "calibration":
            out.append((f"Pelanggan baru: {' '.join(str(customer or 'pelanggan').split()[:2])}", "need"))
        elif k in short:
            out.append((short[k], "wait" if st == "waiting" or c.get("ask") else "need"))
    for x in reasons:
        if "wait for the AI OCR" in x:
            out.append(("Menunggu AI", "wait"))
        elif "wait for a person" in x:
            out.append(("Ada halaman untuk dipastikan", "need"))
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
        docs, pages, where, decisions = _order_pages(c, b["id"])    # from every scan the order has documents in
        so = sat.load(c, [sor]).get(verify.flat(sor))
        lines = sat.items(c, sor)
        pmap = matching.load_map(c, so and so.get("customer_parent"))
    checks = (b["checks"] or {}).get("checks") or {}
    order = sat.paper(so, lines) if so and not sat.free_goods(so) else None
    got = sat.received(so, lines) if so else None
    rows_in = [(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t, _ in docs if t in ("PO", "TTG")]
    pairs = matching.match(rows_in, lines, pmap, decisions)

    def suggest(t, name):
        out = []
        if t == "TTG" and name == "posting_date" and so and so.get("cgr_date"):
            out.append((str(so["cgr_date"]), "tanggal terima barang di Satellite (CGR)"))
        if name == "purchase_order_no" and so and so.get("cpo_no"):
            out.append((so["cpo_no"], "Nomor CPO di SO"))
        if t == "TTG" and name == "no_ref":
            out.append((sor, "SOR order ini"))
        # the order side's reference is the SO as ordered (what the FP printed), never the FP page's reading; the
        # delivery side's is what Satellite received (verification redesign, S2)
        refs = []
        if t == "PO" and order:
            refs = [(order.get("total"), "total order, termasuk pajak (Satellite)"),
                    (order.get("dpp"), "DPP order: total sebelum pajak (Satellite)")] if name == "total" else \
                [(order.get("ppn"), "PPN order (Satellite)")] if name == "ppn" else []

        return out + [(f"{v:.2f}", why) for v, why in refs if v is not None]

    def entry(n, t, name):
        """One header value as a confirm form needs it: its label, what was read, why it isn't settled."""
        p = pages.get(n) or {}
        f = next((x for x in DOCS.get(t, {}).get("header", []) if x["name"] == name), {"label": name})
        v = ((p.get("checks") or {}).get("header") or {}).get(name) or {}
        canon = {b: a for a, b in TYPE_MAP.get(t, {}).items()}
        return {"name": name, "label": bahasa.field(name, t), "value": ((p.get("fields") or {}).get(name) or {}).get("value"),
                "why": v.get("why") or ("didukung" if v.get("verdict") == "ok" else "tidak terbaca"), "ok": v.get("verdict") == "ok",
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
                         "why": "faktur SAMB mencetak baris SO itu sendiri; dicek terhadap datanya"}
                def ok_cell(col):
                    return i < len(cells) and (cells[i].get(col) or {}).get("verdict") == "ok"
                hints = {"qty": [], "unit_price": [], "discount": []}   # a quantity carries its unit: a bare 48 on a
                if s is not None and t == "TTG" and s.get("cgr_qty") is not None:   # carton row was 48 cartons (S5)
                    hints["qty"].append((f"{float(s['cgr_qty']):g} PCS", "pcs diterima, CGR Satellite"))
                if s is not None and t == "PO":
                    hints["qty"].append((f"{float(s['qty_pcs']):g} PCS", "pcs dipesan, di SO"))
                    hints["unit_price"] += [(f"{float(s['price_uom'] or 0):,.2f}", "harga per karton di SO"),
                                            (f"{float(s['price_pcs'] or 0):,.2f}", "harga per pcs di SO")]
                    pct = [f"{float(x['value']):.2f}%" for x in (s.get("discounts") or {}).values()
                           if x.get("type") == "percentage" and x.get("value")]
                    hints["discount"].append((" / ".join(sorted(pct)) or "0", "diskon di SO"))
                label = {"qty": "qty diterima" if t == "TTG" else "qty dipesan", "unit_price": "harga satuan",
                         "discount": "diskon"}
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
    ok, left = crosscheck.can_approve(checks, {n: pages[n] for n in pages}, where)
    items = _open_items(batch, sor, docs, pages, checks, lines, pairs, entry, {"received": got, "order": order})
    pairing = []                     # the customer's rows not paired with a SAMB line yet, and the AI's suggestions
    for n, t, rws in rows_in:
        for r in rws:
            m = pairs.get((n, r["i"])) or {}
            if r["bonus"] or m.get("status") not in ("none", "proposed"):
                continue
            ai = lines[m["line"]] if m.get("status") == "proposed" and m.get("line") is not None else None
            pairing.append({"page": n, "i": r["i"], "type": t, "desc": r["desc"], "code": r["code"], "qty": r["qty"],
                            "uom": r["uom"], "ai": ai and ai["line_no"],
                            "why": (m.get("why") or "").split(": ", 1)[-1] if ai else None})
    flagged = {f["page"] for i in items for f in i.get("fix") or []} | {i["page"] for i in items if i.get("page")}
    strip = [{"page": n, "type": t, "kind": KIND.get(t, t), "first": n == ps[0],
              "thumb": f"/img/{pages[n]['thumb_upright_path']}" if (pages.get(n) or {}).get("thumb_upright_path") else None,
              "img": f"/img/{pages[n]['upright_path']}" if (pages.get(n) or {}).get("upright_path") else None,
              "flag": n in flagged} for n0, t, ps in docs for n in ps]
    # the page beside the decisions (the user, 2026-10-02): where each value and row a card asks about sits on it
    size = {n: _png_size(p["upright_path"]) for n, p in pages.items() if p.get("upright_path")}
    type_of = {n: t for _, t, ps in docs for n in ps}
    for it in items:
        for f in (it.get("fix") or []) + (it.get("fields") or []):
            n = f.get("page") or it.get("page")
            f["box"], f["approx"] = _field_box(pages.get(n), f.get("type") or type_of.get(n), f["name"], f.get("value"),
                                               size.get(n))
        for x in (it.get("qty_fix") or []) + (it.get("odd_rows") or []):
            x["box"] = _row_box(pages.get(x["page"]), x["i"], [x.get("key"), x.get("desc")], size.get(x["page"]))
    passed = [bahasa.CHECK.get(k) or crosscheck.LABEL.get(k, k) for k, c in checks.items()
              if c["status"] in ("pass", "accepted")]
    return {"bundle": b, "sor": sor, "so": so, "lines": lines, "checks": checks,
            "labels": {**crosscheck.LABEL, **bahasa.CHECK},
            "reasons": (b["checks"] or {}).get("reasons") or [], "documents": documents, "can_approve": ok,
            "left": left, "accept_reasons": ACCEPT_REASONS, "none_reasons": NONE_REASONS, "open_items": items,
            "strip": strip, "passed": passed, "where": {str(k): w for k, w in where.items()}, "pairing": pairing,
            "uploads": _order_uploads(where),
            "multi": len({w["batch"] for w in where.values()}) > 1,
            "calibration": _calibration_view(so, checks)}


OPEN = ("fail", "unknown", "waiting")
FIX_FIELDS = {"fp_po_total": ("PO", ("total", "ppn")), "dates": ("TTG", ("posting_date",))}   # a receipt's
# quantities are row cells: its card lists them, each with its own fix


def _money(x):
    return bahasa.rp(x) if isinstance(x, (int, float)) else "—"


def _plain(k, c, refs=None):
    """A check that doesn't pass, said in one plain line (the Review card's title)."""
    gap = c.get("gap")
    if k == "fp_po_total":
        if gap is None:
            return "Total PO belum bisa dibandingkan dengan order SAMB"
        more = (c.get("po") or 0) > (c.get("fp") or 0)
        return f"Total PO {_money(gap)} {'lebih besar' if more else 'lebih kecil'} dari order SAMB"
    if k == "received":
        if c["status"] == "fail":
            return "Qty di Tanda Terima tidak sama dengan barang diterima menurut Satellite"
        return "Ada baris Tanda Terima yang belum dipasangkan dengan barang di order SAMB"
    if k == "dates":
        return "Tanggal di Tanda Terima tidak sesuai dengan tanggal terima barang di Satellite"
    if k == "docs_complete":
        m = re.fullmatch(r"no (.+) in the bundle", c.get("why") or "")       # crosscheck: "no FP or TTG in the bundle"
        missing = [bahasa.DOC.get(t, t) for t in m[1].split(" or ")] if m else []
        return "Ada dokumen yang kurang" + (": " + ", ".join(missing) if missing else "")
    if k == "store_named":
        return "Ada halaman yang menyebut toko lain milik pelanggan ini"
    if k == "sor_in_satellite":
        return "Order ini tidak ada di Satellite"
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
            items.append({"kind": "label", "title": f"Halaman {n}: jenis dokumennya belum pasti", "page": n})
        elif p.get("outcome") == "waiting_ai":
            items.append({"kind": "wait", "title": f"Halaman {n} menunggu AI membaca ulang", "page": n})
        elif p.get("outcome") == "needs_person":
            h = (p.get("checks") or {}).get("header") or {}
            keys = DECIDES.get(t, {}).get("keys", ())
            hold = ([k for k in keys if (p.get("fields") or {}).get(k)] or list(keys)[:1]) \
                if keys and not any(ok(h.get(k)) for k in keys) else []
            hold += [f for f in dec(t, "page") if not ok(h.get(f))]
            hold += [f for f in dec(t, "support") if (h.get(f) or {}).get("conflict")]
            items.append({"kind": "page", "title": f"Halaman {n} · {KIND.get(t, t)}: " + ", ".join(
                entry(n, t, f)["label"] for f in hold) + " belum pasti", "page": n, "fields": [entry(n, t, f) for f in hold]})
    for k, c in checks.items():                                       # the bundle's checks that don't pass
        if c["status"] not in OPEN or k == "calibration":
            continue
        item = {"kind": "check", "key": k, "title": bahasa.CHECK.get(k) or crosscheck.LABEL.get(k, k), "plain": _plain(k, c, refs),
                "status": c["status"], "why": c["why"],
                "print": c.get("print"), "accept": c["status"] != "waiting" and not c.get("ask"),
                "gap": c.get("gap"), "allow": c.get("allow"), "tolakan": c.get("tolakan") or [], "notes": [], "fix": []}
        if k == "fp_po_total" and c.get("po") is not None:
            item["pair"] = [("Total di PO", c["po"]), ("Order SAMB (Satellite, saat dipesan)", c.get("fp"))]
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
            rejected = {}                                # Satellite's tolakan per SO line ("line 10: 40 pieces (…)")
            for t_ in c.get("tolakan") or []:
                m = re.match(r"line (\d+): ([\d.]+) pieces \((.*)\)$", t_)
                if m:
                    rejected[int(m[1])] = (float(m[2]), m[3])
            for x in item["qty_fix"]:                    # one plain problem per row (the user, 2026-10-02: "i dont
                x["issue"] = ("unpaired" if not x["line"] else "pack" if x.get("pack") else    # know what the system
                              "unread" if x.get("pieces") is None else "differs")              # read for qty")
                x["rejected"] = rejected.get(x["line"]) if x["line"] else None
            shown = {x["line"] for x in item["qty_fix"] if x["rejected"]}
            item["tolakan_rows"] = [{"line": ln, "pcs": q, "why": w} for ln, (q, w) in sorted(rejected.items())
                                    if ln not in shown]
            n_ = {k2: sum(1 for x in item["qty_fix"] if x["issue"] == k2) for k2 in ("unread", "pack", "differs", "unpaired")}
            parts = [f"{n_['unread']} jumlah diterima tidak terbaca" if n_["unread"] else "",
                     f"{n_['pack']} angka terbaca adalah isi kemasan" if n_["pack"] else "",
                     f"{n_['differs']} jumlah berbeda dari Satellite" if n_["differs"] else "",
                     f"{n_['unpaired']} baris belum dikenali" if n_["unpaired"] else "",
                     f"{len(item['qty_missing'])} barang tidak ada di Tanda Terima" if item["qty_missing"] else ""]
            if any(parts):
                item["plain"] = "Tanda Terima: " + ", ".join(x for x in parts if x)
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
                    gaps.append({"sor": r["sor_no"], "check": bahasa.CHECK.get(k) or crosscheck.LABEL[k], "gap": g})
    worst = max((g["gap"] for g in gaps), default=None)
    return {"chain": chain, "name": sat.chain_name(so), "asks": asks, "gaps": sorted(gaps, key=lambda g: -g["gap"]),
            "suggest": None, "worst": worst, "steps": []}          # no allowance to choose: Rp 1,000 for everyone


def review_scans(c):
    """The scans that have documents, newest first, each with how many of its orders need a person."""
    return c.execute("""SELECT s.id, s.file_name, s.received_at,
                                count(DISTINCT b.id) FILTER (WHERE b.status = 'needs_review') AS need
                           FROM staging.scan_batch s JOIN staging.document d ON d.batch_id = s.id
                           LEFT JOIN staging.bundle_document bd ON bd.document_id = d.id
                           LEFT JOIN staging.bundle b ON b.id = bd.bundle_id
                          GROUP BY s.id ORDER BY s.received_at DESC""").fetchall()


def _regroup(batch):
    from grouper import group
    group.run(batch)


@app.get("/documents/{sor}.pdf", tags=["Files (page images, crops, PDFs)"])
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


# ---------------------------------------------------------------------------------------------- what was published
# The user (2026-10-02): "the output of the final posting where the data is published to the DB: a table view of the
# data published when a bundle is posted". Read back from Satellite itself, table by table, exactly as stored.

PUB_META = [("page_ref", "Halaman di PDF", None), ("linked_by", "Terhubung lewat", None),
            ("confidence", "Terverifikasi", "pct"), ("source_batch", "Batch scan", None),
            ("source_pages", "Halaman di scan", None)]
PUB_RECORD = [("sor_no", "Nomor SOR", None), ("pdf_path", "File PDF", None), ("page_count", "Jumlah halaman", None),
              ("version", "Versi", None), ("source_batch", "Batch scan", None), ("updated_at", "Ditulis", "time")]


def _pub_cell(v, kind=None):
    """A stored value as the table shows it: amounts and quantities the Indonesian way, dates by name, None stays
    None (shown as empty: the column is NULL)."""
    if v is None:
        return None
    if kind == "amount":
        return bahasa.angka(v)
    if kind == "qty":
        return bahasa.qty(v)
    if kind == "date":
        return bahasa.tgl(v, jam=False)
    if kind == "time":
        return bahasa.tgl(v)
    if kind == "pct":
        return f"{float(v) * 100:.0f}%"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def published_view(c, sor):
    """One published SOR as Satellite holds it: its PDF record (satellite.sor_document), then for each document type
    its rows (satellite.doc_faktur_penjualan / doc_po / doc_ttg) and their line rows (…_line). Columns in the field
    list's order, each with its database name and its label for people. None if the SOR was never published."""
    from common.fields import DOCS, column
    rec = c.execute("SELECT * FROM satellite.sor_document WHERE sor_no=%s", (sor,)).fetchone()
    if not rec:
        return None
    b = c.execute("""SELECT b.published_at, b.json, s.customer_name, s.tgl_so FROM staging.bundle b
                       LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no
                      WHERE b.sor_no=%s AND b.status='published' ORDER BY b.published_at DESC NULLS LAST LIMIT 1""",
                  (sor,)).fetchone() or {}
    tables, counts = [], {}
    for t in ("FP", "PO", "TTG", "FPJ"):
        d = DOCS[t]
        cols = [("id", "ID dokumen", None), ("sor_no", bahasa.field("sor"), None)]
        cols += [(column(f), bahasa.field(f["name"], t), f["kind"]) for f in d["header"]
                 if f["name"] != "sor" and f["source"] != "check"]
        cols += PUB_META
        rows = c.execute(f"SELECT * FROM satellite.{d['table']} WHERE sor_no=%s ORDER BY id", (sor,)).fetchall()
        lcols = [("doc_id", "ID dokumen", None), ("line_no", "Baris", None)]
        lcols += [(f["name"], bahasa.COL.get(f["name"], f["name"]), f["kind"]) for f in d["lines"]]
        lines = c.execute(f"SELECT * FROM satellite.{d['table']}_line WHERE doc_id = ANY(%s) ORDER BY doc_id, line_no",
                          ([r["id"] for r in rows],)).fetchall() if rows and d["lines"] else []
        counts[t] = (len(rows), len(lines))
        if t == "FPJ" and not rows:
            continue                                      # a Faktur Pajak is shown only once one is published
        tables.append({"type": t, "name": f"satellite.{d['table']}", "cols": cols, "lines": False,
                       "rows": [[_pub_cell(r.get(k), kind) for k, _, kind in cols] for r in rows]})
        if d["lines"]:                                    # the Faktur Pajak has no line table
            tables.append({"type": t, "name": f"satellite.{d['table']}_line", "cols": lcols, "lines": True,
                           "rows": [[_pub_cell(r.get(k), kind) for k, _, kind in lcols] for r in lines]})
    fp = c.execute("SELECT total FROM satellite.doc_faktur_penjualan WHERE sor_no=%s ORDER BY id LIMIT 1",
                   (sor,)).fetchone()
    return {"sor": sor, "customer": b.get("customer_name"), "tgl_so": b.get("tgl_so"), "docs": published_docs(c, sor),
            "published_at": b.get("published_at") or rec["updated_at"], "from": (b.get("json") or {}).get("published_from"),
            "total": fp["total"] if fp else None, "pages": rec["page_count"], "version": rec["version"],
            "batch": rec["source_batch"], "counts": counts,
            "record": {"name": "satellite.sor_document", "cols": PUB_RECORD,
                       "rows": [[_pub_cell(rec.get(k), kind) for k, _, kind in PUB_RECORD]]},
            "tables": tables}


def _spot(words, size, texts, near=None):
    """Where a value is printed, from Tesseract's own words: the shortest run of words on one line that spells it
    (letters and digits only, never inside a longer number), as [ymin, xmin, ymax, xmax] on 0-1000. texts: what to
    look for, best first (as printed, then the value). near: the reading's own box, to choose between two places
    that print the same thing. None when no run spells it (Tesseract misread it, or read nothing there)."""
    from worker import zoom
    if not words or not size:
        return None
    w_, h_ = size
    for t in texts:
        s = verify.flat(str(t)) if t not in (None, "") else ""
        if len(s) < 4:                                   # a short value is printed all over a page: no guess
            continue
        found = []
        for ws in zoom.lines_of(words):
            fl = [verify.flat(str(w[0])) for w in ws]
            for i in range(len(ws)):
                acc = ""
                for j in range(i, min(len(ws), i + 8)):
                    acc += fl[j]
                    k = acc.find(s)
                    if k < 0:
                        continue
                    edge = (acc[k - 1] if k else "") + (acc[k + len(s)] if k + len(s) < len(acc) else "")
                    if not (s[0].isdigit() and any(ch.isdigit() for ch in edge)):
                        run = ws[i:j + 1]
                        x0, y0 = min(w[2] for w in run), min(w[3] for w in run)
                        x1, y1 = max(w[2] + w[4] for w in run), max(w[3] + w[5] for w in run)
                        found.append((j - i, [round(y0 * 1000 / h_), round(x0 * 1000 / w_),
                                              round(y1 * 1000 / h_), round(x1 * 1000 / w_)]))
                    break
        if found:
            short = min(n for n, _ in found)
            boxes = [b for n, b in found if n == short]
            if near:
                boxes.sort(key=lambda b: abs(b[0] + b[2] - near[0] - near[2]) + abs(b[1] + b[3] - near[1] - near[3]))
            return boxes[0]
    return None


def _field_box(p, t, name, value, size):
    """Where a header value is printed on its page, as (box on 0-1000, approx?): the reading's box once snapped to
    Tesseract's words (read, then map); else Tesseract's words that spell it (as printed, then the value); else the
    AI OCR's own box, which is only a guess (approx: shown dashed, "perkiraan letak")."""
    from common.fields import TYPE_MAP
    if not p or not t:
        return None, False
    cn = {v: k for k, v in TYPE_MAP.get(t, {}).items()}.get(name, name)
    read = (p.get("fields_all") or {}).get(cn) or {}
    if (((p.get("mapping") or {}).get("fields") or {}).get(cn) or {}).get("box_by") == "tesseract" and read.get("box"):
        return read["box"], False
    if value in (None, ""):
        return None, False
    own = ((p.get("fields") or {}).get(name) or {}).get("source_text")
    got = _spot(p.get("ocr_words"), size, [own, read.get("source_text"), value], near=read.get("box"))
    if got:
        return got, False
    return (read["box"], True) if read.get("box") else (None, False)


def _row_box(p, i, texts, size):
    """Where a table row is printed, across the page: its box from the mapping (read, then map), else Tesseract's
    words spelling its code or name. None when neither knows."""
    if not p:
        return None
    rows = (p.get("mapping") or {}).get("rows") or []
    if i is not None and i < len(rows) and (rows[i] or {}).get("box"):
        return rows[i]["box"]
    got = _spot(p.get("ocr_words"), size, [x for x in texts if x])
    return [got[0], 15, got[2], 985] if got else None


PUB_BACKED = {"text": "print", "zoom": "print", "qr": "print", "second_look": "print", "adds_up": "print",
              "ship_to": "print", "satellite": "satellite", "rows": "satellite", "receipt_no": "satellite",
              "person": "person"}


def published_docs(c, sor):
    """Data terkirim as people read it (the user, 2026-10-02: "show the page in the right when scrolling, so that the
    user can reconfirm while looking at the page"): each published document's values, exactly as Satellite stores
    them, beside the scan pages they came from: where each value sits on its page (the reading's box, 0-1000) and
    what backed it (print, Satellite, a person, or only the AI's reading: the ones to look at)."""
    from common.fields import DOCS, column
    out = []
    for t in ("FP", "PO", "TTG"):
        d = DOCS[t]
        for row in c.execute(f"SELECT * FROM satellite.{d['table']} WHERE sor_no=%s ORDER BY id", (sor,)).fetchall():
            batch, pages = row["source_batch"], list(dict.fromkeys(row["source_pages"] or []))
            src = {r["page_no"]: r for r in c.execute(
                """SELECT page_no, doc_type::text AS doc_type, upright_path, fields, fields_all, mapping, ocr_words
                     FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)""", (batch, pages))}
            size = {n: _png_size(r["upright_path"]) for n, r in src.items() if r["upright_path"]}
            first = src.get(pages[0]) if pages else None
            checks = (verify.load(c, batch, pages[0]).get("header") or {}) if first else {}
            fields = []
            for f in d["header"]:
                if f["source"] == "check":
                    continue
                v, ck = row.get(column(f)), checks.get(f["name"]) or {}
                state = "empty" if v in (None, "") else \
                    PUB_BACKED.get(ck.get("by"), "print") if ck.get("verdict") == "ok" else "ai"
                box, approx = _field_box(first, t, f["name"], v, size.get(pages[0]) if pages else None)
                fields.append({"name": f["name"], "label": bahasa.field(f["name"], t),
                               "value": _pub_cell(v, f["kind"]), "state": state, "page": pages[0] if pages else None,
                               "box": box, "approx": approx})
            spots = []                                   # each line's row on its page, in the publisher's order
            for n in pages:
                p = src.get(n) or {}
                lines = ((p.get("fields") or {}).get("lines") if p.get("doc_type") == t
                         else (p.get("fields_all") or {}).get("lines")) or []
                boxes = (p.get("mapping") or {}).get("rows") or []
                spots += [(n, (boxes[i] or {}).get("box") if i < len(boxes) else None) for i in range(len(lines))]
            cols = [(f["name"], bahasa.COL.get(f["name"], f["name"]), f["kind"]) for f in d["lines"]]
            lines = c.execute(f"SELECT * FROM satellite.{d['table']}_line WHERE doc_id=%s ORDER BY line_no",
                              (row["id"],)).fetchall()
            rows = []
            for i, r in enumerate(lines):
                n, box = spots[i] if i < len(spots) else (pages[0] if pages else None, None)
                if not box and n in src:                 # a row: found by its code or its name, across the page
                    got = _spot(src[n].get("ocr_words"), size.get(n), [r.get(k) for k, _, kind in cols if kind != "amount"
                                                                        and k in ("kode_material", "item_code", "product_code",
                                                                                  "nama_produk", "material_description",
                                                                                  "product_description")])
                    box = [got[0], 15, got[2], 985] if got else None
                rows.append({"cells": [_pub_cell(r.get(k), kind) for k, _, kind in cols], "line": r["line_no"],
                             "page": n, "box": box})
            filled = [f for f in fields if f["state"] != "empty"]
            out.append({"type": t, "name": bahasa.DOC.get(t, t), "id": row["id"], "batch": batch,
                        "page_ref": row["page_ref"] or [], "linked_by": bahasa.LINK.get(row["linked_by"], row["linked_by"]),
                        "pages": [{"n": n, "img": f"/img/{src[n]['upright_path']}"} for n in pages
                                  if (src.get(n) or {}).get("upright_path")],
                        "fields": fields, "cols": cols, "rows": rows,
                        "to_check": sum(1 for f in filled if f["state"] == "ai"),
                        "backed": sum(1 for f in filled if f["state"] != "ai"), "filled": len(filled)})
    return out


def published_rows(c, batch=None, new=(), upload=None):
    """Every SOR published to Satellite (of one scan, or all), newest first, the ones just published on top; and
    the scans that have published SORs, for the picker."""
    new = list(new)
    rows = c.execute("""SELECT d.sor_no, d.updated_at, d.page_count, d.version, d.source_batch, s.customer_name,
                               (SELECT total FROM satellite.doc_faktur_penjualan f WHERE f.sor_no = d.sor_no
                                 ORDER BY id LIMIT 1) AS total,
                               (SELECT count(*) FROM satellite.doc_po p WHERE p.sor_no = d.sor_no) AS pos,
                               (SELECT count(*) FROM satellite.doc_ttg g WHERE g.sor_no = d.sor_no) AS ttgs
                          FROM satellite.sor_document d LEFT JOIN satellite.sor s ON s.sor_no = d.sor_no
                         WHERE (%s::text IS NULL OR %s = ANY(string_to_array(d.source_batch, ',')))
                           AND (%s::text[] IS NULL OR string_to_array(d.source_batch, ',') && %s::text[])
                         ORDER BY d.updated_at DESC, d.sor_no LIMIT 300""",
                     (batch, batch, *([_scans_in(c, None, upload)] * 2))).fetchall()
    from common import uploads as up_
    by_scan = up_.of_scans(c, sorted({b for r in rows for b in (r["source_batch"] or "").split(",") if b}))
    rows = [{**dict(r), "uploads": list({u["code"]: u for u in (by_scan.get(b) for b in (r["source_batch"] or "").split(","))
                                         if u}.values())} for r in rows]
    rows = sorted(rows, key=lambda r: (r["sor_no"] not in new, new.index(r["sor_no"]) if r["sor_no"] in new else 0))
    batches = c.execute("""SELECT DISTINCT b.id, coalesce(s.file_name, b.id) AS name      -- an order across scans:
                             FROM satellite.sor_document d                            -- source_batch lists them
                             CROSS JOIN unnest(string_to_array(d.source_batch, ',')) AS b(id)
                             LEFT JOIN staging.scan_batch s ON s.id = b.id ORDER BY 2""").fetchall()
    return rows, batches


@app.get("/crop/{batch}/{page_no}/{field}", tags=["Files (page images, crops, PDFs)"])
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
    by_order = [f"p{d['page_from']}" for bd in v["bundles"] for d in bd["documents"]
                if d["linked_by"] not in ("sor", "po_no", "billing_no")]
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


@app.get("/api/batches/{batch_id}/phase6", tags=["Teknis (status, acceptance checks)"])
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
    for b in bundles:                 # each against the allowance, Rp 1,000 for every customer (the mentor, 2026-10-08)
        t = ((b["checks"] or {}).get("checks") or {}).get("fp_po_total") or {}
        allow = t.get("allow", ROUNDING)
        if t.get("fp") is not None and t.get("po") is not None and abs(t["fp"] - t["po"]) >= 0.005:
            (rounding if abs(t["fp"] - t["po"]) <= allow + 0.005 else differ).append(b["sor_no"])
            if abs(t["fp"] - t["po"]) > allow + 0.005 and (t.get("status") == "pass" or auto(b)):
                passed.append(b["sor_no"])
    tolak = [x for b in bundles for x in (((b["checks"] or {}).get("checks") or {}).get("received") or {})
             .get("tolakan") or []]
    checks.append((f"A PO's total equals the order's in Satellite (as ordered, what the FP printed) up to its customer's "
                   f"allowance (Rp {ROUNDING:,.0f} for every customer), any larger difference to Review; a tolakan "
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


@app.get("/api/batches/{batch_id}/phase7", tags=["Teknis (status, acceptance checks)"])
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


@app.get("/teknis/scan/{batch_id}", response_class=HTMLResponse, include_in_schema=False)
def tech_scan(request: Request, batch_id: str):
    """A scan's technical detail for the developers. The scan's own page is the web app's /batches/<id>."""
    b, _ = _batch(batch_id)
    checks = {} if not b else {"vf": vf_checks(batch_id), "p6": phase6_checks(batch_id), "p7": phase7_cached(batch_id)} \
        if VF else {"p2": phase2_checks(batch_id), "p3": phase3_checks(batch_id), "p4": phase4_checks(batch_id),
                    "p5": phase5_checks(batch_id)}
    return templates.TemplateResponse("teknis_scan.html", ctx(request, batch_id=batch_id, b=b, depths=_depths(), **checks),
                                      status_code=200 if b else 404)


@app.get("/teknis/halaman/{batch_id}/{page_no}", response_class=HTMLResponse, include_in_schema=False)
def tech_page(request: Request, batch_id: str, page_no: int):
    """A page's technical detail: as scanned, Tesseract's words, classification, the AI OCR's reading, measurements.
    The page itself (its fields, the fixer) is the web app's /batches/<id>/pages/<n>."""
    with db.connect() as c:
        b = c.execute("SELECT id, file_name, file_path, page_total, status FROM staging.scan_batch WHERE id=%s",
                      (batch_id,)).fetchone()
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (batch_id, page_no)).fetchone()
        pc = verify.load(c, batch_id, page_no) if p else None
        vfp = _vf_page(c, p) if VF and p else None
    ticket = json.dumps({"batch_id": batch_id, "page_no": page_no, "image_key": p["image_path"],
                         "pdf_key": b["file_path"]}, indent=2) if b and p else None
    roles = {}
    if VF and p and p["doc_type"]:                 # what each unsettled value does (verification redesign)
        from common.fields import DECIDES
        for level, names in (DECIDES.get(p["doc_type"]) or {}).items():
            roles.update({n: level for n in names})
    return templates.TemplateResponse("teknis_page.html", ctx(
        request, batch_id=batch_id, b=b, p=p, page_no=page_no, ticket=ticket, pc=pc, vfp=vfp, roles=roles,
        words=p["ocr_words"] if p and p["ocr_words"] else [],
        size=_png_size(p["upright_path"]) if p and p["upright_path"] else None), status_code=200 if p else 404)


from api import v1                       # the REST API (/api/v1) the web app calls: before the catch-all below
app.include_router(v1.router)


@app.get("/", include_in_schema=False)
def root():
    """This service's own address: people work in the web app (WEB_URL), so a browser is sent there; without one,
    say where the API and its documentation are."""
    if WEB_URL:
        return RedirectResponse(WEB_URL, status_code=307)
    return {"service": "SAMB Rekonsiliasi AR", "api": "/api/v1", "docs": "/docs", "guide": "docs/api.md"}


@app.get("/{rest:path}", response_class=HTMLResponse, include_in_schema=False)
def not_found(request: Request, rest: str):
    return templates.TemplateResponse("not_found.html", ctx(request), status_code=404)
