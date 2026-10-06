"""Asking the AI which SAMB line a customer's row is, from an order's page (2026-10-06; the user: "we need a mapping
too about our product code and customer's product code … we can iterate this to build up the master data, with AI's
recommendation and user's validation"). grouper.matching.propose_order, the API that runs it in the background, the
order's list of unpaired rows, and the master-data screen. The AI is stubbed: no tokens, no proposal written."""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


def _order_with_unpaired():
    """An order whose customer documents have rows no rule paired (found, never named: real customers' data)."""
    from common import db
    from api import app as A
    with db.connect() as c:
        sors = [r["sor_no"] for r in c.execute(
            """SELECT DISTINCT b.sor_no FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                 JOIN staging.document d ON d.id = bd.document_id
                WHERE d.doc_type IN ('PO', 'TTG') AND b.hold_reason IS NULL ORDER BY 1""")]
    for s in sors:
        with db.connect() as c:
            batch = c.execute("""SELECT d.batch_id FROM staging.document d JOIN staging.bundle_document bd ON bd.document_id = d.id
                                   JOIN staging.bundle b ON b.id = bd.bundle_id WHERE b.sor_no = %s LIMIT 1""",
                              (s,)).fetchone()["batch_id"]
        v = A.review_view(batch, s)
        if v and [p for p in v["pairing"] if p["ai"] is None]:
            return s, batch, v
    pytest.skip("no order with unpaired rows in this database")


def test_the_order_lists_its_unpaired_rows():
    sor, batch, v = _order_with_unpaired()
    p = v["pairing"][0]
    assert {"page", "i", "type", "desc", "code", "ai", "why"} <= set(p) and p["type"] in ("PO", "TTG")


def test_the_matcher_asks_once_per_document_with_unpaired_rows_across_its_scans(monkeypatch):
    from common.models import teacher
    from grouper import matching
    sor, batch, v = _order_with_unpaired()
    asked = []
    monkeypatch.setattr(teacher, "ask_text", lambda prompt, model, role="": (asked.append((prompt, role)) or {"pairs": []},
                                                                             {"model": "stub", "ms": 1}))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    out = matching.propose_order(sor, show=lambda *a: None)
    want_docs = {(p["page"], p["type"]) for p in v["pairing"] if p["ai"] is None}
    assert out["calls"] == len({pg for pg, _ in want_docs}) and out["proposed"] == 0
    assert out["rows"] == sum(1 for p in v["pairing"] if p["ai"] is None)
    assert all(role == "MATCH_MODEL" for _, role in asked)


def test_the_button_runs_in_the_background_and_says_how_it_went(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import app
    from api import v1
    from grouper import matching
    hold = threading.Event()

    def fake(sor, show=print):
        hold.wait(5)
        return {"rows": 3, "proposed": 2, "calls": 1}
    monkeypatch.setattr(matching, "propose_order", fake)
    sor = "SOR-TEST-PAIRING"
    v1._PAIRING.pop(sor, None)
    with TestClient(app) as tc:
        assert tc.post(f"/api/v1/orders/{sor}/pair-proposals", json={"by": ""}).status_code == 400
        r = tc.post(f"/api/v1/orders/{sor}/pair-proposals", json={"by": "test"})
        assert r.status_code == 202 and r.json()["state"] == "running"
        assert tc.post(f"/api/v1/orders/{sor}/pair-proposals", json={"by": "test"}).status_code == 409
        hold.set()
        for _ in range(50):
            got = tc.get(f"/api/v1/orders/{sor}/pair-proposals").json()
            if got["state"] != "running":
                break
            time.sleep(0.1)
        assert got["state"] == "done" and got["proposed"] == 2 and got["rows"] == 3
        assert tc.get("/api/v1/orders/SOR-NEVER-ASKED/pair-proposals").json() == {"state": "idle"}
    v1._PAIRING.pop(sor, None)


def test_a_model_that_fails_is_said_so(monkeypatch):
    from api import v1
    from grouper import matching

    def boom(sor, show=print):
        raise RuntimeError("zai unavailable (HTTP 429) after retries")
    monkeypatch.setattr(matching, "propose_order", boom)
    v1._PAIRING["SOR-TEST-FAIL"] = {"state": "running"}
    v1._pairing("SOR-TEST-FAIL")
    assert v1._PAIRING["SOR-TEST-FAIL"]["state"] == "failed" and "429" in v1._PAIRING["SOR-TEST-FAIL"]["error"]
    v1._PAIRING.pop("SOR-TEST-FAIL", None)


def test_the_master_data_screen_lists_exports_and_asks_a_name_to_remove():
    from fastapi.testclient import TestClient
    from api.app import TECH, app
    assert "/product-codes" in [h for h, _ in TECH]
    with TestClient(app) as tc:
        assert tc.get("/product-codes").status_code == 200
        csv = tc.get("/product-codes.csv")
        assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("customer_chain,customer_name,customer_item_code")
        r = tc.post("/product-codes/delete", data={"customer_code": "x", "customer_item_code": "y", "by": ""},
                    follow_redirects=False)
        assert r.status_code == 303 and "msg=" in r.headers["location"]
