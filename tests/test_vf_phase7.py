"""Phase 7 acceptance on the sample (vlm-first), stage by stage. Graded in the UI only: PO and TTG values against the
answer key read by eye, FP values against Satellite's SO record. The pipeline never sees either as an answer.

Built so far: 7.0 (the grading) and 7a (common/gates.py, rules that take ✅ away). At 7.0 the grading found 16 wrong
values with ✅, the pack-size reads counted (Hero POs 18/24/27: TOTAL NET PURCHASE read as the total; FP amounts cut
at the scan's edge on 10 and 26; 8's faint total; 29's two wrong cut digits that still added up; table quantities from
the wrong column on 7, 9, 12 and 21), and 14 one-digit bends that passed. 7a removed all of them, and cost 33 right
✅ (7 FP amounts Satellite settles in 7b; 26 table quantities and units, for a person until then)."""
import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("API_URL", "http://localhost:8000")
BID = "b-4bab9b736d"
RIGHT_AFTER_7A = 137       # right values with ✅ once 7a was adopted (2026-09-25); later stages only add to it


@pytest.fixture(scope="module")
def p7():
    r = httpx.get(f"{UI}/api/batches/{BID}/phase7", timeout=120)
    if r.status_code == 404:
        pytest.skip("sample batch not cloned into vlm-first")
    return r.json()


def _gate(p7, start):
    return next((ok, detail) for label, ok, detail in p7["checks"] if label.startswith(start))


def test_grouping_still_right(p7):
    ok, detail = _gate(p7, "Grouping still right")
    assert ok, detail


def test_no_wrong_checkmark(p7):
    wrong = [f"p{g['page']} {g['path']}={g['got']!r} (truth {g['want']!r}, ✅ by {g['by']})" for g in p7["wrong"]]
    assert not wrong, wrong


def test_every_bend_caught(p7):
    assert not p7["bend_missed"], p7["bend_missed"]


def test_right_checkmarks_kept(p7):
    """Taking ✅ away is always safe, but never silently: fewer right ✅ than at 7a means a rule got stricter."""
    assert p7["right"]["ok"] >= RIGHT_AFTER_7A, p7["right"]


# ---- the grader itself: it must not call a wrong value right, nor a right one wrong ----

def test_quantities_in_pieces():
    from api.app import _pieces
    assert _pieces("3 KTN", "KTN", 48) == 144
    assert _pieces("40.00", "PC", 40) == 40
    assert _pieces("52 CT", None, 24) == 1248
    assert _pieces("1,248", "EA", 24) == 1248
    assert _pieces("2", "EA", 72) == 2               # p12: 2 read as eaches is not the 144 received
    assert _pieces("1 x 48", None, 48) is None       # a pack size is not a quantity


def test_a_pack_size_is_never_a_quantity():
    """Hari Hari prints '40.00 PC' (pieces per carton) beside '1 KTN' received: reading the first is wrong even though
    one carton holds 40. A Hero PO prints the same quantity twice ('52 CT' and '1,248 EA'): either is right."""
    from api.app import _grade_customer_cell
    hari = {"qty": "1 KTN", "uom": "KTN", "pack": 40, "pcs": 40}
    assert _grade_customer_cell("qty", "40.00", {"uom": "PC"}, hari)[0] == "WRONG"
    assert _grade_customer_cell("qty", "1 KTN", {"uom": "KTN"}, hari)[0] == "right"
    hero_po = {"qty": "52 CT", "uom": "CT", "also": ["1,248 EA"], "pack": 24, "pcs": 1248}
    assert _grade_customer_cell("qty", "52 CT", {}, hero_po)[0] == "right"
    assert _grade_customer_cell("qty", "1,248", {"uom": "EA"}, hero_po)[0] == "right"
    assert _grade_customer_cell("qty", "1 x 24", {}, hero_po)[0] == "WRONG"
    hero_grn = {"qty": "144", "uom": "EA", "pack": 72, "pcs": 144}
    assert _grade_customer_cell("qty", "2", {"uom": "EA"}, hero_grn)[0] == "WRONG"      # page 12: cartons ordered


