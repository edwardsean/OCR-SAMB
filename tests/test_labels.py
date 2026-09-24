"""Labelling: saving works, the pile is drawn once and never changes on relabel, the queue moves past
labelled pages, unknown types are refused. Uses a throwaway batch; never touches real labels."""
import os
import httpx
import pytest

from common import db

UI = os.environ.get("UI_URL", "http://ui:8000")   # vlm-first sets its own UI
BID = "test-labels"


@pytest.fixture(autouse=True)
def fake_batch():
    def clean():
        with db.connect() as c:
            c.execute("DELETE FROM staging.type_label WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
            c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
    clean()
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status)
                     VALUES (%s,'t','t',repeat('1',64),current_date,3,'read')""", (BID,))
        for n in (1, 2, 3):
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, type_status)
                         VALUES (%s,%s,'x','read',%s)""", (BID, n, "decided" if n == 1 else "unsure"))
    yield
    clean()


def save(page, label, **kw):
    return httpx.post(f"{UI}/label", data={"batch": BID, "page": page, "label": label, **kw}, follow_redirects=False)


def pile(page):
    with db.connect() as c:
        return c.execute("SELECT pile, label FROM staging.type_label WHERE batch_id=%s AND page_no=%s", (BID, page)).fetchone()


def test_save_relabel_keeps_pile_and_queue_moves_on():
    assert "Page 2" in httpx.get(f"{UI}/label?batch={BID}").text           # first unsure page
    r = save(2, "PO", customer="Indomaret", note="no title")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/label?batch={BID}")
    first = pile(2)
    assert first["pile"] in ("practice", "exam") and first["label"] == "PO"
    for _ in range(5):                                                       # relabel: label changes, pile never does
        save(2, "CONTINUATION")
        assert pile(2)["pile"] == first["pile"]
    assert pile(2)["label"] == "CONTINUATION"
    assert "Page 3" in httpx.get(f"{UI}/label?batch={BID}").text            # queue skips the labelled page


def test_unknown_type_refused():
    assert save(2, "BANANA").status_code == 400
    assert pile(2) is None

