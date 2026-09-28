"""Phase 7b (common/satellite.py), pure, with the sample's own values: an FP takes its amounts and item lines from the
SO record it was printed from; an FP whose SOR can't be read is resolved only when one SO fits and another attribute
agrees; a receipt date is checked against Satellite's goods receipt date."""
from datetime import date

from common import satellite

SO10 = {"sor_no": "SOR26110257250", "cpo_no": "58423526", "customer_code": "1400001889", "dpp": 971468.48,
        "ppn": 106861.53, "total": 1078330.01, "cgr_date": date(2026, 9, 9)}
SO8 = {"sor_no": "SOR26110256810", "cpo_no": "5213349", "customer_code": "1400001602", "dpp": 679299.82,
       "ppn": 74722.98, "total": 754022.80, "cgr_date": date(2026, 9, 7)}
SO1 = {"sor_no": "SOR26110245292", "cpo_no": "5190721", "customer_code": "1400001602", "dpp": 4321215.41,
       "ppn": 475333.70, "total": 4796549.11, "cgr_date": date(2026, 9, 1)}


def line(no, code, name, per, pcs):
    return {"line_no": no, "item_code": code, "description": name, "pcs_per_uom": per, "invoice_qty": pcs,
            "qty_pcs": pcs}


LINES = {"SOR26110245292": [line(10, "1000566", "PRODIET ADULT 85GR OCEAN FISH", 48, 144),
                            line(20, "1000567", "PRODIET ADULT 85GR TUNA", 48, 144),
                            line(30, "1000565", "PRODIET KITTEN 85GR MACKEREL", 48, 144),
                            line(40, "1000564", "PRODIET KITTEN 85GR TUNA", 48, 144),
                            line(50, "1000556", "PRODIET KITTEN 1.4KG OCEAN FISH", 7, 21)],
         "SOR26110256810": [line(10, "1000669", "ABC 335ML SAMBAL AYAM GORENG", 24, 24),
                            line(20, "1000668", "ABC 335ML SAMBAL MANIS PEDAS BB", 24, 24)],
         "SOR26110257250": [line(10, "1001187", "ELLIPS MOROCCAN 20 GR NUTRICOLOR", 72, 144)]}
OK, QR = {"verdict": "ok", "by": "text"}, {"verdict": "ok", "by": "qr"}
CHECK = {"verdict": "check", "why": "not in Tesseract's text"}


def sos(*records):
    return {satellite.flat(r["sor_no"]): r for r in records}


def items(sor):
    return LINES.get(sor, [])


def v(value, source=None):
    return {"value": value, "source_text": source or value}


def res(header, lines=()):
    return {"version": 1, "header": header, "lines": list(lines), "summary": {}}


def test_cut_amounts_come_from_the_record_and_the_ai_reading_is_kept():
    # page 10: the scan's edge cut all three; 7a took their ✅ away
    f = {"sor": v("SOR26110257250"), "dpp": v("971468.00", "971.468"), "ppn": v("106.86"),
         "total": v("1078330.00", "1.078.330,")}
    fields, out = satellite.settle_record("FP", f, res({"sor": QR, "dpp": CHECK, "ppn": CHECK, "total": CHECK}),
                                          sos(SO10), items)
    assert {k: fields[k]["value"] for k in ("dpp", "ppn", "total")} == \
        {"dpp": "971468.48", "ppn": "106861.53", "total": "1078330.01"}
    assert fields["ppn"]["ai_value"] == "106.86"
    assert all(out["header"][k]["by"] == "satellite" for k in ("dpp", "ppn", "total"))


def test_a_whole_printed_amount_that_differs_goes_to_a_person():
    f = {"sor": v("SOR26110257250"), "dpp": v("971468.40", "971.468,40"), "ppn": v("106861.53", "106.861,53"),
         "total": v("1078330.01", "1.078.330,01")}
    fields, out = satellite.settle_record("FP", f, res({"sor": QR, "dpp": OK, "ppn": OK, "total": OK}), sos(SO10),
                                          items)
    assert out["header"]["dpp"]["verdict"] == "check" and "changed after printing" in out["header"]["dpp"]["why"]
    assert fields["dpp"]["value"] == "971468.40"                    # never overwritten over print
    assert out["header"]["ppn"] == OK and out["header"]["total"] == OK


