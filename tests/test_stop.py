"""Stopping a batch's AI work, and going on with it (the user, 2026-10-15: "a stop button to stop a working batch, but
there should be a condition on where can we stop and when can we not stop"). common/stopping.py holds the rules; the
worker, the queue's doors, the split and the learning step check them; api/actions.py stops and resumes a file or a
whole batch. Nothing here sends to a real queue or calls a model: sends are recorded."""
import json
import os
import types

import pytest

from common import stopping

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
BID, BID2, CODE = "test-stop", "test-stop-2", "TEST-STOP"


def test_when_a_file_can_be_stopped():
    run = {"waiting_worker": 3}
    assert stopping.why_not({"status": "queued"}, run) is None
    assert stopping.why_not({"status": "received"}, {"not_split": 1}) is None
    assert "sudah dihentikan" in stopping.why_not({"status": "stopped"}, run)
    assert "gagal dipecah" in stopping.why_not({"status": "failed"}, {})
    assert "Tidak ada yang sedang berjalan" in stopping.why_not({"status": "read"}, {})


def test_a_stop_is_no_failure():
    """The page flow turns every Exception from a call into "the call failed"; a stop must pass those by."""
    assert issubclass(stopping.Stopped, BaseException) and not issubclass(stopping.Stopped, Exception)


# ------------------------------------------------------------------------------------------------ on the database

@pytest.fixture
def sent(monkeypatch):
    """Every send to a queue, recorded instead of sent; enqueue records its file instead of publishing."""
    from common import intake, queue
    out = {"queue": [], "enqueue": []}
    monkeypatch.setattr(queue, "send", lambda q, msgs: out["queue"].append((q, list(msgs))))
    monkeypatch.setattr(queue, "wake_grouper", lambda *a, **k: None)

    def enqueue(bid, only_rendered=False, why="new"):
        from common import db
        with db.connect() as c:
            row = c.execute("UPDATE staging.scan_batch SET status='queued' WHERE id=%s AND status='split' RETURNING run",
                            (bid,)).fetchone()
            if row:
                c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND status='rendered'", (bid,))
        out["enqueue"].append((bid, only_rendered, row and row["run"]))
        return {"batch_id": bid, "published": 0}
    monkeypatch.setattr(intake, "enqueue", enqueue)
    return out


@pytest.fixture
def batch():
    """One upload with two files. File 1 (queued, run 1): pages 1–2 read, 3–5 queued, 6 read but waiting for a
    look-again. File 2: every page read, nothing waiting."""
    from common import db

    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.model_call WHERE batch_id IN (%s, %s)", (BID, BID2))
            c.execute("DELETE FROM staging.page WHERE batch_id IN (%s, %s)", (BID, BID2))
            c.execute("DELETE FROM staging.scan_batch WHERE id IN (%s, %s)", (BID, BID2))
            c.execute("DELETE FROM staging.upload WHERE code=%s", (CODE,))
    clean()
    with db.connect() as c:
        up = c.execute("""INSERT INTO staging.upload (code, uploaded_by, doc_date) VALUES (%s, 'test', current_date)
                          RETURNING id""", (CODE,)).fetchone()["id"]
        for bid, status, total in ((BID, "queued", 6), (BID2, "read", 2)):
            c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total,
                                                         status, upload_id, run)
                         VALUES (%s, %s, 't', md5(%s) || md5(%s), current_date, %s, %s, %s, 1)""",
                      (bid, bid + ".pdf", bid, bid, total, status, up))
        rows = [(BID, 1, "read", None), (BID, 2, "read", None), (BID, 3, "queued", None), (BID, 4, "queued", None),
                (BID, 5, "queued", None), (BID, 6, "read", "waiting_ai"), (BID2, 1, "read", None), (BID2, 2, "read", None)]
        for bid, n, status, outcome in rows:
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, original_path, status, outcome,
                                                   second_look, extract_status)
                         VALUES (%s, %s, 'stop/pages/x.png', 'stop/pages/x.png', %s, %s, %s, 'done')""",
                      (bid, n, status, outcome,
                       json.dumps({"waiting": "an order asked to look again"}) if outcome else None))
    yield up
    clean()


def _page(bid, n):
    from common import db
    with db.connect() as c:
        return dict(c.execute("SELECT status::text AS status, error FROM staging.page WHERE batch_id=%s AND page_no=%s",
                              (bid, n)).fetchone())


