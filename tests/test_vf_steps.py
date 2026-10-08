"""A batch's five steps (2026-10-05; the user: "cant we just show a "Batches" tab only? then when we click the batch,
the steps for each comes up, but it should be ordered correctly"). api/steps.py: the pure rules, and that the counts
agree with the screens each step opens; the retry of a failed page; the label screen over one batch; the search."""
import os

import pytest

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


# ---------------------------------------------------------------------------------------------- the rules (pure)

def test_a_held_document_is_blocking_waiting_or_not_urgent():
    from api.steps import held_group
    assert held_group("TTG", "no_resolved_key") == "block"
    assert held_group("PO", "so_unknown") == "block"
    assert held_group("FP", "fp_sor_unresolved") == "block"
    assert held_group("FPJ", "fpj_needs_both") == "later"          # a Faktur Pajak never blocks sending
    assert held_group("FPJ", "needs_sap_billing") == "wait"        # SAP will settle it, not a person
    assert held_group("TTG", "not_read") == "wait"


def test_an_order_missing_a_document_waits_while_an_earlier_step_is_open():
    from api.steps import order_state
    missing = {"docs_complete": {"status": "fail"}, "calibration": {"status": "unknown"},
               "sor_in_satellite": {"status": "pass"}, "store_named": {"status": "info"}}
    assert order_state("needs_review", None, missing, earlier_open=True) == "depends"
    assert order_state("needs_review", None, missing, earlier_open=False) == "need"    # accept "comes later", or not
    other = {**missing, "fp_po_total": {"status": "fail"}}                              # something else to decide too
    assert order_state("needs_review", None, other, earlier_open=True) == "need"
    assert order_state("needs_review", "fp_missing", {}, earlier_open=True) == "depends"
    assert order_state("needs_review", "fp_missing", {}, earlier_open=False) == "outside"
    assert order_state("needs_review", "two_fps_one_sor", {}, earlier_open=True) == "need"


def test_an_order_by_status():
    from api.steps import order_state
    assert order_state("published", None, {}, True) == "published"
    assert order_state("auto_ok", None, {}, True) == order_state("reviewed", None, {}, True) == "ready"
    assert order_state("grouping", None, {}, True) == "waiting"
    assert order_state("needs_review", None, {"received": {"status": "waiting"}}, False) == "waiting"


def test_the_steps_from_counts():
    from api.steps import build
    w = build({"pages": 33}, {"done": 32, "failed": 1, "unsure": 3, "classified": 29, "answered": 1},
              {"block": 3, "later": 6}, {"need": 6, "depends": 1})
    states = {s["key"]: s["state"] for s in w["steps"]}
    assert states == {"baca": "need", "jenis": "need", "cocokkan": "need", "periksa": "need", "kirim": "none"}
    assert w["next"] == "baca" and w["blockers"] == ["baca", "jenis", "cocokkan"] and not w["finished"]
    assert w["steps"][4]["after"] == "baca"

    w = build({"pages": 16}, {"done": 16, "classified": 16, "answered": 3}, {"later": 2}, {"ready": 1, "published": 3})
    states = {s["key"]: s["state"] for s in w["steps"]}
    assert states == {"baca": "done", "jenis": "done", "cocokkan": "done", "periksa": "done", "kirim": "need"}
    assert w["steps"][2]["later"] == 2                       # a Faktur Pajak waiting for SAP: said, never blocking
    assert w["next"] == "kirim" and w["blockers"] == []

    w = build({"pages": 16}, {"done": 16, "classified": 16}, {}, {"published": 4})
    assert w["next"] is None and w["finished"]


