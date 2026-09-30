"""Read-then-map, Stage 2a: a person's correction kept as an EXAMPLE (common/knowledge.py, schema/020). A correction
marked on the paper fixes the page at once (field_confirmation) and keeps where the value is printed and what is
printed beside it; a typed one (Review) is placed by finding the value in the page's copy. Pure parts first, then one
round trip through the real routes on a page of the worktree's copy, undone afterwards."""
import os

import httpx
import pytest

from common import db, knowledge

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("UI_URL", "http://ui:8000")

BLOCKS = [
    {"id": "b4", "kind": "printed", "text": "Ref. PO No. : 4505832724", "box": [90, 600, 110, 820]},
    {"id": "b9", "kind": "printed", "text": "Document No", "box": [860, 30, 880, 70]},
    {"id": "b10", "kind": "printed", "text": "5043773365", "box": [860, 80, 880, 300]},
    {"id": "h", "kind": "table_header", "text": "", "cells": ["ITEM", "ORDER QTY", "RECEIVED QTY"], "box": [300, 0, 320, 900]},
    {"id": "r1", "kind": "table_row", "text": "", "cells": ["AYAM 2 TELOR", "2.00", "0.00"], "box": [330, 0, 350, 900]},
    {"id": "hw", "kind": "handwriting", "text": "2", "box": [330, 920, 350, 960]},
]


def test_what_is_printed_beside_the_value():
    assert knowledge.anchor_of([92, 720, 108, 820], "4505832724", BLOCKS)["left"] == "Ref. PO No."   # in its own line
    assert knowledge.anchor_of([862, 90, 878, 290], "5043773365", BLOCKS)["left"] == "Document No"   # the block left
    a = knowledge.anchor_of([332, 620, 348, 880], "0.00", BLOCKS)                                     # a table cell
    assert a["above"] == "ITEM ORDER QTY RECEIVED QTY" and a["header_cell"] == "RECEIVED QTY" and a["under_kind"] == "table_row"
    assert knowledge.anchor_of([330, 915, 352, 965], "2", BLOCKS)["under_kind"] == "handwriting"


def test_a_marked_region_suggests_the_copys_part_and_shows_tesseract():
    words = [["4505832724", 90, 1800, 950, 260, 40], ["Ref.", 90, 1500, 950, 80, 40]]
    got = knowledge.region_contents([92, 720, 108, 820], BLOCKS, words, (2500, 10000))
    assert got["words"] == "4505832724" and got["blocks"][0]["id"] == "b4"
    assert got["suggest"] == "4505832724"                    # the part of the copy's line inside the box, not the label
    assert knowledge.region_contents([0, 0, 5, 5], BLOCKS, words, (2500, 10000))["suggest"] == ""


@pytest.fixture
def page12():
    """Page 12 of b-c80bbbde4d in the worktree's copy; its confirmations and examples are removed afterwards and the
    page is checked again, so nothing a person gave is touched (it has none)."""
    bid, n = "b-c80bbbde4d", 12
    with db.connect() as c:
        had = c.execute("SELECT count(*) AS k FROM staging.field_confirmation WHERE batch_id=%s AND page_no=%s",
                        (bid, n)).fetchone()["k"]
        ok = c.execute("SELECT 1 FROM staging.page WHERE batch_id=%s AND page_no=%s AND transcript IS NOT NULL",
                       (bid, n)).fetchone()
    if had or not ok:
        pytest.skip("page 12 has a person's answers or no copy here: not a test page")
    yield bid, n
    with db.connect() as c:
        c.execute("DELETE FROM staging.field_confirmation WHERE batch_id=%s AND page_no=%s", (bid, n))  # examples cascade
    from worker import vf
    vf.recheck(bid, n)


