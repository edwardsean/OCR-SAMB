"""Phase 7c product matching (grouper/matching.py), pure, with the sample's own rows: which SO line each PO or TTG row
is. Only a person, the product map, the SO's only line, a PO row's numbers, or the PO row a TTG row shares its code
with decide; the AI only proposes."""
from grouper import matching


def so(no, code, name, per, ordered, price_pcs, price_uom=None, cgr=None, rejected=0, reason=None):
    return {"line_no": no, "item_code": code, "description": name, "pcs_per_uom": per, "qty_pcs": ordered,
            "price_pcs": price_pcs, "price_uom": price_uom or price_pcs * per, "cgr_qty": cgr, "rejected_qty": rejected,
            "reject_reason": reason, "discounts": {}}


KAKI = [so(10, "1001032", "CAP KAKI TIGA ANAK STROBERI 238 ML CAN", 24, 1248, 5399.10, 129578.00),
        so(20, "1001046", "CAP KAKI TIGA PLAIN(ORIGINAL) 200 ML BT", 48, 528, 3412.01, 163777.00),
        so(30, "1001036", "CAP KAKI TIGA GUAVA 320ML CAN", 24, 264, 5734.25, 137622.00)]


def po_row(code, qty, price, desc="", row_text=""):
    return {"product_code": code, "product_description": desc, "qty": qty, "uom": "", "unit_price": price,
            "row_text": row_text}


def ttg_row(code, qty, uom="EA", row_text=""):
    return {"item_code": code, "material_description": "", "qty": qty, "uom": uom, "row_text": row_text}


def test_barcodes_carry_their_check_digit():
    assert matching.barcode_ok("8993417489938") and matching.barcode_ok("711844120150")      # EAN-13, UPC-A
    assert not matching.barcode_ok("8993417489937")


def test_a_row_as_the_matcher_sees_it():
    rows = matching.rows_of("TTG", {"lines": [
        {"item_code": "3078035(8993417489938)", "qty": "144", "uom": "EA", "row_text": "1 3078035(8993417489938) …"},
        {"item_code": "81244362", "qty": "1 KTN", "row_text": "81244362 8852021647342 PRODIET 85G OCEAN FISH "
                                                              "48.00 PC 1 KTN 0 0"}]})
    assert rows[0]["code"] == "3078035" and rows[0]["barcodes"] == {"8993417489938"} and not rows[0]["bonus"]
    assert rows[1]["code"] == "81244362" and rows[1]["bonus"]                     # Hari Hari's free carton


def test_quantities_and_prices_in_the_customers_units():
    assert matching.pieces({"qty": "52 CT"}, KAKI[0]) == 1248
    assert matching.pieces({"qty": "144", "uom": "EA"}, KAKI[0]) == 144
    assert matching.pieces({"qty": "1 x 48"}, KAKI[1]) is None                    # a pack size
    assert matching.price_fits("129,578.00", KAKI[0])                            # Hero: per carton
    assert matching.price_fits("63,361", so(10, "1003243", "", 8, 4, 57082.43, 456659.00))   # Boots: per piece + PPN
    assert matching.price_fits("63,000", so(10, "1003243", "", 8, 4, 57082.43, 456659.00)) is False
    assert matching.price_fits(None, KAKI[0]) is None


def test_po_rows_by_their_numbers_and_ttg_rows_by_the_po_row():
    po = matching.rows_of("PO", {"lines": [po_row("3015138", "52 CT", "129,578.00"),
                                           po_row("2950381", "11 CT", "163,777.00"),
                                           po_row("2950327", "11 CT", "137,622.00")]})
    grn = matching.rows_of("TTG", {"lines": [ttg_row("3015138", "1,248"), ttg_row("2950381", "528"),
                                             ttg_row("2950327", "264")]})
    m = matching.match([(18, "PO", po), (19, "TTG", grn)], KAKI, {}, {})
    assert [m[(18, i)]["line"] for i in range(3)] == [0, 1, 2] and m[(18, 0)]["how"] == "numbers"
    assert [m[(19, i)]["line"] for i in range(3)] == [0, 1, 2] and m[(19, 2)]["how"] == "po_row"


def test_rows_the_numbers_cant_tell_apart_stay_open():
    two = [so(20, "1002316", "ESKULIN 30 ML ECHANTED PETALS", 36, 288, 22072.08, 794595.00),
           so(30, "1003229", "ESKULIN EDP 30 ML AURORA BLUSH", 36, 288, 22072.08, 794595.00)]
    po = matching.rows_of("PO", {"lines": [po_row("3126351", "8 CT", "794,595.00"),
                                           po_row("3129330", "8 CT", "794,595.00")]})
    m = matching.match([(24, "PO", po)], two, {}, {})
    assert m[(24, 0)]["status"] == m[(24, 1)]["status"] == "none"               # 288 pieces at the same price, twice


