"""The work behind the screens without n8n (the mentor, 2026-10-02: "n8n banyak issue klo recordsnya udah seribuan dan
multiple worker"): an upload goes on q.intake for the intake worker, and the periodic jobs run in the scheduler, each
once per interval however many schedulers run. Nothing here calls an AI or puts a page on q.pages."""
import io
import os

import pytest

from common import db, intake, notice, queue

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
TEST_BATCH = "b-test-intake"


def _pdf(text="TEST"):
    """A one-page PDF made here: never a customer's document."""
    from PIL import Image, ImageDraw
    im = Image.new("L", (850, 1100), 255)
    ImageDraw.Draw(im).text((100, 100), text, fill=0)
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=100)
    return buf.getvalue()


@pytest.fixture
def scan():
    """A throwaway scan row (and its pages and stored files), removed afterwards."""
    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (TEST_BATCH,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s OR file_name='test-intake.pdf'", (TEST_BATCH,))
    clean()
    yield TEST_BATCH
    from common import storage
    cl = storage.client()
    for prefix in (f"{intake.PREFIX}pages/{TEST_BATCH}/", f"{intake.PREFIX}scans/"):
        for o in cl.list_objects(storage.bucket(), prefix=prefix, recursive=True):
            if TEST_BATCH in o.object_name or o.object_name.endswith("/test-intake.pdf"):
                cl.remove_object(storage.bucket(), o.object_name)
    clean()


# ------------------------------------------------------------------------------------------------ the intake

def test_an_upload_is_recorded_at_once_and_queued_once(scan, monkeypatch):
    sent = []
    monkeypatch.setattr(queue, "send", lambda q, m: sent.append((q, m)))
    data = _pdf("an upload")
    first = intake.receive(data, "test-intake.pdf")
    bid = first["batch_id"]
    with db.connect() as c:
        row = c.execute("SELECT status, page_total FROM staging.scan_batch WHERE id=%s", (bid,)).fetchone()
        c.execute("UPDATE staging.scan_batch SET id=%s WHERE id=%s", (TEST_BATCH, bid))   # the fixture cleans it
    assert row == {"status": "received", "page_total": 1}                  # the scan exists before any worker
    assert sent == [(queue.Q_INTAKE, [{"batch_id": bid}])]
    again = intake.receive(data, "test-intake.pdf")
    assert again["duplicate"] and again["batch_id"] == TEST_BATCH and len(sent) == 1   # the same file: no second ticket


def test_a_file_that_isnt_a_readable_pdf_is_refused(monkeypatch):
    monkeypatch.setattr(queue, "send", lambda q, m: pytest.fail("nothing is queued"))
    with pytest.raises(intake.NotReadable):
        intake.receive(b"%PDF-1.4 but nothing after", "broken.pdf")


def _received(c, bid, minutes_ago=0):
    c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status,
                                                 received_at)
                 VALUES (%s, 'test-intake.pdf', 'nowhere.pdf', repeat('7', 64), current_date, 1, 'received',
                         now() - make_interval(mins => %s))""", (bid, minutes_ago))


def test_a_scan_is_split_by_one_worker_at_a_time(scan):
    with db.connect() as c:
        _received(c, scan)
    with db.connect() as other:                                            # another worker is splitting it
        other.execute("SELECT pg_advisory_lock(hashtext(%s))", ("intake:" + scan,))
        assert intake.split(scan)["busy"] is True
    with db.connect() as c:
        c.execute("UPDATE staging.scan_batch SET status='queued' WHERE id=%s", (scan,))
    assert intake.split(scan) == {"batch_id": scan, "status": "queued"}   # past this step: left alone


def test_a_scan_is_split_into_its_pages(scan, monkeypatch):
    sent = []
    monkeypatch.setattr(queue, "send", lambda q, m: sent.append(q))
    bid = intake.receive(_pdf("split me"), "test-intake.pdf")["batch_id"]
    with db.connect() as c:                                                # the fixture's id, so it is cleaned
        c.execute("UPDATE staging.scan_batch SET id=%s WHERE id=%s", (TEST_BATCH, bid))
    out = intake.split(TEST_BATCH)
    assert out == {"batch_id": TEST_BATCH, "page_total": 1, "status": "split"}
    with db.connect() as c:
        pages = c.execute("SELECT page_no, status FROM staging.page WHERE batch_id=%s", (TEST_BATCH,)).fetchall()
        b = c.execute("SELECT status, pages_rendered FROM staging.scan_batch WHERE id=%s", (TEST_BATCH,)).fetchone()
    assert pages == [{"page_no": 1, "status": "rendered"}] and b == {"status": "split", "pages_rendered": 1}


def test_a_scan_never_taken_goes_back_on_the_queue(scan):
    with db.connect() as c:
        _received(c, scan, minutes_ago=30)
    assert scan in intake.waiting(10) and scan not in intake.waiting(60)


# ------------------------------------------------------------------------------------------------ the scheduler

def test_a_job_runs_once_per_interval_however_many_schedulers():
    from scheduler import serve
    name = "test-job"
    try:
        assert serve.claim(name, 60) is True
        assert serve.claim(name, 60) is False                              # a second scheduler, a moment later
        with db.connect() as c:                                            # an hour on: due again
            c.execute("UPDATE staging.job_run SET last_started = now() - interval '61 minutes' WHERE name=%s", (name,))
        assert serve.claim(name, 60) is True
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.job_run WHERE name=%s", (name,))


def test_a_failing_job_is_recorded_and_never_stops_the_others(monkeypatch):
    from scheduler import serve
    def boom():
        raise RuntimeError("the database went away")
    monkeypatch.setattr(serve, "JOBS", {"test-boom": (lambda: 60, boom, False), "test-ok": (lambda: 60, lambda: {"done": 1}, False)})
    try:
        ran = serve.tick()
        assert ran["test-ok"] == {"done": 1} and "the database went away" in ran["test-boom"]["error"]
        with db.connect() as c:
            rows = {r["name"]: r for r in c.execute("SELECT * FROM staging.job_run WHERE name LIKE 'test-%'")}
        assert rows["test-boom"]["last_error"].startswith("RuntimeError") and rows["test-ok"]["last_result"] == {"done": 1}
        assert serve.tick() == {}                                          # not due again for an hour
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.job_run WHERE name LIKE 'test-%'")


# ------------------------------------------------------------------------------------------------ the notices

def test_a_bundle_is_new_once_per_fingerprint():
    a = {"batch": "b", "sor": "SOR1", "fingerprint": "f1", "customer": "AEON EASTVARA TANGERANG", "reasons": 3}
    b = {**a, "sor": "SOR2", "fingerprint": None, "customer": None}
    assert notice.new_items([a, b], set()) == [a, b]
    assert notice.new_items([a, b], {("SOR1", "f1"), ("SOR2", "")}) == []
    assert notice.new_items([{**a, "fingerprint": "f2"}], {("SOR1", "f1")})[0]["fingerprint"] == "f2"   # changed since
    assert notice.text_of([a, b]) == "2 bundles need you: AEON EASTVARA TANGERANG (SOR1), Unknown customer (SOR2)"
    assert notice.text_of([a]).startswith("1 bundle needs you:")


def test_a_notice_is_recorded_once():
    """On the real bundles, inside a transaction that is rolled back: the second call finds nothing new."""
    c = db.connect()
    try:
        c.execute("DELETE FROM staging.notice")          # rolled back below
        first = notice.record(c)
        with db.connect() as other:
            needing = other.execute("SELECT count(*) AS n FROM staging.bundle WHERE status='needs_review' "
                                    "AND sor_no IS NOT NULL").fetchone()["n"]
        assert (first is not None) == (needing > 0)
        assert notice.record(c) is None
        if first:
            assert len(notice.unseen(c)) == needing
            notice.mark_seen(c)
            assert notice.unseen(c) == []
    finally:
        c.rollback()
        c.close()


def test_the_sweep_sends_nothing_while_the_ai_is_refused(monkeypatch):
    from common import queue
    from worker import vf
    sent = []
    monkeypatch.setattr(queue, "send", lambda q, m: sent.append(q))
    monkeypatch.setattr(vf, "blocked", lambda: "DailyLimit: 40 min left")
    assert vf.sweep() == {"skipped": "DailyLimit: 40 min left"} and sent == []


def test_fetching_review_never_marks_a_notice_seen():
    """Only a browser showing Periksa order marks notices seen (its POST /api/v1/notices/seen after load): the screen
    tests once marked n8n's first notice seen before anyone had looked."""
    import httpx
    api = os.environ.get("API_URL", "http://localhost:8000")
    with db.connect() as c:
        nid = c.execute("INSERT INTO staging.notice (items, text) VALUES ('[]', 'test: never seen by a fetch') "
                        "RETURNING id").fetchone()["id"]
    try:
        assert httpx.get(f"{api}/api/v1/orders", timeout=60).status_code == 200
        with db.connect() as c:
            assert c.execute("SELECT seen_at FROM staging.notice WHERE id=%s", (nid,)).fetchone()["seen_at"] is None
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.notice WHERE id=%s", (nid,))