def _scan(bid):
    from common import db
    with db.connect() as c:
        return dict(c.execute("SELECT * FROM staging.scan_batch WHERE id=%s", (bid,)).fetchone())


class _Held:
    """A worker holding a page (vf.handle's page lock), on its own connection."""
    def __init__(self, bid, n):
        self.bid, self.n = bid, n

    def __enter__(self):
        from common import db
        self.cm = db.connect(autocommit=True)
        self.c = self.cm.__enter__()
        self.c.execute("SELECT pg_advisory_lock(hashtext(%s), %s)", (f"page:{self.bid}", self.n))
        return self

    def __exit__(self, *a):
        self.c.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (f"page:{self.bid}", self.n))
        self.cm.__exit__(*a)


@needs_db
def test_a_stop_marks_waiting_pages_and_leaves_read_ones_and_the_page_being_read(batch, sent):
    from api import actions
    with _Held(BID, 4):                                   # page 4 is being read by a worker
        out = actions.stop_scan(BID, "Edward")
    assert out["stopped"] == [3, 5] and out["finishing"] == [4] and out["waiting_ai"] == 1
    s = _scan(BID)
    assert (s["status"], s["stopped_by"], s["stopped_from"]) == ("stopped", "Edward", "queued") and s["stopped_at"]
    assert _page(BID, 3)["status"] == "failed" and _page(BID, 3)["error"].startswith("Stopped on purpose by Edward")
    assert _page(BID, 4)["status"] == "queued"            # it stops at its next AI call (the worker marks it)
    assert _page(BID, 1)["status"] == _page(BID, 6)["status"] == "read"


@needs_db
def test_what_can_not_be_stopped(batch, sent):
    from api import actions
    with pytest.raises(actions.ActionError) as e:
        actions.stop_scan(BID2, "Edward")                 # every page read, nothing waiting
    assert e.value.status == 409 and "Tidak ada yang sedang berjalan" in e.value.error
    actions.stop_scan(BID, "Edward")
    with pytest.raises(actions.ActionError) as e:
        actions.stop_scan(BID, "Edward")
    assert "sudah dihentikan" in e.value.error
    with pytest.raises(actions.ActionError) as e:
        actions.stop_scan(BID, "")                        # unsigned
    assert e.value.status == 400


@needs_db
def test_a_stopped_file_gets_no_ai_call_no_ticket_no_waiting_room(batch, sent):
    from api import actions
    from common import db
    from worker import vf
    actions.stop_scan(BID, "Edward")
    called = []
    with pytest.raises(stopping.Stopped):
        vf.ai_call("map", BID, 4, lambda: called.append(1))
    with db.connect() as c:
        n = c.execute("SELECT count(*) AS n FROM staging.model_call WHERE batch_id=%s", (BID,)).fetchone()["n"]
    assert not called and n == 0                          # no call, nothing reserved against the day's cap
    assert vf.enqueue(BID, [1, 2, 6]) == [] and vf.again(BID) == []      # the sweep, a look-again, a retry: nothing
    vf.park({"batch_id": BID, "page_no": 4, "run": 1}, "DailyLimit: x")
    assert _page(BID, 4)["status"] == "failed" and not [q for q, _ in sent["queue"] if q.endswith(".wait")]


@needs_db
def test_the_worker_drops_a_stopped_files_ticket_and_ends_a_page_at_its_next_call(batch, sent, monkeypatch):
    from api import actions
    from worker import main, vf
    actions.stop_scan(BID, "Edward")
    acks = []
    ch = types.SimpleNamespace(basic_ack=lambda tag: acks.append(tag),
                               basic_nack=lambda *a, **k: acks.append("nack"), basic_reject=lambda *a, **k: acks.append("reject"))
    method = types.SimpleNamespace(delivery_tag=7, redelivered=False)
    main.on_message(ch, method, None, json.dumps({"batch_id": BID, "page_no": 5, "run": 1}).encode())
    assert acks == [7] and _page(BID, 5)["status"] == "failed"

    def mid_page(*a):                                     # the stop lands while a page is being read
        raise stopping.Stopped(BID)
    monkeypatch.setattr(vf, "_handle", mid_page)
    monkeypatch.setattr(stopping, "is_stopped", lambda bid, conn=None: False)   # not yet when the page started
    assert vf.handle({"batch_id": BID, "page_no": 4, "run": 1}) == "stopped"
    assert _page(BID, 4)["status"] == "failed"


