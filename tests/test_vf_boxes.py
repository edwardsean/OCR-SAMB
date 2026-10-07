"""The page viewer's clickable boxes (read-then-map, Stage 2a): common/boxes.py pairs the AI OCR's copy with
Tesseract's words; worker/boxes.py looks closer at the image and stores them per page. Each case below is one found on
b-c80bbbde4d by looking at the boxes drawn on the pages (p1, p3, p5, p12): the rule is that no box is better than a
box giving what isn't printed there."""
import os

import cv2
import httpx
import numpy as np
import pytest

from common import boxes, db
from worker import boxes as made

UI = os.environ.get("API_URL", "http://localhost:8000")
SIZE = (2500, 10000)                                   # a page 2500 pixels wide, 10000 tall: 0-1000 = pixels / 10


def by_text(units):
    return {u["text"]: u for u in units}


def test_a_word_is_placed_where_tesseract_read_it_and_takes_the_copy_s_characters():
    blocks = [{"id": "b4", "kind": "printed", "text": "Ref. PO No. : 4505832724", "box": [90, 600, 110, 820]},
              {"id": "r1", "kind": "table_row", "text": "", "cells": ["AYAM 2 TELOR", "2.00", "", "0.00"],
               "box": [330, 0, 350, 900]}]
    words = [["Ref.", 90, 1500, 950, 70, 40], ["P0", 90, 1580, 950, 40, 40], ["No.", 90, 1630, 950, 50, 40],
             ["4505832724", 90, 1800, 950, 260, 40],
             ["AYAM", 90, 20, 3350, 120, 40], ["2", 90, 150, 3350, 20, 40], ["TELOR", 90, 180, 3350, 140, 40],
             ["2.00", 90, 1500, 3350, 90, 40]]                  # row 1's "0.00" isn't read: it gets no box
    u = boxes.match(blocks, [words], SIZE)
    t = by_text(u)
    assert t["4505832724"]["box"] == [95, 720, 99, 824] and t["4505832724"]["match"] == "equal"
    assert t["PO"]["tess"] == "P0" and t["PO"]["match"] == "misread"
    assert [x["text"] for x in sorted(u, key=lambda x: x["box"][1]) if x["block"] == "r1"] == ["AYAM", "2", "TELOR", "2.00"]
    assert "0.00" not in t
    assert boxes.match(blocks, [words], None) == []


def test_a_word_takes_a_token_only_when_their_characters_agree():
    """p1: the old rule paired leftovers one for one and put 584.144,00 on the printed word "Dasar"; p3: "NO" on
    "ITEM". Now Dasar pairs with Tesseract's "Casar", and 584.144,00 with the two words Tesseract split it into."""
    blocks = [{"id": "b29", "kind": "printed", "text": "Dasar Pengenaan Pajak 584.144,00", "box": [95, 100, 105, 960]},
              {"id": "b6", "kind": "printed", "text": "RECEIPT NO", "box": [195, 100, 205, 300]}]
    words = [["Casar", 80, 1000, 950, 120, 40], ["Pengenaan", 90, 1150, 950, 230, 40], ["Pajak", 90, 1400, 950, 120, 40],
             ["584", 90, 2100, 950, 70, 40], ["144,00", 90, 2180, 950, 150, 40],
             ["SET", 60, 1000, 1950, 70, 40], ["x8", 50, 1100, 1950, 40, 40]]
    t = by_text(boxes.match(blocks, [words], SIZE))
    assert t["Dasar"]["tess"] == "Casar" and t["Dasar"]["box"][1] == 400
    assert t["584.144,00"]["tess"] == "584 144,00" and t["584.144,00"]["box"][1] == 840 and t["584.144,00"]["box"][3] == 932
    assert "RECEIPT" not in t and "NO" not in t                  # nothing there reads like them