def test_the_map_a_person_and_the_ai():
    po = matching.rows_of("PO", {"lines": [po_row("3126351", "8 CT", "794,595.00"),
                                           po_row("3129330", "8 CT", "794,595.00")]})
    two = [so(20, "1002316", "ESKULIN 30 ML ECHANTED PETALS", 36, 288, 22072.08, 794595.00),
           so(30, "1003229", "ESKULIN EDP 30 ML AURORA BLUSH", 36, 288, 22072.08, 794595.00)]
    m = matching.match([(24, "PO", po)], two, {},
                       {(24, 0): {"how": "ai", "status": "proposed", "so_line_no": 20, "reason": "PETALS"}})
    assert m[(24, 0)]["status"] == "proposed" and m[(24, 0)]["line"] == 0       # waits for a person
    assert m[(24, 1)]["status"] == "none"
    m = matching.match([(24, "PO", po)], two, {("code", "3129330"): "1003229"}, {})
    assert m[(24, 1)] == {"line": 1, "how": "map", "status": "matched", "why": "SAMB's 1003229 (product map)"}
    assert m[(24, 0)]["how"] == "numbers"                                        # the map took one line: one is left
    m = matching.match([(24, "PO", po)], two, {},
                       {(24, 0): {"how": "person", "status": "refused", "so_line_no": None, "reason": "no"}})
    assert m[(24, 0)]["status"] == "refused"


def test_the_only_line_and_a_bonus_row():
    one = [so(10, "1001187", "ELLIPS MOROCCAN 20 GR NUTRICOLOR", 72, 144, 7207.21, 518919.00)]
    grn = matching.rows_of("TTG", {"lines": [ttg_row("3078035", "2")]})
    assert matching.match([(12, "TTG", grn)], one, {}, {})[(12, 0)]["how"] == "only_line"
    rows = matching.rows_of("TTG", {"lines": [ttg_row("81244362", "3 KTN", "KTN", "… 3 KTN 5,630.63 5.00% 770,270.18"),
                                              ttg_row("81244362", "1 KTN", "KTN", "… 1 KTN 0 0")]})
    m = matching.match([(2, "TTG", rows)], one, {}, {})
    assert m[(2, 1)]["status"] == "bonus" and m[(2, 0)]["how"] == "only_line"


def test_the_ais_pairs_must_survive_the_numbers():
    rows = matching.rows_of("PO", {"lines": [po_row("A", "52 CT", "129,578.00"), po_row("B", "11 CT", "163,777.00")]})
    kept = matching.checked("PO", rows, KAKI, [{"row": 0, "line": 10, "why": "ok"},
                                               {"row": 1, "line": 30, "why": "wrong: 11 CT × 24 = 264, but price"},
                                               {"row": 1, "line": 10, "why": "the same line twice"}])
    assert kept == [(0, 10, "ok")]
    grn = matching.rows_of("TTG", {"lines": [ttg_row("A", "2,000")]})
    assert matching.checked("TTG", grn, KAKI, [{"row": 0, "line": 10, "why": "more than ordered"}]) == []


def test_a_po_row_pairs_by_its_printed_amount():
    """Duta Buah's PO (7000363700-03 p6) counts 48 PCS where SAMB's line says 2, so 'numbers' can't pair them; the
    printed amounts agree to a few rupiah. KARAMEL and STRAWBERRY both print 32,760: the name decides, and KARAMEL,
    which SAMB's order doesn't have, stays unpaired (2026-09-28)."""
    from grouper import matching
    fields = {"lines": [
        {"product_description": "ALPENLIEBE LOLY KARAMEL 10 GR", "qty": "48", "uom": "PCS", "row_text": "1 ALPENLIEBE LOLY KARAMEL 10 GR 48 PCS 682.50 32,760.00"},
        {"product_description": "ALPENLIEBE LOLY STRAWBERRY 10 GR", "qty": "48", "uom": "PCS", "row_text": "2 ALPENLIEBE LOLY STRAWBERRY 10 GR 48 PCS 682.50 32,760.00"},
        {"product_description": "BIGBABOL STRAWBERRY STICK 20G", "qty": "40", "uom": "PCS", "row_text": "4 BIGBABOL STRAWBERRY STICK 20G 40 PCS 2,061.44 82,457.66"},
        {"product_description": "CHUPA CHUP GUMFILLE 15 GR", "qty": "30", "uom": "PCS", "row_text": "5 CHUPA CHUP GUMFILLE 15 GR 30 PCS 1,554.05 46,621.62"}]}
    lines = [{"line_no": 10, "item_code": "1001", "description": "ALP LOLLY HGR 8X24X10.5G STRAWBERRY", "qty_pcs": 2,
              "pcs_per_uom": 1, "line_amount": 32760.00, "vat": 3603.60, "price_pcs": 16380},
             {"line_no": 30, "item_code": "1003", "description": "CHUPA CHUPS BIG BABOL STICK 20GR STRAW", "qty_pcs": 40,
              "pcs_per_uom": 20, "line_amount": 82457.60, "vat": 9070.34, "price_pcs": 2061.44},
             {"line_no": 40, "item_code": "1004", "description": "CHUPA CHUPS GUMFILLED", "qty_pcs": 1,
              "pcs_per_uom": 1, "line_amount": 46616.22, "vat": 5127.78, "price_pcs": 46616.22}]
    out = matching.match([(6, "PO", matching.rows_of("PO", fields))], lines, {}, {})
    got = {i: (out[(6, i)]["status"], out[(6, i)]["line"]) for i in range(4)}
    assert got == {0: ("none", None), 1: ("matched", 0), 2: ("matched", 1), 3: ("matched", 2)}
    assert out[(6, 1)]["how"] == "amount"
    far = {"lines": [{"product_description": "X", "qty": "1", "row_text": "X 1 PCS 46,700.00"}]}   # 0.18% off: no
    assert matching.match([(6, "PO", matching.rows_of("PO", far))], lines[1:], {}, {})[(6, 0)]["status"] == "none"
