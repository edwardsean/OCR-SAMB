"""read-then-map Stage 2c: what people taught, as a wiki page per document type (common/wiki.py), the text model's
second mapping with it (pass B, worker/learn.py step), and the gate that replays a proposal before it is used."""
import json
import os

import pytest

from common import context, wiki

PAGE = """# TTG

## Any customer
- posting_date: the date the goods were received, never the PO's date. [label · TGL TERIMA]

## AEON (chain 1100002424)
Prose for people is kept, but only claims reach the text model.
- purchase_order_no: RECEIPT NO is the PO number. [label · RECEIPT NO · pages b-c80bbbde4d/14]
- no_ref: prints no SOR reference; leave it empty. [not_printed · pages b-c80bbbde4d/3]
- lines.qty: the column headed "QTY RCV". [column · QTY RCV · pages b-1/1, b-2/2]
"""


def test_a_page_parses_into_sections_and_claims():
    p = wiki.parse(PAGE)
    assert [(s["head"], s["chain"]) for s in p["sections"]] == [("Any customer", None),
                                                                ("AEON (chain 1100002424)", "1100002424")]
    po = p["sections"][1]["claims"][0]
    assert (po["field"], po["kind"], po["anchor"], po["pages"]) == \
        ("purchase_order_no", "label", "RECEIPT NO", [("b-c80bbbde4d", 14)])
    assert len(p["sections"][1]["claims"]) == 3            # the prose line is not a claim, but it is kept
    assert p["sections"][1]["prose"] == ["Prose for people is kept, but only claims reach the text model."]
    again = wiki.parse(wiki.render("TTG", p["sections"]))
    strip = lambda ps: [(s["head"], s["prose"], [{k: v for k, v in c.items() if k != "line"} for c in s["claims"]])  # noqa: E731
                        for s in ps["sections"]]
    assert strip(again) == strip(p)


def test_a_page_of_a_customer_gets_any_customer_and_its_own_section_only():
    p = wiki.parse(PAGE)
    assert [c["field"] for c in wiki.claims_for(p, "1100002424")] == \
        ["posting_date", "purchase_order_no", "no_ref", "lines.qty"]
    assert [c["field"] for c in wiki.claims_for(p, "1100009999")] == ["posting_date"]
    assert [c["field"] for c in wiki.claims_for(p, None)] == ["posting_date"]


def test_the_text_model_reads_the_combined_lists_names_and_only_named_header_fields_are_laid_over():
    claims = wiki.claims_for(wiki.parse(PAGE), "1100002424")
    text = wiki.hints("TTG", claims)
    assert "- po_number (this TTG's purchase_order_no): RECEIPT NO is the PO number." in text
    assert "- sor (this TTG's no_ref)" in text
    assert wiki.overlay_fields("TTG", claims) == ["po_number", "posting_date", "sor"]   # not the line column
    assert wiki.hints_sha(text) == wiki.hints_sha(text) != wiki.hints_sha(text + " ")


def test_pass_b_replaces_only_its_fields_and_can_be_taken_back():
    fa_a = {"po_number": None, "sor": {"value": "10101000125418"}, "document_no": {"value": "10101000125418"}}
    map_a = {"fields": {"sor": {"block": "b7"}, "document_no": {"block": "b7"}}, "raw": {}}
    fa_b = {"po_number": {"value": "10101000125418"}, "document_no": {"value": "SOMETHING ELSE"}}
    map_b = {"fields": {"po_number": {"block": "b7"}}}
    fa, m = wiki.overlay(fa_a, map_a, fa_b, map_b, ["po_number", "sor"], {"sha": "x"})
    assert fa["po_number"]["value"] == "10101000125418" and "sor" not in fa          # "not printed": emptied
    assert fa["document_no"]["value"] == "10101000125418"                            # not named: pass A's
    assert m["fields"] == {"po_number": {"block": "b7"}, "document_no": {"block": "b7"}}
    back_fa, back_m = wiki.undo(fa, m)
    assert back_fa["sor"] == {"value": "10101000125418"} and back_fa.get("po_number") is None
    assert back_m["fields"] == map_a["fields"] and "pass_b" not in back_m
    assert wiki.pass_a(fa, m) == back_fa                                             # what Jev sees
    assert wiki.changed(fa_a, fa, ["po_number", "sor", "document_no"]) == ["po_number", "sor"]


def test_a_look_again_on_a_field_pass_b_changed_is_forgotten():
    sl = {"asked": ["po_number", "total"], "results": {"po_number": {}, "total": {}}, "bundle_asks": ["x"]}
    assert wiki.forget(sl, ["po_number"]) == {"asked": ["total"], "results": {"total": {}}, "bundle_asks": ["x"]}


def test_truth_comes_from_people_and_satellite_never_from_satellite_for_a_receipts_amounts():
    so = {"sor_no": "SOR26110264129", "cpo_no": "10101000125418", "customer_code": "1400000488",
          "paper": {"dpp": 1000.0, "ppn": 110.0, "total": 1110.0}}
    printed = "|".join(["RECEIPTNO", "10101000125418", "320394.38"])
    assert wiki.truth_of("TTG", {}, so, printed) == {"purchase_order_no": "10101000125418",
                                                     "no_ref": "(not printed)"}   # it prints no SOR reference at all
    assert "purchase_order_no" not in wiki.truth_of("TTG", {}, so, "|NOTHING")                 # PO not printed
    assert wiki.truth_of("TTG", {"no_ref": "(not printed)"}, so, printed)["no_ref"] == "(not printed)"
    assert wiki.truth_of("FP", {}, so, "")["total"] == 1110.0
    assert "total" not in wiki.truth_of("PO", {}, so, printed)