def test_words_tesseract_ran_together_are_shared_out():
    """p1 "OTYiCRT/" is QTY and CRT; "15:16:50" is three tokens. A token must be found in the word on its own: the
    header "(Value) Jumlah (Rp)" once put "Value" on the word "Jumlah" because "ValueJumlah" looks like "Jumlah"."""
    blocks = [{"id": "h", "kind": "table_header", "text": "QTY CRT (Value) Jumlah", "box": [95, 100, 105, 900]},
              {"id": "f", "kind": "printed", "text": "Printed 15:16:50", "box": [195, 100, 205, 500]}]
    words = [["OTYiCRT/", 90, 1000, 950, 160, 40], ["Jumlah", 90, 2000, 950, 140, 40],
             ["Printed", 90, 1000, 1950, 150, 40], ["15:16:50", 90, 1200, 1950, 160, 40]]
    t = by_text(boxes.match(blocks, [words], SIZE))
    assert t["QTY"]["match"] == t["CRT"]["match"] == "split" and t["QTY"]["box"][3] <= t["CRT"]["box"][1] + 1
    assert "Value" not in t and t["Jumlah"]["match"] == "equal"
    xs = [t[k]["box"][1] for k in ("15", "16", "50")]
    assert xs == sorted(xs) and 480 <= xs[0] < xs[2] < 536
    line = boxes.line_text(blocks[1])                            # a pick of all three is cut from the line as written
    assert line[t["15"]["s"]:t["50"]["e"]] == "15:16:50"


def test_a_table_row_s_line_starts_with_its_number():
    b = {"id": "r", "kind": "table_row", "text": "1", "cells": ["AYAM", "20", "CARTON 20"]}
    assert boxes.line_text(b) == "1 AYAM 20 CARTON 20"
    assert [t for t, _, _ in boxes.tokens(b)] == ["1", "AYAM", "20", "CARTON", "20"]
    assert boxes.line_text({"text": "1000142", "cells": ["1000142", "MIE"]}) == "1000142 MIE"   # not twice


def test_a_one_or_two_character_token_needs_its_line():
    """p3: row 5 is empty but for its number "5"; a "5" Tesseract saw in the black band's noise, above row 5's box
    but inside its window, took it. Alone in its line, a short token must sit inside that line's own box."""
    row5 = {"id": "r5", "kind": "table_row", "text": "5", "cells": ["", ""], "box": [485, 16, 517, 923]}
    noise = [["5", 90, 2175, 4620, 20, 40], ["AYAM", 90, 100, 1000, 120, 40]]
    assert boxes.match([row5], [noise], SIZE) == []
    printed = [["5", 90, 100, 4960, 20, 40], ["AYAM", 90, 100, 1000, 120, 40]]
    assert [u["text"] for u in boxes.match([row5], [printed], SIZE)] == ["5"]


def test_a_word_far_wider_than_its_characters_is_left_out():
    """p5: Tesseract read the three quantity columns 30.00 30.00 0.00 as one word "30000"; a box on it would take a
    column that isn't the value's."""
    b = {"id": "r", "kind": "table_row", "text": "", "cells": ["CHUPA", "PCS", "30.00"], "box": [95, 0, 105, 1000]}
    words = [["CHUPA", 90, 100, 950, 120, 40], ["PCS", 90, 800, 950, 70, 40], ["30000", 60, 1100, 950, 750, 40],
             ["BIGBABOL", 90, 100, 1500, 190, 40], ["STRAWBERRY", 90, 320, 1500, 240, 40]]
    t = by_text(boxes.match([b], [words], SIZE))
    assert "CHUPA" in t and "PCS" in t and "30.00" not in t


def test_each_printed_line_keeps_its_own_copied_line():
    """p3: a table row's window reaches into the rows beside it (the AI's boxes can be half a row off). Row 1's
    02701899 has no word of its own; row 2's printed 02701936 is five digits alike but stays row 2's."""
    r1 = {"id": "r1", "kind": "table_row", "text": "1", "cells": ["AYAM", "02701899"], "box": [100, 0, 130, 900]}
    r2 = {"id": "r2", "kind": "table_row", "text": "2", "cells": ["PILIHAN", "BUNDA", "02701936"], "box": [130, 0, 160, 900]}
    words = [["1", 90, 50, 1080, 20, 40], ["AYAM", 90, 200, 1080, 120, 40],
             ["2", 90, 50, 1390, 20, 40], ["PILIHAN", 90, 200, 1390, 170, 40], ["BUNDA", 90, 400, 1390, 120, 40],
             ["02701936", 90, 1000, 1390, 190, 40]]
    u = boxes.match([r1, r2], [words], SIZE)
    assert {(x["block"], x["text"]) for x in u if x["tess"] == "02701936"} == {("r2", "02701936")}


