"""Stopping a file's AI work, and going on with it later (the user, 2026-10-15: "a stop button to stop a working batch,
but there should be a condition on where can we stop and when can we not stop").

A person stops a FILE (staging.scan_batch); the Batch page stops every file of its upload (api/actions.py). From then
on no new AI call is made for it:
  - its pages still waiting for a worker are marked stopped at once (status 'failed', error "Stopped on purpose by …",
    which api/stuck.py and the screens read as "Dihentikan"); a worker that takes their ticket drops it;
  - a page a worker is reading stops at its next AI call (vf.ai_call raises Stopped). A call already under way can't be
    cut off: it is paid for, and what it saved (the AI OCR copy, learned answers) is kept for later;
  - nothing sends its pages again by itself: not the sweep, an order's look-again, a label, or the learning step
    (vf.enqueue, vf.park, learn, app.vf_after_label);
  - a file not split yet is never split; a file being split finishes rendering (local, no AI) and is never queued.
Pages already read stay read, and grouping and checks (no AI) go on, so a complete order can still be reviewed and
sent. A sent order is never touched.

A file can be stopped while something of it runs or waits for the AI (`running`): not split yet, being split, pages
queued or being read, pages waiting for a look-again or for a failed call's next try. It can't when nothing runs (every
page read, nothing waiting), when it is already stopped, or when it couldn't be split (nothing runs). Going on: its
stopped pages go back on the queue under a new run, its waiting pages are sent, a file stopped before it was split is
split; refused while one of its pages is still finishing its last call (`finishing`)."""
from common import db

STOPPED = "stopped"
WHY = "Stopped on purpose"                  # api/stuck.py: "Dihentikan dengan sengaja"
NOT_SPLIT = ("received", "splitting")


class Stopped(BaseException):
    """Raised before an AI call for a page of a stopped file. A BaseException on purpose: the page flow turns every
    Exception from a call into "the call failed" (stored, and tried again); a stop is no failure, so it passes those
    by, up to vf.handle (and learn's loop), which mark the page stopped."""


def is_stopped(bid, conn=None):
    q = "SELECT status = %s AS stopped FROM staging.scan_batch WHERE id=%s"
    if conn is not None:
        r = conn.execute(q, (STOPPED, bid)).fetchone()
    else:
        with db.connect() as c:
            r = c.execute(q, (STOPPED, bid)).fetchone()
    return bool(r and r["stopped"])


def check(bid):
    """Before an AI call: raise Stopped when the page's file was stopped."""
    if is_stopped(bid):
        raise Stopped(bid)


def mark_page(bid, n, conn=None):
    """A page of a stopped file that won't be read now: shown as stopped (never a page some ticket already read)."""
    q = """UPDATE staging.page p SET status='failed',
                  error = %s || ' by ' || coalesce(s.stopped_by, 'someone') || ' at ' || to_char(s.stopped_at, 'YYYY-MM-DD HH24:MI')
             FROM staging.scan_batch s
            WHERE s.id = p.batch_id AND p.batch_id=%s AND p.page_no=%s AND p.status <> 'read' AND s.status = %s"""
    if conn is not None:
        conn.execute(q, (WHY, bid, n, STOPPED))
    else:
        with db.connect() as c:
            c.execute(q, (WHY, bid, n, STOPPED))


def finishing(c, bid):
    """The pages of a file a worker holds right now (vf.handle's page lock): being read, so they stop at their next
    AI call, or finish if they make none."""
    return sorted(r["page_no"] for r in c.execute(
        """SELECT objid::int AS page_no FROM pg_locks
            WHERE locktype = 'advisory' AND objsubid = 2 AND granted
              AND classid = (hashtext(%s)::bigint & 4294967295)::oid""", (f"page:{bid}",)))


RUNNING = """
SELECT count(*) FILTER (WHERE p.status IN ('queued', 'rendered')) AS waiting_worker,
       count(*) FILTER (WHERE p.status = 'read' AND (p.outcome = 'waiting_ai' OR p.extract_status = 'failed'
                          OR p.second_look->>'waiting' IS NOT NULL)) AS waiting_ai,
       count(*) FILTER (WHERE p.status <> 'read') AS unread,
       count(*) AS pages
  FROM staging.page p WHERE p.batch_id = %s"""


