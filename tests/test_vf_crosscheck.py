"""Phase 7c cross-checks (grouper/crosscheck.py), pure, on the Boots bundle's own numbers, with the verification
redesign's rules (S2, the user 2026-09-26/27): the PO against Satellite's SO as ordered, the receipt against what
Satellite received, totals up to a few rupiah of rounding, an AI reading counts within that; a receipt short of the
order is a tolakan when Satellite records it; the bundle's status and reasons; a review its inputs change is undone."""
import copy
from datetime import date

from grouper import crosscheck, matching

OK, SAT, PERSON = {"verdict": "ok", "by": "text"}, {"verdict": "ok", "by": "satellite"}, {"verdict": "ok", "by": "person"}
CHECK = {"verdict": "check", "why": "not in Tesseract's text"}
DPP, PPN, TOTAL = 1014424.32, 111586.68, 1126011.00          # SOR26110255837 as ordered (= invoiced: no tolakan)
SO = {"sor_no": "SOR26110255837", "tgl_so": date(2026, 9, 4), "order_dpp": DPP, "order_ppn": PPN,
      "order_total": TOTAL, "dpp": DPP, "ppn": PPN, "total": TOTAL, "status": "INVOICE_GENERATED", "cgr_no": "CGR1",
      "cgr_date": date(2026, 9, 10), "vat_pct": 11, "billing_no": "7000356306"}
LINE = {"line_no": 10, "item_code": "1003243", "description": "VASELINE BW 425ML LUMINOUS GLUTAGLOW", "pcs_per_uom": 8,
        "qty_pcs": 4, "price_pcs": 57082.43, "price_uom": 456659.00, "cgr_qty": 4, "rejected_qty": 0, "discounts": {},
        "line_amount": DPP, "vat": PPN, "invoice_amount": DPP, "invoice_qty": 4}


def page(doc_type, header, verdicts, lines=(), line_verdicts=(), outcome="clear"):
    return {"doc_type": doc_type, "fields": {**{k: {"value": v} for k, v in header.items()}, "lines": list(lines)},
            "checks": {"header": verdicts, "lines": list(line_verdicts)}, "outcome": outcome}


