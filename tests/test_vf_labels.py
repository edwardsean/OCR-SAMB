"""vlm-first labels: a label decides the page; a practice label the machine got unsure or wrong becomes a lesson;
an exam label never reaches the teacher. Uses a throwaway batch; touches nothing else."""
import json
import os

import httpx
import pytest

from common import db
from common.models import teacher

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("UI_URL", "http://ui:8000")
BID = "test-vf-labels"
READING = {"document_no": {"value": "6155059", "source_text": "6155059"}, "lines": []}


@pytest.fixture
def batch():
    def clean():
        with db.connect() as c:
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
    r = httpx.post(f"{UI}/label", data={"batch": BID, "page": page, "label": value}, follow_redirects=False)
    assert r.status_code == 303


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


def test_teacher_answers_are_parsed_strictly():
    assert teacher.parse('```json\n{"change": {"kind": "none"}}\n```') == {"change": {"kind": "none"}}
    assert teacher.parse('Here: {"evidence": "x", "change": {"kind": "none"}} done')["evidence"] == "x"
    with pytest.raises(ValueError):
        teacher.parse("no json here")
