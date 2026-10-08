"""Intake: one uploaded PDF → page images + page rows → one queue message per page. No n8n (the mentor, 2026-10-02:
n8n struggles with thousands of records and several workers): RabbitMQ carries the work.

  receive(data, file_name)   the upload: store the PDF, record the scan ('received'), put it on q.intake
  split(batch_id)            the intake worker (intake/serve.py): render every page, write the page rows ('split')
  enqueue(batch_id)          then one ticket per page on q.pages ('queued')
  waiting(minutes)           scans received but not split after a while: the scheduler puts them back on q.intake

Each step is safe to repeat: a scan is split by one worker at a time (a lock per scan), only 'received' (or a split a
worker died in) is split, and only the call that moves 'split' → 'queued' publishes the pages.
"""
import hashlib
import io
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pika
from PIL import Image
from pypdf import PdfReader

from . import config, db, queue, storage, trace

RENDER_DPI = 300        # the scans are 300 dpi bilevel; this keeps every pixel
RENDER_WORKERS = config.RENDER_WORKERS
THUMB_WIDTH = 220
PREFIX = config.STORAGE_PREFIX                       # "" for v1; "vf/" for the vlm-first experiment


def batch_id_for(sha256: str) -> str:
    return "b-" + sha256[:10]


def page_key(batch_id, page_no, kind):
    ext = "jpg" if kind == "thumb" else "png"
    return f"{PREFIX}pages/{batch_id}/{kind}/p{page_no:03d}.{ext}"


def _render_one(pdf_path, batch_id, page_no, tmpdir):
    out = os.path.join(tmpdir, f"p{page_no:03d}")
    subprocess.run(["pdftoppm", "-r", str(RENDER_DPI), "-png", "-mono", "-singlefile",
                    "-f", str(page_no), "-l", str(page_no), pdf_path, out],
                   check=True, capture_output=True)
    png = out + ".png"
    im = Image.open(png).convert("L")
    im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=70)

    c = storage.client()
    orig_key, thumb_key = page_key(batch_id, page_no, "original"), page_key(batch_id, page_no, "thumb")
    c.fput_object(storage.bucket(), orig_key, png, content_type="image/png")
    c.put_object(storage.bucket(), thumb_key, io.BytesIO(buf.getvalue()), len(buf.getvalue()), content_type="image/jpeg")
    os.remove(png)

    with db.connect() as conn:
        conn.execute("""
            INSERT INTO staging.page (batch_id, page_no, image_path, original_path, thumb_path, status)
            VALUES (%s, %s, %s, %s, %s, 'rendered')
            ON CONFLICT (batch_id, page_no) DO NOTHING""",
            (batch_id, page_no, orig_key, orig_key, thumb_key))
        conn.execute("UPDATE staging.scan_batch SET pages_rendered = pages_rendered + 1 WHERE id = %s", (batch_id,))
    return page_no


WIB = timezone(timedelta(hours=7))                   # the scans' day is Jakarta's


class NotReadable(ValueError):
    """The file says it is a PDF but its pages can't be read."""


