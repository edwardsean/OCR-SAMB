"""The REST API the Next.js frontend calls (/api/v1, services/api/v1.py): every read answers JSON with what its screen needs,
the same data the server-rendered pages show; a refused action answers with its status and a reason, and writes
nothing. Nothing here writes to the database (publishing is replaced, like test_vf_publish does)."""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

UI = os.environ.get("API_URL", "http://localhost:8000")
VF = os.environ.get("PIPELINE") == "vlm-first"
pytestmark = pytest.mark.skipif(not VF, reason="vlm-first only")


def get(path, **params):
    r = httpx.get(f"{UI}/api/v1{path}", params=params, timeout=60)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    assert r.headers["content-type"].startswith("application/json")
    return r.json()


def _scan_with_orders():
    rows = get("/orders")["scans"]
    return next((s["id"] for s in rows if s["need"]), rows[0]["id"] if rows else None)


def test_the_frame_answers():
    s = get("/session")
    assert {"needs_you", "unsure_left", "teacher", "today", "vf"} <= set(s) and s["vf"] is True
    w = get("/words")
    assert w["DOC"]["FP"] == "Faktur Penjualan" and w["BUNDLE"]["needs_review"] == "Perlu dicek"
    assert w["FIELD_BY_TYPE"]["TTG"]["document_no"] == "Nomor tanda terima"
    assert [t["key"] for t in w["LABEL_TYPES"]][:3] == ["FP", "TTG", "PO"]
    h = get("/health")
    assert isinstance(h["bad"], list) and h["n"] >= 1
    home = get("/home")
    assert {"scans", "todo", "busy"} <= set(home) and set(home["todo"]) == {"need", "unsure", "ready", "held"}


def test_a_scan_and_its_pages_answer():
    for s in get("/scans", limit=50)["scans"]:          # the newest scan with a page read (the newest may be reading)
        d = get(f"/scans/{s['id']}")
        if any(p["doc_type"] for p in d["pages"]):
            sid = s["id"]
            break
    else:
        pytest.skip("no scan has a page read yet")
    assert d["scan"]["id"] == sid and d["pages"] and {"need", "ready", "held", "total"} <= set(d["orders"])
    n = next(p["page_no"] for p in d["pages"] if p["doc_type"])
    p = get(f"/scans/{sid}/pages/{n}")
    assert p["page"]["page_no"] == n and p["scan"]["page_total"] == d["scan"]["page_total"]
    assert "ocr_words" not in p["page"]                              # only what the viewer needs
    if p["fix"]:
        f = p["fix"]["fields"][0]
        assert {"name", "label", "value", "box", "verdict", "says"} <= set(f)
    assert httpx.get(f"{UI}/api/v1/scans/{sid}/pages/99999", timeout=30).status_code == 404
    assert httpx.get(f"{UI}/api/v1/scans/{sid}/pages/{n}/region", params={"box": "5,5"}, timeout=30).status_code == 400


def test_orders_answer_with_what_the_review_screen_shows():
    from api import app
    sid = _scan_with_orders()
    if not sid:
        pytest.skip("needs a scan with orders: none in this database yet")
    d = get("/orders", batch=sid)
    assert d["batch"] == sid and {r["sor_no"] for r in d["rows"]} == {r["sor_no"] for r in app.review_list(sid)}
    sor = d["rows"][0]["sor_no"]
    o = get(f"/orders/{sor}", batch=sid)
    assert o["sor"] == sor and o["batch"] == sid
    assert {"open_items", "strip", "passed", "can_approve", "left", "calibration", "documents"} <= set(o)
    assert "checks" not in o["bundle"] and o["bundle"]["sor_no"] == sor   # the bundle row, not its whole record
    assert all(not x.startswith("pages [") for x in o["left"])           # "what is left" is said in Indonesian
    assert httpx.get(f"{UI}/api/v1/orders/SOR0", params={"batch": sid}, timeout=30).status_code == 404


def test_berkas_label_and_published_answer():
    sid = _scan_with_orders()
    b = get("/bundles", batch=sid)
    assert b["batch"] == sid and {"bundles", "held", "unplaced"} <= set(b["view"])
    lab = get("/labels")
    assert "batch" in lab
    if lab["batch"] and lab.get("p"):
        assert lab["types"][0]["key"] == "FP" and {"page_no", "full", "type"} <= set(lab["near"][0])
    pub = get("/published")
    assert {"rows", "batches"} <= set(pub)
    if pub["rows"]:
        one = get(f"/published/{pub['rows'][0]['sor_no']}")
        assert one["docs"] and {"name", "value", "state", "box"} <= set(one["docs"][0]["fields"][0])
    assert httpx.get(f"{UI}/api/v1/published/SOR0", timeout=30).status_code == 404


def test_a_refused_action_says_why_and_writes_nothing():
    post = lambda path, body: httpx.post(f"{UI}/api/v1{path}", json=body, timeout=60)
    r = post("/orders/SOR0/confirmations", {"batch": "b-0", "page": 1, "field": "total", "value": "1", "by": " "})
    assert r.status_code == 400 and "who you are" in r.json()["error"]
    r = post("/labels", {"batch": "b-0", "page": 1, "label": "NOPE"})
    assert r.status_code == 400 and r.json()["error"] == "unknown label"
    r = post("/orders/SOR0/calibrations", {"chain": "x", "by": "test", "allowance": "abc"})
    assert r.status_code == 400 and "number of rupiah" in r.json()["error"]
    r = post("/orders/SOR0/acceptances", {"batch": "b-0", "check": "fp_po_total", "input_print": "", "reason": "",
                                          "by": "test"})
    assert r.status_code == 400
    assert post("/orders/SOR0/pairings", {"batch": "b-0"}).status_code == 422        # the body is checked first
    r = httpx.post(f"{UI}/api/v1/scans", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, timeout=30)
    assert r.status_code == 400 and "bukan PDF" in r.json()["error"]


def test_publishing_answers_with_the_orders_written(monkeypatch):
    from publisher import publish as pub
    from api import app
    c = TestClient(app.app)
    monkeypatch.setattr(pub, "publish", lambda bid, sors=None: [{"sor": "SOR1"}, {"sor": "SOR2"}])
    r = c.post("/api/v1/publications", json={"batch": "b-1", "by": "test"})
    assert r.status_code == 200 and r.json() == {"ok": True, "result": ["SOR1", "SOR2"]}
    assert c.post("/api/v1/publications", json={"batch": "b-1", "by": ""}).status_code == 400
