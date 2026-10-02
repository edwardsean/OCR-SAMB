"""Phase 5: every value the AI OCR read is checked by plain code (common/verify.py).
Unit tests pin each rule; the acceptance test reads the batch's phase-5 checks (pages 1–31)."""
import os

import pytest
import hashlib

import httpx

from common import verify

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") == "vlm-first", reason="v1 acceptance; vlm-first has its own")

UI = os.environ.get("API_URL", "http://localhost:8000")
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def v(value, source):
    return {"value": value, "source_text": source}


FP_TEXT = """Sales Order [SO] # : SOR26110255837
Nomor CPO : 4505832724
Dasar Pengenaan Pajak 1.014.424,36
TOTAL 1.126.911,00"""


def fp(**over):
    f = {"sor": v("SOR26110255837", "SOR26110255837"), "dpp": v("1014424.36", "1.014.424,36"),
         "ppn": v("111586.68", "111.586,68"), "total": v("1126011.04", "1.126.011,04"),
         "nomor_cpo": v("4505832724", "4505832724"), "customer_name": v(None, None), "customer_code": v(None, None)}
    f.update(over)
    return f


def test_printed_value_is_confirmed():
    h = verify.header("FP", fp(), FP_TEXT, None)
    assert h["nomor_cpo"] == {"verdict": "ok", "by": "text"}
    assert h["dpp"] == {"verdict": "ok", "by": "text"}
    assert h["customer_name"] == {"verdict": "empty"}


def test_amounts_that_add_up_are_confirmed():
    # Tesseract misread the total (1.126.911) and never saw the PPN; DPP + PPN = Total and PPN = 11% of DPP
    h = verify.header("FP", fp(), FP_TEXT, None)
    assert h["ppn"]["by"] == "adds_up" and h["total"]["by"] == "adds_up"


def test_amounts_that_dont_add_up_go_to_a_person():
    h = verify.header("FP", fp(ppn=v("111588.68", "111.588,68")), FP_TEXT, None)
    assert h["ppn"]["verdict"] == "check" and h["total"]["verdict"] == "check"


def test_no_close_enough():
    # page 22: printed S10232, Tesseract read $10232, the AI read 510232 -> a person, not a match
    text = "Kepada YTH : $10232 PT. SARANA ABADI MAKMUR BERSAMA"
    f = {"vendor_number": v("510232", "510232")}
    assert verify.header("TTG", f, text, None)["vendor_number"]["verdict"] == "check"


def test_value_must_match_its_own_printed_text():
    h = verify.header("FP", fp(nomor_cpo=v("4505832725", "4505832724")), FP_TEXT, None)
    assert h["nomor_cpo"]["verdict"] == "check" and "doesn't match" in h["nomor_cpo"]["why"]


def test_qr_decides_the_fp_sor():
    assert verify.header("FP", fp(), "", "SOR26110255837")["sor"] == {"verdict": "ok", "by": "qr"}
    other = verify.header("FP", fp(), FP_TEXT, "SOR26110255838")["sor"]
    assert other["verdict"] == "check" and "QR code says" in other["why"]


def test_never_glued_across_the_page():
    # the digits appear, but split over lines that aren't neighbours: not "printed"
    text = "No PO : 45058\nsomething else\n32724"
    assert verify.header("PO", {"purchase_order_no": v("4505832724", "4505832724")}, text, None)["purchase_order_no"]["verdict"] == "check"
    # a value that wraps onto the next line is still found
    assert verify.header("PO", {"purchase_order_no": v("4505832724", "4505832724")}, "No PO : 45058\n32724", None)["purchase_order_no"]["verdict"] == "ok"


def test_a_number_never_matches_inside_a_longer_number():
    f = {"purchase_order_no": v("450583272", "450583272")}          # last digit dropped
    assert verify.header("PO", f, "No PO : 4505832724", None)["purchase_order_no"]["verdict"] == "check"
    t = {"total": v("1126011", "1.126.011")}                        # decimals dropped
    assert verify.header("PO", t, "Total 1.126.011,00", None)["total"]["verdict"] == "check"
    assert verify.header("PO", t, "Total 1.126.011 IDR", None)["total"]["verdict"] == "ok"
    # Tesseract splitting a number with a space is fine; a space next to it is a boundary
    assert verify.found("4505832724", verify.windows("PO 4505 832724 12"))


def test_short_values_only_count_on_their_own_row():
    text = "Delivery 4 days\n81244362 PRODIET 85G OCEAN FISH 48.00 PC 3 KTN\n81244379 PRODIET 85G TUNA 48.00 PC 1 KTN"
    rows = [{"item_code": "81244379", "material_description": "PRODIET 85G TUNA", "qty": "1", "uom": "KTN",
             "row_text": "81244379 PRODIET 85G TUNA 48.00 PC 1 KTN"},
            {"item_code": "81244362", "material_description": "PRODIET 85G OCEAN FISH", "qty": "4", "uom": "KTN",
             "row_text": "81244362 PRODIET 85G OCEAN FISH 48.00 PC 4 KTN"}]
    out = verify.lines("TTG", rows, text)
    assert all(c["verdict"] == "ok" for c in out[0].values())
    assert out[1]["qty"]["verdict"] == "check"          # "4" is on the page, but not on this row
    assert out[1]["item_code"]["verdict"] == "ok"


def test_dates_compare_by_meaning():
    assert verify.agrees("date", "2026-09-04", "04-Sep-2026") and verify.agrees("date", "2026-08-31", "31-AUG-26")
    assert not verify.agrees("date", "2026-09-05", "04-Sep-2026")


def test_phase5_acceptance():
    r = httpx.get(f"{UI}/api/batches/{BID}/phase5", timeout=60).json()
    failed = [(label, detail) for label, ok, detail in r["checks"] if not ok]
    assert not failed, failed
