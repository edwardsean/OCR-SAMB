"""Page worker. Reads WORKER_CONCURRENCY pages from q.pages at a time (2026-10-08, the throughput work: a page spends
~85% of its time waiting for the AI's answer, so one worker reads several while it waits).

Steps: enhance + classical OCR (2, worker/enhance.py) → classify (3) → AI OCR (4) → check every value by code (5, common/verify.py).
After a page is done: tick the batch scoreboard; the worker that ticks it to N of N rings the bell on q.group.
"""
import functools
import io
import json
import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pika
from PIL import Image
from psycopg.types.json import Json

from common import config, db, health, queue, settings, storage, verify
from common import keys as keymod
from common.models import schemas, vlm
from worker import classify, enhance

EXTRACT_VERSION = 2      # 2: field lists follow §6.1 (common/fields.py). Bump to re-run extraction; otherwise re-runs reuse the stored result (and don't pay again)

THUMB_WIDTH = 220


def load(key):
    o = storage.client().get_object(storage.bucket(), key)
    try:
        return np.array(Image.open(io.BytesIO(o.read())).convert("L"))
    finally:
        o.close(); o.release_conn()


def put_png(key, a):
    ok, buf = cv2.imencode(".png", a)
    storage.client().put_object(storage.bucket(), key, io.BytesIO(buf.tobytes()), len(buf), content_type="image/png")


def put_thumb(key, a):
    im = Image.fromarray(a); im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
    b = io.BytesIO(); im.save(b, "JPEG", quality=70)
    storage.client().put_object(storage.bucket(), key, io.BytesIO(b.getvalue()), len(b.getvalue()), content_type="image/jpeg")


def current_run(bid):
    with db.connect() as c:
        r = c.execute("SELECT run FROM staging.scan_batch WHERE id=%s", (bid,)).fetchone()
    return r and r["run"]


PHASE2_COLS = ("rotation, osd_conf, skew_angle, black_ratio, dark_band_ratio, speckle_ratio, ocr_variant, variant_scores, "
               "ocr_conf, confident_chars, ocr_words, classical_text, quality_flags, qr_text, ms_enhance_ocr")


PREV_COLS = (PHASE2_COLS + ", doc_type, type_status, type_guess, type_votes, doc_type_conf, layout_score, footer, classify_version, "
             "fields, keys, extract_status, extract_error, extract_version, vlm_meta, vlm_read")


def png_bytes(a):
    ok, buf = cv2.imencode(".png", a)
    return buf.tobytes()


def extract_step(up, r, cls, prev):
    """Phase 4. Unsure pages first get a second classification try from the AI OCR's reading.
    A failure (e.g. Google overloaded all day) is recorded on the page and never fails the page."""
    same_type = prev and prev["doc_type"] == cls["doc_type"] and prev["type_status"] == cls["type_status"]
    if prev and same_type and prev["extract_version"] == EXTRACT_VERSION and prev["extract_status"] in ("done", "skipped"):
        return {k: prev[k] for k in ("fields", "keys", "extract_status", "extract_error", "vlm_meta", "vlm_read")}, cls
    out = {"fields": {}, "keys": {}, "extract_status": None, "extract_error": None, "vlm_meta": {}, "vlm_read": None}
    img = png_bytes(up)
    try:
        if cls["type_status"] == "unsure":
            read, meta = vlm.read(img)
            out["vlm_read"], out["vlm_meta"]["read"] = read, meta
            status, doc_type, guess, votes2 = classify.second_try(cls["type_votes"], read, r["quality_flags"])
            cls = {**cls, "type_status": status, "doc_type": doc_type, "type_guess": guess,
                   "type_votes": {**cls["type_votes"], "second_try": votes2}}
        if cls["type_status"] == "decided" and cls["doc_type"] in schemas.SCHEMAS:
            fields, meta = vlm.extract(img, cls["doc_type"])
            out["fields"], out["vlm_meta"]["extract"] = fields, meta
            out["keys"] = keymod.derive(cls["doc_type"], fields, r["classical_text"], r["qr_text"])
            out["extract_status"] = "done"
        else:
            out["extract_status"] = "skipped"
    except Exception as e:
        out["extract_status"], out["extract_error"] = "failed", f"{type(e).__name__}: {e}"[:500]
    return out, cls


