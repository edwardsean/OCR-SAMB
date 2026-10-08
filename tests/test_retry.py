"""Trying stuck work again (the user, 2026-10-08: "if a workflow fails, there is no retry button … if the data is
published to satellite already, it cant retry"). api/stuck.py says what is stuck and why; api/actions.py's retry_page,
retry_scan and retry_upload try it, never for an order already sent to Satellite, never while it is being tried."""
import os

import pytest

from api import stuck

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
BID, SOR, CODE = "test-retry", "SORTEST-RETRY", "TEST-RETRY"


def test_what_is_stuck():
    assert stuck.kind("dead_letter", None, None) == stuck.kind("failed", None, None) == "crashed"
    assert stuck.kind("read", "failed", None) == "call_failed"                         # the reading kept failing
    assert stuck.kind("read", "done", "the call failed: ReadTimeout") == "call_failed"  # its look-again did
    assert stuck.kind("read", "done", "new questions for the look-again: total") is None   # waits by itself
    assert stuck.kind("queued", "failed", None) is None                                 # being tried: not stuck
    assert stuck.kind("read", "done", None) is None


def test_why_in_plain_words():
    assert stuck.cause("RuntimeError: dashscope:x: HTTP 400: MissingSessionID")[0] == "setting"   # the mentor's case
    assert stuck.cause("RuntimeError: x: the endpoint refused the API key (HTTP 401)")[0] == "setting"
    assert stuck.cause("NotSet: the vision model not set")[0] == "setting"
    assert stuck.cause("ReadTimeout: The read operation timed out")[0] == "connection"
    assert stuck.cause("RuntimeError: x: unavailable after retries (last HTTP 503)")[0] == "connection"
    assert stuck.cause("JSONDecodeError: Expecting value")[0] == "answer"
    assert stuck.cause("ValueError: something else") == ("other", "Halaman ini gagal dibaca.")
    v = stuck.view({"status": "read", "extract_status": "failed", "extract_error": "ReadTimeout: x", "published": True})
    assert v["kind"] == "call_failed" and v["published"] and not v["can_retry"]         # sent: never again
    assert stuck.view({"status": "dead_letter", "error": None})["reason"].startswith("Pemrosesan")
    assert "lanjut sendiri" in stuck.blocked_text("NotSet: the vision model not set: set it on …")
    assert "45 menit" in stuck.blocked_text("DailyLimit: 45 min left of x's refusal") and stuck.blocked_text(None) is None


@pytest.fixture
def pages(monkeypatch):
    """Pages of one temporary file: 1 a reading that kept failing, 2 a dead page, 3 read fine, 4 being tried, 5 a
    reading that kept failing on an order already sent to Satellite. Nothing is really sent to a queue."""
    from common import db, intake, queue
    from worker import vf
    from api import actions

    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.bundle_document WHERE bundle_id IN (SELECT id FROM staging.bundle WHERE sor_no=%s)", (SOR,))
            c.execute("DELETE FROM staging.bundle WHERE sor_no=%s", (SOR,))
            c.execute("DELETE FROM staging.document WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
            c.execute("DELETE FROM staging.upload WHERE code=%s", (CODE,))
    clean()
    with db.connect() as c:
        up = c.execute("""INSERT INTO staging.upload (code, uploaded_by, doc_date) VALUES (%s, 'test', current_date)
                          RETURNING id""", (CODE,)).fetchone()["id"]
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status, upload_id)
                     VALUES (%s,'t.pdf','t',repeat('7',64),current_date,5,'read',%s)""", (BID, up))
        for n, status, extract, err in ((1, "read", "failed", "ReadTimeout: The read operation timed out"),
                                        (2, "dead_letter", None, None), (3, "read", "done", None),
                                        (4, "queued", None, None), (5, "read", "failed", "ReadTimeout: x")):
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, extract_status, extract_error)
                         VALUES (%s,%s,%s,%s,%s,%s)""", (BID, n, f"rtm/pages/{BID}/p{n}.png", status, extract, err))
        doc = c.execute("""INSERT INTO staging.document (batch_id, doc_type, page_from, page_to) VALUES (%s,'PO',5,5)
                           RETURNING id""", (BID,)).fetchone()["id"]
        b = c.execute("INSERT INTO staging.bundle (sor_no, status) VALUES (%s,'published') RETURNING id", (SOR,)).fetchone()["id"]
        c.execute("INSERT INTO staging.bundle_document (bundle_id, document_id) VALUES (%s,%s)", (b, doc))
    done = []
    monkeypatch.setattr(actions, "_not_now", lambda: None)
    monkeypatch.setattr(intake, "rerun", lambda bid, ns: done.append(("rerun", bid, list(ns))))
    monkeypatch.setattr(vf, "again", lambda bid, ns: done.append(("again", bid, list(ns))) or list(ns))
    monkeypatch.setattr(queue, "send", lambda q, msgs: done.append(("send", q, msgs)))
    yield up, done
    clean()


