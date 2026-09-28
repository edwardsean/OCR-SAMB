"""Verification redesign, S2 (the user, 2026-09-26/27): totals decide, on two sides, each against its own reference
in Satellite. The order side: PO ↔ the SO as ordered (what the FP printed); the FP page is never read. The delivery
side: TTG ↔ what Satellite received (its goods receipt, and the invoice built from it); never the FP. Rows only
explain, unless a document has no usable total. A value beyond the allowance follows one rule (item 11)."""
import copy
from datetime import date

from common import satellite
from grouper import crosscheck
from worker import vf

TEXT = {"verdict": "ok", "by": "text"}
CHECK = {"verdict": "check", "why": "not in Tesseract's text"}
PRICE, PIECES = 42265.68, 24                       # one line, 24 pieces at Rp 42,265.68 a piece, 11% VAT


def so(**more):
    net = round(PRICE * PIECES, 2)
    return {"sor_no": "SOR1", "order_dpp": net, "order_ppn": round(net * .11, 2), "order_total": round(net * 1.11, 2),
            "dpp": net, "ppn": round(net * .11, 2), "total": round(net * 1.11, 2), "status": "INVOICE_GENERATED",
            "cgr_no": "CGR1", "cgr_date": date(2026, 9, 10), "tgl_so": date(2026, 9, 4), "vat_pct": 11,
            "billing_no": "7000000001", **more}


def line(**more):
    net = round(PRICE * PIECES, 2)
    return {"line_no": 10, "line_amount": net, "vat": round(net * .11, 2), "qty_pcs": PIECES, "cgr_qty": PIECES,
            "rejected_qty": 0, "reject_reason": None, "invoice_amount": net, "invoice_qty": PIECES, "pcs_per_uom": 1,
            "price_pcs": PRICE, "price_uom": PRICE, "discounts": {}, "item_code": "1000001", "description": "X", **more}


def amount(v, printed=True):
    return {"value": f"{v:.2f}", "source_text": f"{v:,.2f}"}, (TEXT if printed else CHECK)


def page(doc_type, values=None, text="", lines=None, second=None, rows=None, **fields_all):
    """lines: rows by their printed text only; rows: rows as dicts (a receipt's item_code, qty, uom)."""
    fields, header = {}, {}
    for name, (f, v) in (values or {}).items():
        fields[name], header[name] = f, v
    if lines is not None:
        fields["lines"] = [{"row_text": t} for t in lines]
    if rows is not None:
        fields["lines"] = [{"row_text": "", **r} for r in rows]
    return {"doc_type": doc_type, "fields": fields, "checks": {"header": header, "lines": []}, "outcome": "clear",
            "classical_text": text, "fields_all": fields_all or None, "second_look": second}


def bundle(po=None, ttg=None, the_so=None, lines=None, fp_total=1.0, **kw):
    """FP on page 1 (its total deliberately nonsense: the checks must never read it), PO on 2, TTG on 3."""
    pages = {1: page("FP", {"total": amount(fp_total)})}
    docs = [(1, "FP")]
    if po:
        pages[2], docs = po, docs + [(2, "PO")]
    if ttg:
        pages[3], docs = ttg, docs + [(3, "TTG")]
    from grouper import matching
    ls = lines or [line()]
    m = matching.match([(n, t, matching.rows_of(t, pages[n]["fields"])) for n, t in docs if t in ("PO", "TTG")],
                       ls, {}, {})
    return crosscheck.check_bundle("SOR1", docs, pages, the_so or so(), ls, m, scan_day=date(2026, 9, 23), **kw)


# ── the order side ─────────────────────────────────────────────────────────────────────────────────────────────

def test_a_po_total_before_tax_passes_on_the_orders_dpp():
    s = so()
    c = bundle(po=page("PO", {"total": amount(s["order_dpp"] - 1.13)}))["fp_po_total"]
    assert c["status"] == "pass" and "DPP" in c["why"] and c["fp"] == s["order_dpp"]
    assert c["meaning"] == "before tax"                       # published as such (phase 8), never as a total with tax
    assert bundle(po=page("PO", {"total": amount(s["order_total"])}))["fp_po_total"]["meaning"] == "with tax"