def running(c, scan):
    """Pure-ish: what of this file still runs or waits for the AI, {why: count}; empty when nothing does. scan: its
    staging.scan_batch row."""
    if scan["status"] in NOT_SPLIT:
        return {"not_split": 1}
    if scan["status"] in ("failed", STOPPED):
        return {}
    r = c.execute(RUNNING, (scan["id"],)).fetchone()
    out = {"waiting_worker": r["waiting_worker"], "waiting_ai": r["waiting_ai"],
           "reading": len(finishing(c, scan["id"]))}
    return {k: v for k, v in out.items() if v}


def why_not(scan, run):
    """Pure: why a file can't be stopped now (for people), else None."""
    if scan["status"] == STOPPED:
        return "File ini sudah dihentikan."
    if scan["status"] == "failed":
        return "File ini gagal dipecah per halaman: tidak ada yang sedang berjalan."
    if not run:
        return "Tidak ada yang sedang berjalan: semua halaman file ini sudah dibaca."
    return None


def stop(bid, by):
    """Stop one file (see the module). Returns {batch_id, stopped: pages marked now, finishing: pages a worker holds,
    waiting_ai: pages whose look-again won't be sent}, or raises Refused (why)."""
    from common import trace
    with db.connect() as c:
        s = c.execute("SELECT * FROM staging.scan_batch WHERE id=%s FOR UPDATE", (bid,)).fetchone()
        if not s:
            raise Refused("File itu tidak ada.", 404)
        run = running(c, s)
        why = why_not(s, run)
        if why:
            raise Refused(why)
        c.execute("""UPDATE staging.scan_batch SET stopped_from=status, status=%s, stopped_at=now(), stopped_by=%s
                      WHERE id=%s""", (STOPPED, by.strip(), bid))
        held = finishing(c, bid)
        marked = [r["page_no"] for r in c.execute(
            """UPDATE staging.page p SET status='failed',
                      error = %s || ' by ' || %s || ' at ' || to_char(now(), 'YYYY-MM-DD HH24:MI')
                WHERE p.batch_id=%s AND p.status IN ('queued', 'rendered') AND NOT (p.page_no = ANY(%s))
            RETURNING p.page_no""", (WHY, by.strip(), bid, held))]
    out = {"batch_id": bid, "was": s["status"], "stopped": sorted(marked), "finishing": held,
           "waiting_ai": run.get("waiting_ai", 0)}
    trace.event("file.stopped", "info", batch=bid, who=by.strip(), was=s["status"], pages_stopped=len(marked),
                finishing=held or None, waiting_ai=out["waiting_ai"] or None)
    return out


def resume_plan(bid):
    """What going on with a stopped file means, without doing it: {from, split: split it again, pages: its stopped
    pages, finishing}. Raises Refused when it isn't stopped, or while a page still finishes its last call."""
    with db.connect() as c:
        s = c.execute("SELECT * FROM staging.scan_batch WHERE id=%s", (bid,)).fetchone()
        if not s:
            raise Refused("File itu tidak ada.", 404)
        if s["status"] != STOPPED:
            raise Refused("File ini tidak dihentikan.")
        held = finishing(c, bid)
        if held:
            raise Refused(f"{len(held)} halaman masih menyelesaikan panggilan AI terakhirnya. Coba lagi sebentar lagi.")
        pages = [r["page_no"] for r in c.execute(
            """SELECT page_no FROM staging.page WHERE batch_id=%s AND status <> 'read' ORDER BY page_no""", (bid,))]
        unread_all = not c.execute("SELECT 1 FROM staging.page WHERE batch_id=%s AND status='read' LIMIT 1",
                                   (bid,)).fetchone()
    return {"from": s["stopped_from"], "split": s["stopped_from"] in NOT_SPLIT or (unread_all and not pages),
            "pages": pages}


def unstop(bid, status, conn=None):
    """Clear the stop: the file's status becomes `status` again (only while it is stopped). True when it was."""
    q = """UPDATE staging.scan_batch SET status=%s, stopped_at=NULL, stopped_by=NULL, stopped_from=NULL
            WHERE id=%s AND status=%s RETURNING id"""
    if conn is not None:
        return bool(conn.execute(q, (status, bid, STOPPED)).fetchone())
    with db.connect() as c:
        return bool(c.execute(q, (status, bid, STOPPED)).fetchone())


class Refused(Exception):
    def __init__(self, why, status=409):
        super().__init__(why)
        self.why, self.status = why, status
