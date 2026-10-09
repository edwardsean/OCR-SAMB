"""The customer field mapping (2026-10-09): rows keep their satuan (pieces in one pack) next to uom, and one knowledge
section can name every Satellite customer of a chain (Alfamart's DCs; Total Buah Segar, whose every store is its own
customer). No database, no AI."""
from decimal import Decimal

from common import wiki
from common.fields import DOCS, LINE_CANON, LINE_MAP, lift, project
from publisher.publish import pack

PAGE = """# TTG

## Any customer
- posting_date: the date the goods were received.

## Alfamart (chain 1100002312, 1100002314, 1100002311, 1100002310)
- purchase_order_no: printed after "Nomor F P P". [label · Nomor F P P]

## AEON (chain 1100002424)
- purchase_order_no: RECEIPT NO is the PO number. [label · RECEIPT NO]
"""


def test_po_and_receipt_rows_keep_satuan_next_to_uom():
    for t in ("PO", "TTG"):
        cols = [f["name"] for f in DOCS[t]["lines"]]
        assert "uom" in cols and "satuan" in cols and LINE_MAP[t]["satuan"] == "satuan"
    assert "satuan" in LINE_CANON and "satuan" not in LINE_MAP["FP"]     # SAMB's invoice has its own Kemasan
    rows = {"lines": [{"qty": "12", "uom": "CTN", "satuan": "72", "row_text": "1078012 … 12 72"}]}
    assert project(lift(project(rows, "PO"), "PO"), "PO")["lines"][0]["satuan"] == "72"


def test_a_satuan_is_the_pieces_in_one_pack():
    assert pack("CTN/72") == Decimal("72.000")
    assert pack("CTN12") == Decimal("12.000")
    assert pack("24 / EA") == Decimal("24.000")
    assert pack("1x6") == Decimal("6.000")                 # Hero's ISI KARTON: the number after the x
    assert pack("Isi 24.00") == Decimal("24.000")
    assert pack("EA") is None and pack("") is None and pack("(not printed)") is None


def test_one_section_can_name_several_customers():
    parsed = wiki.parse(PAGE)
    alfa = next(s for s in parsed["sections"] if s["head"].startswith("Alfamart"))
    assert alfa["chain"] == "1100002312" and len(alfa["chains"]) == 4
    for chain in ("1100002312", "1100002310"):
        got = wiki.claims_for(parsed, chain)
        assert [c["text"] for c in got if c["field"] == "purchase_order_no"] == ['printed after "Nomor F P P".']
        assert any(c["field"] == "posting_date" for c in got)
    assert [c["anchor"] for c in wiki.claims_for(parsed, "1100002424") if c["field"] == "purchase_order_no"] \
        == ["RECEIPT NO"]
    assert [c["field"] for c in wiki.claims_for(parsed, "1100009999")] == ["posting_date"]   # another customer
    again = wiki.parse(wiki.render("TTG", parsed["sections"]))                                # render sorts by name
    assert sorted(again["sections"], key=lambda s: s["head"]) == sorted(parsed["sections"], key=lambda s: s["head"])


def test_a_drafted_claim_joins_the_section_that_names_its_customer():
    parsed = wiki.parse(PAGE)
    claim = wiki.claim("document_no", "No LPB, top left. [label · No LPB]")
    sections, new = wiki.merge_draft(parsed, [("1100002314", claim, None)], {})
    alfa = [s for s in sections if s["head"].startswith("Alfamart")]
    assert len(alfa) == 1 and claim in alfa[0]["claims"] and new == [claim]
    assert len(sections) == len(parsed["sections"])          # no new "(chain 1100002314)" section


def test_installing_a_page_lists_the_claims_it_would_leave_out():
    server = PAGE + "\n## Hero (chain 1100002473)\n- document_no: printed after \"Receiving No\". [label · Receiving No · pages b-1/2]\n"
    assert wiki.dropped(server, server) == [] and wiki.dropped(PAGE, server) == []     # adding is never a loss
    assert wiki.dropped(server, PAGE) == [("Hero (chain 1100002473)",
                                           '- document_no: printed after "Receiving No". [label · Receiving No · pages b-1/2]')]
    reworded = PAGE.replace("[label · Nomor F P P]", "[label · Nomor F P P · pages b-9/9]")
    assert wiki.dropped(PAGE, reworded) == []                # the same claim with other brackets is the same claim
    moved = PAGE.replace("1100002310)", "1100002310, 1100002545)")
    assert [line for _, line in wiki.dropped(PAGE, moved)] == \
        ['- purchase_order_no: printed after "Nomor F P P". [label · Nomor F P P]']   # its customers changed