@needs_db
def test_a_split_never_overwrites_a_stop(monkeypatch):
    from common import db, intake
    bid = "test-stop-split"
    with db.connect() as c:
        c.execute("DELETE FROM staging.page WHERE batch_id=%s", (bid,))
        c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (bid,))
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s, 'x.pdf', 'x', md5('s') || md5('t'), current_date, 2, 'received')""", (bid,))
    monkeypatch.setattr(intake.storage, "client", lambda: types.SimpleNamespace(fget_object=lambda *a: None))

    def render(pdf, b, n, tmp):                          # a person stops the file while it is being split
        with db.connect() as c:
            c.execute("UPDATE staging.scan_batch SET status='stopped' WHERE id=%s", (b,))
    monkeypatch.setattr(intake, "_render_one", render)
    try:
        assert intake.split(bid)["status"] == "stopped"
        assert _scan(bid)["status"] == "stopped"         # never 'split', so never queued
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (bid,))


@needs_db
def test_going_on_requeues_the_stopped_pages_under_a_new_run_and_sends_the_waiting_ones(batch, sent):
    from api import actions
    actions.stop_scan(BID, "Edward")
    with _Held(BID, 4):
        with pytest.raises(actions.ActionError) as e:
            actions.resume_scan(BID, "Edward")           # page 4 still finishes its last call
        assert "menyelesaikan panggilan AI" in e.value.error
    out = actions.resume_scan(BID, "Edward")
    assert out["pages"] == [3, 4, 5] and not out["split"]
    s = _scan(BID)
    assert s["status"] == "queued" and s["run"] == 2 and s["stopped_at"] is None and s["stopped_by"] is None
    assert sent["enqueue"] == [(BID, True, 2)]            # only the stopped pages, under run 2
    assert [p for q, msgs in sent["queue"] for p in msgs if q == "q.pages"] and out["sent"] == [6]   # the look-again
    with pytest.raises(actions.ActionError):
        actions.resume_scan(BID, "Edward")               # not stopped any more


@needs_db
def test_a_file_stopped_before_it_was_split_is_split_when_it_goes_on(batch, sent):
    from api import actions
    from common import db
    with db.connect() as c:
        c.execute("UPDATE staging.scan_batch SET status='received' WHERE id=%s", (BID2,))
        c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID2,))
    assert actions.stop_scan(BID2, "Edward")["was"] == "received"
    out = actions.resume_scan(BID2, "Edward")
    assert out["split"] and _scan(BID2)["status"] == "received"
    assert ("q.intake", [{"batch_id": BID2}]) in sent["queue"]


@needs_db
def test_a_whole_batch(batch, sent):
    from api import actions
    out = actions.stop_upload(batch, "Edward")
    assert [f["batch_id"] for f in out["stopped"]] == [BID] and out["left"][0]["batch_id"] == BID2
    with pytest.raises(actions.ActionError) as e:
        actions.stop_upload(batch, "Edward")             # nothing runs any more
    assert e.value.status == 409
    assert [f["batch_id"] for f in actions.resume_upload(batch, "Edward")["resumed"]] == [BID]
    with pytest.raises(actions.ActionError) as e:
        actions.resume_upload(batch, "Edward")
    assert "tidak dihentikan" in e.value.error


@needs_db
def test_a_stopped_file_is_not_stuck_and_says_so(batch, sent):
    from api import actions, activity, app, steps, stuck
    from common import db
    with _Held(BID, 4):
        actions.stop_scan(BID, "Edward")
        with db.connect() as c:
            view = app.stop_view(c, [dict(r) for r in c.execute(
                "SELECT * FROM staging.scan_batch WHERE upload_id=%s", (batch,))])
            work = steps.of_uploads(c, [batch])[batch]
            stuck_n = c.execute(f"SELECT count(*) AS n FROM staging.page p WHERE p.batch_id=%s AND {stuck.STUCK}",
                                (BID,)).fetchone()["n"]
    assert not view["can"] and view["stopped"][0]["by"] == "Edward" and view["finishing"] == 1
    baca = work["steps"][0]
    assert baca["state"] == "need" and baca["stopped"] == 4 and baca["failed"] == 0 and stuck_n == 0
    page = {"scan_status": "stopped", "stopped_by": "Edward", "status": "failed", "was_read": False}
    assert activity.state(page)["state"] == "stopped" and "Edward" in activity.state(page)["text"]
    assert activity.state({**page, "status": "queued", "reading_since": "x"})["state"] == "stopping"
    assert activity.state({**page, "status": "read", "outcome": "clear"})["state"] == "done"


@needs_db
def test_retrying_a_page_of_a_stopped_file_says_to_go_on(batch, sent):
    from api import actions
    actions.stop_scan(BID, "Edward")
    with pytest.raises(actions.ActionError) as e:
        actions.retry_page(BID, 3, "Edward")
    assert e.value.status == 409 and "Lanjutkan membaca" in e.value.error


@needs_db
def test_the_api(batch, sent):
    import httpx
    url = os.environ.get("API_URL", "http://localhost:8000")
    r = httpx.post(f"{url}/api/v1/scans/{BID2}/stop", json={"by": "Edward"})
    assert r.status_code == 409 and "Tidak ada yang sedang berjalan" in r.json()["error"]
    r = httpx.post(f"{url}/api/v1/uploads/{batch}/resume", json={"by": "Edward"})
    assert r.status_code == 409


# ------------------------------------------------------------------------------------------------ restart (Baca ulang)

def test_when_a_page_can_be_read_again_from_scratch():
    from api import actions
    p = {"status": "read", "published": False, "scan_status": "read"}
    assert actions.why_not_restart(p) is None
    assert actions.why_not_restart({**p, "status": "failed"}) is None              # a stuck page, from scratch
    assert "dikirim" in actions.why_not_restart({**p, "published": True})
    assert "dihentikan" in actions.why_not_restart({**p, "scan_status": "stopped"})
    assert "sedang dibaca" in actions.why_not_restart({**p, "status": "queued"})
    assert "belum dibaca" in actions.why_not_restart({**p, "status": "rendered"})


@needs_db
def test_restarting_a_batch_reads_its_pages_again_from_scratch_and_lists_the_rest(batch, sent):
    from api import actions
    from common import db
    with db.connect() as c:
        c.execute("UPDATE staging.page SET transcript='[{\"id\": \"b1\"}]', transcript_version='v1' WHERE batch_id=%s", (BID,))
    out = actions.restart_upload(batch, "Edward")
    got = {(p["batch_id"], p["page_no"]) for p in out["pages"]}
    assert got == {(BID, 1), (BID, 2), (BID, 6), (BID2, 1), (BID2, 2)} and out["calls"] == 5 * actions.CALLS_PER_PAGE
    assert {(l["batch_id"], l["page_no"]) for l in out["left"]} == {(BID, 3), (BID, 4), (BID, 5)}   # being read
    tickets = [t for q, msgs in sent["queue"] if q == "q.pages" for t in msgs]
    assert len(tickets) == 5 and all(t["fresh"] and t["run"] == 1 for t in tickets)   # no new run for the file
    with db.connect() as c:
        r = c.execute("SELECT status::text AS status, transcript FROM staging.page WHERE batch_id=%s AND page_no=1",
                      (BID,)).fetchone()
    assert r["status"] == "queued" and r["transcript"] is None        # its copy is made again, never the old one reused


@needs_db
def test_restarting_chosen_pages_only_and_never_a_stopped_files(batch, sent):
    from api import actions
    out = actions.restart_upload(batch, "Edward", [{"batch_id": BID2, "page_no": 2}])
    assert [(p["batch_id"], p["page_no"]) for p in out["pages"]] == [(BID2, 2)] and not out["left"]
    actions.stop_scan(BID, "Edward")
    with pytest.raises(actions.ActionError) as e:
        actions.restart_scan(BID, "Edward", [1])
    assert e.value.status == 409 and "dihentikan" in e.value.error


@needs_db
def test_the_restart_api(batch, sent):
    import httpx
    url = os.environ.get("API_URL", "http://localhost:8000")
    r = httpx.post(f"{url}/api/v1/scans/{BID}/restart", json={"by": "Edward", "pages": [3]})
    assert r.status_code == 409 and "Sedang dibaca" in r.json()["error"]          # page 3 is queued