def test_no_step_is_done_while_an_earlier_one_is_still_working():
    """The mentor's batch (2026-10-08): 4 pages; page 1 read, page 2 read and sent back by its order for a look-again,
    pages 3–4 waiting for a worker. It said "1 dari 4 halaman dibaca" with two files "Selesai dibaca", and ✓ Selesai
    on Jenis halaman. Now step 1 counts what its list shows, and the later steps wait for it."""
    from api.steps import build
    w = build({"pages": 4}, {"done": 1, "busy": 3, "again": 1, "classified": 2}, {"later": 1}, {})
    s = {x["key"]: x for x in w["steps"]}
    assert (s["baca"]["state"], s["baca"]["done"], s["baca"]["busy"], s["baca"]["again"]) == ("sys", 1, 3, 1)
    assert s["jenis"]["state"] == "none" and s["jenis"]["after"] == "baca" and s["jenis"]["pending"] == 2
    assert all(s[k]["state"] == "none" and s[k]["after"] == "baca" for k in ("cocokkan", "periksa", "kirim"))
    assert not w["finished"] and w["next"] is None


def test_pages_never_scheduled_hold_no_order_back():
    """A big scan read only in part (the sample: 31 of 288): the system's step stays amber, but nothing would ever
    finish those pages, so they never make an order wait, and a person can still act on what is there."""
    from api.steps import build
    w = build({"pages": 288}, {"done": 31, "unscheduled": 257, "classified": 31}, {}, {"need": 4, "published": 7})
    baca = w["steps"][0]
    assert baca["state"] == "sys" and baca["unscheduled"] == 257 and w["blockers"] == []
    assert w["steps"][3]["state"] == "need" and w["next"] == "periksa"


def test_a_batch_still_being_read():
    from api.steps import build
    w = build({"pages": 10, "splitting": 0}, {"done": 4, "busy": 6, "classified": 4}, {}, {})
    assert w["steps"][0]["state"] == "sys" and w["steps"][0]["unscheduled"] == 0
    assert w["blockers"] == ["baca"] and w["next"] is None


# ---------------------------------------------------------------------------------------------- against the screens

@needs_db
def test_each_step_counts_what_its_screen_lists():
    """The counts on the batch list and the stepper are the rows each step's screen shows."""
    from common import db
    from api import app as A, steps
    with db.connect() as c:
        work = steps.of_uploads(c)
    if not work:
        pytest.skip("needs an upload batch: none in this database yet")
    for u, w in work.items():
        s = {x["key"]: x for x in w["steps"]}
        v = A.upload_view(u)
        assert s["baca"]["failed"] >= len(v["failed"])
        assert s["jenis"]["unsure"] == len(v["unsure"])
        held = A.bundles_screen(None, u)["held"]
        assert s["cocokkan"]["block"] == sum(1 for h in held if h["group"] == "block")
        assert s["cocokkan"]["later"] == sum(1 for h in held if h["group"] == "later")
        rows = A.review_list(None, u)
        assert s["periksa"]["orders"] == len(rows)
        assert s["periksa"]["need"] == sum(1 for r in rows if r["step"] == "need")
        assert s["kirim"]["ready"] == sum(1 for r in rows if r["step"] == "ready")
        assert w["next"] == next((x["key"] for x in w["steps"] if x["state"] == "need"), None)


@needs_db
def test_the_api_gives_the_steps_and_the_top_bar_count():
    from fastapi.testclient import TestClient
    from api.app import app
    with TestClient(app) as tc:
        ups = tc.get("/api/v1/uploads").json()["uploads"]
        if not ups:
            pytest.skip("needs an upload batch: none in this database yet")
        assert all(len(u["steps"]) == 5 for u in ups)
        assert [s["key"] for s in ups[0]["steps"]] == ["baca", "jenis", "cocokkan", "periksa", "kirim"]
        one = tc.get(f"/api/v1/uploads/{ups[0]['id']}").json()
        assert {"steps", "next", "blockers", "failed", "unsure", "orders"} <= set(one)
        assert tc.get("/api/v1/session").json()["batches_need"] == sum(1 for u in ups if u["next"])


# ---------------------------------------------------------------------------------------------- step 1: retry