def handle(ticket):
    bid, n = ticket["batch_id"], ticket["page_no"]
    run = ticket.get("run", 1)
    if run != current_run(bid):
        return "stale"          # ticket from before a re-run: the page has a newer ticket
    with db.connect() as c:
        prev = c.execute(f"SELECT enhance_version, upright_path, clean_path, thumb_upright_path, {PREV_COLS} "
                         "FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()

    # Phase 2 — enhance + classical OCR. Reused when this page was already read by the same code version.
    enhance_reused = bool(prev and prev["enhance_version"] == enhance.ENHANCE_VERSION and prev["upright_path"])
    if not enhance_reused and prev and prev["enhance_version"] == 1 and prev["upright_path"]:
        # v1 -> v2 only changed how orientation is decided. If the new decision matches the rotation v1 applied,
        # everything read from the page is still valid: keep it (and its classification) instead of redoing it.
        original = load(ticket["image_key"])
        new_rot = enhance.upright(enhance.mask_bands(original, *enhance.measure(original)[2:]))[1]
        enhance_reused = new_rot == prev["rotation"]
    if enhance_reused:
        up = load(prev["upright_path"])
        r = {k: prev[k] for k in PHASE2_COLS.replace(" ", "").split(",")}
        up_key, clean_key, thumb_key = prev["upright_path"], prev["clean_path"], prev["thumb_upright_path"]
    else:
        original = load(ticket["image_key"])
        up, clean, r = enhance.process(original)
        base = f"pages/{bid}"
        up_key, clean_key, thumb_key = f"{base}/upright/p{n:03d}.png", f"{base}/clean/p{n:03d}.png", f"{base}/thumb_upright/p{n:03d}.jpg"
        put_png(up_key, up); put_png(clean_key, clean); put_thumb(thumb_key, up)

    # Phase 3 — classification. Reused only if the page image is unchanged AND the classify code is unchanged (saves a Jev call).
    if enhance_reused and prev["classify_version"] == classify.CLASSIFY_VERSION and prev["type_status"] and \
            not ((prev["type_votes"] or {}).get("second_try")):
        cls = {k: prev[k] for k in ("type_status", "doc_type", "type_guess", "type_votes", "doc_type_conf",
                                    "layout_score", "footer", "classify_version")}
    else:
        cls = classify.classify(up, r["ocr_words"], r["classical_text"], r["qr_text"], r["quality_flags"])
    if (cls["type_votes"] or {}).get("second_try"):          # a stored second try is phase 4's result; start from the first
        cls["type_votes"] = {k: v for k, v in cls["type_votes"].items() if k != "second_try"}

    # Phase 4 — AI OCR extraction (and a second classification try for unsure pages).
    ex, cls = extract_step(up, r, cls, prev)

    # Phase 5 — check every value against the page (plain code, cheap: always recomputed, never reused).
    checks = None
    if ex["extract_status"] == "done":
        ex["keys"] = keymod.derive(cls["doc_type"], ex["fields"], r["classical_text"], r["qr_text"])
        checks = verify.run(cls["doc_type"], ex["fields"], r["classical_text"], r["qr_text"])

    with db.connect() as c:
        # Save the page only if (a) nobody saved it already and (b) this ticket's run is still current.
        saved = c.execute("""
            UPDATE staging.page SET
              status='read', upright_path=%(up)s, clean_path=%(clean)s, thumb_upright_path=%(thumb)s,
              rotation=%(rotation)s, osd_conf=%(osd_conf)s, skew_angle=%(skew_angle)s,
              black_ratio=%(black_ratio)s, dark_band_ratio=%(dark_band_ratio)s, speckle_ratio=%(speckle_ratio)s,
              ocr_variant=%(ocr_variant)s, variant_scores=%(variant_scores)s, ocr_conf=%(ocr_conf)s,
              confident_chars=%(confident_chars)s, ocr_words=%(ocr_words)s, classical_text=%(classical_text)s,
              quality_flags=%(quality_flags)s, qr_text=%(qr_text)s, ms_enhance_ocr=%(ms_enhance_ocr)s,
              enhance_version=%(ev)s,
              doc_type=%(doc_type)s, type_status=%(type_status)s, type_guess=%(type_guess)s, type_votes=%(type_votes)s,
              doc_type_conf=%(doc_type_conf)s, layout_score=%(layout_score)s, footer=%(footer)s,
              classify_version=%(classify_version)s,
              fields=%(fields)s, keys=%(keys)s, extract_status=%(extract_status)s, extract_error=%(extract_error)s,
              extract_version=%(xv)s, vlm_meta=%(vlm_meta)s, vlm_read=%(vlm_read)s,
              model_vlm=%(model_vlm)s, model_ms=%(model_ms)s,
              verify_version=%(vv)s,
              error=NULL, read_at=now()
            WHERE batch_id=%(bid)s AND page_no=%(n)s AND status <> 'read'
              AND EXISTS (SELECT 1 FROM staging.scan_batch WHERE id=%(bid)s AND run=%(run)s)
            RETURNING page_no""",
            {**r, **cls, "variant_scores": Json(r["variant_scores"]), "ocr_words": Json(r["ocr_words"]),
             "type_votes": Json(cls["type_votes"]), "ev": enhance.ENHANCE_VERSION,
             "fields": Json(ex["fields"] or {}), "keys": Json(ex["keys"] or {}), "extract_status": ex["extract_status"],
             "extract_error": ex["extract_error"], "xv": EXTRACT_VERSION, "vlm_meta": Json(ex["vlm_meta"] or {}),
             "vlm_read": Json(ex["vlm_read"]) if ex["vlm_read"] else None,
             "model_vlm": ((ex["vlm_meta"] or {}).get("extract") or (ex["vlm_meta"] or {}).get("read") or {}).get("model"),
             "model_ms": sum(m.get("ms", 0) for m in (ex["vlm_meta"] or {}).values()) or None,
             "vv": verify.VERIFY_VERSION if checks else None,
             "up": up_key, "clean": clean_key, "thumb": thumb_key, "bid": bid, "n": n, "run": run}).fetchone()
        if saved:      # same transaction as the page save: the page's value checks (staging.field_check)
            verify.store(c, bid, n, ex["fields"], checks)
    if not saved:
        return None
    return tick(bid, run)


def tick(bid, run):
    """Scoreboard = how many pages are actually read (recounted, never +1, so it can't drift).
    The bell is a state change reading → read that exactly one worker can win, and only when every page is read."""
    with db.connect() as c:
        score = c.execute("""
            UPDATE staging.scan_batch b
               -- GREATEST: two workers recounting at once could otherwise save an older, lower count last
               SET page_done = GREATEST(b.page_done, (SELECT count(*) FROM staging.page p WHERE p.batch_id=b.id AND p.status='read')),
                   status = CASE WHEN b.status='queued' THEN 'reading' ELSE b.status END
             WHERE b.id=%s AND b.run=%s RETURNING page_done, page_total""", (bid, run)).fetchone()
    with db.connect() as c:
        won = c.execute("""
            UPDATE staging.scan_batch b SET status='read'
             WHERE b.id=%s AND b.run=%s AND b.status IN ('queued','reading')
               AND b.page_total = (SELECT count(*) FROM staging.page p WHERE p.batch_id=b.id AND p.status='read')
            RETURNING page_total""", (bid, run)).fetchone()
    return {"page_done": score["page_done"] if score else None, "page_total": score and score["page_total"],
            "ring": bool(won)}


def ring_bell(ch, bid, run):
    ch.basic_publish("", queue.Q_GROUP, json.dumps({"batch_id": bid, "run": run}).encode(),
                     pika.BasicProperties(delivery_mode=2, content_type="application/json"))


PIPELINE = config.PIPELINE


def on_message(ch, method, props, body):
    settings.refresh()                          # the models and keys saved on the Teknis screen
    ticket = json.loads(body)
    try:
        if PIPELINE == "vlm-first":            # the experiment on branch vlm-first (worker/vf.py)
            from worker import vf
            if ticket.get("run", 1) != current_run(ticket["batch_id"]):
                ch.basic_ack(method.delivery_tag); return  # an earlier run's ticket (re-run, or stopped): never re-parked
            why = ticket.get("parked") and vf.blocked()
            if why:                            # back from the waiting room, but the AI still can't be asked: no work
                vf.park(ticket, why)
                ch.basic_ack(method.delivery_tag); return
            score = vf.handle(ticket)
            if score not in ("stale", "done"):
                did = vf.after(ticket)         # a call the limit stopped waits in q.pages.wait; a failed one retries
                if did:
                    print(f"{ticket['batch_id']} p{ticket['page_no']}: {did}", flush=True)
                if score:
                    queue.wake_grouper(ticket["batch_id"], f"page {ticket['page_no']} was read")
        else:
            score = handle(ticket)
        if score in ("stale", "done"):
            ch.basic_ack(method.delivery_tag); return
        if score and score["ring"]:
            ring_bell(ch, ticket["batch_id"], ticket.get("run", 1))
            print(f"{ticket['batch_id']}: N of N ({score['page_total']}) — bell rung on {queue.Q_GROUP}")
        ch.basic_ack(method.delivery_tag)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:500]
        traceback.print_exc()
        with db.connect() as c:
            # never downgrade a page another ticket already read successfully
            c.execute("UPDATE staging.page SET error=%s, status=%s WHERE batch_id=%s AND page_no=%s AND status <> 'read'",
                      (err, "dead_letter" if method.redelivered else "queued", ticket["batch_id"], ticket["page_no"]))
        from common import trace
        trace.event("page.crashed", "fail", batch=ticket["batch_id"], page=ticket["page_no"], error=err,
                    then="given up (dead letter)" if method.redelivered else "tried once more")
        if method.redelivered:
            ch.basic_reject(method.delivery_tag, requeue=False)   # second failure → q.pages.dlq
        else:
            ch.basic_nack(method.delivery_tag, requeue=True)      # first failure → one retry