def test_scoring():
    assert wiki.score("purchase_order_no", "1010-1000-125418", "10101000125418") == "right"
    assert wiki.score("purchase_order_no", "10101000125419", "10101000125418") == "wrong"
    assert wiki.score("purchase_order_no", None, "10101000125418") == "empty"
    assert wiki.score("no_ref", None, "(not printed)") == "right"
    assert wiki.score("no_ref", "SOR1", "(not printed)") == "wrong"
    assert wiki.score("total", "1.126.011,00", 1126011) == "right"
    assert wiki.score("total", "1.126.011,00", "1126011.01") == "wrong"


def test_leave_one_out_a_page_never_counts_for_what_was_learned_from_it_or_its_bundle():
    c = [wiki.claim("purchase_order_no", "x [label · A · pages b-1/14]")]
    assert wiki.counts(c, ("b-1", 14), {}) == {"purchase_order_no": False}
    assert wiki.counts(c, ("b-1", 3), {}) == {"purchase_order_no": True}
    assert wiki.counts(c, ("b-1", 3), {("b-1", 3): 7, ("b-1", 14): 7}) == {"purchase_order_no": False}  # one bundle
    assert wiki.counts([wiki.claim("sor", "a person's claim, no page")], ("b-1", 3), {}) == {"sor": True}


def test_the_verdict():
    r = lambda b, a: {"page": "p", "field": "f", "before": b, "after": a}     # noqa: E731
    assert wiki.verdict([r("empty", "right")])["passed"]
    assert not wiki.verdict([r("empty", "right"), r("right", "empty")])["passed"]      # a right value lost
    assert not wiki.verdict([r("empty", "right"), r("empty", "wrong")])["passed"]      # newly wrong
    assert not wiki.verdict([r("right", "right")])["passed"]                            # makes nothing right
    assert not wiki.verdict([r("empty", "right")], flips=1)["passed"]                   # within the model's noise


def _ex(bid, n, chain, field="purchase_order_no", kind="value", left="RECEIPT NO", source="marked"):
    return {"batch_id": bid, "page_no": n, "doc_type": "TTG", "chain": chain, "field": field, "kind": kind,
            "anchor": {"left": left} if left else {}, "source": source}


def test_a_draft_needs_two_pages_in_two_bundles_agreeing():
    one = [_ex("b", 3, "A")]
    assert wiki.draft("TTG", one, {("b", 3): 1}, {}) == []
    same_bundle = [_ex("b", 3, "A"), _ex("b", 4, "A")]
    assert wiki.draft("TTG", same_bundle, {("b", 3): 1, ("b", 4): 1}, {}) == []
    two = [_ex("b", 3, "A"), _ex("b", 14, "A", source="typed")]
    (ch, c, source), = wiki.draft("TTG", two, {("b", 3): 1, ("b", 14): 2}, {})
    assert ch == "A" and c["kind"] == "label" and c["anchor"] == "RECEIPT NO" and source == "typed"
    assert c["pages"] == [("b", 3), ("b", 14)]


def test_a_label_two_customers_agree_on_goes_to_any_customer_unless_another_example_disagrees():
    ex = [_ex("b", 3, "A"), _ex("b", 14, "B")]
    (ch, c, source), = wiki.draft("TTG", ex, {("b", 3): 1, ("b", 14): 2}, {})
    assert ch is None and source == "marked"
    assert wiki.draft("TTG", ex + [_ex("b", 20, "C", left="PO NO")], {("b", 3): 1, ("b", 14): 2, ("b", 20): 3},
                      {}) == []


def test_a_drafted_claim_replaces_its_sections_claim_on_the_same_field():
    p = wiki.parse(PAGE)
    new = {"field": "purchase_order_no", "text": "the value printed right of the label \"ORDER NO\".",
           "kind": "label", "anchor": "ORDER NO", "pages": [("b", 1), ("b", 2)]}
    sections, added = wiki.merge_draft(p, [("1100002424", new, "marked")], {})
    aeon = next(s for s in sections if s["chain"] == "1100002424")
    assert [c["anchor"] for c in aeon["claims"] if c["field"] == "purchase_order_no"] == ["ORDER NO"]
    assert added == [new]
    assert wiki.merge_draft(wiki.parse(wiki.render("TTG", sections)), [("1100002424", new, "marked")], {})[1] == []


# ---------------------------------------------------------------------------------------------- the gate, on the DB

