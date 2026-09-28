"""Phase 7a rules (common/gates.py), pure: one case per rule, with the sample's own values. The rules only take ✅
away; the one exception is DPP + PPN = Total over two amounts something besides the AI backs."""
import copy
from datetime import date

from common import gates


def v(value, source=None):
    return {"value": value, "source_text": source or value}


OK, CHECK = {"verdict": "ok", "by": "text"}, {"verdict": "check", "why": "not in Tesseract's text"}


def ok(by):
    return {"verdict": "ok", "by": by}


def res(header, lines=()):
    return {"version": 1, "header": header, "lines": list(lines), "summary": {}}


def fp(dpp, ppn, total):
    return {"dpp": v(*dpp), "ppn": v(*ppn), "total": v(*total)}


def test_whole_amounts_only():
    assert gates.complete("1.126.011,00") and gates.complete(" 4.321.215,41 ") and gates.complete("0,00")
    for cut in ("106.86", "971.468", "1.014.424,3", "1.078.330,", "11.911.105"):
        assert not gates.complete(cut), cut


def test_an_fp_amount_cut_at_the_edge_loses_its_check():
    # page 10: PPN 106.861,53 printed, "106.86" left by the scan's edge (and read so by Tesseract too)
    f = fp(("971468.48", "971.468,48"), ("106.86", "106.86"), ("1078330.01", "1.078.330,01"))
    out = gates.apply("FP", f, res({"dpp": OK, "ppn": OK, "total": OK}))
    assert out["header"]["ppn"]["verdict"] == "check" and "whole amount" in out["header"]["ppn"]["why"]
    assert out["header"]["dpp"] == OK


def test_sums_over_the_ais_own_values_prove_nothing():
    # page 29: the AI read .95 → .90 and .28 → .23; with the PPN they add up exactly
    f = fp(("9283593.90", "9.283.593,90"), ("1021195.33", "1.021.195,33"), ("10304789.23", "10.304.789,23"))
    out = gates.apply("FP", f, res({"dpp": CHECK, "ppn": OK, "total": CHECK}))
    assert out["header"]["dpp"]["verdict"] == "check" and out["header"]["total"]["verdict"] == "check"
    assert "only the AI read the others" in out["header"]["total"]["why"]


def test_sums_fill_in_one_amount_from_two_backed_ones():
    f = fp(("4321215.41", "4.321.215,41"), ("475333.70", "475.333,70"), ("4796549.11", "4.796.549,11"))
    out = gates.apply("FP", f, res({"dpp": OK, "ppn": ok("satellite"), "total": CHECK}))
    assert out["header"]["total"] == {"verdict": "ok", "by": "adds_up"}
    bent = {**f, "total": v("4796550.11", "4.796.550,11")}     # Rp 1 off: the old rule let it through
    assert gates.apply("FP", bent, res({"dpp": OK, "ppn": OK, "total": CHECK}))["header"]["total"]["verdict"] == "check"


def test_three_whole_amounts_that_dont_add_up():
    # page 8: faint .80 read as .86 by the look-again; 679,292.82 + 74,722.28 = 754,015.10
    f = fp(("679292.82", "679.292,82"), ("74722.28", "74.722,28"), ("754022.86", "754.022,86"))
    out = gates.apply("FP", f, res({"dpp": OK, "ppn": ok("zoom"), "total": ok("second_look")}))
    assert all(out["header"][k]["verdict"] == "check" for k in ("dpp", "ppn", "total"))
    assert "misread" in out["header"]["total"]["why"]
    kept = gates.apply("FP", f, res({"dpp": ok("satellite"), "ppn": OK, "total": OK}))
    assert kept["header"]["dpp"] == ok("satellite")             # Satellite and a person are never overruled


def test_a_po_total_must_include_ppn():
    # page 18: TOTAL NET PURCHASE (before tax) read as the total; 9,410,527 × 11% = the PPN printed
    p18 = {"total": v("9410527.00"), "ppn": v("1035158.00")}
    out = gates.apply("PO", p18, res({"total": OK, "ppn": OK}))
    assert out["header"]["total"]["verdict"] == "check" and "computed on" in out["header"]["total"]["why"]
    p11 = {"total": v("1078329.00"), "ppn": v("106861.00")}     # TOTAL NETTO = 971,468 + 106,861
    assert gates.apply("PO", p11, res({"total": OK, "ppn": OK}))["header"]["total"] == OK
    p24 = {"total": v("36445251.00"), "ppn": {}}                # the page ends before PPN and TOTAL NETTO
    out = gates.apply("PO", p24, res({"total": OK, "ppn": {"verdict": "empty"}}))
    assert out["header"]["total"]["verdict"] == "check"
    assert gates.apply("PO", p24, res({"total": ok("person"), "ppn": {"verdict": "empty"}}))["header"]["total"] == \
        ok("person")


def test_quantities_and_units_need_more_than_their_row():
    # page 12: the row prints 2 (cartons ordered) and 144 (received); "2" was on the row, so it had ✅
    lines = [{"item_code": OK, "qty": OK, "uom": OK, "material_description": OK}]
    out = gates.apply("TTG", {"lines": []}, res({}, lines))["lines"][0]
    assert out["qty"]["verdict"] == "check" and out["uom"]["verdict"] == "check"
    assert out["item_code"] == OK and out["material_description"] == OK
    assert gates.columns([{"qty": ok("person")}])[0]["qty"] == ok("person")
    fp_row = gates.columns([{"qty_crt": OK, "qty_pcs": OK, "kode_material": OK}])[0]
    assert fp_row["qty_crt"]["verdict"] == fp_row["qty_pcs"]["verdict"] == "check" and fp_row["kode_material"] == OK


def test_no_date_after_the_scan_day():
    later = {"posting_date": v("2026-09-30")}
    out = gates.apply("TTG", later, res({"posting_date": OK}), date(2026, 9, 23))
    assert out["header"]["posting_date"]["verdict"] == "check"
    before = {"posting_date": v("2026-09-09")}
    assert gates.apply("TTG", before, res({"posting_date": OK}), date(2026, 9, 23))["header"]["posting_date"] == OK
    assert gates.apply("TTG", later, res({"posting_date": OK}))["header"]["posting_date"] == OK   # no scan day known


def test_the_rules_never_add_a_check_but_by_sums_and_leave_their_input_alone():
    cases = [("FP", fp(("679292.82", "679.292,82"), ("74722.28", "74.722,28"), ("754022.86", "754.022,86")),
              res({"dpp": OK, "ppn": CHECK, "total": ok("second_look")}, [{"qty_crt": OK}])),
             ("PO", {"total": v("9410527.00"), "ppn": v("1035158.00")}, res({"total": OK, "ppn": OK}, [{"qty": OK}]))]
    for dt, f, r in cases:
        before = copy.deepcopy(r)
        out = gates.apply(dt, f, r)
        assert r == before
        for k, x in out["header"].items():
            assert x["verdict"] != "ok" or x.get("by") == "adds_up" or before["header"][k] == x
        assert out["summary"]["header"]["ok"] == sum(x["verdict"] == "ok" for x in out["header"].values())