def test_no_record_without_a_resolved_sor():
    f = {"sor": v("SOR26110257250"), "dpp": v("971468.00", "971.468")}
    fields, out = satellite.settle_record("FP", f, res({"sor": CHECK, "dpp": CHECK}), sos(SO10), items)
    assert out["header"]["dpp"] == CHECK and fields["dpp"]["value"] == "971468.00"


def test_rows_pair_by_name_when_a_code_is_misread():
    # page 1 read 1000566 on rows 1 and 4, and 1000564 on row 5
    rows = [{"kode_material": c, "nama_produk": n, "qty_crt": "3", "qty_pcs": "0"} for c, n in
            [("1000566", "PRODIET ADULT 85GR OCEAN FISH"), ("1000567", "PRODIET ADULT 85GR TUNA"),
             ("1000565", "PRODIET KITTEN 85GR MACKEREL"), ("1000566", "PRODIET KITTEN 85GR TUNA"),
             ("1000564", "PRODIET KITTEN 1.4KG OCEAN FISH")]]
    pairs, missing = satellite.pair_lines(rows, LINES["SOR26110245292"])
    assert pairs == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)] and missing == []
    f = {"sor": v("SOR26110245292"), "lines": rows}
    verdicts = [{c: dict(CHECK) for c in ("kode_material", "nama_produk", "qty_crt", "qty_pcs")} for _ in rows]
    fields, out = satellite.settle_record("FP", f, res({"sor": QR}, verdicts), sos(SO1), items)
    assert [r["kode_material"] for r in fields["lines"]] == ["1000566", "1000567", "1000565", "1000564", "1000556"]
    assert fields["lines"][3]["ai_values"] == {"kode_material": "1000566"}
    assert all(c["by"] == "satellite" for r in out["lines"] for c in r.values())
    assert out["so_lines"]["missing"] == []


def test_a_row_without_a_readable_name_stays_unpaired_and_a_missed_row_shows():
    rows = [{"kode_material": "1000669", "nama_produk": ""}]           # the code alone could be another line's
    pairs, missing = satellite.pair_lines(rows, LINES["SOR26110256810"])
    assert pairs == [] and missing == [0, 1]
    assert satellite.pair_lines(rows, LINES["SOR26110256810"], trusted={0}) == ([(0, 0)], [1])   # print backs it
    rows = [{"kode_material": "1000669", "nama_produk": "ABC 335ML SAMBAL AYAM GORENG"}]
    assert satellite.pair_lines(rows, LINES["SOR26110256810"]) == ([(0, 0)], [1])


BOOTS = [line(10, "1003243", "VASELINE BW 425ML LUMINOUS GLUTAGLOW", 8, 4),
         line(20, "1003244", "VASELINE BW 425ML SMOOTH GLUTAGLOW", 8, 4),
         line(30, "1003245", "VASELINE BW 425ML YOUTHFUL GLUTAGLOW", 8, 4)]


def test_variants_of_one_product_pair_by_their_name_and_a_code_breaks_only_a_tie():
    # a misread code that is another variant's never moves the row: LUMINOUS stays LUMINOUS
    rows = [{"kode_material": "1003244", "nama_produk": "VASELINE BW 425ML LUMINOUS GLUTAGLOW"}]
    assert satellite.pair_lines(rows, BOOTS) == ([(0, 0)], [1, 2])
    # the variant word unread: the names tie, and only a code print backs may choose
    rows = [{"kode_material": "1003245", "nama_produk": "VASELINE BW 425ML GLUTAGLOW"}]
    assert satellite.pair_lines(rows, BOOTS)[0] == []
    assert satellite.pair_lines(rows, BOOTS, trusted={0})[0] == [(0, 2)]