def receive(data, file_name, upload=None):
    """The upload: the PDF stored under scans/<day>/<batch>/, the scan recorded as 'received', a ticket on q.intake.
    upload: the upload batch it belongs to ({id, doc_date}, common/uploads.py): the scan joins it and takes its scan
    date. Returns {"batch_id", "duplicate": False}, or the earlier scan with "duplicate": True when the same file (by
    its SHA-256) came before. A ticket that can't be sent now is sent by the scheduler later (waiting): the scan is
    recorded either way."""
    sha = hashlib.sha256(data).hexdigest()
    with db.connect() as conn:
        dup = conn.execute("SELECT id, file_name, received_at FROM staging.scan_batch WHERE sha256=%s", (sha,)).fetchone()
    if dup:
        return {"batch_id": dup["id"], "duplicate": True, "earlier": dict(dup)}
    try:
        page_total = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        raise NotReadable(f"{type(e).__name__}: {e}") from e
    if not page_total:
        raise NotReadable("the PDF has no pages")
    batch_id, day = batch_id_for(sha), datetime.now(WIB).date().isoformat()
    key = f"{PREFIX}scans/{day}/{batch_id}/{file_name}"
    storage.ensure_bucket().put_object(storage.bucket(), key, io.BytesIO(data), len(data), content_type="application/pdf")
    scanned = str((upload or {}).get("doc_date") or day)     # the day the papers were scanned, as the uploader said
    with db.connect() as conn:
        row = conn.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total,
                                                              status, upload_id)
                              VALUES (%s, %s, %s, %s, %s, %s, 'received', %s) ON CONFLICT DO NOTHING RETURNING id""",
                           (batch_id, file_name, key, sha, scanned, page_total, (upload or {}).get("id"))).fetchone()
    if not row:                                         # the same file, uploaded twice at the same moment
        with db.connect() as conn:
            dup = conn.execute("SELECT id, file_name, received_at FROM staging.scan_batch WHERE sha256=%s",
                               (sha,)).fetchone()
        if not dup:
            raise RuntimeError(f"{batch_id} is taken by another file")   # two files sharing 10 hex digits of SHA-256
        return {"batch_id": dup["id"], "duplicate": True, "earlier": dict(dup)}
    trace.event("file.received", batch=batch_id, file=file_name, pages=page_total, kb=len(data) // 1024,
                upload=(upload or {}).get("id"))
    try:
        queue.send(queue.Q_INTAKE, [{"batch_id": batch_id}])
    except Exception as e:                              # recorded: the scheduler sends it later
        print(f"{batch_id}: received, not queued yet ({type(e).__name__}: {e})", flush=True)
    return {"batch_id": batch_id, "duplicate": False}


def split(batch_id):
    """Render all pages of a received scan. One worker at a time per scan (an advisory lock held while it renders,
    released by itself if the worker dies): a second ticket for the scan finds it busy, or past this step, and does
    nothing. A split a worker died in starts over."""
    with db.connect() as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", ("intake:" + batch_id,)).fetchone()["ok"]:
            return {"batch_id": batch_id, "status": "splitting", "busy": True}
        lock.commit()                                   # the lock is the connection's: it lasts until it closes
        with db.connect() as conn:
            row = conn.execute("""UPDATE staging.scan_batch SET status='splitting', pages_rendered=0, error=NULL
                                   WHERE id=%s AND status IN ('received', 'splitting')
                               RETURNING file_path, page_total""", (batch_id,)).fetchone()
            if not row:
                cur = conn.execute("SELECT status FROM staging.scan_batch WHERE id=%s", (batch_id,)).fetchone()
                return {"batch_id": batch_id, "status": cur and cur["status"]}
            conn.execute("DELETE FROM staging.page WHERE batch_id=%s", (batch_id,))   # what a dead split left
        page_total = row["page_total"]
        with tempfile.TemporaryDirectory() as tmp, trace.span("file.split", batch=batch_id, pages=page_total):
            pdf_path = os.path.join(tmp, "in.pdf")
            try:
                storage.client().fget_object(storage.bucket(), row["file_path"], pdf_path)
                with ThreadPoolExecutor(RENDER_WORKERS) as ex:
                    list(ex.map(lambda n: _render_one(pdf_path, batch_id, n, tmp), range(1, page_total + 1)))
            except Exception as e:
                with db.connect() as conn:
                    conn.execute("UPDATE staging.scan_batch SET status='failed', error=%s WHERE id=%s",
                                 (f"{type(e).__name__}: {e}"[:500], batch_id))
                raise
        with db.connect() as conn:
            conn.execute("UPDATE staging.scan_batch SET status='split' WHERE id=%s", (batch_id,))
    return {"batch_id": batch_id, "page_total": page_total, "status": "split"}


def waiting(minutes):
    """Scans received (or being split) longer than `minutes` ago and not split yet: their ticket was never sent, or
    its worker died. Putting them back on q.intake is harmless: a scan being split is busy, a split one is past it."""
    with db.connect() as conn:
        return [r["id"] for r in conn.execute(
            """SELECT id FROM staging.scan_batch WHERE status IN ('received', 'splitting')
                 AND received_at < now() - make_interval(mins => %s) ORDER BY received_at""", (minutes,))]


def enqueue(batch_id, only_rendered=False, why="new"):
    """Publish one ticket per page. Only the call that moves split → queued publishes, so a retry can't double-queue."""
    with db.connect() as conn:
        row = conn.execute("""UPDATE staging.scan_batch SET status='queued'
                              WHERE id=%s AND status='split' RETURNING page_total, file_path, run""",
                           (batch_id,)).fetchone()
        if not row:
            cur = conn.execute("SELECT status FROM staging.scan_batch WHERE id=%s", (batch_id,)).fetchone()
            return {"batch_id": batch_id, "published": 0, "status": cur and cur["status"]}
        pages = conn.execute("SELECT page_no, image_path, original_path FROM staging.page WHERE batch_id=%s "
                             + ("AND status='rendered' " if only_rendered else "") + "ORDER BY page_no", (batch_id,)).fetchall()

    mq = queue.connect()
    try:
        ch = mq.channel()
        queue.declare(ch)
        ch.confirm_delivery()
        for p in pages:
            ticket = {"batch_id": batch_id, "run": row["run"], "page_no": p["page_no"],
                      "image_key": p["original_path"] or p["image_path"], "pdf_key": row["file_path"]}
            ch.basic_publish("", queue.Q_PAGES, json.dumps(ticket).encode(),
                             pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        mq.close()

    with db.connect() as conn:
        conn.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND status='rendered'", (batch_id,))
    trace.events("page.queued", [{"batch": batch_id, "page": p["page_no"]} for p in pages], why=why)
    return {"batch_id": batch_id, "published": len(pages), "status": "queued"}


def rerun(batch_id, pages=None):
    """Put pages back on q.pages (all, or only `pages`) under a new run number. Pages not listed stay 'read',
    so the scoreboard starts from them and the bell rings once the listed pages are done."""
    with db.connect() as conn:
        conn.execute("UPDATE staging.scan_batch SET status='split', page_done=0, run=run+1 WHERE id=%s", (batch_id,))
        if pages:
            conn.execute("UPDATE staging.page SET status='rendered', error=NULL WHERE batch_id=%s AND page_no = ANY(%s)",
                         (batch_id, list(pages)))
        else:
            conn.execute("UPDATE staging.page SET status='rendered', error=NULL WHERE batch_id=%s", (batch_id,))
    return enqueue(batch_id, only_rendered=bool(pages), why="tried again")


def queue_depth(name=queue.Q_PAGES):
    mq = queue.connect()
    try:
        ch = mq.channel(); queue.declare(ch)
        return ch.queue_declare(queue=name, passive=True).method.message_count
    finally:
        mq.close()