DUTA = "1100002339"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
def test_the_gate_replays_a_proposal_with_kept_answers_and_touches_only_the_pages_it_changes(monkeypatch):
    """A person's claim for Duta Buah's receipt (b-c80bbbde4d p5, its only one) on top of whatever TTG page is active.
    Pass B's answer is seeded (no model call): the PO number from the block printing PO.2026.09.32029. The claim cites
    no page (a person's), so p5 counts: Edward confirmed PO.2026.09.32029 where the reading had PO.RCV-14905/IX/2026.
    Other customers' pages are untouched (their knowledge doesn't change). A tip that passes is switched on at once
    (the user, 2026-10-08), whoever wrote it: here only recorded, never really activated or kept; one built on a page
    that is no longer active is rejected instead."""
    from common import db
    from worker import learn, vf
    with db.connect() as c:
        if c.execute("SELECT 1 FROM staging.knowledge_page WHERE doc_type='TTG' AND status='proposed'").fetchone():
            pytest.skip("a TTG proposal waits for a person")
        now = learn.active(c, "TTG")
        p5 = next((p for p in learn._scope(c, "TTG") if (p["batch_id"], p["page_no"]) == ("b-c80bbbde4d", 5)), None)
        if not p5 or "Duta Buah" in ((now or {}).get("markdown") or "") or not (p5["mapping"] or {}).get("raw"):
            pytest.skip("b-c80bbbde4d p5 isn't stored with its mapping, or Duta Buah already has a section")
        ctx = learn._ctx(c)
    md = ((now or {}).get("markdown") or "# TTG\n\n## Any customer\n").rstrip() + (
        f"\n\n## DUTA BUAH (chain {DUTA})\n- purchase_order_no: the PO number printed as PO.<year>.<month>.<number>, "
        "never the receipt's own PO.RCV number.\n")
    parsed = wiki.parse(md)
    text = wiki.hints("TTG", wiki.claims_for(parsed, DUTA))
    _, mv, _ = vf.two_step_versions(ctx)
    sha = wiki.hints_sha(text)
    block = next(b for b in p5["transcript"] if "32029" in json.dumps(b))
    raw = json.loads(json.dumps(p5["mapping"]["raw"]))
    raw["fields"]["po_number"] = {"block": block["id"], "text": "PO.2026.09.32029", "value": "PO.2026.09.32029"}
    version = None
    try:
        with db.connect() as c:
            c.execute("""INSERT INTO staging.knowledge_map (batch_id, page_no, hints_sha, map_version, raw, raw2)
                         VALUES ('b-c80bbbde4d', 5, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                      (sha, mv, json.dumps(raw), json.dumps(raw)))
        version = learn.propose("TTG", md, "person", "test")

        def no_call(*a, **k):
            raise AssertionError(f"a model call: {a[:3]}")
        monkeypatch.setattr(vf, "ai_call", no_call)
        used = []
        monkeypatch.setattr(learn, "activate", lambda t, v, by, show=print, lint=False: used.append((t, v, by)))
        g = learn.gate("TTG", version, show=lambda *a: None)
        assert list(g["pages"]) == ["b-c80bbbde4d/5"]                  # only Duta Buah's knowledge changed
        rows = g["pages"]["b-c80bbbde4d/5"]["rows"]
        assert [(r["field"], r["before"], r["after"]) for r in rows if r["counted"] and r["field"] == "purchase_order_no"] \
            == [("purchase_order_no", "wrong", "right")]
        assert g["passed"] and not g["lost"] and not g["new_wrong"] and g["mapped"] == 1
        assert used == [("TTG", version, context.AUTO)]                # passed: used at once, no person approves

        def stale(t, v, by, show=print, lint=False):
            raise ValueError(f"TTG #{v} was built on #1, but #2 is active now")
        monkeypatch.setattr(learn, "activate", stale)
        assert learn.gate("TTG", version, show=lambda *a: None)["passed"]
        with db.connect() as c:
            r = c.execute("SELECT status, approved_by FROM staging.knowledge_page WHERE doc_type='TTG' AND version=%s",
                          (version,)).fetchone()
        assert r["status"] == "rejected" and r["approved_by"].startswith("the gate: ")   # never left open
    finally:
        with db.connect() as c:
            if version:
                c.execute("DELETE FROM staging.knowledge_page WHERE doc_type='TTG' AND version=%s", (version,))
            c.execute("DELETE FROM staging.knowledge_map WHERE batch_id='b-c80bbbde4d' AND page_no=5 AND hints_sha=%s",
                      (sha,))


# ---------------------------------------------------------------------------------------------- Stage 2c's rest + Stage 3

def test_a_claim_can_carry_a_region_and_round_trips():
    c = wiki.claim("posting_date", "stamped top right. [position · region 40,700,90,960 · pages b-1/2, b-2/3]")
    assert (c["kind"], c["region"], c["pages"]) == ("position", [40, 700, 90, 960], [("b-1", 2), ("b-2", 3)])
    assert wiki.claim(c["field"], wiki.claim_line(c)[len("- posting_date: "):])["region"] == [40, 700, 90, 960]


def test_a_customers_own_claim_replaces_the_general_one_on_the_same_field():
    md = PAGE + "- posting_date: the stamp's date. [label · DITERIMA]\n"
    claims = wiki.claims_for(wiki.parse(md), "1100002424")
    assert [c["text"] for c in claims if c["field"] == "posting_date"] == ["the stamp's date."]


def test_a_position_claim_reaches_the_text_model_with_its_place_and_a_visual_one_never_does():
    """The user (2026-10-01): what is in the copy, the text model maps (a position too: every line of the copy has
    its position); what isn't (handwriting, a stamp, a mark), the AI OCR reads from the region's crop."""
    text = wiki.claim("purchase_order_no", "RECEIPT NO is the PO number. [label · RECEIPT NO]")
    pos = wiki.claim("posting_date", "printed top right. [position · region 40,700,90,960]")
    eye = wiki.claim("customer_name", "handwritten. [visual · region 100,100,150,400]")
    h = wiki.hints("TTG", [text, pos, eye])
    assert h.splitlines()[1] == ("- posting_date: printed top right. Where: around x 700-960, y 40-90 "
                                 "(positions as in the transcript).") and "handwritten" not in h
    assert wiki.by_region([text, pos, eye]) == [eye]
    assert wiki.overlay_fields("TTG", [text, pos, eye]) == ["po_number", "posting_date"]
    assert wiki.knowledge_sha("TTG", [text]) != wiki.knowledge_sha("TTG", [text, pos])
    assert wiki.knowledge_sha("TTG", [text, pos]) != wiki.knowledge_sha("TTG", [text, pos, eye])
    loose = wiki.claim("posting_date", "somewhere top right. [position]")                 # no place: nothing to say
    cell = wiki.claim("lines.qty", "the fourth number. [position · region 300,500,900,600]")   # rows move
    assert wiki.by_text([loose, cell]) == [] and wiki.by_region([loose, cell]) == []


def test_a_table_column_is_taken_from_pass_bs_row_made_from_the_same_line_of_the_copy():
    fa_a = {"lines": [{"qty": "12", "row_text": "A"}, {"qty": "10", "row_text": "B"}, {"qty": "1", "row_text": "C"}]}
    map_a = {"rows": [{"block": "b10"}, {"block": "b11"}, {"block": "b12"}], "fields": {}}
    fa_b = {"lines": [{"qty": "0", "row_text": "B"}, {"qty": "2", "row_text": "A"}]}   # other order, one row missed
    map_b = {"rows": [{"block": "b11"}, {"block": "b10"}]}
    fa, m = wiki.overlay(fa_a, map_a, fa_b, map_b, [], {"sha": "x"}, cols=["qty"])
    assert [r["qty"] for r in fa["lines"]] == ["2", "0", "1"]                    # C keeps pass A's cell
    assert wiki.pass_a(fa, m)["lines"] == fa_a["lines"] and wiki.undo(fa, m)[0]["lines"] == fa_a["lines"]


def test_region_values_are_laid_over_and_taken_back():
    fa, m = wiki.put({"posting_date": {"value": "2026-09-01"}}, {"fields": {}},
                     {"posting_date": {"value": "12/09/2026", "source_text": "12/09/2026", "box": [1, 2, 3, 4]}},
                     {"posting_date": "visual"})
    assert fa["posting_date"]["value"] == "12/09/2026" and m["pass_b"]["regions"] == ["posting_date"]
    assert wiki.undo(fa, m)[0]["posting_date"] == {"value": "2026-09-01"}


def test_a_position_claim_lets_the_text_model_choose_among_the_lines_in_its_place():
    """Two lines sit in the learned place, the date and a receipt number beside it. Code used to give up on two lines
    (and take a single wrong one); the text model is told the place and sees each line there with its position."""
    from common import transcript
    blocks = [{"id": "b1", "kind": "printed", "text": "12/09/2026", "box": [50, 720, 70, 900]},
              {"id": "b4", "kind": "printed", "text": "R-55012", "box": [45, 710, 60, 760]}]
    pos = wiki.claim("posting_date", "printed in the same place on this customer's TTGs. [position · region 40,700,90,960]")
    p = transcript.map_prompt(blocks, "- posting_date: the date the goods were received", "qty (received)",
                              wiki.hints("TTG", [pos]))
    assert "- posting_date: printed in the same place on this customer's TTGs. Where: around x 700-960, y 40-90" in p
    assert "[b1] printed (x 720-900, y 50-70) 12/09/2026" in p and "[b4] printed (x 710-760, y 45-60) R-55012" in p
    assert p.index("LEARNED FROM PEOPLE") < p.index("TRANSCRIPT:") and "its place" in p


def test_a_receipt_printing_no_sor_reference_at_all_has_none():
    so = {"sor_no": "SOR26110264129", "cpo_no": "10101000125418"}
    assert wiki.truth_of("TTG", {}, so, "|RECEIPTNO|10101000125418")["no_ref"] == "(not printed)"
    assert "no_ref" not in wiki.truth_of("TTG", {}, so, "|NOREF|SOR26110299999")   # prints another: not known
    assert wiki.line_truth({"lines[A1].qty": "2", "lines[ZZ].qty": "5", "total": "1"}, ["B1", "A1"]) == {(1, "qty"): "2"}


def test_nothing_to_score_is_not_a_pass():
    g = wiki.verdict([])
    assert not g["passed"] and "no stored page" in g["why"]


def test_what_kind_a_correction_teaches_is_set_by_code():
    e = lambda **a: {"kind": "value", "field": "posting_date", "region": [1, 2, 3, 4], "anchor": a}   # noqa: E731
    assert wiki.kind_of(e(left="TGL TERIMA", under_kind="printed")) == ("label", "TGL TERIMA")
    assert wiki.kind_of(e(left="TGL", under_kind="stamp")) == ("visual", None)
    assert wiki.kind_of(e(under_kind="printed")) == ("position", None)
    assert wiki.kind_of({**e(header_cell="QTY RCV"), "field": "lines.qty"}) == ("column", "QTY RCV")
    assert wiki.kind_of({**e(), "field": "lines.qty"}) is None
    assert wiki.kind_of({"kind": "not_printed", "field": "no_ref"}) == ("not_printed", None)


def test_position_examples_draft_one_claim_only_where_their_regions_agree():
    base = {"doc_type": "TTG", "field": "posting_date", "kind": "value", "anchor": {"under_kind": "printed"},
            "chain": "A", "source": "marked"}
    ex = [{**base, "batch_id": "b", "page_no": 1, "region": [40, 700, 80, 900]},
          {**base, "batch_id": "b", "page_no": 2, "region": [45, 710, 85, 905]},
          {**base, "batch_id": "b", "page_no": 3, "region": [600, 100, 640, 300]}]
    (ch, c, _), = wiki.draft("TTG", ex, {("b", 1): 1, ("b", 2): 2, ("b", 3): 3}, {})
    assert (ch, c["kind"], c["region"], c["pages"]) == ("A", "position", [40, 700, 85, 905], [("b", 1), ("b", 2)])


def test_the_lint_finds_a_claim_a_person_later_contradicted_never_on_its_own_page():
    parsed = wiki.parse(PAGE)
    page = lambda n, v, said: {"page": ("b-c80bbbde4d", n), "fields_all": {"po_number": {"value": v}},   # noqa: E731
                               "practice": {"purchase_order_no": said},
                               "pass_b": {"chain": "1100002424", "fields": ["po_number", "sor"]}}
    drop, why = wiki.contradictions("TTG", parsed, [page(20, "1", "2"), page(21, "3", "3")])
    assert drop == {("1100002424", "purchase_order_no")} and "b-c80bbbde4d/20" in why[0]
    assert wiki.contradictions("TTG", parsed, [page(14, "1", "2")]) == (set(), [])       # learned there
    kept = wiki.render("TTG", wiki.without(parsed, drop))
    assert "purchase_order_no" not in kept and "no_ref" in kept


EX = {"id": 1, "batch_id": "b-1", "page_no": 3, "field": "purchase_order_no", "kind": "value",
      "value": "10101000125418", "shown": None, "region": [120, 810, 140, 900], "source": "marked",
      "anchor": {"left": "RECEIPT NO"}}
COPY = [{"id": "b6", "kind": "printed", "text": "RECEIPT NO", "box": [120, 700, 140, 800]},
        {"id": "b7", "kind": "printed", "text": "10101000125418", "box": [122, 815, 140, 897]}]


def test_the_teachers_claim_is_checked_by_code():
    ok = {"claim": "the number printed as RECEIPT NO is the PO number", "kind": "label", "anchor": "RECEIPT NO",
          "scope": "customer", "why": "AEON prints no PO field"}
    c, scope = wiki.teacher_claim(ok, EX, COPY)
    assert (c["kind"], c["anchor"], c["pages"], scope) == ("label", "RECEIPT NO", [("b-1", 3)], "customer")
    assert wiki.teacher_claim({**ok, "claim": "it is 10101000125418"}, EX, COPY)[0] is None       # this page's value
    assert wiki.teacher_claim({**ok, "anchor": "ORDER NO"}, EX, COPY)[0] is None                  # not printed here
    assert wiki.teacher_claim({**ok, "kind": "guess"}, EX, COPY)[0] is None
    assert wiki.teacher_claim({**ok, "kind": "column"}, EX, COPY)[0] is None      # a header field isn't a column
    assert wiki.teacher_claim({**ok, "kind": "label"}, {**EX, "field": "lines.qty"}, COPY)[0] is None
    assert wiki.teacher_claim({"no_change": "a one-off"}, EX, COPY) == (None, "the teacher: a one-off")
    pos, _ = wiki.teacher_claim({**ok, "kind": "position", "anchor": None}, EX, COPY)
    assert pos["region"] == [120, 810, 140, 900] and pos["anchor"] is None
    assert wiki.teacher_claim({**ok, "kind": "visual"}, {**EX, "region": None}, COPY)[0] is None
    assert "RECEIPT NO" in wiki.teach_prompt("TTG", "a receipt", "the PO number", EX, "AEON", "", "", "",
                                             "[b6] …", None)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
def test_a_lesson_whose_claim_no_other_page_can_prove_waits_for_pages_without_a_mapping(monkeypatch):
    """Duta Buah's receipt (b-c80bbbde4d p5): the only Duta Buah receipt stored, so a claim learned from it can't be
    proven anywhere else. The teacher (faked) writes one; the gate sees before any call that nothing can score it:
    the lesson needs pages, the proposal is rejected, nothing was mapped. The lesson is put back after."""
    from common import db
    from worker import learn, vf
    with db.connect() as c:
        e = c.execute("""SELECT e.*, NULL::bigint AS bundle_id FROM staging.extract_example e
                          WHERE batch_id='b-c80bbbde4d' AND page_no=5 AND field='purchase_order_no' AND status='active'
                            AND pile='practice'""").fetchone()
        if not e or c.execute("SELECT 1 FROM staging.knowledge_page WHERE doc_type='TTG' AND status='proposed'"
                              ).fetchone():
            pytest.skip("p5's lesson isn't there, or a TTG proposal waits for a person")
        blocks = c.execute("SELECT transcript FROM staging.page WHERE batch_id='b-c80bbbde4d' AND page_no=5"
                           ).fetchone()["transcript"]
        saved = (e["lesson_status"], e["lesson"], e["lesson_doc"], e["lesson_version"], e["lesson_at"])
    label = next(b["text"] for b in blocks if (b.get("text") or "").strip() and len(b["text"]) > 4
                 and "32029" not in b["text"])

    def no_call(*a, **k):
        raise AssertionError(f"a model call: {a[:3]}")
    monkeypatch.setattr(vf, "ai_call", no_call)
    asked = []
    fake = lambda prompt: asked.append(prompt) or ({"claim": "the PO number sits beside this label", "kind": "label",  # noqa: E731
                                                    "anchor": label, "scope": "customer", "why": "test"}, {"model": "fake"})
    version = None
    try:
        try:
            status = learn.teach_one(dict(e), ask=fake, show=lambda *a: None)
        finally:                                   # whatever happened, find the proposal it made (to remove it)
            with db.connect() as c:
                made = c.execute("""SELECT max(version) AS v FROM staging.knowledge_page WHERE doc_type='TTG'
                                     AND created_by LIKE 'teacher%%' AND note LIKE '%%: test'""").fetchone()
                version = made["v"] if made else None
        with db.connect() as c:
            r = c.execute("SELECT lesson_status, lesson_version FROM staging.extract_example WHERE id=%s",
                          (e["id"],)).fetchone()
            version = r["lesson_version"]
            kp = c.execute("SELECT status, gate FROM staging.knowledge_page WHERE doc_type='TTG' AND version=%s",
                           (version,)).fetchone()
        assert len(asked) == 1 and status == "needs_pages" and r["lesson_status"] == "needs_pages"
        assert kp["status"] == "rejected" and kp["gate"]["needs_pages"] and kp["gate"]["mapped"] == 0
    finally:
        with db.connect() as c:
            c.execute("""UPDATE staging.extract_example SET lesson_status=%s, lesson=%s, lesson_doc=%s,
                                lesson_version=%s, lesson_at=%s WHERE id=%s""",
                      (saved[0], json.dumps(saved[1]) if saved[1] is not None else None, saved[2], saved[3], saved[4],
                       e["id"]))
            if version:
                c.execute("DELETE FROM staging.knowledge_page WHERE doc_type='TTG' AND version=%s", (version,))


# ---------------------------------------------------------------------------------------------- the status bar (2026-10-01)

def _lesson_ex(status, pile="practice", **lesson):
    return {"pile": pile, "lesson_status": status, "lesson": lesson, "doc_type": "TTG"}


def _states(lp):
    return [s for _, s in lp["steps"]]


def test_the_status_bar_follows_a_fix_from_save_to_applied():
    """The user (2026-10-01): after a fix, show what is going on, in plain words, step by step."""
    tip = {"answers": [{"answer": {"claim": "On AEON receipts, the PO number is the number after RECEIPT NO"}}]}
    lp = wiki.lesson_progress(_lesson_ex("waiting"), ahead=2)
    assert lp["headline"] == "Waiting for the teacher (2 lessons ahead)." and not lp["final"]
    assert _states(lp) == ["done", "done", "now", "todo", "todo", "todo", "todo"]
    assert "still open" in wiki.lesson_progress(_lesson_ex("waiting"), pending=True)["headline"]
    assert "couldn't reach" in wiki.lesson_progress(_lesson_ex("waiting", error="timeout"))["headline"]
    assert _states(wiki.lesson_progress(_lesson_ex("teaching")))[3] == "now"
    testing = wiki.lesson_progress(_lesson_ex("proposed", **tip), {"progress": {"step": "testing", "done": 3, "of": 7}})
    assert testing["headline"] == "Testing the tip on other pages (3 of 7)…" and _states(testing)[4] == "now"
    assert testing["tip"].startswith("On AEON receipts")
    waits = wiki.lesson_progress(_lesson_ex("proposed", **tip), {"gate": {"passed": True}, "progress": {"step": "tested"}})
    assert waits["headline"] == "The tip passed its test. Switching it on…" and _states(waits)[5] == "now"
    assert not waits["final"]                                       # no person to wait for: it is switched on now
    retry = wiki.lesson_progress(_lesson_ex("proposed", **tip), {"gate": {"passed": False}})
    assert "trying again" in retry["headline"] and not retry["final"]
    applying = wiki.lesson_progress(_lesson_ex("learned", **tip), {"progress": {"step": "applying", "done": 5, "of": 12}})
    assert applying["headline"] == "Learned. Applying the tip to stored pages (5 of 12)…" and not applying["final"]
    done = wiki.lesson_progress(_lesson_ex("learned", **tip), {"progress": {"step": "applied", "of": 12, "changed": 4}})
    assert done["final"] and "4 of 12 stored pages changed" in done["headline"] and _states(done) == ["done"] * 7


def test_the_status_bar_says_when_and_why_nothing_will_be_learned():
    none = wiki.lesson_progress(None)
    assert none["final"] and "wasn't found in the page's copy" in none["headline"] and _states(none)[1] == "stop"
    exam = wiki.lesson_progress(_lesson_ex(None, pile="exam"))
    assert exam["final"] and "test pile" in exam["headline"] and _states(exam)[2:] == ["skip"] * 5
    right = wiki.lesson_progress(_lesson_ex("already_right"))
    assert right["final"] and "already reads this value right" in right["headline"]
    assert "rejected by Edward" in wiki.lesson_progress(_lesson_ex("no_change", rejected_by="Edward"))["headline"]
    assert wiki.lesson_progress(_lesson_ex("no_change", why="not kept after two tries"))["headline"] == \
        "No tip was kept: not kept after two tries"
    pages = wiki.lesson_progress(_lesson_ex("needs_pages"))
    assert pages["final"] and "no other stored page" in pages["headline"] and _states(pages)[4] == "stop"


def test_the_top_bar_names_what_the_teacher_is_doing():
    assert wiki.teacher_badge(progress=("TTG", {"step": "testing", "done": 3, "of": 7})) == \
        "Teacher: testing a TTG tip (3/7)"
    assert wiki.teacher_badge(progress=("PO", {"step": "applying", "done": 5, "of": 12})) == \
        "Teacher: applying a PO tip (5/12)"
    assert wiki.teacher_badge(teaching="TTG", waiting=3) == "Teacher: writing a TTG tip"
    assert wiki.teacher_badge(waiting=1, approvals=2) == "2 tips waiting for your approval"
    assert wiki.teacher_badge(waiting=1) == "Teacher: 1 lesson waiting"
    assert wiki.teacher_badge() is None


def test_a_tip_is_applied_once_per_page_and_never_to_a_published_order():
    """The user (2026-10-01): a published order is never checked again, so re-reading its pages only costs calls."""
    pages = [{"batch_id": "b", "page_no": 1, "bundle_status": "auto_ok"},
             {"batch_id": "b", "page_no": 2, "bundle_status": "published"},
             {"batch_id": "b", "page_no": 2, "bundle_status": None},          # the same page on a second row
             {"batch_id": "b", "page_no": 3, "bundle_status": None},          # not grouped yet
             {"batch_id": "b", "page_no": 1, "bundle_status": "auto_ok"}]
    assert [p["page_no"] for p in wiki.to_apply(pages)] == [1, 3]


def test_the_status_bar_says_it_in_indonesian_and_asks_again_until_final():
    """What the web app's status bar after a fix shows (/api/v1/lessons): it asks again until the lesson is final."""
    from api import bahasa
    live = bahasa.lesson(wiki.lesson_progress(_lesson_ex("teaching")))
    assert not live["final"] and live["headline"] == "Guru AI sedang menulis kiat dari perbaikan Anda…"
    assert ("Guru AI menulis kiat", "now") in live["steps"]
    final = bahasa.lesson(wiki.lesson_progress(_lesson_ex("already_right")))
    assert final["final"] and any(state == "skip" for _, state in final["steps"])


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database (and migration 023)")
def test_the_status_queries_run_on_the_database():
    from common import db
    from api import app
    with db.connect() as c:
        lp = app.lesson_status(c, "no-such-batch", 1, "purchase_order_no")
        assert lp["final"] and "wasn't found" in lp["headline"]          # no fix there: no example
        line = app.teacher_now(c)
        assert line is None or line.startswith(("Teacher:", "1 tip", "2 tips")) or "tips waiting" in line


# ---------------------------------------------------------------------------------------------- the knowledge screen

def test_the_knowledge_screen_shows_every_type_with_a_field_list():
    """A Faktur Pajak page the teacher wrote (FPJ #2, 2026-10-05) was missing from /knowledge: the screen had a fixed
    list of three types. It now follows the field list, and shows any other type a page was written for."""
    from api.app import knowledge_types
    from common.fields import DOCS
    assert knowledge_types() == [t for t, d in DOCS.items() if d.get("header")]
    assert "FPJ" in knowledge_types()
    assert knowledge_types([{"doc_type": "PEL"}, {"doc_type": "TTG"}])[-1] == "PEL"


def test_the_teacher_is_told_the_place_in_words_and_how_positions_run():
    """FPJ #2 (2026-10-06): the teacher read x 35-220 (the left fifth) as "bottom right". The prompt had two position
    formats and never said which way the axes run. Now: one format (x/y), the axes explained, the place in words."""
    assert wiki.place([719, 34, 727, 118]) == "lower left, about 72% down the page"
    assert wiki.place([40, 700, 90, 960]) == "top right, about 6% down the page"
    assert wiki.place([480, 400, 520, 600]) == "centre, about 50% down the page"
    assert wiki.place([920, 20, 960, 990]).startswith("bottom part, across the page")
    p = wiki.teach_prompt("FPJ", "a tax invoice", "the billing number", {**EX, "field": "billing_number",
                          "region": [719, 34, 727, 118]}, None, "", "", "", "[b28] …", None)
    assert "WHERE IT IS PRINTED: lower left, about 72% down the page (x 34-118, y 719-727)" in p
    assert "x runs from the left edge (0) to the right\nedge (1000)" in p and "[top, left, bottom, right]" not in p
    assert "never your own\n  reading of the numbers" in p
    assert "not marked on the page" in wiki.teach_prompt("FPJ", "", "", {**EX, "region": None}, None, "", "", "", "", None)


# ---------------------------------------------------------------------------------------------- what is tested, what is redone
# 2026-10-06 (the user: "we dont need to remap all the related docs … we just leave some for exam"): the test reads
# again only pages with a known answer (Satellite now gives a Faktur Pajak's), and a switched-on tip is used only on
# pages that still need it.

SO_A = {"sor_no": "SOR26110200001", "billing_no": "7000300001", "customer_code": "1"}


def test_a_faktur_pajaks_billing_number_is_known_from_satellite_where_the_page_prints_it():
    printed = "FAKTURPAJAK|7000300001SOR26110200001|JAKARTA"
    assert wiki.truth_of("FPJ", {}, SO_A, printed) == {"billing_number": "7000300001"}
    assert wiki.truth_of("FPJ", {}, SO_A, "FAKTURPAJAK|SOR26110200001") == {}          # not printed: can't score it
    assert wiki.truth_of("FPJ", {}, None, printed) == {}
    assert wiki.truth_of("FPJ", {"billing_number": "7000300009"}, SO_A, printed)["billing_number"] == "7000300009"


def test_the_order_a_faktur_pajak_prints_is_the_one_sor_it_names():
    sos = {"SOR26110200001": SO_A, "SOR26110200002": {"sor_no": "SOR26110200002"}}
    assert wiki.printed_order("7000300001SOR26110200001", sos) is SO_A
    assert wiki.printed_order("7000300001|26110200001", sos) is SO_A                  # printed without its letters
    assert wiki.printed_order("SOR26110200001|SOR26110200002", sos) is None           # two orders: neither
    assert wiki.printed_order("SOR26110299999", sos) is None                          # misread: no order
    assert wiki.printed_order("", sos) is None


def test_a_billing_number_is_scored_on_its_own_digits():
    assert wiki.score("billing_number", "7000359122/S OR 26110260590", "7000359122") == "right"
    assert wiki.score("billing_number", "7000358320/26110260343", "7000358320") == "right"
    assert wiki.score("billing_number", "7000359123", "7000359122") == "wrong"
    assert wiki.score("billing_number", None, "7000359122") == "empty"


def test_the_test_reads_people_s_answers_first_then_the_newest_up_to_its_limit():
    from datetime import datetime
    plan = [{"id": i, "person": i in (7, 3), "at": datetime(2026, 10, 1 + i)} for i in range(10)]
    assert [x["id"] for x in wiki.test_pages(plan, cap=4)] == [7, 3, 9, 8]
    assert len(wiki.test_pages(plan * 5)) == wiki.TEST_PAGES


def test_a_switched_on_tip_is_used_only_where_its_field_isnt_settled():
    assert not wiki.needs_tip({"billing_number"}, {"billing_number", "sor"})
    assert wiki.needs_tip({"billing_number"}, {"sor"})
    assert wiki.needs_tip({"lines.qty"}, {"lines.qty"})                     # a column is never settled as a whole
    assert wiki.needs_tip({"billing_number", "sor"}, {"billing_number"})
    assert "text" not in wiki.INDEPENDENT                                    # print shows characters, not the field


# ---------------------------------------------------------------------------------------------- pass B asks only what it names

def test_the_knowledge_pass_asks_only_for_the_fields_its_tips_name(monkeypatch):
    """The user (2026-10-09): mapping the whole list again to change one or two fields is wasted; asking only for
    those is about half the tokens, with no rows or notes in the answer when no column is named."""
    import numpy as np
    from common import db, transcript
    from worker import learn, vf
    with db.connect() as c:
        ctx = learn._ctx(c)
    claims = wiki.claims_for(wiki.parse(PAGE), "1100002424")
    fields, cols = wiki.overlay_fields("TTG", claims), wiki.line_columns(claims)
    assert (fields, cols) == (["po_number", "posting_date", "sor"], ["qty"])

    full = context.vlm_schema(ctx)
    only = context.named_schema(full, ["po_number"])
    assert list(only["properties"]) == ["po_number"]                    # no other field, and no rows
    rows = context.named_schema(full, ["po_number"], ["qty"])["properties"]["lines"]["items"]["properties"]
    assert list(rows) == ["qty", "row_text"]

    blocks = [{"id": "b1", "kind": "printed", "text": "RECEIPT NO 10101000125543", "box": [10, 600, 20, 900]},
              {"id": "b2", "kind": "printed", "text": "Kepada PT SARANA ABADI MAKMUR BERSAMA", "box": [30, 40, 40, 400]}]
    prompt = transcript.map_named_prompt(blocks, "- po_number: the customer's PO", "", "- po_number: RECEIPT NO.")
    assert "Task 1 - fields" in prompt and "Task 2" not in prompt and "Task 3" not in prompt and "notes" not in prompt
    assert '"lines"' not in prompt and "RECEIPT NO 10101000125543" in prompt and "SARANA" in prompt
    assert "Task 2 - lines" in transcript.map_named_prompt(blocks, "- po_number: x", "qty (the quantity)", "- tip")

    sent = []

    def call(purpose, bid, n, fn, blocks_, schema, text):
        sent.append((purpose, fn, list(schema["properties"])))
        return {"fields": {"po_number": {"block": "b1", "text": "10101000125543", "value": "10101000125543"}}}, {}
    monkeypatch.setattr(vf, "ai_call", call)
    monkeypatch.setattr(learn, "_kept", lambda *a: None)
    monkeypatch.setattr(learn, "_keep", lambda *a, **k: None)
    page = {"transcript": blocks, "ocr_words": None, "upright_path": None}
    fa, _, _ = learn.pass_b("b-x", 1, page, "- po_number: RECEIPT NO.", "sha", ctx, np.full((1000, 800), 255, np.uint8),
                            ["po_number"])
    assert {(p, f, tuple(s)) for p, f, s in sent} == {("map_b", vf.map_named, ("po_number",))}
    assert fa["po_number"]["value"] == "10101000125543" and "customer_name" not in fa