def test_a_correction_marked_on_the_paper_fixes_the_page_and_keeps_an_example(page12):
    bid, n = page12
    r = httpx.post(f"{UI}/page/fix", data={"batch": bid, "page": n, "field": "posting_date", "value": "2026-09-14",
                                           "by": "test", "region": "300,500,330,700", "shown": "",
                                           "back": "/review/SORX?batch=" + bid}, follow_redirects=False, timeout=120)
    assert r.status_code == 303 and r.headers["location"].startswith("/review/SORX?batch=" + bid)
    with db.connect() as c:
        conf = c.execute("SELECT value, confirmed_by FROM staging.field_confirmation WHERE batch_id=%s AND page_no=%s "
                         "AND field='posting_date'", (bid, n)).fetchone()
        ex = c.execute("SELECT * FROM staging.extract_example WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchall()
    assert conf["value"] == "2026-09-14" and len(ex) == 1
    e = ex[0]
    assert e["source"] == "marked" and e["region"] == [300, 500, 330, 700] and e["doc_type"] == "TTG"
    assert e["field"] == "posting_date" and e["kind"] == "value" and e["pile"] in ("practice", "exam")
    r = httpx.post(f"{UI}/page/fix", data={"batch": bid, "page": n, "field": "posting_date", "value": "(not printed)",
                                           "by": "test", "region": ""}, follow_redirects=False, timeout=120)
    with db.connect() as c:
        rows = c.execute("SELECT kind, status FROM staging.extract_example WHERE batch_id=%s AND page_no=%s ORDER BY id",
                         (bid, n)).fetchall()
    assert [(x["kind"], x["status"]) for x in rows] == [("value", "superseded"), ("not_printed", "active")]


def test_the_viewer_shows_the_fields_and_the_region_api_answers():
    r = httpx.get(f"{UI}/batches/b-c80bbbde4d/pages/12?fix=purchase_order_no", timeout=60)
    assert r.status_code == 200 and 'id="fixer"' in r.text and 'data-f="purchase_order_no"' in r.text
    j = httpx.get(f"{UI}/api/region/b-c80bbbde4d/12?box=225,587,255,781", timeout=60).json()
    assert "13,987,409.70" in j["suggest"]
    assert httpx.get(f"{UI}/api/region/b-c80bbbde4d/12?box=9,9,1,1", timeout=60).status_code == 400


def test_what_a_person_can_click_on_the_page():
    """Boxes sit on Tesseract's words (exact places); their text is the copy's, aligned word by word within the line,
    so a word Tesseract misread still gets the copy's characters. A word only the copy has gets no box."""
    blocks = [{"id": "b4", "kind": "printed", "text": "Ref. PO No. : 4505832724", "box": [90, 600, 110, 820]},
              {"id": "r1", "kind": "table_row", "text": "", "cells": ["AYAM 2 TELOR", "2.00", "", "0.00"],
               "box": [330, 0, 350, 900]}]
    words = [["Ref.", 90, 1500, 950, 70, 40], ["P0", 90, 1580, 950, 40, 40], ["No.", 90, 1630, 950, 50, 40],
             ["4505832724", 90, 1800, 950, 260, 40],                     # the PO line, on a 2500 x 10000 page
             ["AYAM", 90, 20, 3350, 120, 40], ["2", 90, 150, 3350, 20, 40], ["TELOR", 90, 180, 3350, 140, 40],
             ["2.00", 90, 1500, 3350, 90, 40]]                           # row 1: "0.00" under the black band
    u = knowledge.pick_units(blocks, words, (2500, 10000))
    by = {x["tess"]: x for x in u}
    assert by["4505832724"]["text"] == "4505832724" and by["4505832724"]["box"] == [95, 720, 99, 824]
    assert by["P0"]["text"] == "PO" and by["P0"]["match"] == "misread"         # Tesseract's P0, the copy's PO
    assert [x["text"] for x in u if x["block"] == "r1"] == ["AYAM", "2", "TELOR", "2.00"]
    assert "0.00" not in {x["text"] for x in u}                                  # nowhere to put it: no box
    assert knowledge.pick_units(blocks, words, None) == []