@pytest.fixture
def failed_page():
    """A throwaway batch with one scan whose only page failed to read; removed after."""
    from common import db, uploads
    u = uploads.create("test")
    sid = "b-test-steps-1"
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status,
                                                      upload_id)
                     VALUES (%s, 'test-steps.pdf', 't', repeat('7', 64), current_date, 1, 'queued', %s)""",
                  (sid, u["id"]))
        c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, error)
                     VALUES (%s, 1, 't', 'dead_letter', 'ConnectError: test')""", (sid,))
    yield u["id"], sid
    with db.connect() as c:
        c.execute("DELETE FROM staging.page WHERE batch_id = %s", (sid,))
        c.execute("DELETE FROM staging.scan_batch WHERE id = %s", (sid,))
        c.execute("DELETE FROM staging.upload WHERE id = %s", (u["id"],))


@needs_db
def test_a_failed_page_is_step_one_and_can_be_retried(failed_page, monkeypatch):
    """The retry puts the page back on the queue (intake.rerun, stubbed here: a real one calls the AI)."""
    from fastapi.testclient import TestClient
    from api import actions
    from api.app import app
    up, sid = failed_page
    sent = []
    monkeypatch.setattr(actions.intake, "rerun", lambda b, pages=None: sent.append((b, pages)) or {"published": 1})
    with TestClient(app) as tc:
        v = tc.get(f"/api/v1/uploads/{up}").json()
        assert v["next"] == "baca" and v["blockers"] == ["baca"]
        assert [(p["batch_id"], p["page_no"]) for p in v["failed"]] == [(sid, 1)]
        assert tc.post(f"/api/v1/scans/{sid}/pages/1/retry", json={"by": ""}).status_code == 400
        r = tc.post(f"/api/v1/scans/{sid}/pages/1/retry", json={"by": "test"})
        assert r.status_code == 200 and sent == [(sid, [1])]


@needs_db
def test_a_page_that_did_not_fail_is_not_retried():
    from fastapi.testclient import TestClient
    from common import db
    from api.app import app
    import _data
    p = _data.row("SELECT batch_id, page_no FROM staging.page WHERE status = 'read' LIMIT 1", what="a read page")
    with TestClient(app) as tc:
        r = tc.post(f"/api/v1/scans/{p['batch_id']}/pages/{p['page_no']}/retry", json={"by": "test"})
        assert r.status_code == 409


# ---------------------------------------------------------------------------------------------- step 2, search

@needs_db
def test_the_label_screen_walks_one_batch():
    from fastapi.testclient import TestClient
    from api.app import app
    with TestClient(app) as tc:
        for u in tc.get("/api/v1/uploads").json()["uploads"]:
            todo = [(p["batch_id"], p["page_no"]) for p in tc.get(f"/api/v1/uploads/{u['id']}").json()["unsure"]]
            d = tc.get(f"/api/v1/labels?upload={u['id']}").json()
            assert d["left"] == len(todo) and d["upload"]["code"] == u["code"]
            if todo:
                assert (d["batch"], d["page"]) == todo[0]
                after = tc.get(f"/api/v1/labels?upload={u['id']}&batch={todo[0][0]}&after={todo[0][1]}").json()
                assert (after["batch"], after["page"]) == (todo[1] if len(todo) > 1 else todo[0])   # wraps round
            else:
                assert d.get("page") is None


@needs_db
def test_the_search_finds_an_order_and_wants_two_characters():
    from fastapi.testclient import TestClient
    from common import db
    from api.app import app
    import _data
    sor = _data.row("SELECT sor_no FROM staging.bundle ORDER BY id DESC LIMIT 1", what="an order")["sor_no"]
    with TestClient(app) as tc:
        assert tc.get("/api/v1/search?q=S").json()["orders"] == []
        got = tc.get(f"/api/v1/search?q={sor[-6:]}").json()
        assert sor in [o["sor_no"] for o in got["orders"]]
