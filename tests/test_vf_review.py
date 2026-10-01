"""Phase 7d, the Review screen: a person's line confirmation follows its row, an accepted difference holds only while
the check says the same, approval waits until nothing is left, and the screens answer."""
import os

import httpx
import pytest

from common import satellite
from grouper import crosscheck

UI = os.environ.get("UI_URL", "http://ui:8000")
BID = "b-4bab9b736d"
VF = os.environ.get("PIPELINE") == "vlm-first"


def test_rows_are_keyed_by_their_code_not_their_place():
    rows = [{"item_code": "3078035(8993417489938)"}, {"item_code": "81244362"}, {"item_code": "81244362"}, {}]
    assert satellite.row_keys("TTG", rows) == ["3078035", "81244362", "81244362#2", "ROW4"]


def test_a_line_confirmation_follows_its_row_when_the_rows_move():
    confirmed = {"lines[3015138].qty": {"value": "1248", "confirmed_by": "Edward"}}
    rows = [{"item_code": "2950381", "qty": "528"}, {"item_code": "3015138", "qty": "52"}]   # read in another order
    fields, verdicts = satellite.person_lines("TTG", {"lines": rows}, [{"qty": {"verdict": "check"}}] * 2, confirmed)
    assert fields["lines"][1]["qty"] == "1248" and fields["lines"][1]["ai_values"] == {"qty": "52"}
    assert verdicts[1]["qty"]["by"] == "person" and verdicts[0]["qty"] == {"verdict": "check"}
    assert fields["lines"][0]["qty"] == "528"
    _, h = satellite.settle("TTG", {"lines": rows}, {}, {}, confirmed)
    assert not any(k.startswith("lines") for k in h)                     # never taken for a header field


def test_an_accepted_difference_holds_only_while_the_check_says_the_same():
    fail = {"status": "fail", "why": "FP total 1,126,011.00 ≠ PO total 1,126,000.00 (11.00 apart)"}
    decision = {"input_print": crosscheck.check_print(fail), "reason": "the customer's own price", "note": "",
                "decided_by": "Edward"}
    out = crosscheck.accept({"fp_po_total": fail}, {"fp_po_total": decision})["fp_po_total"]
    assert out["status"] == "accepted" and "the customer's own price" in out["why"] and out["was"] == "fail"
    moved = {**fail, "why": "FP total 1,126,011.00 ≠ PO total 1,125,000.00 (1,011.00 apart)"}
    assert crosscheck.accept({"fp_po_total": moved}, {"fp_po_total": decision})["fp_po_total"]["status"] == "fail"
    assert crosscheck.decide({"fp_po_total": out}, {1: {"outcome": "clear"}})[0] == "auto_ok"


def test_approval_waits_until_nothing_is_left():
    clear = {1: {"outcome": "clear"}}
    ok = {"sor_in_satellite": {"status": "pass", "why": ""}, "fpj": {"status": "n/a", "why": ""},
          "fp_po_total": {"status": "accepted", "why": "rounding"}}
    assert crosscheck.can_approve(ok, clear) == (True, [])
    assert not crosscheck.can_approve({**ok, "received": {"status": "unknown", "why": "w"}}, clear)[0]
    assert not crosscheck.can_approve(ok, {1: {"outcome": "needs_person"}})[0]
    assert not crosscheck.can_approve(ok, {1: {"outcome": "waiting_ai"}})[0]


@pytest.mark.skipif(not VF, reason="vlm-first only")
def test_the_review_screens_answer():
    r = httpx.get(f"{UI}/review", params={"batch": BID}, timeout=60)
    assert r.status_code == 200 and "SOR26110257250" in r.text
    r = httpx.get(f"{UI}/review/SOR26110257250", params={"batch": BID}, timeout=60)
    assert r.status_code == 200 and "CGR" in r.text                    # the check "Barang diterima = CGR Satellite"
    assert any(pill in r.text for pill in ("Terkirim ke Satellite", "Siap disetujui", "Perlu Anda", "Menunggu sistem"))


# ------------------------------------------------------------------------------------------ S5: anomalies only

def test_a_bundle_never_checked_cant_be_approved():
    assert crosscheck.can_approve({}, {1: {"outcome": "clear"}})[0] is False


