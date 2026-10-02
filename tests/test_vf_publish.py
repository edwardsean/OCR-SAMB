"""Phase 8, publish (services/publisher/publish.py): the plan is pure. A finished bundle becomes one row per document
(copies of one PO merged), typed by the field list, and one PDF in the order FP, PO, receipt."""
from datetime import date
from decimal import Decimal

from publisher import publish

OK, CHECK = {"verdict": "ok", "by": "text"}, {"verdict": "check"}


def page(t, fields, checks=None, fields_all=None):
    return {"doc_type": t, "fields": fields, "fields_all": fields_all or {}, "checks": {"header": checks or {}}}


def test_a_bundle_becomes_rows_and_one_pdf():
    """The Duta Buah bundle of 7000363700-03: FP p4, receipt p5, the PO four times (p6, p7 prints; p8, p9 terms)."""
    po = {"purchase_order_no": {"value": "PO.2026.09.32029"}, "total": {"value": "775397.00"},
          "ppn": {"value": "76841.00"}, "vendor_code": {"value": None},
          "lines": [{"product_code": "899", "product_description": "MENTOS ROLL FRUIT 37G", "qty": "24 PCS",
                     "uom": "PCS", "unit_price": "3,004.00", "discount": None}]}
    pages = {4: page("FP", {"sor": {"value": "SOR26110264346"}, "dpp": {"value": "665749.82"},
                            "ppn": {"value": "73232.48"}, "total": {"value": "738982.30"},
                            "lines": [{"kode_material": "1001", "nama_produk": "MENTOS", "kemasan": "24X37G",
                                       "qty_crt": "1", "qty_pcs": "0"}]},
                     {"dpp": OK, "ppn": OK, "total": OK}),
             5: page("TTG", {"posting_date": {"value": "2026-09-14"}, "document_no": {"value": "PO.RCV-14905/IX/2026"},
                             "purchase_order_no": {"value": "PO.2026.09.32029"}, "total": {"value": "(not printed)"},
                             "lines": []}, {"posting_date": OK, "purchase_order_no": OK, "document_no": CHECK}),
             6: page("PO", po, {"purchase_order_no": OK, "total": OK, "ppn": OK}),
             7: page("PO", po), 8: page("PO", {**po, "lines": []}), 9: page("PO", {**po, "lines": []})}
    docs = [{"type": "PO", "pages": [p], "linked_by": "po_no"} for p in (6, 7, 8, 9)] + \
        [{"type": "TTG", "pages": [5], "linked_by": "po_no"}, {"type": "FP", "pages": [4], "linked_by": "sor"}]
    p = publish.plan("SOR26110264346", docs, pages)
    assert p["pdf"] == [4, 6, 7, 8, 9, 5]                                  # FP, the PO (all copies), the receipt
    fp, po_row, ttg = p["documents"]
    assert [d["table"] for d in p["documents"]] == ["doc_faktur_penjualan", "doc_po", "doc_ttg"]
    assert fp["header"]["sor_no"] == "SOR26110264346" and fp["header"]["total"] == Decimal("738982.300")
    assert fp["lines"][0]["qty_crt"] == Decimal("1.000") and fp["confidence"] == 1.0
    assert po_row["source_pages"] == [6, 7, 8, 9] and po_row["page_ref"] == [2, 3, 4, 5]     # four copies, one row
    assert po_row["lines"][0]["qty"] == Decimal("24.000") and po_row["lines"][0]["unit_price"] == Decimal("3004.000")
    assert ttg["header"]["posting_date"] == date(2026, 9, 14) and ttg["page_ref"] == [6]
    assert "total" not in ttg["header"]                     # a receipt's total is read for the checks, not stored
    assert ttg["confidence"] == round(2 / 3, 3)             # document number read, not backed


def test_not_printed_is_empty_and_continuation_rows_follow():
    pages = {10: page("PO", {"purchase_order_no": {"value": "58423526"}, "ppn": {"value": "(not printed)"},
                             "lines": [{"product_code": "A", "qty": "2"}]}),
             11: page("CONTINUATION", {}, fields_all={"lines": [{"customer_item_code": "B", "qty": "5"}]})}
    p = publish.plan("SOR1", [{"type": "PO", "pages": [10, 11], "linked_by": "po_no"}], pages)
    d = p["documents"][0]
    assert d["header"]["ppn"] is None and [l["product_code"] for l in d["lines"]] == ["A", "B"]
    assert p["pdf"] == [10, 11] and d["page_ref"] == [1, 2]


# ---------------------------------------------------------------------------------------------- what was published
# The user (2026-10-02): "a table view of the data published when a bundle is posted" (/published, Data terkirim).

def _published_sor():
    import os
    if not os.environ.get("DATABASE_URL"):
        return None
    from common import db
    with db.connect() as c:
        r = c.execute("SELECT sor_no FROM satellite.sor_document ORDER BY sor_no LIMIT 1").fetchone()
    return r and r["sor_no"]


