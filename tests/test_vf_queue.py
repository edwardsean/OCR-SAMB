"""vlm-first on the queue (Stage 1, the user 2026-09-29: "we should be using RabbitMQ queues and multiple workers"):
several page workers share the AI's daily cap without overspending it; a call the daily limit stopped waits in
q.pages.wait and comes back (never counted as a failure), a failed call is tried 3 times; vf-grouper takes the
wake-ups waiting together as one round per batch and sends back only the pages a bundle asked to look again."""
import os
import threading

import pytest

from common import db
from grouper import serve
from grouper.crosscheck import ASK_WAIT

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
PROVIDER = "test-queue"
BID = "test-vf-queue"


SPEC = PROVIDER + ":vision"


@pytest.fixture
def vf(monkeypatch):
    from worker import vf
    monkeypatch.setattr(vf, "AI_OCR", SPEC)
    monkeypatch.setattr(vf, "AI_MAP", SPEC)
    monkeypatch.setattr(vf, "CAPS", {SPEC: 150})
    yield vf
    with db.connect() as c:
        c.execute("DELETE FROM staging.model_call WHERE provider=%s", (PROVIDER,))


def test_workers_side_by_side_never_spend_past_the_cap(vf, monkeypatch):
    monkeypatch.setattr(vf, "CAPS", {SPEC: 3})
    made, refused, start = [], [], threading.Barrier(8)

    def worker(i):
        start.wait()                                 # all eight ask at the same moment
        try:
            vf.ai_call("second_look", BID, i, lambda: (made.append(i), {}) and ({}, {"ms": 5}))
        except vf.OutOfBudget:
            refused.append(i)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(made) == 3 and len(refused) == 5
    with db.connect() as c:
        rows = c.execute("SELECT ok, error, ms FROM staging.model_call WHERE provider=%s", (PROVIDER,)).fetchall()
    assert len(rows) == 3 and all(r["ok"] and r["error"] is None and r["ms"] == 5 for r in rows)   # claims completed


def test_a_refusal_that_names_no_time_waits_an_hour(vf):
    """Model Studio's 'Free Quota Only' refusal says no time: without a wait, every page would call and be refused."""
    with db.connect() as c:
        c.execute("""INSERT INTO staging.model_call (at, pacific_day, provider, model, purpose, ok, error)
                     VALUES (now() - interval '10 minutes', current_date, %s, %s, 'read_all', false,
                             'DailyLimit: dashscope:x: free quota used up (AllocationQuota.FreeTierOnly)')""",
                  (PROVIDER, SPEC))
    assert 49 * 60 < vf.refused_for() <= 50 * 60
    assert vf.blocked().startswith("DailyLimit")


def test_what_waits_for_a_retry():
    from worker import vf
    limit = "the call failed: DailyLimit: dashscope:x: free quota used up"
    assert vf.retry_kind("done", None, limit) == ("limit", limit)
    assert vf.retry_kind("failed", "OutOfBudget: cap used up", None)[0] == "limit"
    assert vf.retry_kind("failed", "RuntimeError: HTTP 500", None)[0] == "failed"
    assert vf.retry_kind("done", None, "the call failed: ReadTimeout")[0] == "failed"
    assert vf.retry_kind("done", None, ASK_WAIT + "posting_date") is None     # vf-grouper sends these
    assert vf.retry_kind("done", None, None) is None


def test_a_limit_waits_without_counting_and_a_failure_three_times(monkeypatch):
    from worker import vf
    parked = []
    monkeypatch.setattr(vf, "park", lambda t, why: parked.append(t))
    monkeypatch.setattr(vf, "retry_of", lambda b, n: ("limit", "DailyLimit: x"))
    for _ in range(5):                               # a long limit: parked every time, never given up
        assert vf.after({"batch_id": BID, "page_no": 1, "tries": 0}).startswith("parked (limit)")
    assert all(t["tries"] == 0 for t in parked)
    monkeypatch.setattr(vf, "retry_of", lambda b, n: ("failed", "RuntimeError: HTTP 500"))
    t, parked[:] = {"batch_id": BID, "page_no": 1}, []
    for _ in range(2):
        vf.after(t)
        t = parked[-1]
    assert [p["tries"] for p in parked] == [1, 2]
    assert vf.after(t).startswith("gave up after 3")
    assert len(parked) == 2
    monkeypatch.setattr(vf, "retry_of", lambda b, n: None)
    assert vf.after(t) is None                       # nothing to retry: nothing parked


class Channel:
    def __init__(self, bodies):
        self.bodies = list(bodies)

    def basic_get(self, q):
        if not self.bodies:
            return None, None, None
        tag = 10 - len(self.bodies)
        return type("M", (), {"delivery_tag": tag})(), None, self.bodies.pop(0)


def test_wakeups_waiting_together_are_one_round_per_batch():
    ch = Channel([b'{"batch_id": "b-1"}', b'{"batch_id": "b-2"}', b'{"batch_id": "b-1", "run": 2}', b"junk"])
    batches, last = serve.take(ch)
    assert batches == ["b-1", "b-2"] and last == 9 and ch.bodies == []
    assert serve.take(ch) == ([], None)


@pytest.fixture
def pages():
    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
    clean()
    waits = {1: ASK_WAIT + "posting_date", 2: ASK_WAIT + "total", 3: "the call failed: ReadTimeout", 4: None}
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s,'t','t',repeat('3',64),current_date,4,'read')""", (BID,))
        for n, w in waits.items():
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, second_look)
                         VALUES (%s,%s,%s,%s,%s)""",
                      (BID, n, f"vf/pages/{BID}/original/p{n:03d}.png", "queued" if n == 2 else "read",
                       None if w is None else f'{{"waiting": "{w}"}}'))
    yield
    clean()


def test_the_grouper_sends_back_only_pages_a_bundle_asked(pages, monkeypatch):
    """Page 1: a bundle asked, read → sent (and 'queued' now). Page 2: asked, but it has a ticket already. Page 3:
    its call failed (the worker's waiting room handles it, never the grouper: no bouncing). Page 4: waits for nothing."""
    from common import queue
    sent = []
    monkeypatch.setattr(queue, "send", lambda q, msgs: sent.extend((q, m) for m in msgs))
    assert serve.dispatch(BID) == [1]
    assert sent == [(queue.Q_PAGES, {"batch_id": BID, "page_no": 1, "run": 1,
                                     "image_key": f"vf/pages/{BID}/original/p001.png"})]
    with db.connect() as c:
        assert c.execute("SELECT status FROM staging.page WHERE batch_id=%s AND page_no=1", (BID,)).fetchone()["status"] \
            == "queued"
    assert serve.dispatch(BID) == []                 # sent once: it has its ticket now


def test_a_labelled_page_uploaded_here_goes_back_to_a_worker():
    """A label on a page uploaded to vlm-first (vf/pages/…) must resume it: page 12 of 7000363700-03 stayed unsure."""
    from api import app
    assert app.resumable("vf/pages/b-c80bbbde4d/original/p012.png") and app.resumable("pages/b-1/original/p001.png")
    assert not app.resumable("x") and not app.resumable(None)
