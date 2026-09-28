"""Verification redesign, S3 (the user, 2026-09-26/27, decisions 4, 5 and 8): each customer (chain) gets two looks from
a person, once each. Its first bundle sets how far its amounts may be from Satellite's (its rounding: Rp 5 until
then); its first bundle Satellite records a tolakan on says what its receipts print. A customer whose receipts print
the whole order passes without a tolakan, and goes to a person with one."""
import os
from datetime import date

import pytest

from common import db
from grouper import crosscheck
from test_vf_totals import amount, line, page, so

HERO = {"chain": "1100002447", "name": "chain 1100002447 (HERO DC PBF [320] CIBITUNG BEKASI)"}


def checks(po_total, profile=None, ttg_total=None, **kw):
    s, lines = kw.pop("the_so", None) or so(), kw.pop("lines", None) or [line()]
    pages = {1: page("FP"), 2: page("PO", {"total": amount(po_total)})}
    docs = [(1, "FP"), (2, "PO")]
    if ttg_total is not None:
        pages[3], docs = page("TTG", {"total": amount(ttg_total)}), docs + [(3, "TTG")]
    return crosscheck.check_bundle("SOR1", docs, pages, s, lines, {}, ("FP",), date(2026, 9, 23), profile=profile)


def test_a_new_customers_first_bundle_waits_for_its_allowance():
    s = so()
    c = checks(s["order_total"] + 9.36, HERO)                     # Hero's rounded carton prices: 9.36 off, printed
    assert c["fp_po_total"]["status"] == "fail" and c["fp_po_total"]["gap"] == 9.36
    cal = c["calibration"]
    assert cal["status"] == "unknown" and cal["calibrate"][0]["what"] == "allowance"
    assert cal["calibrate"][0]["suggest"] == 10 and "9.36" in cal["why"]
    assert crosscheck.decide(c, {1: {"outcome": "clear"}})[0] == "needs_review"
    # even a bundle within Rp 5 waits for that first look
    assert checks(s["order_total"] + 1.0, HERO)["calibration"]["status"] == "unknown"


def test_once_confirmed_the_customers_rounding_passes():
    s = so()
    c = checks(s["order_total"] + 9.36, {**HERO, "allowance": 20})
    assert c["fp_po_total"]["status"] == "pass" and "up to Rp 20" in c["fp_po_total"]["why"]
    assert c["calibration"]["status"] == "pass"
    assert crosscheck.decide(c, {1: {"outcome": "clear"}}) == ("auto_ok", [])
    assert checks(s["order_total"] + 26, {**HERO, "allowance": 20})["fp_po_total"]["status"] == "fail"   # beyond


def test_the_suggestion_is_the_smallest_round_step_that_covers_the_gap():
    assert [crosscheck.allowance_for(g) for g in (None, 0.25, 5.0, 9.36, 14.36, 19.79, 99.0)] == \
        [5, 5, 5, 10, 15, 20, 100]
    assert crosscheck.allowance_for(150.0) is None                     # not rounding: a person looks at it


def rejected():
    net10, net5 = round(42265.68 * 10, 2), round(42265.68 * 5, 2)
    s = so(order_dpp=net10, order_ppn=round(net10 * .11, 2), order_total=round(net10 * 1.11, 2),
           dpp=net5, ppn=round(net5 * .11, 2), total=round(net5 * 1.11, 2))
    ln = line(line_amount=net10, vat=round(net10 * .11, 2), qty_pcs=10, cgr_qty=5, rejected_qty=5,
              reject_reason="TOLAK TOKO", invoice_amount=net5, invoice_qty=5)
    return s, ln


def test_the_first_tolakan_asks_what_the_receipt_prints():
    s, ln = rejected()
    c = checks(s["order_total"], {**HERO, "allowance": 5}, ttg_total=s["total"], the_so=s, lines=[ln])
    ask = c["calibration"]["calibrate"]
    assert c["calibration"]["status"] == "unknown" and [a["what"] for a in ask] == ["receipt"]
    assert ask[0]["suggest"] == "received"                    # the receipt showed the lower total
    done = checks(s["order_total"], {**HERO, "allowance": 5, "receipt_shows": "received"}, ttg_total=s["total"],
                  the_so=s, lines=[ln])
    assert done["calibration"]["status"] == done["received"]["status"] == "pass"


def test_a_customer_whose_receipts_print_the_whole_order():
    s, ln = rejected()
    ordered = {**HERO, "allowance": 5, "receipt_shows": "ordered"}
    c = checks(s["order_total"], ordered, ttg_total=s["order_total"], the_so=s, lines=[ln])
    assert c["received"]["status"] == "unknown" and "can't show the tolakan" in c["received"]["why"]
    plain = so()                                               # no tolakan: what was ordered is what was received
    c = checks(plain["order_total"], ordered, ttg_total=plain["total"])
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
        assert crosscheck.calibrate(chain, "a test chain", "tester", allowance=15) == []   # no bundle of it anywhere
        assert crosscheck.calibrate(chain, "a test chain", "tester", receipt_shows="received") == []
        with db.connect() as c:
            r = c.execute("""SELECT rounding_allowance, allowance_by, receipt_shows, receipt_by
                               FROM satellite.customer_profile WHERE customer_code=%s""", (chain,)).fetchone()
        assert (float(r["rounding_allowance"]), r["allowance_by"], r["receipt_shows"], r["receipt_by"]) == \
            (15.0, "tester", "received", "tester")
        with pytest.raises(ValueError):
            crosscheck.calibrate(chain, "a test chain", "tester", receipt_shows="sometimes")
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM satellite.customer_profile WHERE customer_code=%s", (chain,))
