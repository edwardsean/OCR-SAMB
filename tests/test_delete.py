"""Deleting a batch so the same PDF can be uploaded again (the user, 2026-10-16: "add a delete batch button so that i
can retry to upload the same document again; think when can we delete, when can we not"). common/deleting.py holds
the rules. Storage is never touched here (removals are recorded) and grouping of the other batches is recorded."""
import os

import pytest

from common import db, deleting

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
BID, BID2, OTHER = "test-del", "test-del-2", "test-del-other"
CODE, OTHER_CODE = "TEST-DEL", "TEST-DEL-OTHER"
SOR_A, SOR_B = "SORTESTDEL0A", "SORTESTDEL0B"


def _clean():
    with db.connect() as c:
        files = [BID, BID2, OTHER]
        c.execute("""DELETE FROM staging.bundle_document bd USING staging.document d
                      WHERE d.id = bd.document_id AND d.batch_id = ANY(%s)""", (files,))
        c.execute("DELETE FROM staging.bundle WHERE sor_no IN (%s, %s)", (SOR_A, SOR_B))
        c.execute("DELETE FROM staging.document WHERE batch_id = ANY(%s)", (files,))
        for t in ("type_label", "field_confirmation", "page"):
            c.execute(f"DELETE FROM staging.{t} WHERE batch_id = ANY(%s)", (files,))
        c.execute("DELETE FROM staging.scan_batch WHERE id = ANY(%s)", (files,))
        c.execute("DELETE FROM staging.efaktur WHERE file_name LIKE 'test-del%%'")
        c.execute("DELETE FROM staging.upload WHERE code IN (%s, %s)", (CODE, OTHER_CODE))


@pytest.fixture
def batch(monkeypatch):
    """Upload TEST-DEL: file BID (2 pages read) and BID2 (1 page read). Order A is only BID's FP; order B is BID's TTG
    plus the FP of another upload's file. A person labelled BID p2 and confirmed a value on it."""
    from grouper import group
    _clean()
    with db.connect() as c:
        ups = {}
        for code in (CODE, OTHER_CODE):
            ups[code] = c.execute("""INSERT INTO staging.upload (code, uploaded_by, doc_date) VALUES (%s, 'test', current_date)
                                     RETURNING id""", (code,)).fetchone()["id"]
        for bid, up, n in ((BID, CODE, 2), (BID2, CODE, 1), (OTHER, OTHER_CODE, 1)):
            c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total,
                                                         status, upload_id, run)
                         VALUES (%s, %s, %s, md5(%s) || md5(%s), current_date, %s, 'read', %s, 1)""",
                      (bid, bid + ".pdf", f"test/scans/{bid}.pdf", bid, bid, n, ups[up]))
            for p in range(1, n + 1):
                c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, thumb_path, status)
                             VALUES (%s, %s, %s, %s, 'read')""", (bid, p, f"test/pages/{bid}/{p}.png", f"test/t/{bid}/{p}.png"))
        doc = lambda bid, n, t: c.execute("""INSERT INTO staging.document (batch_id, doc_type, page_from, page_to)
                                             VALUES (%s, %s, %s, %s) RETURNING id""", (bid, t, n, n)).fetchone()["id"]
        bun = lambda sor, status: c.execute("INSERT INTO staging.bundle (sor_no, status) VALUES (%s, %s) RETURNING id",
                                            (sor, status)).fetchone()["id"]
        a, b = bun(SOR_A, "needs_review"), bun(SOR_B, "reviewed")
        for bundle, d in ((a, doc(BID, 1, "FP")), (b, doc(BID, 2, "TTG")), (b, doc(OTHER, 1, "FP"))):
            c.execute("INSERT INTO staging.bundle_document (bundle_id, document_id) VALUES (%s, %s)", (bundle, d))
        c.execute("INSERT INTO staging.type_label (batch_id, page_no, label, pile) VALUES (%s, 2, 'TTG', 'practice')", (BID,))
        c.execute("""INSERT INTO staging.field_confirmation (batch_id, page_no, field, value, confirmed_by)
                     VALUES (%s, 2, 'purchase_order_no', '123', 'test')""", (BID,))
    out = {"upload": ups[CODE], "removed": [], "regrouped": []}
    monkeypatch.setattr(deleting, "_remove", lambda keys, files: out["removed"].extend(keys) or len(keys))
    monkeypatch.setattr(group, "run", lambda bid, folders=True: out["regrouped"].append(bid))
    yield out
    _clean()