def test_a_row_print_backs_differently_goes_to_a_person():
    rows = [{"kode_material": "1001188", "nama_produk": "ELLIPS MOROCCAN 20 GR NUTRICOLOR", "qty_crt": "2",
             "qty_pcs": "0"}]
    verdicts = [{"kode_material": OK, "nama_produk": OK, "qty_crt": CHECK, "qty_pcs": CHECK}]
    fields, out = satellite.settle_record("FP", {"sor": v("SOR26110257250"), "lines": rows},
                                          res({"sor": QR}, verdicts), sos(SO10), items)
    assert out["lines"][0]["kode_material"]["verdict"] == "check" and fields["lines"][0]["kode_material"] == "1001188"
    assert out["lines"][0]["qty_crt"]["by"] == "satellite"           # 144 pieces at 72 a carton print "2 / 0"


SO_TOLAK = {"sor_no": "SOR26110244049", "cpo_no": "1", "customer_code": "1", "dpp": 302588.08, "ppn": 33284.69,
            "total": 335872.77}                                   # Satellite's invoice: only what was received
TOLAK = [{**line(10, "1001188", "ELLIPS MOROCCAN 20 GR SMOOTH&SHINY", 12, 12), "line_amount": 302588.08,
          "vat": 33284.69},
         {**line(30, "1001190", "ELLIPS MOROCCAN 20 GR PINK NUTRI", 12, 0), "qty_pcs": 12, "line_amount": 83472.46,
          "vat": 9181.97}]                                        # line 30: ordered 12, all 12 rejected by the store


def test_after_a_tolakan_the_fp_is_what_was_ordered():
    """SOR26110244049 (real, Sep 2026). The FP went out with the goods and is never printed again (the user,
    2026-09-25), so it shows the SO as ordered, while Satellite invoices only what was received. The SO's lines keep
    what was ordered (their amounts and VAT add up to the invoice on all 31,512 unchanged SOs), so the FP's amounts
    are the lines' sums: 386,060.54 + 42,466.66 = 428,527.20."""
    assert satellite.paper(SO_TOLAK, TOLAK) == {"dpp": 386060.54, "ppn": 42466.66, "total": 428527.20}
    assert satellite.paper(SO_TOLAK, TOLAK[:1]) == {"dpp": 302588.08, "ppn": 33284.69, "total": 335872.77}
    rows = [{"kode_material": "1001188", "nama_produk": "ELLIPS MOROCCAN 20 GR SMOOTH&SHINY", "qty_crt": "1",
             "qty_pcs": "0"},
            {"kode_material": "1001190", "nama_produk": "ELLIPS MOROCCAN 20 GR PINK NUTRI", "qty_crt": "3",
             "qty_pcs": "0"}]                                     # the second row's cartons misread
    f = {"sor": v("SOR26110244049"), "dpp": v("386060.54", "386.060,54"), "ppn": {},
         "total": v("428527.20", "428.527,20"), "lines": rows}
    verdicts = [{c: dict(CHECK) for c in ("kode_material", "nama_produk", "qty_crt", "qty_pcs")} for _ in rows]
    fields, out = satellite.settle_record("FP", f, res({"sor": QR, "dpp": CHECK, "ppn": {"verdict": "empty"},
                                                        "total": CHECK}, verdicts), sos(SO_TOLAK), lambda s: TOLAK)
    assert out["header"]["dpp"]["by"] == out["header"]["total"]["by"] == "satellite"
    assert "as ordered" in out["header"]["total"]["why"]
    assert fields["ppn"]["value"] == "42466.66"                   # not read: the FP's PPN, not the invoice's 33,284.69
    assert fields["lines"][1]["qty_crt"] == "1" and fields["lines"][1]["ai_values"] == {"qty_crt": "3"}


