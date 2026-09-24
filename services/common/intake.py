"""Phase 1 intake: one PDF → page images + page rows → one queue message per page.

n8n calls these two steps in order (see n8n/intake.workflow.json):
  split(batch)   render every page, store images, write scan_batch + page rows
  enqueue(batch) publish one ticket per page to q.pages
"""
import io
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pika
from PIL import Image
from pypdf import PdfReader

from . import db, queue, storage

RENDER_DPI = 300        # the scans are 300 dpi bilevel; this keeps every pixel
RENDER_WORKERS = 6
THUMB_WIDTH = 220


def batch_id_for(sha256: str) -> str:
    return "b-" + sha256[:10]


def page_key(batch_id, page_no, kind):
    ext = "jpg" if kind == "thumb" else "png"
    return f"pages/{batch_id}/{kind}/p{page_no:03d}.{ext}"


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


def split(batch_id, object_key, file_name, sha256, scanned_day):
    """Render all pages. Safe to call twice: the second call sees the batch exists and returns it."""
    with db.connect() as conn:
        existing = conn.execute("SELECT id, status, page_total FROM staging.scan_batch WHERE sha256 = %s",
                                (sha256,)).fetchone()
    if existing:
        return {"batch_id": existing["id"], "page_total": existing["page_total"],
                "status": existing["status"], "duplicate": True}

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "in.pdf")
        storage.client().fget_object(storage.bucket(), object_key, pdf_path)
        page_total = len(PdfReader(pdf_path).pages)

        with db.connect() as conn:
            conn.execute("""
                INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'splitting')""",
                (batch_id, file_name, object_key, sha256, scanned_day, page_total))
        try:
            with ThreadPoolExecutor(RENDER_WORKERS) as ex:
                list(ex.map(lambda n: _render_one(pdf_path, batch_id, n, tmp), range(1, page_total + 1)))
        except Exception as e:
            with db.connect() as conn:
                conn.execute("UPDATE staging.scan_batch SET status='failed', error=%s WHERE id=%s",
                             (f"{type(e).__name__}: {e}"[:500], batch_id))
            raise

    with db.connect() as conn:
        conn.execute("UPDATE staging.scan_batch SET status='split' WHERE id=%s", (batch_id,))
    return {"batch_id": batch_id, "page_total": page_total, "status": "split", "duplicate": False}


def enqueue(batch_id, only_rendered=False):
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
    return enqueue(batch_id, only_rendered=bool(pages))


def queue_depth(name=queue.Q_PAGES):
    mq = queue.connect()
    try:
        ch = mq.channel(); queue.declare(ch)
        return ch.queue_declare(queue=name, passive=True).method.message_count
    finally:
        mq.close()
