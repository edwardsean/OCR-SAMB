"""vlm-first labels: a label decides the page; a practice label the machine got unsure or wrong becomes a lesson;
an exam label never reaches the teacher. Uses a throwaway batch; touches nothing else."""
import json
import os

import httpx
import pytest

from common import db
from common.models import teacher

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("API_URL", "http://localhost:8000")
BID = "test-vf-labels"
READING = {"document_no": {"value": "6155059", "source_text": "6155059"}, "lines": []}


@pytest.fixture
def batch():
    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.model_call WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.lesson WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.type_label WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
    clean()
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s,'t','t',repeat('2',64),current_date,3,'read')""", (BID,))
        machine = {1: {"status": "decided", "doc_type": "TTG"}, 2: {"status": "unsure", "guess": "PO"},
                   3: {"status": "unsure", "guess": "PO"}}
        for n in (1, 2, 3):
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, type_status, fields_all, type_votes)
                         VALUES (%s,%s,'x','read',%s,%s,%s)""",
                      (BID, n, "decided" if n == 1 else "unsure", json.dumps(READING), json.dumps({"machine": machine[n]})))
        for n, pile in ((1, "practice"), (2, "practice"), (3, "exam")):   # piles fixed up front, like a drawn pile
            c.execute("""INSERT INTO staging.type_label (batch_id, page_no, label, pile) VALUES (%s,%s,'OTHER',%s)""",
                      (BID, n, pile))
    yield
    clean()


def label(page, value):
    r = httpx.post(f"{UI}/api/v1/labels", json={"batch": BID, "page": page, "label": value}, timeout=60)
    assert r.status_code == 200


def lessons():
    with db.connect() as c:
        return {r["page_no"]: r["status"] for r in c.execute(
            "SELECT page_no, status FROM staging.lesson WHERE batch_id=%s", (BID,))}


def test_practice_label_the_machine_missed_becomes_a_lesson_exam_never(batch):
    label(1, "TTG")            # machine already said TTG: nothing to learn
    label(2, "TTG")            # machine unsure, practice pile: a lesson
    label(3, "TTG")            # machine unsure, but exam pile: never a lesson
    assert lessons() == {2: "waiting"}
    with db.connect() as c:
        piles = {r["page_no"]: r["pile"] for r in c.execute(
            "SELECT page_no, pile FROM staging.type_label WHERE batch_id=%s", (BID,))}
    assert piles == {1: "practice", 2: "practice", 3: "exam"}          # relabelling never moves a pile


def test_the_teacher_is_never_called_for_an_exam_page(batch, monkeypatch):
    from worker import lesson
    with db.connect() as c:
        c.execute("INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, 3, 'TTG')", (BID,))
        row = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=3", (BID,)).fetchone()

    def boom(*a, **k):
        raise AssertionError("the teacher was called for an exam-pile page")
    monkeypatch.setattr(teacher, "ask", boom)
    assert lesson.run_one(row) == "failed"
    assert lessons()[3] == "failed"


def test_one_change_at_a_time():
    from worker import lesson
    asked = []

    def ask(l):
        asked.append(l)
        return {"a": "no_change", "b": "proposed", "c": "proposed"}[l]
    assert [s for _, s in lesson.ask_in_turn(["a", "b", "c"], ask)] == ["no_change", "proposed"]
    assert asked == ["a", "b"]                   # c waits until a person approves or rejects b
    asked.clear()
    assert [s for _, s in lesson.ask_in_turn(["a", "r", "c"], lambda l: asked.append(l) or
                                             {"a": "failed", "r": "retry", "c": "proposed"}[l])] == ["failed", "retry"]
    assert asked == ["a", "r"]                   # a model can't be reached: stop, retry later


def test_the_replay_keeps_each_answer_with_its_page():
    """Jev is asked about several pages at once; the counts must still pair each page's before with its after."""
    from worker import lesson
    practice = [{"page_no": n, "truth": t, "fields_all": {}, "qr_text": None, "layout_score": 0, "batch_id": "b"}
                for n, t in ((2, "TTG"), (7, "TTG"), (11, "PO"), (15, "CONTINUATION"))]
    anchors = [{"page_no": n, "fields_all": {}, "qr_text": "SOR1", "layout_score": 0.9, "batch_id": "b"}
               for n in (1, 3)]
    old, new = {"name": "old"}, {"name": "new"}
    answers = {("old", 2): None, ("new", 2): "TTG", ("old", 7): "TTG", ("new", 7): "TTG",
               ("old", 11): None, ("new", 11): "TTG", ("old", 15): "CONTINUATION", ("new", 15): "CONTINUATION",
               ("new", 1): "FP", ("new", 3): "FP"}

    def fake(fields_all, ctx, qr, lay, bid, n, purpose, title=None):
        return answers[(ctx["name"], n)], True
    g = lesson.verdict(lesson.score(practice, anchors, old, new, decide=fake))
    assert (g["right_before"], g["right_after"], g["unsure_before"], g["unsure_after"]) == (2, 3, 2, 0)
    assert g["new_wrong"] == [11] and not g["anchor_flips"] and not g["passed"]     # p11 PO became TTG: refused
    assert g["pages"]["2"] == {"label": "TTG", "before": None, "after": "TTG"}

    def jev_down_before(fields_all, ctx, qr, lay, bid, n, purpose, title=None):
        return (None, False) if ctx["name"] == "old" else (answers[("new", n)] if n != 11 else "PO", True)
    g = lesson.verdict(lesson.score(practice, anchors, old, new, decide=jev_down_before))
    assert g["jev_errors"] and not g["passed"]  # a failed "before" would look like an improvement: never passes