class _OnItsThread:
    """The channel as a page thread uses it (ack, nack, reject, publish): each call runs on the connection's own thread,
    because pika's connection is not thread-safe. Fire and forget: a call on a connection that has closed is dropped,
    and RabbitMQ gives the unacknowledged page to a worker again."""

    def __init__(self, conn, ch):
        self.conn, self.ch = conn, ch

    def __getattr__(self, name):
        fn = getattr(self.ch, name)

        def later(*a, **k):
            try:
                self.conn.add_callback_threadsafe(functools.partial(fn, *a, **k))
            except Exception as e:                          # the connection closed meanwhile
                print(f"worker: {name} dropped ({type(e).__name__}): the page is given again", flush=True)
        return later


def _guarded(ch, method, props, body):
    """A page thread's work: on_message, and should even its own error handling fail (the database down), the ticket
    is put back rather than left unacknowledged."""
    try:
        on_message(ch, method, props, body)
    except Exception:
        traceback.print_exc()
        ch.basic_nack(method.delivery_tag, requeue=True)


STOP = threading.Event()


def _stop(*_):
    """docker stop / restart (SIGTERM): take no new page, finish the ones started (stop_grace_period, 3 minutes in
    docker-compose.yml). Stopping mid-page threw away AI answers already paid for (2026-10-08: 21 calls)."""
    if not STOP.is_set():
        print("worker stopping: no new page; finishing the pages it has", flush=True)
    STOP.set()


