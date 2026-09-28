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