def test_rows_pair_by_code_then_description_then_quantity():
    from api.app import _codes, _pair, _pieces
    key = [{"code": "81244379", "ean": "8852021647335", "description": "PRODIET 85G TUNA", "pack": 48, "pcs": 144},
           {"code": "81244379", "ean": "8852021647335", "description": "PRODIET 85G TUNA", "pack": 48, "pcs": 48,
            "bonus": True},
           {"code": None, "ean": "8852021647359", "description": "PRODIET 85G KITTEN TUNA", "pack": 48, "pcs": 144}]
    key = [{**k, "_codes": _codes(k)} for k in key]
    stored = [{"item_code": "81244379", "material_description": "PRODIET 85G TUNA", "qty": "1 KTN"},     # the bonus row
              {"item_code": "81244349", "material_description": "PRODIET 85G KITTEN TUNA", "qty": "3 KTN"}]  # misread code
    pairs = _pair(stored, key, lambda x: x["item_code"], lambda x: x["material_description"],
                  lambda row, k: _pieces(row["qty"], None, k["pack"]) == k["pcs"])
    assert pairs == [1, 2]


def test_fp_rows_pair_by_description_so_a_misread_code_is_wrong():
    from api.app import _grade_fp_cell, _pair
    so = [{"line_no": 10, "item_code": "1000566", "description": "PRODIET ADULT 85GR OCEAN FISH", "pcs_per_uom": 48,
           "invoice_qty": 144, "qty_pcs": 144},
          {"line_no": 40, "item_code": "1000564", "description": "PRODIET KITTEN 85GR TUNA", "pcs_per_uom": 48,
           "invoice_qty": 144, "qty_pcs": 144}]
    so = [{**s, "_codes": {s["item_code"]}} for s in so]
    stored = [{"kode_material": "1000566", "nama_produk": "PRODIET ADULT 85GR OCEAN FISH"},
              {"kode_material": "1000566", "nama_produk": "PRODIET KITTEN 85GR TUNA"}]     # page 1 read 1000566 twice
    pairs = _pair(stored, so, lambda x: x["kode_material"], lambda x: x["nama_produk"], lambda r, s: False,
                  code_weight=0.5)
    assert pairs == [0, 1]
    assert _grade_fp_cell("kode_material", "1000566", so[1])[0] == "WRONG"
    assert _grade_fp_cell("qty_crt", "3", so[1])[0] == "right"
    assert _grade_fp_cell("qty_pcs", "0", so[1])[0] == "right"


def test_fp_quantity_and_pack_size():
    from api.app import _grade_fp_cell
    hari = {"item_code": "1000371", "description": "SENNA KRUPUKKU 500GR UDANG", "pcs_per_uom": 24, "invoice_qty": 72,
            "qty_pcs": 72}
    assert _grade_fp_cell("qty_crt", "3", hari)[0] == "right"     # 72 pieces at 24 a carton print '3 / 0'
    assert _grade_fp_cell("qty_pcs", "0", hari)[0] == "right"
    boots = {**hari, "pcs_per_uom": 8, "invoice_qty": 4, "qty_pcs": 4}
    assert _grade_fp_cell("qty_crt", "0", boots)[0] == "right"    # 4 pieces at 8 a carton print '0 / 4'
    assert _grade_fp_cell("kemasan", "6X425ML", boots)[0] == "WRONG"
    assert _grade_fp_cell("kemasan", "8X425ML", boots)[0] == "right"
    assert _grade_fp_cell("kemasan", "75GR", boots)[0] is None     # names no count: can't be graded


def test_answer_key_rows_add_up():
    """Each row read by eye: pieces = cartons × pieces per carton; only PO and TTG pages carry rows."""
    import json
    g = json.load(open(os.path.join(os.path.dirname(__file__), "..", "testdata", "golden_p1-32.json")))
    for pg, rows in g["lines"].items():
        assert g["page_types"][pg] in ("PO", "TTG"), pg
        for k in rows:
            n = float(k["qty"].split()[0].replace(",", ""))
            want = n * k["pack"] if k["uom"] in ("KTN", "CT") else n
            assert want == k["pcs"], (pg, k)
