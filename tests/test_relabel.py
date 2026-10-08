"""Changing a page's type (the user, 2026-10-08: "a mechanism that a user can reclassify a document that is classified
by the classifier … then the teacher analyzes why and changes the classifier's context"). api/relabel.py says when a
type may be changed and what the correction is doing; actions.save_label refuses it for an order already sent to
Satellite and while the page is queued; a practice label the classifier answered otherwise is a lesson."""
import json
import os

import pytest

from api import relabel

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
BID, SOR = "test-relabel", "SORTEST-RELABEL"
NAMES = {"FP": "Faktur Penjualan", "TTG": "Tanda Terima", "PO": "Purchase Order (PO)"}


def state(label="PO", pile="practice", machine=("decided", "TTG"), status="read", doc_type="PO", lesson=None,
          proposal=None, pending=None, ahead=0):
    return {"page": {"status": status, "doc_type": doc_type, "machine": {"status": machine[0], "doc_type": machine[1]}},
            "label": {"label": label, "labelled_by": "Edward", "labelled_at": "t", "pile": pile, "note": None},
            "lesson": lesson, "proposal": proposal, "pending": pending, "ahead": ahead}


def steps(lp):
    return [s for _, s in lp["steps"]]


def test_when_a_type_cant_be_changed():
    assert relabel.refusal("read", False) is None
    assert relabel.refusal("rendered", False) is None                 # not read yet: the label waits for the reading
    assert "Satellite" in relabel.refusal("read", True)                # Satellite keeps the values it was sent
    assert "sedang diproses" in relabel.refusal("queued", False)       # its worker would save the old type after it


def test_what_a_correction_is_doing():
    assert relabel.progress({**state(), "label": None}, NAMES) is None
    same = relabel.progress(state(label="TTG", doc_type="TTG"), NAMES)
    assert "sama dengan jawaban sistem" in same["headline"] and same["final"]
    exam = relabel.progress(state(pile="exam"), NAMES)
    assert "ujian" in exam["headline"] and steps(exam) == ["done", "done"] and exam["final"]
    busy = relabel.progress(state(status="queued", pile="exam"), NAMES)
    assert steps(busy) == ["done", "now"] and not busy["final"]       # processed again as PO: not yet

    waiting = {"status": "waiting", "answer": None, "error": None, "proposal": None}
    lp = relabel.progress(state(lesson=waiting, ahead=2), NAMES)
    assert steps(lp) == ["done", "done", "now", "todo"] and "2 pelajaran" in lp["headline"] and not lp["final"]
    lp = relabel.progress(state(lesson=waiting, pending=5), NAMES)    # an old proposal from before 2026-10-08 waits
    assert "#5" in lp["headline"] and lp["final"] and steps(lp)[2] == "todo"
    lp = relabel.progress(state(lesson={**waiting, "error": "will retry: teacher: ReadTimeout"}), NAMES)
    assert "belum bisa dihubungi" in lp["headline"] and lp["detail"].startswith("will retry")

    change = {"kind": "field_for_type", "type": "PO", "field": "po_number", "how_often": "sometimes",
              "note": "Hero's Surat Pesanan prints No. Pesanan"}
    kept = {"status": "proposed", "proposal": 5, "error": None,
            "answer": {"why_missed": "no title, and the rows look like a receipt", "change": change}}
    lp = relabel.progress(state(lesson=kept, proposal={"version": 5, "status": "active", "approved_by": "auto"}), NAMES)
    assert steps(lp) == ["done"] * 4 and "langsung diubah (konteks #5)" in lp["headline"] and lp["final"]  # no person
    assert lp["why"].startswith("no title") and "po_number" in lp["tip"] and "diganti" not in lp["headline"]
    lp = relabel.progress(state(lesson=kept, proposal={"version": 5, "status": "retired", "approved_by": "auto"}), NAMES)
    assert steps(lp) == ["done"] * 4 and "diganti lagi" in lp["headline"]      # a later change, or taken back
    lp = relabel.progress(state(lesson=kept, proposal={"version": 5, "status": "rejected", "approved_by": "Edward"}), NAMES)
    assert steps(lp)[-1] == "stop" and "ditolak" in lp["headline"]

    lp = relabel.progress(state(lesson={"status": "no_change", "answer": {"note": "context #6 … already decides it"},
                                        "error": None, "proposal": None}), NAMES)
    assert "sudah menjawab" in lp["headline"]
    failed = {"status": "failed", "proposal": None, "error": "no change was kept after 2 tries: …",
              "answer": {"tries": [{"why_missed": "the AI read no title", "change": change, "not_kept": "…"}]}}
    lp = relabel.progress(state(lesson=failed), NAMES)
    assert steps(lp)[-1] == "stop" and lp["why"] == "the AI read no title" and lp["tip"] is None   # never "proposed"
    assert "tetap dipakai" in lp["headline"] and lp["detail"].startswith("no change")


