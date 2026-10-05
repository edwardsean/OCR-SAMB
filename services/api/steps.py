"""The five steps of an upload batch (2026-10-05). The user: "cant we just show a "Batches" tab only? then when we click
the batch, the steps for each comes up, but it should be ordered correctly so that the user isnt confused … jenis
halaman first -> berkas per SOR's keys review -> periksa order (review) -> data terkirim".

Each step makes what the next one needs: a page's type decides which number the AI looks for, the number decides
which order a document joins, an order is checked once its documents are in, and only checked orders are sent. So a
batch shows its work in that order, with the system's own reading first:

  baca      Dibaca AI           the system reads every page; a person only retries a page that failed
  jenis     Jenis halaman       pages whose type the system couldn't decide
  cocokkan  Cocokkan ke order   documents whose linking number (SOR or PO) isn't sure
  periksa   Periksa order       orders with something to decide
  kirim     Kirim ke Satellite  finished orders to send, and what was sent

A step's state: `need` (a person can act now), `sys` (the system is working: wait), `done` (nothing left), `none`
(nothing has reached it), `later` (only what never blocks sending: a Faktur Pajak waiting for its number, an order
waiting for a document from another batch).

Ordered, not locked: every step can be opened. But an order whose only open problem is a missing document, while an
earlier step of this batch is still open, `depends` on that step (its document may be there) and isn't counted as
needing a person. Computed here once for the batch list, the batch's own page and the top bar, so they never disagree.
"""
from collections import defaultdict

KEYS = ("baca", "jenis", "cocokkan", "periksa", "kirim")
CONFIRMABLE = {"FP", "TTG", "PO"}                  # a person can settle their key (app.CONFIRM_FIELD)
SYSTEM_HOLDS = {"not_read", "type_unknown", "needs_sap_billing"}   # held until the AI, a label or SAP: not a person
MISSING_DOC = "docs_complete"
SIDE = {"calibration"}                             # a customer's first look: asked again once its documents are in
OPEN = ("fail", "unknown")                          # a check a person has to look at (info, n/a, pass never block)


def held_group(doc_type, hold_reason):
    """Where a document waiting for its order belongs: 'block' (a person confirms its number; an order waits for it),
    'wait' (the system or SAP will settle it), or 'later' (never blocks sending: a Faktur Pajak, Pelunasan)."""
    if hold_reason in SYSTEM_HOLDS:
        return "wait"
    return "block" if doc_type in CONFIRMABLE else "later"


def order_state(status, hold, checks, earlier_open):
    """One order in a batch's step 4: need · depends (waits for an earlier step of this batch) · outside (waits for a
    document from another batch) · waiting (the system) · ready · published."""
    if status == "published":
        return "published"
    if status in ("auto_ok", "reviewed") and not hold:
        return "ready"
    if status == "grouping":
        return "waiting"
    if hold == "fp_missing":                         # its invoice isn't among the grouped pages
        return "depends" if earlier_open else "outside"
    if hold:
        return "need"
    st = {k: (v or {}).get("status") for k, v in (checks or {}).items()}
    open_ = {k for k, s in st.items() if s in OPEN}
    if MISSING_DOC in open_ and not (open_ - {MISSING_DOC} - SIDE) and earlier_open:
        return "depends"
    if not open_ and any(s == "waiting" for s in st.values()):
        return "waiting"
    return "need"


def build(scan, page, held, orders):
    """Pure: the five steps from one batch's counts. scan: {files, pages, splitting, failed}; page: {read, failed,
    busy, waiting_ai, unsure, answered, loose_unread, loose_unsure, loose_other}; held: {block, wait, later};
    orders: {need, depends, outside, waiting, ready, published}. Returns {steps, next, finished, blockers: the open
    steps among 1–3}."""
    total = scan.get("pages") or 0
    failed = page.get("failed", 0) + scan.get("failed", 0)
    busy = page.get("busy", 0) + scan.get("splitting", 0)
    read = page.get("read", 0)
    unscheduled = max(0, total - read - page.get("failed", 0) - page.get("busy", 0))
    baca = {"key": "baca", "pages": total, "read": read, "failed": failed, "busy": busy,
            "waiting_ai": page.get("waiting_ai", 0), "unscheduled": 0 if busy else unscheduled}
    baca["state"] = ("need" if failed else "sys" if busy or baca["waiting_ai"] or baca["unscheduled"]
                     else "done" if total else "none")

    jenis = {"key": "jenis", "unsure": page.get("unsure", 0), "answered": page.get("answered", 0)}
    jenis["state"] = "need" if jenis["unsure"] else "done" if read else "none"

    coc = {"key": "cocokkan", "block": held.get("block", 0), "wait": held.get("wait", 0),
           "later": held.get("later", 0), "loose_unread": page.get("loose_unread", 0),
           "loose_unsure": page.get("loose_unsure", 0), "loose_other": page.get("loose_other", 0)}
    coc["state"] = ("need" if coc["block"] else "sys" if coc["wait"] else "later" if coc["later"]
                    else "done" if read else "none")

    o = {k: orders.get(k, 0) for k in ("need", "depends", "outside", "waiting", "ready", "published")}
    per = {"key": "periksa", **o, "orders": sum(o.values())}
    per["state"] = ("need" if o["need"] else "sys" if o["depends"] or o["waiting"] else "later" if o["outside"]
                    else "done" if per["orders"] else "none")

    kirim = {"key": "kirim", "ready": o["ready"], "published": o["published"]}
    kirim["state"] = "need" if o["ready"] else "done" if o["published"] else "none"

    steps = [baca, jenis, coc, per, kirim]
    nxt = next((s["key"] for s in steps if s["state"] == "need"), None)
    return {"steps": steps, "next": nxt, "blockers": earlier_open(baca, jenis, coc),
            "finished": bool(total) and all(s["state"] in ("done", "later") for s in steps)}