def test_the_ppn_is_compared_when_the_po_prints_one():
    s = so()
    c = bundle(po=page("PO", {"total": amount(s["order_total"]), "ppn": amount(s["order_ppn"] + 20)}))["fp_po_total"]
    assert c["status"] == "fail" and "PPN" in c["why"]
    c = bundle(po=page("PO", {"total": amount(s["order_total"]), "ppn": amount(s["order_ppn"] - 0.68)}))["fp_po_total"]
    assert c["status"] == "pass"


def test_item_11_on_the_orders_side():
    s = so()
    off = s["order_total"] + 30
    # print backs a reading that doesn't fit: a real difference, a person now, no look-again
    c = bundle(po=page("PO", {"total": amount(off)}))["fp_po_total"]
    assert c["status"] == "fail" and not c.get("ask")
    # only the AI read it: one look-again first
    c = bundle(po=page("PO", {"total": amount(off, printed=False)}))["fp_po_total"]
    assert c["status"] == "unknown" and c["ask"] == [(2, "total")]
    # the look-again read a total that fits: it counts (the AI's reading, within the allowance)
    looked = {"asked": ["total"], "results": {"total": {"second": {"value": f"{s['order_total']:.2f}",
                                                                   "source_text": f"{s['order_total']:,.2f}"}}}}
    c = bundle(po=page("PO", {"total": amount(off, printed=False)}, second=looked))["fp_po_total"]
    assert c["status"] == "pass" and "look-again" in c["why"]
    # it read the same again: a person, with both readings, and nothing asked twice
    again = {"asked": ["total"], "results": {"total": {"second": {"value": f"{off:.2f}"}}}}
    c = bundle(po=page("PO", {"total": amount(off, printed=False)}, second=again))["fp_po_total"]
    assert c["status"] == "unknown" and not c.get("ask")


def test_no_usable_total_the_rows_decide():
    s, ln = so(), line()
    rows = [f"1 1000001 X 24 EA 42,265.68 {ln['line_amount']:,.2f}"]
    cut = {"total": ({"value": "1126011.0", "source_text": "1.126.011,"}, CHECK)}      # cut at the scan's edge
    assert bundle(po=page("PO", cut, lines=rows))["fp_po_total"]["status"] == "pass"
    c = bundle(po=page("PO", cut, lines=["1 1000001 X 24 EA 42,265.68 999,999.00"]))["fp_po_total"]
    assert c["status"] == "unknown" and not c.get("ask")                    # a cut total: nothing to look at again
    c = bundle(po=page("PO", {}, lines=["1 1000001 X 24 EA 42,265.68 999,999.00"]))["fp_po_total"]
    assert c["status"] == "unknown" and c["ask"] == [(2, "total")]          # none read: the AI looks for it once


def test_with_a_usable_total_the_rows_only_explain():
    s = so()
    c = bundle(po=page("PO", {"total": amount(s["order_total"])}, lines=["1 1000001 X 3 EA 1.00 3.00"]))
    assert c["fp_po_total"]["status"] == "pass" and c["fp_po_lines"]["status"] == "info"


def test_the_fp_page_is_never_the_reference():
    s = so()
    po = page("PO", {"total": amount(s["order_total"])})
    assert bundle(po=po, fp_total=1.0) == bundle(po=po, fp_total=999_999_999.0)


# ── the delivery side ──────────────────────────────────────────────────────────────────────────────────────────

def rejected_half():
    """The user's example: 10 ordered, the FP says 10, Satellite's goods receipt: 5 received + 5 rejected."""
    net10 = round(PRICE * 10, 2)
    net5 = round(PRICE * 5, 2)
    s = so(order_dpp=net10, order_ppn=round(net10 * .11, 2), order_total=round(net10 * 1.11, 2),
           dpp=net5, ppn=round(net5 * .11, 2), total=round(net5 * 1.11, 2))
    ln = line(line_amount=net10, vat=round(net10 * .11, 2), qty_pcs=10, cgr_qty=5, rejected_qty=5,
              reject_reason="TOLAK TOKO (OVERSTOCK)", invoice_amount=net5, invoice_qty=5)
    return s, ln


def receipt(pieces):
    return page("TTG", rows=[{"item_code": "1000001", "qty": str(pieces), "uom": "PCS"}])