def test_a_page_shows_only_the_values_that_hold_it():
    """A PO waits for its key; its total and PPN are the bundle's to judge and its vendor code is kept as read, so
    the card asks for the PO number alone."""
    from ui import app
    check = {"verdict": "check", "why": "not in Tesseract's text"}
    pages = {6: {"outcome": "needs_person", "fields": {"purchase_order_no": {"value": "PO.2026.09.32029"},
                                                       "total": {"value": "775397.00"}},
                 "checks": {"header": {"purchase_order_no": check, "total": check, "ppn": check, "vendor_code": check}}},
             4: {"outcome": "clear", "fields": {}, "checks": {"header": {}}}}
    entry = lambda n, t, f: {"name": f, "label": f}
    items = app._open_items("b", "SOR1", [(4, "FP", [4]), (6, "PO", [6])], pages, {}, [], {}, entry)
    assert [(i["kind"], [f["name"] for f in i.get("fields", [])]) for i in items] == [("page", ["purchase_order_no"])]


def test_open_checks_become_cards_and_passing_ones_dont():
    from ui import app
    checks = {"sor_in_satellite": {"status": "pass", "why": ""}, "fp_po_lines": {"status": "info", "why": ""},
              "docs_complete": {"status": "fail", "why": "no TTG in the bundle", "print": "x"},
              "received": {"status": "waiting", "why": "no goods receipt yet", "print": "y"},
              "calibration": {"status": "unknown", "why": "first look", "print": "z"}}
    items = app._open_items("b", "SOR1", [], {}, checks, [], {}, lambda n, t, f: {})
    assert [(i["key"], i["accept"]) for i in items] == [("docs_complete", True), ("received", False)]
    assert items[0]["held_link"] == "/bundles?batch=b"


@pytest.mark.skipif(not VF, reason="vlm-first only")
def test_review_asks_only_for_what_is_left():
    """Before S5 the Duta Buah bundle drew 73 forms for 2 open items; a row or a value kept as read never asks."""
    import re
    from ui import app
    for batch, sor in ((BID, "SOR26110256810"), (BID, "SOR26110257259"), (BID, "SOR26110255837")):
        v = app.review_view(batch, sor)
        r = httpx.get(f"{UI}/review/{sor}", params={"batch": batch}, timeout=60)
        top = r.text.split('<details class="rv-all"')[0]
        cal = sum(len(c["asks"]) for c in [v["calibration"]] if c)
        allowed = sum(1 + len(i.get("fix") or []) + len(i.get("fields") or [])
                      + sum(1 for x in i.get("qty_fix") or [] if (x.get("key") and x.get("line")) or not x.get("line"))
                      for i in v["open_items"]) + cal + \
            (1 if v["can_approve"] else 0)
        assert len(re.findall(r"<form", top)) <= allowed, sor
        for d in v["documents"]:                          # quantity suggestions carry their unit (a bare 48 on a
            for row in d["rows"]:                         # carton row was taken as 48 cartons)
                for x in row["todo"]:
                    assert x["col"] != "qty" or all(val.endswith(" PCS") for val, _ in x["hints"]), (sor, row["key"])


def test_a_person_can_say_a_value_isnt_printed():
    """7000363700-03 p5: the AI took the receipt's quantity total 190.00 for its money total; the receipt prints none.
    The answer empties the value (the AI's reading kept), and the bundle never reads the AI's instead."""
    fields = {"total": {"value": "190.00", "source_text": "190.00"}, "purchase_order_no": {"value": "PO.RCV-1"}}
    said = {"total": {"value": satellite.NOT_PRINTED, "confirmed_by": "Edward"},
            "purchase_order_no": {"value": satellite.NOT_PRINTED, "confirmed_by": "Edward"}}
    f, h = satellite.settle("TTG", fields, {"total": {"verdict": "ok", "by": "text"}}, {}, said)
    assert f["total"] == {"value": satellite.NOT_PRINTED, "source_text": None, "ai_value": "190.00"}
    assert h["total"]["by"] == "person" and "not printed" in h["total"]["why"]
    from common import keys as keymod
    assert "po_no" not in keymod.derive("TTG", f, "", None, h)                  # no PO number: no key
    pages = {5: {"doc_type": "TTG", "fields": f, "fields_all": {"total": {"value": "190.00"}},
                 "checks": {"header": h}}}
    assert crosscheck._read(pages, [5], "TTG", "total") is None                 # never the AI's 190.00