def test_the_change_in_a_sentence():
    assert relabel.change_text({"kind": "none"}, NAMES) is None
    assert relabel.change_text({"kind": "edit_type", "type": "TTG", "what": "A receipt.", "titles_add": ["BPB"]},
                               NAMES) == "Deskripsi Tanda Terima: A receipt. Judul baru: BPB."
    s = relabel.change_text({"kind": "new_field", "type": "PO", "note": "Hero",
                             "field": {"name": "order_ref", "meaning": "the order's reference"}}, NAMES)
    assert s == "Isian baru untuk Purchase Order (PO): order_ref, the order's reference (Hero)."


@pytest.fixture
def pages(monkeypatch):
    """Pages of one temporary file, each read and decided by the classifier: 1 TTG (really a PO), 2 PO (exam pile
    already drawn), 3 being processed, 4 in an order already sent to Satellite. Nothing is sent to a queue."""
    from common import db, queue
    from api import app

    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.bundle_document WHERE bundle_id IN (SELECT id FROM staging.bundle WHERE sor_no=%s)", (SOR,))
            c.execute("DELETE FROM staging.bundle WHERE sor_no=%s", (SOR,))
            c.execute("DELETE FROM staging.document WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.model_call WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.lesson WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.type_label WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
    clean()
    reading = json.dumps({"document_no": {"value": "1", "source_text": "1"}, "lines": []})
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s,'t.pdf','t',repeat('8',64),current_date,4,'read')""", (BID,))
        for n, status, t in ((1, "read", "TTG"), (2, "read", "PO"), (3, "queued", "PO"), (4, "read", "FP")):
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, type_status, doc_type,
                                                   fields_all, type_votes)
                         VALUES (%s,%s,'x',%s,'decided',%s,%s,%s)""",
                      (BID, n, status, t, reading, json.dumps({"machine": {"status": "decided", "doc_type": t}})))
        c.execute("INSERT INTO staging.type_label (batch_id, page_no, label, pile) VALUES (%s, 2, 'PO', 'exam')", (BID,))
        doc = c.execute("""INSERT INTO staging.document (batch_id, doc_type, page_from, page_to) VALUES (%s,'FP',4,4)
                           RETURNING id""", (BID,)).fetchone()["id"]
        b = c.execute("INSERT INTO staging.bundle (sor_no, status) VALUES (%s,'published') RETURNING id", (SOR,)).fetchone()["id"]
        c.execute("INSERT INTO staging.bundle_document (bundle_id, document_id) VALUES (%s,%s)", (b, doc))
    woken = []
    monkeypatch.setattr(queue, "wake_teacher", woken.append)
    monkeypatch.setattr(app, "EXAM_SHARE", 0.0)                   # a new label is drawn into the practice pile
    yield woken
    clean()


def lessons():
    from common import db
    with db.connect() as c:
        return {r["page_no"]: (r["label"], r["status"]) for r in c.execute(
            "SELECT page_no, label::text AS label, status FROM staging.lesson WHERE batch_id=%s", (BID,))}


@needs_db
def test_a_wrong_type_is_changed_and_taught_never_for_a_sent_order(pages):
    from api import actions
    woken = pages
    actions.save_label(BID, 1, "PO", note="Surat Pesanan, no title", labelled_by="tester")
    assert lessons() == {1: ("PO", "waiting")} and len(woken) == 1      # the classifier said TTG: the teacher learns
    actions.save_label(BID, 2, "TTG", labelled_by="tester")             # exam pile: graded, never taught
    assert 2 not in lessons()
    for page, why in ((3, "sedang diproses"), (4, "Satellite")):
        with pytest.raises(actions.ActionError, match=why) as e:
            actions.save_label(BID, page, "TTG", labelled_by="tester")
        assert e.value.status == 409
    with pytest.raises(actions.ActionError) as e:
        actions.save_label(BID, 99, "TTG", labelled_by="tester")
    assert e.value.status == 404


@needs_db
def test_the_page_shows_its_type_and_what_the_correction_is_doing(pages):
    from api import actions, v1
    actions.save_label(BID, 1, "PO", labelled_by="tester")
    d = json.loads(v1.page(BID, 1).body)
    assert d["label"]["label"] == "PO" and d["machine"] == {"status": "decided", "doc_type": "TTG"}
    assert d["relabel_refused"] is None
    assert "Satellite" in json.loads(v1.page(BID, 4).body)["relabel_refused"]
    lp = json.loads(v1.type_lesson(BID, 1).body)
    assert lp["steps"][2]["state"] == "now" and not lp["final"]       # the teacher has it next
    assert v1.type_lesson(BID, 3).status_code == 404                   # no type from a person


