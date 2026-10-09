"""Documents that print one number (the user, 2026-10-09). Copies of one paper agree and count once; two that disagree
are two documents, so one number was misread, and the bundle goes to Review instead of passing with a foreign PO
counted as a 'copy' (a misread PO number that is another order's Nomor CPO joins that order's bundle)."""
from datetime import date

from grouper import crosscheck, matching
from test_vf_totals import TEXT, amount, line, page, so


def number(v, name="purchase_order_no"):
    return {name: ({"value": v, "source_text": v}, TEXT)}


def checks(*extra):
    """FP on page 1, then each (page_no, page) given, all in one bundle of SOR1."""
    pages, docs = {1: page("FP", {"total": amount(1.0)})}, [(1, "FP")]
    for n, p in extra:
        pages[n], docs = p, docs + [(n, p["doc_type"])]
    ls = [line()]
    m = matching.match([(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t in docs if t in ("PO", "TTG")],
                       ls, {}, {})
    return crosscheck.check_bundle("SOR1", docs, pages, so(), ls, m, scan_day=date(2026, 9, 23)), pages


def po(no, total=None, lines=None):
    return page("PO", {**number(no), **({"total": amount(total)} if total is not None else {})}, lines=lines)


def test_one_document_per_number_is_not_a_question():
    c, _ = checks((2, po("4505832724", so()["order_total"])))
    assert c["same_number"]["status"] == "n/a"


def test_copies_of_one_paper_agree_and_count_once():
    """Duta Buah's PO.2026.09.32029: two prints and a terms page carrying only the number."""
    t = so()["order_total"]
    c, _ = checks((2, po("PO.2026.09.32029", t)), (3, po("PO.2026.09.32029", t)), (4, po("PO.2026.09.32029")))
    assert c["same_number"]["status"] == "pass" and "copies, counted once" in c["same_number"]["why"]
    assert c["fp_po_total"]["status"] == "pass"                     # counted once, not three times


def test_a_misread_number_counted_as_a_copy_goes_to_review():
    """The real PO …3258 fits its order; page 3 is PO …3256 misread as …3258 (print backed it). Before, page 3 was
    skipped as a copy and the bundle passed."""
    t = so()["order_total"]
    c, pages = checks((2, po("58423258", t)), (3, po("58423258", 850_000.0)))
    s = c["same_number"]
    assert c["fp_po_total"]["status"] == "pass"                     # the first PO fits: the old rule saw nothing
    assert s["status"] == "fail" and "pages 2, 3 both print PO 58423258" in s["why"] and "misread" in s["why"]
    assert [(d["page"], d["total"]) for d in s["docs"]] == [(2, round(t, 2)), (3, 850_000.0)]
    assert crosscheck.decide(c, pages)[0] == "needs_review"


def test_without_totals_the_rows_amounts_decide_never_codes_or_prices():
    """AEON's two copies of PO 10101000125543: codes read 09768114 / 09788114, prices 20,160 / 20,180; the rows'
    printed amounts agree."""
    a = ["SIMPLE GENTLE 1.00 EACH 09768114 8999999600037 60.00 0.00 - 20,160.00 1,210,800.00 -",
         "SIMPLE PURE 1.00 EACH 09768138 8999999600020 48.00 0.00 - 20,160.00 968,640.00 -"]
    b = ["1 SIMPLE GENTLE 1.00 EACH 09788114 8999999600037 60.00 0.00 - 20,180.00 1,210,800.00 -",
         "3 SIMPLE PURE 1.00 EACH 09788138 8999999600020 48.00 0.00 - 20,180.00 968,640.00 -"]
    c, _ = checks((2, po("10101000125543", lines=a)), (3, po("10101000125543", lines=b)))
    assert c["same_number"]["status"] == "pass"
    other = ["SIMPLE GENTLE 1.00 EACH 09788114 8999999600037 30.00 0.00 - 20,180.00 605,400.00 -"]
    c, _ = checks((2, po("10101000125543", lines=a)), (3, po("10101000125543", lines=other)))
    assert c["same_number"]["status"] == "fail" and "rows differ" in c["same_number"]["why"]


def test_a_cut_total_is_not_compared():
    """An amount cut at the scan's edge isn't a reading of the whole number: the rows decide instead."""
    rows = ["1 X 24 EA 42,265.68 1,014,376.32"]
    cut = page("PO", {**number("58423258"), "total": ({"value": "1126011.0", "source_text": "1.126.011,"}, TEXT)},
               lines=rows)
    c, _ = checks((2, po("58423258", 1_126_011.0, lines=rows)), (3, cut))
    assert c["same_number"]["status"] == "pass"


def test_receipts_are_copies_by_their_receipt_number_too():
    ttg = lambda rows: page("TTG", number("GR-001", "document_no"), lines=rows)
    c, _ = checks((2, ttg(["X 24 PCS 24.00"])), (3, ttg(["X 24 PCS 24.00"])))
    assert c["same_number"]["status"] == "pass"
    c, _ = checks((2, ttg(["X 24 PCS 24.00"])), (3, ttg(["Y 10 PCS 10.00", "Z 5 PCS 5.00"])))
    assert c["same_number"]["status"] == "fail" and "TTG GR-001" in c["same_number"]["why"]


def test_review_shows_both_pages_and_a_fix_for_each_number():
    from api import app
    c, _ = checks((2, po("58423258", so()["order_total"])), (3, po("58423258", 850_000.0)))
    entry = lambda n, t, f: {"name": f, "label": f}
    items = app._open_items("b", "SOR1", [], {}, {"same_number": {**c["same_number"], "print": "p"}}, [], {}, entry)
    it, = items
    assert it["plain"].startswith("Dua PO bernomor sama") and it["accept"]
    assert [x[1] for x in it["pair"]] == [round(so()["order_total"], 2), 850_000.0] and it["gap"] is None
    assert [(f["page"], f["name"]) for f in it["fix"]] == [(2, "purchase_order_no"), (2, "total"),
                                                            (3, "purchase_order_no"), (3, "total")]
