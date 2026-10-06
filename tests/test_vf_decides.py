"""Verification redesign, S1 (the user, 2026-09-26: a person only for an unsure page or an anomaly in the AI's result;
Tesseract failing to read something is never, by itself, a reason). Values that decide something are apart from values
kept as read; a page decides only its type, its key and the FP's own values; the look-again asks only those."""
import os

import pytest

from common import context
from common import fields as F
from grouper import crosscheck
from worker import classify, vf

OK = {"verdict": "ok", "by": "text"}
CHECK = {"verdict": "check", "why": "not in Tesseract's text"}
EMPTY = {"verdict": "empty"}


def ctx():
    return context.seed_content()


def test_decision_values_are_fields_of_their_type():
    for t, levels in F.DECIDES.items():
        names = {f["name"] for f in F.DOCS[t]["header"]}
        assert F.decides(t) <= names, (t, F.decides(t) - names)
        assert F.decides(t) == {f for level in levels.values() for f in level}


def test_no_reading_is_redone(monkeypatch):
    """A reading's version hashes the AI's field list. DECIDES lives apart from it, so changing which values decide
    something never re-reads a page (the free tier can't afford that)."""
    before = context.fields_version(ctx())
    monkeypatch.setattr(F, "DECIDES", {"FP": {"keys": ("sor",)}})
    assert context.fields_version(ctx()) == before


def test_a_page_waits_only_on_what_it_decides():
    # a receipt linked by its PO number: its date, number and vendor number unconfirmed don't hold it
    ttg = {"purchase_order_no": OK, "posting_date": CHECK, "document_no": CHECK, "vendor_number": EMPTY,
           "no_ref": EMPTY, "customer_name": CHECK}
    assert vf.outcome("decided", "TTG", {"header": ttg}) == "clear"
    assert vf.outcome("decided", "TTG", {"header": {**ttg, "purchase_order_no": CHECK}}) == "needs_person"
    assert vf.outcome("decided", "TTG", {"header": {**ttg, "purchase_order_no": CHECK, "no_ref": OK}}) == "clear"
    # a PO linked by its number: its total is the bundle's to judge, its vendor is kept as read
    po = {"purchase_order_no": OK, "total": CHECK, "ppn": EMPTY, "vendor_code": CHECK, "vendor_name": CHECK}
    assert vf.outcome("decided", "PO", {"header": po}) == "clear"
    # the FP settles its key and its amounts; its CPO only blocks when print contradicts Satellite
    fp = {"sor": OK, "dpp": OK, "ppn": OK, "total": OK, "nomor_cpo": EMPTY, "customer_name": CHECK}
    assert vf.outcome("decided", "FP", {"header": fp}) == "clear"
    conflict = {"verdict": "check", "why": "printed '5190721', but Satellite's SO record says 5190722",
                "conflict": "satellite"}
    assert vf.outcome("decided", "FP", {"header": {**fp, "nomor_cpo": conflict}}) == "needs_person"
    assert vf.outcome("decided", "FP", {"header": {**fp, "total": CHECK}}, looked=False) == "waiting_ai"


def test_the_look_again_asks_only_what_the_page_decides():
    asks = lambda t, h, asked=(): vf.to_ask(t, h, asked)
    # linked by its PO number: nothing to ask, whatever else is unconfirmed (the bundle judges the date, S2)
    assert asks("TTG", {"purchase_order_no": OK, "posting_date": CHECK, "document_no": CHECK,
                        "vendor_number": EMPTY, "no_ref": EMPTY}) == []
    # no key settled: the key it read is asked; an empty SOR reference isn't (many receipts don't print one)
    assert asks("TTG", {"purchase_order_no": CHECK, "no_ref": EMPTY, "document_no": CHECK}) == ["purchase_order_no"]
    assert asks("PO", {"purchase_order_no": EMPTY, "total": CHECK, "vendor_code": CHECK}) == ["purchase_order_no"]
    assert asks("PO", {"purchase_order_no": CHECK}, asked={"po_number"}) == []           # asked once per reading
    # the FP: its own amounts, never a value print holds against Satellite, never a cut amount
    fp = {"sor": OK, "dpp": CHECK, "ppn": OK, "total": OK, "customer_name": CHECK,
          "nomor_cpo": {**CHECK, "conflict": "satellite"}}
    assert asks("FP", fp) == ["dpp"]
    assert asks("FP", {**fp, "dpp": {**CHECK, "cut": True}}) == []
    # the same with the context's words, as the look-again sends them
    sent = vf.second_look_asks("TTG", {"header": {"purchase_order_no": CHECK, "no_ref": EMPTY}}, ctx())
    assert [c for c, _ in sent] == ["po_number"]


def test_the_vendor_is_information():
    def vendor(name, verdict):
        pages = {3: {"doc_type": "FP", "fields": {}, "checks": {"header": {}, "lines": []}},
                 4: {"doc_type": "PO", "fields": {"vendor_name": {"value": name}},
                     "checks": {"header": {"vendor_name": verdict}, "lines": []}}}
        return crosscheck.check_bundle("SOR1", [(3, "FP"), (4, "PO")], pages, None, [], {})["vendor_is_samb"]
    assert vendor("PT SARANA ABADI MAKMUR BERSAMA", OK)["status"] == "info"
    assert vendor("PT SARANA ABADI MAKMUR BERSAMA", CHECK)["status"] == "info"         # as read: proves nothing
    assert vendor(None, EMPTY)["status"] == "info"
    assert vendor("PT LAIN SEKALI", CHECK)["status"] == "info"                        # an AI reading alone never fails
    assert vendor("PT LAIN SEKALI", OK)["status"] == "fail"                           # print names another vendor
    assert crosscheck.decide({"vendor_is_samb": {"status": "info", "why": ""}}, {1: {"outcome": "clear"}}) == \
        ("auto_ok", [])


@pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
def test_review_folds_values_kept_as_read():
    from api import app
    v = app.review_view("b-4bab9b736d", "SOR26110257259")                  # Hero: FP 29, PO 30, receipt 31
    if not v:
        pytest.skip("bundle not in this database")
    for d in v["documents"]:
        assert all(f["name"] in F.decides(d["type"]) for f in d["head"]), d["page"]
        assert all(f["name"] not in F.decides(d["type"]) for f in d["kept"]), d["page"]
    receipt = next(d for d in v["documents"] if d["type"] == "TTG")
    assert "vendor_number" in {f["name"] for f in receipt["kept"]}             # Tesseract read S10232 as 510232
