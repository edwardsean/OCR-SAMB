"""Each customer's amounts may differ from Satellite's by up to Rp 1,000 per document, and nobody is asked for it (the
mentor, 2026-10-08: "every customer's selisih wajar max is 1000 rupiah; if more than that, flag the anomaly"; before,
S3 asked a person for each customer's own rounding, Rp 5 until then). What stays from S3: a customer's first bundle
Satellite records a tolakan on asks once what its receipts print. A customer whose receipts print the whole order
passes without a tolakan, and goes to a person with one."""
import os
from datetime import date

import pytest

from common import db
from grouper import crosscheck
from test_vf_totals import amount, line, page, so

HERO = {"chain": "1100002447", "name": "chain 1100002447 (HERO DC PBF [320] CIBITUNG BEKASI)"}


def checks(po_total, profile=None, ttg_qty=None, **kw):
    """ttg_qty: the receipt's one row, in pieces (a receipt is compared in quantities, 2026-09-28)."""
    from grouper import matching
    s, lines = kw.pop("the_so", None) or so(), kw.pop("lines", None) or [line()]
    pages = {1: page("FP"), 2: page("PO", {"total": amount(po_total)})}
    docs = [(1, "FP"), (2, "PO")]
    if ttg_qty is not None:
        pages[3], docs = page("TTG", rows=[{"item_code": "1000001", "qty": str(ttg_qty), "uom": "PCS"}]), docs + [(3, "TTG")]
    m = matching.match([(3, "TTG", matching.rows_of("TTG", pages[3]["fields"]))], lines, {}, {}) if 3 in pages else {}
    return crosscheck.check_bundle("SOR1", docs, pages, s, lines, m, ("FP",), date(2026, 9, 23), profile=profile)


def test_a_new_customer_is_never_asked_and_may_differ_by_up_to_rp_1000():
    s = so()
    c = checks(s["order_total"] + 9.36, HERO)                     # Hero's rounded carton prices: 9.36 off, printed
    assert c["fp_po_total"]["status"] == "pass" and c["fp_po_total"]["gap"] == 9.36
    assert c["calibration"]["status"] == "pass" and "Rp 1,000" in c["calibration"]["why"]
    assert crosscheck.decide(c, {1: {"outcome": "clear"}}) == ("auto_ok", [])
    assert checks(s["order_total"] + 999.99, HERO)["fp_po_total"]["status"] == "pass"


def test_more_than_rp_1000_is_flagged_for_a_person():
    s = so()
    c = checks(s["order_total"] + 1000.01, HERO)
    assert c["fp_po_total"]["status"] == "fail" and c["fp_po_total"]["gap"] == 1000.01
    assert crosscheck.decide(c, {1: {"outcome": "clear"}})[0] == "needs_review"


def test_an_allowance_saved_before_no_longer_counts():
    s = so()
    assert checks(s["order_total"] + 26, {**HERO, "allowance": 20})["fp_po_total"]["status"] == "pass"   # was beyond 20
    assert checks(s["order_total"] + 1500, {**HERO, "allowance": 5000})["fp_po_total"]["status"] == "fail"


def test_an_allowance_can_no_longer_be_set():
    from api import actions
    with pytest.raises(actions.ActionError) as e:
        actions.calibrate("1100002447", "Hero", "tester", allowance="20")
    assert e.value.status == 400 and "Rp 1,000" in str(e.value)
    assert not hasattr(crosscheck, "allowance_for") and not hasattr(crosscheck, "STEPS")


def rejected():
    net10, net5 = round(42265.68 * 10, 2), round(42265.68 * 5, 2)
    s = so(order_dpp=net10, order_ppn=round(net10 * .11, 2), order_total=round(net10 * 1.11, 2),
           dpp=net5, ppn=round(net5 * .11, 2), total=round(net5 * 1.11, 2))
    ln = line(line_amount=net10, vat=round(net10 * .11, 2), qty_pcs=10, cgr_qty=5, rejected_qty=5,
              reject_reason="TOLAK TOKO", invoice_amount=net5, invoice_qty=5)
    return s, ln


def test_the_first_tolakan_asks_what_the_receipt_prints():
    s, ln = rejected()
    c = checks(s["order_total"], {**HERO, "allowance": 5}, ttg_qty=5, the_so=s, lines=[ln])
    ask = c["calibration"]["calibrate"]
    assert c["calibration"]["status"] == "unknown" and [a["what"] for a in ask] == ["receipt"]
    assert ask[0]["suggest"] == "received"                    # the receipt showed what was received: 5 of 10
    done = checks(s["order_total"], {**HERO, "allowance": 5, "receipt_shows": "received"}, ttg_qty=5,
                  the_so=s, lines=[ln])
    assert done["calibration"]["status"] == done["received"]["status"] == "pass"


def test_a_customer_whose_receipts_print_the_whole_order():
    s, ln = rejected()
    ordered = {**HERO, "allowance": 5, "receipt_shows": "ordered"}
    c = checks(s["order_total"], ordered, ttg_qty=10, the_so=s, lines=[ln])
    assert c["received"]["status"] == "unknown" and "can't show the tolakan" in c["received"]["why"]
    plain = so()                                               # no tolakan: what was ordered is what was received
    c = checks(plain["order_total"], ordered, ttg_qty=24)
    assert c["received"]["status"] == "pass"


def test_the_calibration_is_never_accepted_away():
    c = crosscheck.accept({"calibration": {"status": "unknown", "why": "w"}},
                          {"calibration": {"input_print": crosscheck.check_print({"status": "unknown", "why": "w"}),
                                           "decided_by": "x", "reason": "rounding"}})
    assert c["calibration"]["status"] == "unknown"


@pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
def test_calibrate_stores_the_answer_once_per_customer():
    chain = "TEST-CHAIN-S3"
    try:
        assert crosscheck.calibrate(chain, "a test chain", "tester", receipt_shows="received") == []   # no bundle of it
        with db.connect() as c:
            r = c.execute("""SELECT receipt_shows, receipt_by FROM satellite.customer_profile WHERE customer_code=%s""",
                          (chain,)).fetchone()
        assert (r["receipt_shows"], r["receipt_by"]) == ("received", "tester")
        with pytest.raises(ValueError):
            crosscheck.calibrate(chain, "a test chain", "tester", receipt_shows="sometimes")
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM satellite.customer_profile WHERE customer_code=%s", (chain,))