def boots(po_ok=OK, po_total="1126006.00", grn_total="1126006.00", grn_ok=OK, cgr=4, rejected=0, reason=None,
          received=1.0):
    line = {**LINE, "cgr_qty": cgr, "rejected_qty": rejected, "reject_reason": reason,
            "invoice_amount": round(DPP * received, 2), "invoice_qty": cgr}
    so = {**SO, "dpp": round(DPP * received, 2), "ppn": round(PPN * received, 2), "total": round(TOTAL * received, 2)}
    pages = {3: page("FP", {"total": "1126011.00", "ppn": "111586.68"}, {"total": SAT, "ppn": SAT}),
             4: page("PO", {"total": po_total, "ppn": "111586.00", "vendor_name": "SARANA ABADI MAKMUR BERSAMA PT"},
                     {"total": po_ok, "ppn": OK, "vendor_name": OK},
                     [{"product_code": "K6N302030561", "qty": "4", "uom": "EA", "unit_price": "63,361", "row_text": ""}],
                     [{"qty": CHECK, "unit_price": OK}]),
             5: page("TTG", {"posting_date": "2026-09-10", "total": grn_total}, {"posting_date": OK, "total": grn_ok},
                     [{"item_code": "K6N302030561", "qty": "4", "uom": "EA", "row_text": ""}], [{"qty": CHECK}])}
    docs = [(3, "FP"), (4, "PO"), (5, "TTG")]
    rows = [(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t in docs if t != "FP"]
    m = matching.match(rows, [line], {}, {})
    return crosscheck.check_bundle(SO["sor_no"], docs, pages, so, [line], m, ("FP", "TTG"), date(2026, 9, 23)), pages


def test_the_po_and_the_order_agree_up_to_a_few_rupiah_of_rounding():
    c, _ = boots()                                   # Boots rounds per piece with PPN: 1,126,011.00 vs 1,126,006.00
    assert c["fp_po_total"]["status"] == "pass" and "5.00 apart, rounding" in c["fp_po_total"]["why"]
    c, _ = boots(po_total="1126000.00")              # 11 rupiah, printed: a price-input error, not rounding
    assert c["fp_po_total"]["status"] == "fail" and "11.00 from the order's total" in c["fp_po_total"]["why"]
    assert c["dates"]["status"] == c["docs_complete"]["status"] == "pass"
    assert c["vendor_is_samb"]["status"] == "info"      # information only: the PO joined SAMB's SO by its number


def test_an_ai_reading_counts_within_the_allowance():
    """Tesseract couldn't read the PO's total or the receipt's: the AI's readings fit Satellite, so they pass. The
    rows only explain (a row's quantity never decides)."""
    c, _ = boots(po_ok=CHECK, grn_ok=CHECK)
    assert c["fp_po_total"]["status"] == c["received"]["status"] == "pass"
    assert c["fp_po_lines"]["status"] == "info"
    c, _ = boots(po_ok=CHECK, po_total="1126100.00")  # beyond, only the AI read it: it looks again first
    assert c["fp_po_total"]["status"] == "unknown" and c["fp_po_total"]["ask"] == [(4, "total")]


def test_a_receipt_that_shows_the_tolakan_passes_and_it_is_named():
    c, _ = boots(grn_total=f"{TOTAL / 2:.2f}", cgr=2, rejected=2, reason="TOLAK TOKO ( OVER STOCK )", received=0.5)
    assert c["received"]["status"] == "pass" and "tolakan" in c["received"]["why"]
    assert c["received"]["tolakan"] == ["line 10: 2 pieces (TOLAK TOKO ( OVER STOCK ))"]
    assert c["fp_po_total"]["status"] == "pass"         # the order side is still the order: the FP never changes


def test_a_receipt_that_disagrees_with_what_satellite_received():
    # the receipt prints the whole order, Satellite's goods receipt records half rejected
    c, _ = boots(grn_total="1126006.00", cgr=2, rejected=2, reason="TOLAK TOKO", received=0.5)
    assert c["received"]["status"] == "fail" and "tolakan" in c["received"]["why"]
    c, _ = boots(grn_total="1126006.00", grn_ok=CHECK, cgr=2, rejected=2, reason="TOLAK TOKO", received=0.5)
    assert c["received"]["status"] == "unknown" and c["received"]["ask"]   # only the AI read it: look again first


def test_the_checks_never_change_a_page():
    _, pages = boots()
    before = copy.deepcopy(pages)
    docs = [(3, "FP"), (4, "PO"), (5, "TTG")]
    rows = [(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t in docs if t != "FP"]
    crosscheck.check_bundle(SO["sor_no"], docs, pages, SO, [LINE], matching.match(rows, [LINE], {}, {}))
    assert pages == before


def test_status_and_reasons():
    passing = {"sor_in_satellite": {"status": "pass", "why": ""}, "fpj": {"status": "n/a", "why": ""}}
    clear = {1: {"outcome": "clear"}}
    assert crosscheck.decide(passing, clear) == ("auto_ok", [])
    assert crosscheck.decide(passing, {1: {"outcome": "waiting_ai"}})[0] == "grouping"
    assert crosscheck.decide(passing, {1: {"outcome": "needs_person"}}) == \
        ("needs_review", ["pages [1]: their type, key or FP values wait for a person"])
    open_ = {"received": {"status": "unknown", "why": "w"}}
    assert crosscheck.decide(open_, clear, "reviewed", "a", "a") == ("reviewed", ["Received vs Satellite's CGR: w"])
    status, reasons = crosscheck.decide(open_, clear, "reviewed", "a", "b")
    assert status == "needs_review" and reasons[0].startswith("it was reviewed, then")