def test_the_table_view_shows_every_column_satellite_stores_exactly_as_stored():
    import pytest
    from common import db
    from api import app
    sor = _published_sor()
    if not sor:
        pytest.skip("nothing published in this database")
    with db.connect() as c:
        pv = app.published_view(c, sor)
        for tb in pv["tables"] + [pv["record"]]:
            schema, name = tb["name"].split(".")
            stored = [r["column_name"] for r in c.execute(
                """SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s
                    ORDER BY ordinal_position""", (schema, name))]
            assert sorted(k for k, _, _ in tb["cols"]) == sorted(stored), tb["name"]     # nothing left out or made up
        fp = c.execute("SELECT * FROM satellite.doc_faktur_penjualan WHERE sor_no=%s ORDER BY id", (sor,)).fetchall()
    t = next(x for x in pv["tables"] if x["name"] == "satellite.doc_faktur_penjualan")
    assert len(t["rows"]) == len(fp)
    keys = [k for k, _, _ in t["cols"]]
    for row, rec in zip(t["rows"], fp):
        for k, v in zip(keys, row):
            assert (v is None) == (rec[k] is None), k                               # a NULL stays empty
    with db.connect() as c:
        assert app.published_view(c, "SOR00000000000") is None                     # never published


def test_cells_are_shown_the_indonesian_way():
    from datetime import date
    from api import app
    assert app._pub_cell(Decimal("9410536.36"), "amount") == "9.410.536,36"
    assert app._pub_cell(Decimal("52.000"), "qty") == "52"
    assert app._pub_cell(date(2026, 9, 9), "date") == "9 Sep 2026"
    assert app._pub_cell(Decimal("0.800"), "pct") == "80%"
    assert app._pub_cell([3, 4], None) == "3, 4" and app._pub_cell(None, "amount") is None


def test_publishing_answers_with_the_orders_written(monkeypatch):
    """The web app opens Data terkirim on what was just written (the user, 2026-10-02: "a table view of the data
    published when a bundle is posted"): publishing answers with those SORs."""
    from publisher import publish as pub
    from api import actions
    monkeypatch.setattr(pub, "publish", lambda bid, sors=None: [{"sor": "SOR1"}, {"sor": "SOR2"}])
    assert actions.publish("b-1", "test") == ["SOR1", "SOR2"]
    monkeypatch.setattr(pub, "publish", lambda bid, sors=None: [])
    assert actions.publish("b-1", "test") == []


def test_what_was_published_answers():
    import os
    import httpx
    import pytest
    api = os.environ.get("API_URL", "http://localhost:8000")
    sor = _published_sor()
    if not sor:
        pytest.skip("nothing published in this database")
    rows = httpx.get(f"{api}/api/v1/published", params={"just": sor}, timeout=30).json()["rows"]
    assert rows[0]["sor_no"] == sor                                       # what was just written comes first
    one = httpx.get(f"{api}/api/v1/published/{sor}", timeout=30).json()
    assert one["sor"] == sor and "satellite.doc_faktur_penjualan_line" in [t["name"] for t in one["tables"]]
    assert httpx.get(f"{api}/api/v1/published/SOR0", timeout=30).status_code == 404


# ---------------------------------------------------------------------------------------------- reconfirming on the page
# The user (2026-10-02): "show the page in the right when scrolling, so that the user can reconfirm while looking at the
# page". A value is boxed where Tesseract read it printed, or shown as the AI's guess; never placed on a longer number.

def test_a_value_is_boxed_where_it_is_printed():
    from api import app
    words = [["NO", 90, 100, 100, 40, 20], ["GRN", 90, 150, 100, 60, 20], [":", 90, 220, 100, 5, 20],
             ["5002054248", 90, 400, 100, 200, 20],
             ["15002054248", 90, 400, 300, 220, 20]]                       # a longer number lower down: never this one
    assert app._spot(words, (1000, 1000), ["5002054248"]) == [100, 400, 120, 600]
    assert app._spot(words, (1000, 1000), ["12"]) is None                  # too short to place: printed everywhere
    assert app._spot(words, (1000, 1000), ["9999999"]) is None             # not printed: no guess
    name = [["PT", 90, 10, 50, 30, 20], ["DFI", 90, 50, 50, 40, 20], ["RETAIL", 90, 100, 50, 80, 20]]
    assert app._spot(name, (1000, 1000), ["PT DFI RETAIL"]) == [50, 10, 70, 180]   # across words, one line
    assert app._spot(words, (2000, 1000), ["5002054248"]) == [100, 200, 120, 300]  # pixels → 0-1000 of the page


def test_each_published_document_comes_with_its_page_and_what_backed_each_value():
    import pytest
    from common import db
    from api import app
    sor = _published_sor()
    if not sor:
        pytest.skip("nothing published in this database")
    with db.connect() as c:
        docs = app.published_docs(c, sor)
    assert docs and docs[0]["type"] == "FP"
    for d in docs:
        assert d["pages"] and all(p["img"].startswith("/img/") for p in d["pages"]), d["type"]
        assert {f["state"] for f in d["fields"]} <= {"print", "satellite", "person", "ai", "empty"}
        assert d["backed"] + d["to_check"] == d["filled"]
        for f in d["fields"]:
            assert f["box"] is None or (len(f["box"]) == 4 and all(0 <= v <= 1000 for v in f["box"])), f["name"]