def pages_at_once():
    """Pages per worker, as saved on Teknis → Model & kunci API (common/settings.py NUMBERS; read again at most every
    ten seconds), within 1..WORKER_CONCURRENCY_MAX."""
    settings.refresh()
    return max(1, min(config.WORKER_CONCURRENCY, config.WORKER_CONCURRENCY_MAX))


def run(concurrency=None):
    """Consume q.pages, the saved number of pages at a time (pages_at_once; `concurrency` fixes it): the connection's
    thread takes the tickets and keeps the heartbeat; a pool of page threads reads them. RabbitMQ hands this worker at
    most that many at once (prefetch), so a worker never holds pages it can't start; a change on the screen is taken
    within seconds (a lower number lets the pages in hand finish). On SIGTERM it stops taking pages and returns once
    the pages it has are done (their acks go out first)."""
    n = concurrency or pages_at_once()
    signal.signal(signal.SIGTERM, _stop)
    while not STOP.is_set():
        pool, conn, ch, busy = None, None, None, []
        try:
            conn = queue.connect()
            ch = conn.channel()
            queue.declare(ch)
            if PIPELINE == "vlm-first":
                queue.declare_wait(ch)
            ch.basic_qos(prefetch_count=n)
            pool = ThreadPoolExecutor(config.WORKER_CONCURRENCY_MAX, thread_name_prefix="page")   # prefetch limits it
            safe = _OnItsThread(conn, ch)

            def take(_ch, method, props, body):
                busy[:] = [f for f in busy if not f.done()] + [pool.submit(_guarded, safe, method, props, body)]
            tag = ch.basic_consume(queue.Q_PAGES, take)
            print(f"worker consuming {queue.Q_PAGES}, {n} pages at a time", flush=True)
            while not STOP.is_set():
                conn.process_data_events(time_limit=1)    # the tickets, the heartbeat, the page threads' acks
                now = concurrency or pages_at_once()
                if now != n:                              # changed on the screen: RabbitMQ hands out that many
                    ch.basic_qos(prefetch_count=now)
                    print(f"worker: {n} → {now} pages at a time", flush=True)
                    n = now
            ch.basic_cancel(tag)                          # no new page: RabbitMQ keeps the rest for other workers
            while any(not f.done() for f in busy):
                conn.process_data_events(time_limit=1)    # the started pages finish, and their acks go out
            conn.process_data_events(time_limit=1)
            conn.close()
            print("worker stopped: every page it had is done", flush=True)
        except Exception as e:
            if STOP.is_set():
                break
            print("worker reconnecting:", e); time.sleep(3)
        finally:
            if pool:
                pool.shutdown(wait=STOP.is_set())


if __name__ == "__main__":
    health.serve("worker", role="vlm-first: prepare → AI OCR all fields → Jev → Tesseract → check → look again"
                 if PIPELINE == "vlm-first" else "per page: enhance (2) → classify (3) → AI OCR (4) → check values (5)")
    run()