@needs_db
def test_a_stuck_page_is_tried_again_and_only_a_stuck_one(pages):
    from api import actions
    _, done = pages
    actions.retry_page(BID, 1, "tester")                         # a call kept failing: back as it is (vf.again)
    with pytest.raises(actions.ActionError, match="masih dibaca"):
        actions.retry_page(BID, 2, "tester")                     # dead, but page 4 of its file is being read
    for page, why in ((3, "tidak gagal"), (4, "sedang dibaca"), (5, "sudah dikirim ke Satellite")):
        with pytest.raises(actions.ActionError, match=why) as e:
            actions.retry_page(BID, page, "tester")
        assert e.value.status == 409
    assert done == [("again", BID, [1])]


@needs_db
def test_nothing_is_tried_while_a_model_isnt_set(pages, monkeypatch):
    from api import actions
    _, done = pages
    monkeypatch.setattr(actions, "_not_now", lambda: "Model belum diatur: halaman menunggu …")
    with pytest.raises(actions.ActionError, match="Model belum diatur"):
        actions.retry_page(BID, 1, "tester")
    assert done == []


@needs_db
def test_a_file_that_couldnt_be_split_is_split_again(pages):
    from api import actions
    from common import db, queue
    _, done = pages
    with pytest.raises(actions.ActionError, match="tidak gagal diproses"):
        actions.retry_scan(BID, "tester")
    with db.connect() as c:
        c.execute("UPDATE staging.scan_batch SET status='failed', error='OSError: broken PDF' WHERE id=%s", (BID,))
    actions.retry_scan(BID, "tester")
    assert done == [("send", queue.Q_INTAKE, [{"batch_id": BID}])]
    with db.connect() as c:
        r = c.execute("SELECT status::text AS s, error FROM staging.scan_batch WHERE id=%s", (BID,)).fetchone()
    assert (r["s"], r["error"]) == ("received", None)


@needs_db
def test_try_everything_skips_what_was_sent_and_says_why(pages):
    from api import actions
    from common import db
    up, done = pages
    with db.connect() as c:
        c.execute("UPDATE staging.page SET status='read' WHERE batch_id=%s AND page_no=4", (BID,))   # no longer busy
    r = actions.retry_upload(up, "tester")
    assert r["pages"] == 2 and r["files"] == 0
    assert r["left"] == [{"batch_id": BID, "page_no": 5, "why": "sudah dikirim ke Satellite"}]
    assert ("rerun", BID, [2]) in done and ("again", BID, [1]) in done


@needs_db
def test_the_batch_shows_what_is_stuck_why_and_whether_it_may_be_tried(pages):
    from api import app
    up, _ = pages
    v = app.upload_view(up)
    stuck_pages = {p["page_no"]: p for p in v["failed"]}
    assert sorted(stuck_pages) == [1, 2, 5]
    assert stuck_pages[1]["cause"] == "connection" and stuck_pages[1]["can_retry"]
    assert stuck_pages[5]["published"] and not stuck_pages[5]["can_retry"]
    assert v["failed_files"] == [] and "not_now" in v
    baca = v["steps"][0]
    assert baca["failed"] == 3 and baca["state"] == "need"        # stuck pages make step 1 red, not "running"


@needs_db
def test_the_automatic_retry_never_reads_a_sent_orders_page_again(pages):
    from worker import vf
    assert vf.waiting(BID) == [1]            # 1 kept failing; 5 too, but its order is already sent to Satellite