def test_a_rejection_the_receipt_against_what_was_received():
    """The user's example: FP 10, the goods receipt 5 received + 5 rejected, the receipt 5: it passes on what was
    received, in quantities (the mentors, 2026-09-28), and the FP is never read."""
    s, ln = rejected_half()
    ttg = receipt(5)
    c = bundle(ttg=ttg, the_so=s, lines=[ln], fp_total=s["order_total"])["received"]
    assert c["status"] == "pass" and "tolakan" in c["why"] and c["tolakan"]
    # the receipt showing the whole order: Satellite recorded a rejection it doesn't show
    c = bundle(ttg=receipt(10), the_so=s, lines=[ln])["received"]
    assert c["status"] == "fail" and "Satellite received 5" in c["why"]
    # and the FP (printed as ordered, 10) is never what the receipt is compared with
    assert bundle(ttg=ttg, the_so=s, lines=[ln], fp_total=1.0) == bundle(ttg=ttg, the_so=s, lines=[ln], fp_total=9e9)


def test_the_received_value_follows_satellites_state():
    s, ln = so(), line()
    assert satellite.received(s, [ln])["state"] == "invoiced"
    billed_not = so(status="CGR", total=0, dpp=0, ppn=0)
    r = satellite.received(billed_not, [ln])
    assert r["state"] == "reconstructed" and abs(r["dpp"] - ln["line_amount"]) < 0.01
    assert satellite.received(so(status="GOOD_ISSUED", cgr_no=None, total=0), [ln])["state"] == "waiting"
    assert satellite.received(so(status="CGR", total=0), [line(cgr_qty=0, rejected_qty=0)])["state"] == "waiting"
    assert satellite.received(so(sor_no="SOF26110002444"), [ln])["state"] == "unknown"
    assert satellite.received(None, [ln])["state"] == "unknown"
    waiting = bundle(ttg=receipt(24), the_so=so(status="GOOD_ISSUED", cgr_no=None, total=0))
    assert waiting["received"]["status"] == "waiting"
    assert crosscheck.decide(waiting, {1: {"outcome": "clear"}})[0] == "grouping"          # waits: never a person


def test_two_receipts_are_summed():
    """A split delivery: 12 + 12 pieces on two receipts of one line of 24."""
    from grouper import matching
    s, ln = so(), line()
    spans = {3: [3], 4: [4]}
    pages = {1: page("FP"), 3: receipt(12), 4: receipt(12)}
    docs = [(1, "FP"), (3, "TTG"), (4, "TTG")]
    m = {(n, 0): {"line": 0, "status": "matched", "how": "person"} for n in (3, 4)}
    c = crosscheck.check_bundle("SOR1", docs, pages, s, [ln], m, scan_day=date(2026, 9, 23), spans=spans)["received"]
    assert c["status"] == "pass"


def test_every_line_received_must_be_on_the_receipt():
    two = [line(line_no=10, description="PRODIET ADULT 85GR OCEAN FISH"),
           line(line_no=20, item_code="1000002", description="SENNA KRUPUKKU 500GR UDANG")]
    one = page("TTG", rows=[{"qty": "24", "uom": "PCS", "material_description": "PRODIET 85G OCEAN FISH"}])
    c = bundle(ttg=one, lines=two)["received"]
    assert c["status"] == "unknown" and "lines [20]" in c["why"]          # received, and not on the receipt


def test_free_goods_go_to_a_person():
    s = so(sor_no="SOF26110002444")
    c = bundle(po=page("PO", {"total": amount(1.0)}), ttg=receipt(24), the_so=s)
    assert c["fp_po_total"]["status"] == c["received"]["status"] == "unknown"


# ── the receipt's date (decision 7) ────────────────────────────────────────────────────────────────────────────

def test_the_receipt_date():
    def dated(day, printed=True, second=None):
        v = ({"value": day, "source_text": day}, TEXT if printed else CHECK)
        return bundle(ttg=page("TTG", {"posting_date": v}, second=second))["dates"]
    assert dated("2026-09-10")["status"] == "pass"                            # Satellite's goods receipt date
    assert dated("2026-09-12", printed=False)["status"] == "pass"             # in order, kept as read
    assert dated("2026-10-12")["status"] == "fail"                            # printed, out of order: a person
    c = dated("2026-10-12", printed=False)
    assert c["status"] == "unknown" and c["ask"] == [(3, "posting_date")]     # only the AI read it: look again
    fixed = {"asked": ["posting_date"], "results": {"posting_date": {"second": {"value": "2026-09-12"}}}}
    assert dated("2026-10-12", printed=False, second=fixed)["status"] == "pass"