def test_a_gap_s_closer_reading_pairs_only_with_that_gap():
    """p3 row 1: the zoomed reading of the stretch after the barcode read "200" (it is 2.00); paired with the whole
    line it went to the wrapped description's 200GR, which scored higher."""
    b = {"id": "r", "kind": "table_row", "text": "1", "cells": ["KERITING 200GR", "8993047311111", "2.00"],
         "box": [100, 0, 130, 900]}
    run = [4]                                                    # 2.00: the token after the barcode
    u = boxes.place(b, run, [["200", 86, 1760, 1080, 40, 40]], SIZE, [])
    assert [(x["text"], x["i"]) for x in u] == [("2.00", 4)]


def test_stamps_and_marks_get_no_boxes():
    """The copy describes them ("illegible circular stamp", "circle around 320"); p3's stamp got "BERKAS" from a
    "bass" Tesseract saw in it."""
    b = {"id": "s", "kind": "stamp", "text": "PT. ABADI MAKMUR BERKAS illegible circular stamp", "box": [95, 0, 105, 900]}
    assert boxes.match([b], [[["BERKAS", 90, 100, 950, 150, 40]]], SIZE) == []


# ------------------------------------------------------------------------------------------ the image (worker)

def page(draw):
    """A white page (0 = ink) with the text and lines `draw` puts on it, and its ink as the worker sees it."""
    img = np.full((400, 1200), 255, np.uint8)
    draw(img)
    return img, made.ink_of(img)[1]


def test_a_box_shrinks_to_its_word_not_its_underline_or_the_border_it_touches():
    def word(img):
        cv2.putText(img, "10", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    alone, _ = page(word)
    xs = np.nonzero((alone < 128).any(axis=0))[0]                # where "10" alone is inked

    def draw(img):
        word(img)
        for x in range(90, 400, 8):                                                      # a dotted underline
            img[212:214, x:x + 3] = 0
        for y in range(40, 380, 9):                                                      # a dashed column border
            img[y:y + 5, 300:302] = 0
    img, ink = page(draw)
    H, W = img.shape
    wide = [int(160 / H * 1000), int(90 / W * 1000), int(216 / H * 1000), int(305 / W * 1000)]
    x0, x1 = (v * W / 1000 for v in made.tighten(wide, ink, W, H)[1::2])
    assert abs(x0 - xs[0]) <= 3 and abs(x1 - xs[-1] - 1) <= 3   # "10" alone, not to the border or the dots' end


def test_a_word_whose_ink_is_columns_apart_is_left_out():
    def draw(img):
        for x in (100, 500, 900):
            cv2.putText(img, "30.00", (x, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    img, ink = page(draw)
    one = made.on_ink([["30000", 60, 90, 170, 1000, 40]], ink)
    assert one == []
    spaced = made.on_ink([["30.00", 60, 400, 170, 220, 40]], ink)     # blank before a right-aligned number
    assert len(spaced) == 1 and spaced[0][2] >= 495 and spaced[0][4] < 120


def test_a_dashed_border_is_found_but_no_letter():
    def draw(img):
        for y in range(10, 390, 9):
            img[y:y + 4, 600:602] = 0
        cv2.putText(img, "1 l I", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 0, 2)
    img, _ = page(draw)
    text = 255 - made.ink_of(img)[0]                             # the ink Tesseract reads (ruling lines removed)
    d = made.dashed(text)
    assert d[:, 598:604].sum() > 0 and d[:, :560].sum() == 0


def test_the_viewer_uses_the_boxes_the_worker_stored():
    with db.connect() as c:
        try:
            p = c.execute("SELECT pick, transcript_version FROM staging.page WHERE batch_id='b-c80bbbde4d' AND page_no=3"
                          ).fetchone()
        except Exception:
            pytest.skip("no staging.page.pick here (schema/021 not applied)")
    if not p or not p["pick"] or p["pick"].get("v") != made.version(p["transcript_version"]):
        pytest.skip("page 3's boxes not made here: python -m worker.boxes b-c80bbbde4d")
    fix = httpx.get(f"{UI}/api/v1/scans/b-c80bbbde4d/pages/3", timeout=60).json()["fix"]
    assert len(fix["units"]) == len(p["pick"]["units"])
    assert any(u["match"] in ("ink", "split") for u in fix["units"])
    assert fix["lines"]                                                   # the copy's lines, to cut a pick from