def test_an_fp_whose_printed_amounts_cant_be_known_corrects_no_amount():
    unpriced = [TOLAK[0], {**TOLAK[1], "line_amount": 0}]        # a line with no amount (free goods)
    assert satellite.paper(SO_TOLAK, unpriced) is None
    f = {"sor": v("SOR26110244049"), "total": v("1.00", "1,00")}
    fields, out = satellite.settle_record("FP", f, res({"sor": QR, "total": CHECK}), sos(SO_TOLAK),
                                          lambda s: unpriced)
    assert out["header"]["total"] == CHECK and fields["total"]["value"] == "1.00"


def fp8(total="754.022,86", codes=("1000669", "1000668"), customer="14000045001"):
    return {"sor": v("SOR26110245292"), "nomor_cpo": {}, "customer_code": v(customer), "total": v(None, total),
            "lines": [{"kode_material": c} for c in codes]}


def test_an_unread_sor_resolved_from_the_total_and_the_item_codes():
    # a faint SOR read as page 1's, the total .80 as .86, the two item codes read right
    so, why = satellite.resolve_fp(fp8(), sos(SO8, SO1, SO10), items)
    assert so["sor_no"] == "SOR26110256810" and "6→0" in why and "every item code" in why
    fields, verdicts = satellite.settle("FP", fp8(), {"sor": CHECK}, sos(SO8, SO1, SO10), None, items)
    assert fields["sor"]["value"] == "SOR26110256810" and fields["sor"]["ai_value"] == "SOR26110245292"
    assert verdicts["sor"]["by"] == "satellite"


CILEDUG = {**SO8, "sor_no": "SOR26110259023", "customer_code": "1400000087", "cpo_no": "5222225",
           "customer_name": "HARI HARI CILEDUK TANGERANG"}          # the same order to another store (real data)


def test_resolution_needs_exactly_one_so_the_page_agrees_with():
    LINES["SOR26110259023"] = LINES["SOR26110256810"]
    bintaro = {**SO8, "customer_name": "HARI HARI BINTARO TANGSEL"}
    try:
        so, why = satellite.resolve_fp(fp8(), sos(SO8, CILEDUG), items)
        assert so is None and "tells them apart" in why              # identical items: only the store could tell
        store = {**fp8(), "customer_name": v("HARI HARI CILEDUK TANGERANG")}
        assert satellite.resolve_fp(store, sos(bintaro, CILEDUG), items)[0]["sor_no"] == "SOR26110259023"
        torn = {**store, "customer_code": v("1400001602")}         # the name says Ciledug, the code says Bintaro
        assert satellite.resolve_fp(torn, sos(bintaro, CILEDUG), items)[0] is None
        misread = {**fp8(codes=()), "customer_name": v("HARI HARI BINTANG TANOSSEL")}   # page 8, as read
        assert satellite.resolve_fp(misread, sos(bintaro, CILEDUG), items)[0] is None
    finally:
        LINES.pop("SOR26110259023")
    so, why = satellite.resolve_fp(fp8(codes=("1000660",)), sos(SO8, SO1), items)
    assert so is None and "nothing else on the page agrees" in why
    boots = [{**SO10, "sor_no": f"SOR2611025{n}", "cpo_no": f"450583272{n % 10}", "total": 1126011.00}
             for n in (5799, 5815, 5837)]                          # one order sent to many stores, no store read
    so, why = satellite.resolve_fp(fp8(total="1.126.011,00", codes=()), sos(*boots), items)
    assert so is None and "could be 3 SOs" in why


def test_a_receipt_date_against_satellites_goods_receipt_date():
    def ttg(day, verdict):
        f = {"no_ref": v("SOR26110256810"), "posting_date": v(day)}
        return satellite.settle("TTG", f, {"no_ref": OK, "posting_date": verdict}, sos(SO8))[1]["posting_date"]
    assert ttg("2026-09-07", CHECK)["by"] == "satellite"            # equal: Satellite confirms it
    assert ttg("2026-09-06", OK) == OK                               # a day apart (page 2): left as it is
    assert ttg("2026-08-07", OK)["verdict"] == "check"               # a month apart: misread?