def test_a_finished_batch_can_be_deleted_and_says_what_goes(batch):
    with db.connect() as c:
        v = deleting.view(c, batch["upload"])
    assert v["can"] and v["code"] == CODE
    assert v["impact"] == {"files": 2, "pages": 3, "orders": 1, "shared": 1, "approved": 1, "labels": 1,
                           "confirmations": 1, "efaktur": 0}


def test_deleting_takes_the_batch_and_its_own_order_and_regroups_a_shared_one(batch):
    out = deleting.delete(batch["upload"], "tester", CODE.lower())       # the code, whatever its case
    assert out["files"] == 2 and out["pages"] == 3 and out["regrouped"] == [OTHER]
    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM staging.upload WHERE code=%s", (CODE,)).fetchone()
        assert not c.execute("SELECT 1 FROM staging.page WHERE batch_id IN (%s, %s)", (BID, BID2)).fetchone()
        assert not c.execute("SELECT 1 FROM staging.type_label WHERE batch_id=%s", (BID,)).fetchone()
        assert not c.execute("SELECT 1 FROM staging.bundle WHERE sor_no=%s", (SOR_A,)).fetchone()
        left = c.execute("""SELECT d.batch_id FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                              JOIN staging.document d ON d.id = bd.document_id WHERE b.sor_no=%s""", (SOR_B,)).fetchall()
        assert [r["batch_id"] for r in left] == [OTHER]                # the other upload's FP stays in its order
        # the same file can be uploaded again: its SHA-256 is free
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s, 'again.pdf', 'x', md5(%s) || md5(%s), current_date, 1, 'received')""", (BID, BID, BID))
    assert f"test/scans/{BID}.pdf" in batch["removed"] and f"test/pages/{BID}/1.png" in batch["removed"]


def test_the_code_must_be_typed(batch):
    with pytest.raises(deleting.Refused) as e:
        deleting.delete(batch["upload"], "tester", "BATCH-WRONG")
    assert e.value.status == 400 and CODE in e.value.why


def test_never_a_batch_an_order_sent_to_satellite_was_made_from(batch):
    with db.connect() as c:
        c.execute("UPDATE staging.bundle SET status='published' WHERE sor_no=%s", (SOR_A,))
        assert SOR_A in deleting.why_not(c, batch["upload"])
    with pytest.raises(deleting.Refused):
        deleting.delete(batch["upload"], "tester", CODE)


def test_never_while_it_runs(batch):
    with db.connect() as c:
        c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND page_no=2", (BID,))
        c.execute("UPDATE staging.scan_batch SET status='queued' WHERE id=%s", (BID,))
        assert "Hentikan dulu" in deleting.why_not(c, batch["upload"])
        c.execute("UPDATE staging.scan_batch SET status='stopped' WHERE id=%s", (BID,))   # stopped: it may go
        assert deleting.why_not(c, batch["upload"]) is None


def test_never_when_its_efaktur_is_inside_a_sent_pdf(batch):
    with db.connect() as c:
        c.execute("""INSERT INTO staging.efaktur (sha256, upload_id, file_name, file_path, pages, billing_no, sor_no, status)
                     VALUES (md5('test-del-ef'), %s, 'test-del-ef.pdf', 'x', 1, '7000000000', 'SORTESTDEL0A', 'attached')""", (batch["upload"],))
        assert "e-Faktur" in deleting.why_not(c, batch["upload"])


def test_an_unknown_batch():
    with pytest.raises(deleting.Refused) as e:
        deleting.delete(987654321, "tester", "X")
    assert e.value.status == 404