def earlier_open(baca, jenis, coc):
    """Steps 1–3 a missing document may still be waiting in: a page that failed or is being read, a page whose type is
    unsure, a document whose number a person has to confirm. Pages never scheduled don't count: nothing would ever
    finish them."""
    return [k for k, open_ in (("baca", baca["failed"] or baca["busy"]), ("jenis", jenis["unsure"]),
                               ("cocokkan", coc["block"])) if open_]


def of_uploads(c, ids=None):
    """{upload id: {steps, next, finished, blockers, orders: {sor_no: state}}} for these batches (None: every one)."""
    if ids is None:
        ids = [r["id"] for r in c.execute("SELECT id FROM staging.upload")]
    ids = list(ids)
    if not ids:
        return {}
    scan = {r["upload_id"]: dict(r) for r in c.execute(
        """SELECT upload_id, count(*) AS files, coalesce(sum(page_total), 0) AS pages,
                  count(*) FILTER (WHERE status IN ('received', 'splitting')) AS splitting,
                  count(*) FILTER (WHERE status = 'failed') AS failed
             FROM staging.scan_batch WHERE upload_id = ANY(%s) GROUP BY 1""", (ids,))}
    page = {r["upload_id"]: dict(r) for r in c.execute(
        """SELECT s.upload_id,
                  count(*) FILTER (WHERE p.status = 'read') AS read,
                  count(*) FILTER (WHERE p.status IN ('dead_letter', 'failed')) AS failed,
                  count(*) FILTER (WHERE p.status = 'queued') AS busy,
                  count(*) FILTER (WHERE p.status = 'read' AND p.outcome = 'waiting_ai') AS waiting_ai,
                  count(*) FILTER (WHERE p.type_status = 'unsure' AND l.page_no IS NULL) AS unsure,
                  count(l.page_no) AS answered,
                  count(*) FILTER (WHERE loose AND p.status <> 'read') AS loose_unread,
                  count(*) FILTER (WHERE loose AND p.status = 'read' AND p.type_status = 'unsure'
                                     AND l.page_no IS NULL) AS loose_unsure,
                  count(*) FILTER (WHERE loose AND p.status = 'read' AND NOT (p.type_status = 'unsure'
                                     AND l.page_no IS NULL)) AS loose_other
             FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
             LEFT JOIN staging.type_label l ON l.batch_id = p.batch_id AND l.page_no = p.page_no
             CROSS JOIN LATERAL (SELECT NOT EXISTS (SELECT 1 FROM staging.document d WHERE d.batch_id = p.batch_id
                                   AND p.page_no BETWEEN d.page_from AND d.page_to) AS loose) x
            WHERE s.upload_id = ANY(%s) GROUP BY 1""", (ids,))}
    held = defaultdict(lambda: defaultdict(int))
    for r in c.execute("""SELECT s.upload_id, d.doc_type::text AS t, d.hold_reason, count(*) AS n
                            FROM staging.document d JOIN staging.scan_batch s ON s.id = d.batch_id
                           WHERE s.upload_id = ANY(%s)
                             AND NOT EXISTS (SELECT 1 FROM staging.bundle_document bd WHERE bd.document_id = d.id)
                           GROUP BY 1, 2, 3""", (ids,)):
        held[r["upload_id"]][held_group(r["t"], r["hold_reason"])] += r["n"]
    pairs = c.execute("""SELECT DISTINCT s.upload_id, bd.bundle_id FROM staging.bundle_document bd
                           JOIN staging.document d ON d.id = bd.document_id
                           JOIN staging.scan_batch s ON s.id = d.batch_id
                          WHERE s.upload_id = ANY(%s)""", (ids,)).fetchall()
    bundles = {r["id"]: r for r in c.execute(
        """SELECT id, sor_no, status::text AS status, hold_reason, checks->'checks' AS checks
             FROM staging.bundle WHERE id = ANY(%s)""", (sorted({p["bundle_id"] for p in pairs}),))}
    by_upload = defaultdict(list)
    for p in pairs:
        by_upload[p["upload_id"]].append(bundles[p["bundle_id"]])

    out = {}
    for u in ids:
        sc, pg, hd = scan.get(u, {}), page.get(u, {}), held.get(u, {})
        first = build(sc, pg, hd, {})                     # steps 1–3 decide whether an order may still be waiting
        earlier = bool(first["blockers"])
        states = {b["sor_no"]: order_state(b["status"], b["hold_reason"], b["checks"], earlier)
                  for b in by_upload.get(u, [])}
        counts = defaultdict(int)
        for s in states.values():
            counts[s] += 1
        out[u] = {**build(sc, pg, hd, counts), "orders": states}
    return out