# ------------------------------------------------- a change that passes the replay is used at once (2026-10-08)

def test_the_teacher_goes_on_after_a_change_is_used_and_stops_when_a_model_is_out_of_reach():
    from worker import lesson
    asked = []
    ask = lambda l: asked.append(l) or {"a": "proposed", "b": "proposed", "r": "retry", "c": "no_change"}[l]
    assert [s for _, s in lesson.ask_in_turn(["a", "b", "r", "c"], ask)] == ["proposed", "proposed", "retry"]
    assert asked == ["a", "b", "r"]          # no person to wait for: b is taught on a's context; r: retried later


@needs_db
def test_a_change_that_passed_is_active_at_once_and_a_person_can_take_it_back():
    """In one transaction that is rolled back: the running stack's context is never changed."""
    from common import context, db
    from worker import lesson
    with db.connect() as c:
        try:
            was = c.execute("SELECT version, content FROM staging.context_version WHERE status='active'").fetchone()
            new, problems = context.apply_change(was["content"], {"kind": "edit_type", "type": "PO",
                                                                  "what": "A customer's order to SAMB (test)."})
            assert not problems
            v = lesson.adopt(c, new, was["version"], "teacher:test", "edit_type for PO (test)", {"passed": True})
            rows = {r["version"]: r for r in c.execute(
                "SELECT version, status, approved_by, gate FROM staging.context_version WHERE version IN (%s, %s)",
                (v, was["version"]))}
            assert rows[v]["status"] == "active" and rows[v]["approved_by"] == context.AUTO and rows[v]["gate"]["passed"]
            assert rows[was["version"]]["status"] == "retired"
            with pytest.raises(ValueError):  # built on a context that is no longer active: nothing is used
                lesson.adopt(c, new, was["version"], "teacher:test", "stale", {"passed": True})

            back = context.revert(c, was["version"], "tester")              # the undo: a new version, old content
            r = c.execute("SELECT status, content, created_by, note FROM staging.context_version WHERE version=%s",
                          (back,)).fetchone()
            assert r["status"] == "active" and r["content"] == was["content"] and r["created_by"] == "person:tester"
            with pytest.raises(ValueError, match="active one already"):
                context.revert(c, back, "tester")
        finally:
            c.rollback()


@needs_db
def test_a_lesson_whose_change_passes_is_used_without_a_person(pages, monkeypatch):
    import numpy as np
    from common import db
    from common.models import teacher
    from worker import lesson
    from api import actions
    actions.save_label(BID, 1, "PO", labelled_by="tester")
    with db.connect() as c:
        row = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=1", (BID,)).fetchone()
        active = c.execute("SELECT version FROM staging.context_version WHERE status='active'").fetchone()["version"]
    change = {"kind": "edit_type", "type": "PO", "what": "A customer's order to SAMB, also a Surat Pesanan (test)."}
    monkeypatch.setattr(teacher, "ask", lambda *a: ({"change": change, "why_missed": "no title"}, {}))
    monkeypatch.setattr(lesson.v1, "load", lambda key: np.zeros((8, 8), np.uint8))
    monkeypatch.setattr(lesson.v1, "png_bytes", lambda a: b"png")
    monkeypatch.setattr(lesson, "replay", lambda old, new, trial=None: {"passed": True, "jev_errors": 0})
    used, exam = [], []
    monkeypatch.setattr(lesson, "adopt", lambda c, new, parent, by, note, gate: used.append((parent, note)) or active)
    monkeypatch.setattr(lesson, "score_exam", exam.append)
    assert lesson.run_one(row) == "proposed"
    assert used == [(active, "edit_type for PO from page 1")] and exam == [active]   # used at once, then graded
    with db.connect() as c:
        r = c.execute("SELECT status, proposal FROM staging.lesson WHERE id=%s", (row["id"],)).fetchone()
    assert (r["status"], r["proposal"]) == ("proposed", active)

    def taken_back(*a):                      # a person took the context back while the lesson was taught
        raise ValueError("context #9 was built on #4, but #10 is active now")
    monkeypatch.setattr(lesson, "adopt", taken_back)
    with db.connect() as c:
        c.execute("UPDATE staging.lesson SET status='waiting', proposal=NULL WHERE id=%s", (row["id"],))
    assert lesson.run_one(row) == "retry"    # taught again on the context in use now