def test_a_lesson_a_newer_context_already_fixed_skips_the_teacher(batch, monkeypatch):
    import numpy as np
    from worker import lesson
    with db.connect() as c:
        c.execute("INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, 2, 'OTHER')", (BID,))
        row = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=2", (BID,)).fetchone()
    asked = []
    monkeypatch.setattr(teacher, "ask", lambda *a: asked.append(a) or ({"change": {"kind": "none"}}, {}))
    monkeypatch.setattr(lesson.v1, "load", lambda key: np.zeros((8, 8), np.uint8))
    monkeypatch.setattr(lesson.v1, "png_bytes", lambda a: b"png")
    monkeypatch.setattr(lesson, "jev_decides", lambda *a: "OTHER")            # the label (type_label says OTHER)

    monkeypatch.setattr(lesson, "approved_after", lambda c, v, when: True)     # a change approved since the miss
    assert lesson.run_one(row) == "no_change" and not asked                   # fixed already: no teacher call

    monkeypatch.setattr(lesson, "approved_after", lambda c, v, when: False)    # the context that missed it
    assert lesson.run_one(row) == "no_change" and len(asked) == 1             # asked (a re-ask could pass by luck)


def test_the_gate_keeps_only_a_change_that_helps():
    from worker import lesson
    base = {"practice_pages": 6, "right_before": 1, "unsure_before": 5, "wrong_before": 0, "new_wrong": [],
            "anchor_flips": []}
    assert not lesson.verdict({**base, "right_after": 1, "unsure_after": 5, "wrong_after": 0})["passed"]  # no help
    assert lesson.verdict({**base, "right_after": 3, "unsure_after": 3, "wrong_after": 0})["passed"]
    assert not lesson.verdict({**base, "right_after": 3, "unsure_after": 2, "wrong_after": 1, "new_wrong": [9]})["passed"]
    assert not lesson.verdict({**base, "right_after": 3, "unsure_after": 3, "wrong_after": 0,
                               "anchor_flips": [1]})["passed"]


def test_the_teacher_gets_a_second_try_told_why(batch, monkeypatch):
    import numpy as np
    from worker import lesson
    with db.connect() as c:
        c.execute("INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, 2, 'OTHER')", (BID,))
        row = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=2", (BID,)).fetchone()
    answers = iter([{"change": {"kind": "field_for_type", "field": "document_title", "how_often": "usually"}},
                    {"change": {"kind": "none"}}])
    prompts = []
    monkeypatch.setattr(teacher, "ask", lambda png, p: prompts.append(p) or (next(answers), {}))
    monkeypatch.setattr(lesson.v1, "load", lambda key: np.zeros((8, 8), np.uint8))
    monkeypatch.setattr(lesson.v1, "png_bytes", lambda a: b"png")
    monkeypatch.setattr(lesson, "approved_after", lambda c, v, when: False)
    assert lesson.run_one(row) == "no_change" and len(prompts) == 2
    assert "EARLIER TRY" not in prompts[0] and "on every type already" in prompts[1]


def test_the_teacher_is_told_which_fields_pull_toward_another_type():
    """Page 11 (DFI purchase order): DPP was found, FP lists it as always, PO didn't list it at all (the context before
    that lesson; the seed has learned it since: "a PO usually prints DPP")."""
    import copy
    from common import context
    from worker import classify, lesson
    learned = context.seed_content()
    ctx = copy.deepcopy(learned)
    ctx["types"]["PO"]["fields"] = [f for f in ctx["types"]["PO"]["fields"] if f["name"] != "dpp"]
    state = {"found": {"dpp": "890,512.33", "ppn": "106,861.00", "total": "1,078,329.00", "po_number": "58423526"}}
    probs = {"probabilities": {"PO": 0.86, "FP": 0.11, "TTG": 0.02}}
    facts = lesson.pulls(ctx, "PO", state, probs)
    assert facts == {"FP": {"weight": 0.11, "fields": {"dpp": "always"}}}      # ppn and total: PO lists them
    assert "dpp (always)" in lesson.prompt(ctx, "PO", None, state, {"probabilities": {"PO": 0.86, "FP": 0.11}})
    assert lesson.pulls(learned, "PO", state, probs) == {}                    # learned: DPP pulls nowhere now


def test_template_text_and_unprinted_titles_are_refused():
    from worker import lesson
    copied = {"kind": "edit_type", "type": "PO", "what": "POs from DFI carry a received stamp",   # page 11, try 1
              "not_for": "optional", "titles_add": ["title as printed"]}
    assert lesson.placeholder_problems(copied)
    assert lesson.placeholder_problems({"kind": "field_for_type", "field": "<a name from FIELD LIST>"})
    assert not lesson.placeholder_problems({"kind": "field_for_type", "field": "dpp", "how_often": "sometimes",
                                            "note": "DFI's PURCHASE ORDER prints DPP"})
    page = {"classical_text": "PURCHASE ORDER\nPT DFI RETAIL NUSANTARA, TBK\nDPP 890,512.33"}
    assert not lesson.titles_problems({"titles_add": ["PURCHASE ORDER"]}, page)
    assert lesson.titles_problems({"titles_add": ["GOODS RECEIPT"]}, page)


def test_teacher_answers_are_parsed_strictly():
    assert teacher.parse('```json\n{"change": {"kind": "none"}}\n```') == {"change": {"kind": "none"}}
    assert teacher.parse('Here: {"evidence": "x", "change": {"kind": "none"}} done')["evidence"] == "x"
    with pytest.raises(ValueError):
        teacher.parse("no json here")