# ── the look-again the bundle asks for ─────────────────────────────────────────────────────────────────────────

def test_the_bundle_asks_and_the_page_looks_again():
    header = {"purchase_order_no": TEXT, "total": CHECK, "ppn": CHECK}
    assert vf.to_ask("PO", header) == []                                     # the page alone asks nothing
    assert vf.to_ask("PO", header, requested=("total", "ppn")) == ["total", "ppn"]
    assert vf.to_ask("PO", header, asked={"total"}, requested=("total", "ppn")) == ["ppn"]    # once each


def test_asks_make_the_bundle_wait():
    s = so()
    x = {"sor": "SOR1", "docs": [(1, "FP"), (2, "PO")], "status": "needs_review", "fingerprint": None,
         "pages": {1: page("FP"), 2: page("PO", {"total": amount(s["order_total"] + 30, printed=False)})},
         "so": s, "lines": [line()], "expected": ("FP", "PO"), "pmap": {}, "decisions": {}, "accepted": {},
         "scan_day": date(2026, 9, 23), "spans": {}}
    r = crosscheck.evaluate(copy.deepcopy(x))
    assert r["asks"] == {2: ["total"]} and r["status"] == "grouping"


def test_copies_of_one_po_count_once():
    """7000363700-03: PO.2026.09.32029 is in the bundle four times (two prints, two terms pages); its total was summed
    to 775,397 × 4 = 3,101,588 (2026-09-28)."""
    from grouper import crosscheck
    page = lambda number: {"fields": {"purchase_order_no": {"value": number}}, "checks": {"header": {}}}
    pages = {6: page("PO.2026.09.32029"), 7: page("PO.2026.09.32029"), 8: page("PO.2026.09.32029"),
             20: page("PO.2026.09.40001"), 21: page(None), 22: page(None)}
    assert crosscheck.distinct(pages, [6, 7, 8, 20, 21, 22], "purchase_order_no") == [6, 20, 21, 22]


def test_a_pack_size_read_as_a_quantity_is_named_and_a_persons_answers_settle_it():
    """7000363700-03 p3 (AEON): the AI read 20 / 10 CARTON, the pack sizes of 20X200GR / 10X320GR, where the receipt
    prints 0 and 1 carton received (2026-09-28). The check names it; a person's quantity, and a person's 'not in
    SAMB's order' on a row, settle it."""
    lines = [line(line_no=10, description="MIE CAP AYAM 2 TELOR 200GR REGULER", pcs_per_uom=20, qty_pcs=40,
                  cgr_qty=0, rejected_qty=40),
             line(line_no=30, item_code="1000003", description="BIHUN CAP TANAM JAGUNG 320GR", pcs_per_uom=10,
                  qty_pcs=20, cgr_qty=10, rejected_qty=10)]
    ttg = page("TTG", rows=[{"material_description": "AYAM 2 TELOR MI TELOR LEBAR KERITING 200GR", "qty": "20.0", "uom": "CARTON"},
                            {"material_description": "CAP TANAM JAGUNG BIHUN JAGUNG 320G", "qty": "10.0", "uom": "CARTON"},
                            {"material_description": "BIHUNKU SEDUH RASA SOTO AYAM 144G", "qty": "20.0", "uom": "CARTON"},
                            {"material_description": "", "qty": None}])            # a blank row: never counted
    c = bundle(ttg=ttg, lines=lines)["received"]
    assert c["status"] == "fail" and "pack size" in c["why"]
    assert [r["pack"] for r in c["qty_rows"] if r["line"]] == [True, True]
    ttg["fields"]["lines"][0]["qty"], ttg["fields"]["lines"][1]["qty"] = "0 CTN", "1 CTN"     # a person's answers
    from grouper import matching
    pages = {1: page("FP"), 3: ttg}
    m = matching.match([(3, "TTG", matching.rows_of("TTG", ttg["fields"]))], lines, {}, {})
    m[(3, 2)] = {"line": None, "status": "refused", "how": "person", "why": "not in SAMB's order"}
    c = crosscheck.check_bundle("SOR1", [(1, "FP"), (3, "TTG")], pages, so(), lines, m,
                                scan_day=date(2026, 9, 23))["received"]
    assert c["status"] == "pass"
