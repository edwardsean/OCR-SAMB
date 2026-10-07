"""Upload batches (2026-10-05). The user: "for an upload (which can be a lot of documents) we should input the
uploader's name, date, and a generated batch number, so that in view we can see the separated processes per batch".

One upload action is one batch, however many files: BATCH-20261005-01 (the day it was made in WIB, then a counter for
that day), who uploaded it, and the date they give (the day the papers were scanned: every file's scanned_day, which
the date checks use). Each file is a scan (staging.scan_batch) of its batch. Screens show and filter by batch.
"""
from datetime import date, datetime, timedelta, timezone

from . import db

WIB = timezone(timedelta(hours=7))
UNKNOWN = "(tidak tercatat)"      # scans uploaded before batches existed: nobody typed a name
GAP = timedelta(minutes=10)       # backfill: scans that arrived this close together were one upload


def _code(c, day):
    """The next number for that day: BATCH-YYYYMMDD-NN. Serialised so two uploads at once never share a number."""
    c.execute("SELECT pg_advisory_xact_lock(hashtext('upload-code'))")
    prefix = f"BATCH-{day:%Y%m%d}-"
    n = c.execute("SELECT count(*) AS n FROM staging.upload WHERE code LIKE %s", (prefix + "%",)).fetchone()["n"]
    return f"{prefix}{n + 1:02d}"


def create(by, doc_date=None, note=None):
    """A new batch: {id, code, uploaded_by, doc_date, created_at}. by is required; doc_date defaults to today (WIB)."""
    by = (by or "").strip()
    if not by:
        raise ValueError("say who uploads")
    today = datetime.now(WIB).date()
    d = date.fromisoformat(str(doc_date)) if doc_date else today
    if d > today:
        raise ValueError("the scan date can't be in the future")
    with db.connect() as c:
        return dict(c.execute("""INSERT INTO staging.upload (code, uploaded_by, doc_date, note) VALUES (%s, %s, %s, %s)
                                 RETURNING id, code, uploaded_by, doc_date, created_at""",
                              (_code(c, today), by, d, (note or "").strip() or None)).fetchone())


def get(c, upload_id):
    r = c.execute("SELECT * FROM staging.upload WHERE id=%s", (upload_id,)).fetchone()
    return dict(r) if r else None


def listing(c, limit=100):
    """Every batch, newest first, with its files, pages and how far reading has come."""
    return [dict(r) for r in c.execute(
        """SELECT u.id, u.code, u.uploaded_by, u.doc_date, u.created_at, u.note,
                  count(s.id) AS files, coalesce(sum(s.page_total), 0) AS pages,
                  coalesce(sum(s.page_done), 0) AS read,
                  count(s.id) FILTER (WHERE s.status IN ('received', 'splitting', 'queued', 'reading')) AS busy
             FROM staging.upload u LEFT JOIN staging.scan_batch s ON s.upload_id = u.id
            GROUP BY u.id ORDER BY u.created_at DESC, u.id DESC LIMIT %s""", (limit,))]


def scans_of(c, upload_id):
    return [r["id"] for r in c.execute("SELECT id FROM staging.scan_batch WHERE upload_id=%s", (upload_id,))]


def of_scans(c, batch_ids):
    """{scan id: {id, code, uploaded_by, doc_date}} for these scans (the batch each belongs to)."""
    return {r["scan"]: {k: r[k] for k in ("id", "code", "uploaded_by", "doc_date")} for r in c.execute(
        """SELECT s.id AS scan, u.id, u.code, u.uploaded_by, u.doc_date FROM staging.scan_batch s
             JOIN staging.upload u ON u.id = s.upload_id WHERE s.id = ANY(%s)""", (list(batch_ids),))}


def backfill():
    """Scans from before batches existed get one: those that arrived within GAP of each other were one upload. The
    uploader wasn't recorded; the date is their scan day. Returns the batches made."""
    made = []
    with db.connect() as c:
        rows = c.execute("""SELECT id, received_at, scanned_day FROM staging.scan_batch WHERE upload_id IS NULL
                             ORDER BY received_at, id""").fetchall()
        group, last = [], None
        for r in rows + [None]:
            if r is None or (last is not None and r["received_at"] - last > GAP):
                if group:
                    day = group[0]["received_at"].astimezone(WIB).date()
                    u = c.execute("""INSERT INTO staging.upload (code, uploaded_by, doc_date, created_at, note)
                                     VALUES (%s, %s, %s, %s, 'dibuat untuk scan yang diunggah sebelum ada batch')
                                     RETURNING id, code""",
                                  (_code(c, day), UNKNOWN, min(g["scanned_day"] for g in group),
                                   group[0]["received_at"])).fetchone()
                    c.execute("UPDATE staging.scan_batch SET upload_id=%s WHERE id = ANY(%s)",
                              (u["id"], [g["id"] for g in group]))
                    made.append((u["code"], len(group)))
                group = []
            if r is not None:
                group.append(r)
                last = r["received_at"]
    return made


if __name__ == "__main__":
    for code, n in backfill():
        print(code, n, "scans")
