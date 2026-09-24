"""vlm-first: Jev's context. The combined field list always equals the union of the types' fields, and only the
allowed changes get through (a field new for a type leaves the list alone; a genuinely new field joins both)."""
import copy

from common import context
from common.fields import CANON
from worker import classify


def seed():
    return context.seed(classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS, classify.JEV_TYPES)


def test_seed_is_valid_and_the_union_is_the_list():
    s = seed()
    assert context.validate(s) == []
    assert context.union_equals_list(s)
    assert set(s["fields"]) == set(CANON)                      # 18 stored + 2 clue fields
    assert set(s["types"]) == set(context.TYPES)


def test_generated_schema_and_question_cover_every_field():
    s = seed()
    schema = context.vlm_schema(s)
    assert set(schema["properties"]) == set(s["fields"]) | {"lines"}
    crit = context.jev_question(s)["doc_type"]["criteria"]
    named = {x.split(":")[0] for c in crit.values() for k, v in c.items() if k.endswith("_has") for x in v}
    assert named == set(s["fields"])
    assert "issuer" not in s["fields"]                       # the AI OCR once named SAMB as a PO's issuer


def test_orphan_and_dangling_fields_are_rejected():
    s = seed()
    orphan = copy.deepcopy(s)
    orphan["fields"]["loose_end"] = {"kind": "id", "meaning": "no type has it", "printed_as": [], "role": "clue"}
    assert any("no type has" in p for p in context.validate(orphan))
    dangling = copy.deepcopy(s)
    dangling["types"]["TTG"]["fields"].append({"name": "ghost", "how_often": "sometimes", "note": ""})
    assert any("not on the combined field list" in p for p in context.validate(dangling))


def test_new_type_removed_field_and_budgets_are_rejected():
    s = seed()
    extra = copy.deepcopy(s)
    extra["types"]["INVOICE2"] = copy.deepcopy(extra["types"]["OTHER"])
    assert context.validate(extra)
    removed = copy.deepcopy(s)
    del removed["fields"]["dpp"]
    for t in removed["types"].values():
        t["fields"] = [f for f in t["fields"] if f["name"] != "dpp"]
    assert any("stored field 'dpp' was removed" in p for p in context.validate(removed))
    long = copy.deepcopy(s)
    long["types"]["TTG"]["what"] = "x" * 500
    assert context.validate(long)


def test_field_new_for_a_type_leaves_the_list_unchanged():
    s = seed()
    new, problems = context.apply_change(s, {"kind": "field_for_type", "type": "PO", "field": "sor",
                                             "how_often": "sometimes", "note": "some customers' POs quote the SOR"})
    assert problems == []
    assert set(new["fields"]) == set(s["fields"])            # the combined list is the same
    assert "sor" in [f["name"] for f in new["types"]["PO"]["fields"]]
    assert context.union_equals_list(new)


def test_genuinely_new_field_joins_the_list_and_the_type():
    s = seed()
    change = {"kind": "new_field", "type": "TTG", "how_often": "sometimes", "note": "Indomaret BPB",
              "field": {"name": "bpb_number", "kind": "id", "meaning": "Indomaret's goods-receipt (BPB) number",
                        "printed_as": ["BPB No."], "example": {"value": "92963", "source_text": "92963"}}}
    new, problems = context.apply_change(s, change)
    assert problems == []
    assert new["fields"]["bpb_number"]["role"] == "clue"     # staging only, never a Satellite column by itself
    assert context.union_equals_list(new)
    assert context.fields_version(new) != context.fields_version(s)   # the AI OCR will read it from now on


def test_existing_fields_cannot_be_renamed_as_new_or_redefined():
    s = seed()
    _, problems = context.apply_change(s, {"kind": "new_field", "type": "TTG", "field": {
        "name": "po_number", "kind": "id", "meaning": "x", "example": {"source_text": "1"}}})
    assert problems and "already on the list" in problems[0]
    _, problems = context.apply_change(s, {"kind": "field_for_type", "type": "TTG", "field": "no_such_field"})
    assert problems
    _, problems = context.apply_change(s, {"kind": "rename_field", "type": "TTG"})
    assert problems
