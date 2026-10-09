"""Read, then map (the mentor, 2026-09-29): the AI OCR copies the whole page, a text model maps the copy onto the
field list and collects notes (common/transcript.py). Pure tests: the text model may only point at the transcript,
handwriting never leaks into a table row, a cut number stays cut, notes stay out of the reading, a prompt change
bumps its version."""
import io
import os
import random

import pytest
from PIL import Image

from common import transcript, verify
from common.models import openai_vlm, vlm

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")

BLOCKS = transcript.normalise_blocks([
    {"id": "b1", "kind": "printed", "text": "Ref. PO No. : 4505832724", "box": [90, 600, 110, 820]},
    {"id": "b2", "kind": "printed", "text": "Document No 5043773365", "box": [860, 80, 880, 300]},
    {"id": "b3", "kind": "table_header", "text": "", "cells": ["No", "Description", "Qty", "UoM", "Total"],
     "box": [300, 50, 320, 950]},
    {"id": "b4", "kind": "table_row", "text": "", "cells": ["0001", "VASELINE B/WASH 425ML", "4", "", "253,444"],
     "box": [330, 50, 350, 950]},
    {"id": "b5", "kind": "handwriting", "text": "2", "about": "b4", "box": [330, 960, 350, 990]},
    {"id": "b6", "kind": "printed", "text": "TOTAL 1.078.330,", "box": [900, 700, 920, 995]},
    {"id": "b7", "kind": "mark", "text": "[cross over the row]", "about": "b4", "box": [328, 40, 352, 955]},
])
NAMES = ["purchase_order_no", "document_no", "total"]
COLS = ["description", "qty", "uom", "unit_price"]


def test_a_value_counts_only_when_it_is_in_the_block_it_names():
    raw = {"fields": {
        "purchase_order_no": {"block": "b1", "text": "4505832724", "value": "4505832724"},
        "document_no": {"block": "b1", "text": "5043773365", "value": "5043773365"},      # wrong block
        "total": {"block": "b9", "text": "1.078.330,", "value": "1078330"}}}             # no such block
    fa, mapping, _ = transcript.to_fields_all(raw, BLOCKS, NAMES, COLS)
    # the box is the value's share of its line, not the whole line with its label
    assert fa["purchase_order_no"] == {"value": "4505832724", "source_text": "4505832724", "box": [90, 721, 110, 820]}
    # named in the wrong block, or a block that doesn't exist: kept only because the value IS on the page, taken
    # from the block that prints it (its box and source are that block's), and marked as moved
    assert fa["document_no"]["source_text"] == "5043773365" and fa["document_no"]["box"] == [860, 193, 880, 300]
    assert fa["total"]["source_text"] == "1.078.330,"
    assert set(mapping["moved"]) == {"document_no", "total"} and not mapping["dropped"]
    raw["fields"]["document_no"] = {"block": "b1", "text": "5043773366", "value": "5043773366"}   # not on the page
    fa, mapping, _ = transcript.to_fields_all(raw, BLOCKS, NAMES, COLS)
    assert fa["document_no"] is None and mapping["dropped"][0]["field"] == "document_no"


def test_a_cut_number_stays_cut_so_it_never_gets_a_tick():
    raw = {"fields": {"total": {"block": "b6", "text": "1.078.330,", "value": "1078330"}}}
    fa, _, _ = transcript.to_fields_all(raw, BLOCKS, NAMES, COLS)
    assert fa["total"]["source_text"] == "1.078.330," and verify.CUT_OFF.search(fa["total"]["source_text"])


def test_handwriting_beside_a_row_never_enters_the_row():
    """Indomaret page 12: the received quantity is handwritten beside the row. row_text is the printed cells only,
    so matching and Tesseract's row check see what they see today; the handwriting is a note."""
    raw = {"lines": [{"row": "b4", "description": "VASELINE B/WASH 425ML", "qty": "2", "uom": None}],
           "notes": [{"kind": "handwriting", "text": "2", "blocks": ["b5"], "about": "row b4 qty"},
                     {"kind": "mark", "text": "cross over the row", "blocks": ["b7"]},
                     {"kind": "handwriting", "text": "?", "blocks": ["b99"]},
                     {"kind": "signature", "text": "Budi", "blocks": ["b5"]}]}
    fa, mapping, notes = transcript.to_fields_all(raw, BLOCKS, NAMES, COLS)
    row = fa["lines"][0]
    assert row["row_text"] == "0001 VASELINE B/WASH 425ML 4 253,444"
    assert row["qty"] is None                                     # the "2" isn't printed in the row: not its cell
    assert {"row": "b4", "column": "qty", "text": "2", "why": "not in its row"} in mapping["dropped"]
    assert [n["kind"] for n in notes] == ["handwriting", "mark", "signature"]   # b99 points at nothing: dropped
    assert notes[0]["box"] == [330, 960, 350, 990] and notes[2]["text"] is None


def test_empty_cells_keep_their_place():
    assert BLOCKS[3]["cells"] == ["0001", "VASELINE B/WASH 425ML", "4", "", "253,444"]
    assert "| 4 |  | 253,444" in transcript.render(BLOCKS)


def test_notes_never_enter_the_reading():
    """crosscheck._store_named reads every dict in fields_all as store words, and the page view counts them."""
    raw = {"fields": {"purchase_order_no": {"block": "b1", "text": "4505832724"}},
           "notes": [{"kind": "remark", "text": "retur 2 ctn", "blocks": ["b5"]}]}
    fa, _, notes = transcript.to_fields_all(raw, BLOCKS, NAMES, COLS)
    assert set(fa) == set(NAMES) | {"lines"} and notes and "notes" not in fa


def test_ids_are_unique_and_kinds_known():
    b = transcript.normalise_blocks([{"id": "b1", "kind": "printed", "text": "A"},
                                     {"id": "b1", "kind": "doodle", "text": "B"},
                                     {"kind": "printed", "text": "  "}])
    assert [x["id"] for x in b] == ["b1", "b2"] and b[1]["kind"] == "printed"


def test_a_prompt_change_bumps_its_version():
    """A stored transcript or mapping is reused while its version matches: editing a prompt without bumping its
    version would silently keep readings made with the old one."""
    assert transcript.prompt_sha(vlm.TRANSCRIBE) == transcript.PROMPT_SHA[("TRANSCRIBE", vlm.TRANSCRIBE_V)]
    assert transcript.prompt_sha(vlm.MAP) == transcript.PROMPT_SHA[("MAP", vlm.MAP_V)]
    assert transcript.prompt_sha(vlm.MAP_NAMED + vlm.MAP_NAMED_LINES) == \
        transcript.PROMPT_SHA[("MAP_NAMED", vlm.MAP_NAMED_V)]


def test_versions_separate_the_image_read_from_the_mapping():
    tv = transcript.transcript_version("dashscope:qwen3-vl-plus", 1)
    a = transcript.map_version("abc", tv, "dashscope:qwen-plus")
    b = transcript.map_version("abc", tv, "dashscope:qwen-flash")
    assert tv in a and tv in b and a != b                # a new text model re-maps, never re-reads the image


def test_what_people_taught_reaches_only_the_second_pass():
    heads, cols = "- purchase_order_no: the PO number", "qty (received)"
    assert "LEARNED FROM PEOPLE" not in transcript.map_prompt(BLOCKS, heads, cols)
    p = transcript.map_prompt(BLOCKS, heads, cols, '- purchase_order_no: right of "Ref. PO No."')
    assert "LEARNED FROM PEOPLE" in p and p.index("LEARNED") < p.index("TRANSCRIPT:")


def test_boxes_snap_to_the_printed_words():
    """A block is a whole line (labels and other values too); the value's box tightens to Tesseract's words for it."""
    fa = {"purchase_order_no": {"value": "4505832724", "source_text": "4505832724", "box": [90, 600, 110, 820]}}
    mapping = {"fields": {"purchase_order_no": {"block": "b1", "box_by": "block"}}}
    words = [["Ref.", 90, 1500, 950, 80, 40], ["PO", 90, 1590, 950, 50, 40], ["No.", 90, 1650, 950, 60, 40],
             ["4505832724", 90, 1800, 950, 260, 40], ["elsewhere", 90, 100, 3000, 200, 40]]
    transcript.snap_boxes(fa, mapping, words, (10000, 2500))
    assert fa["purchase_order_no"]["box"] == [95, 720, 99, 824] and mapping["fields"]["purchase_order_no"]["box_by"] == "tesseract"


def test_two_mappings_measure_the_text_models_noise():
    from worker import vf
    a = {"total": {"value": "1"}, "po": {"value": "X"}, "lines": [{"qty": "2", "row_text": "r"}]}
    b = {"total": {"value": "1"}, "po": {"value": "Y"}, "lines": [{"qty": "3", "row_text": "r"}, {"qty": "1"}]}
    assert vf.flips(a, b) == {"fields": ["po"], "cells": 2}


def test_a_shrunk_page_reports_the_size_it_was_sent_at():
    """Pixel boxes are scaled by the size the model saw; the original size put them in the wrong place."""
    random.seed(1)
    im = Image.frombytes("L", (2600, 2600), bytes(random.getrandbits(8) for _ in range(2600 * 2600)))
    b = io.BytesIO(); im.save(b, "PNG")
    assert len(b.getvalue()) > openai_vlm.MAX_IMAGE_BYTES
    _, w, h = openai_vlm._image(b.getvalue())
    assert (w, h) == (2000, 2000)


def test_a_value_is_a_whole_token_never_part_of_a_longer_one():
    total = {"id": "t", "kind": "printed", "text": "TOTAL 1.126.011,00"}
    assert transcript.grounded("1.126.011,00", total)
    assert not transcript.grounded("1.126.011", total)              # the same digits, cut short: not what's printed
    assert not transcript.grounded("126.011,00", total)
    row = {"id": "r", "kind": "table_row", "cells": ["20034699", "BIHUN JAGUNG", "6", "64,955.00"]}
    assert transcript.grounded("6", row) and transcript.grounded("64,955.00", row)
    assert not transcript.grounded("64,955", row) and not transcript.grounded("5", row)


def test_the_value_not_its_line():
    """qwen-plus copied the whole line ("NO.PO : AH9Q69089 DIV : T …") as the value's text: the print check must
    compare the value itself (Indomaret page 12, the first trial)."""
    blocks = transcript.normalise_blocks([
        {"id": "b3", "kind": "printed", "text": "NO.PO : AH9Q69089 DIV : T TOP : 30 (18844)"},
        {"id": "b9", "kind": "printed", "text": "TOTAL SELURUHNYA 13,987,409.70"},
        {"id": "b2", "kind": "printed", "text": "KRM KE: SPI CIKOKOL 1 W.P : PT. MEGAH DAYA INTI HARAPAN"}])
    raw = {"fields": {"purchase_order_no": {"block": "b3", "text": blocks[0]["text"], "value": "AH9Q69089"},
                      "total": {"block": "b9", "text": "TOTAL SELURUHNYA 13,987,409.70", "value": "13987409.70"},
                      "customer_name": {"block": "b2", "text": blocks[2]["text"], "value": "PT. MEGAH DAYA INTI HARAPAN"}}}
    fa, _, _ = transcript.to_fields_all(raw, blocks, ["purchase_order_no", "total", "customer_name"], [])
    assert fa["purchase_order_no"]["source_text"] == "AH9Q69089"
    assert fa["total"]["source_text"] == "13,987,409.70"
    assert fa["customer_name"]["source_text"] == "PT. MEGAH DAYA INTI HARAPAN"


def test_a_broken_long_transcript_keeps_its_whole_blocks():
    text = ('{"blocks": [{"id": "b1", "kind": "printed", "text": "RECEIVING NOTE", "box": {"x0": 1, "y0": 1, "x1": 9, "y1": 9}},'
            ' {"id": "b2", "kind": "table_row", "text": "", "cells": ["1", "BIHUNKU"]}, {"id": "b3", "kind": "prin')
    got = openai_vlm.salvage_blocks(text)
    assert [b["id"] for b in got] == ["b1", "b2"]


def test_a_label_copied_as_the_value_is_mended_or_refused():
    """The first 16-page trial: qwen-plus named the value's block but copied the label ("VAT AMOUNT" for block
    "80,924.00"), copied table cells with our " | " ("TOTAL | 775,397"), and once wrote "normalised" as a value."""
    blocks = transcript.normalise_blocks([
        {"id": "b55", "kind": "printed", "text": "VAT AMOUNT"},
        {"id": "b56", "kind": "printed", "text": "80,924.00"},
        {"id": "b54", "kind": "table_row", "text": "", "cells": ["TOTAL", "775,397"]},
        {"id": "b64", "kind": "printed", "text": "NET TOTAL WITH VAT"}])
    raw = {"fields": {"ppn": {"block": "b56", "text": "VAT AMOUNT", "value": "80924.00"},
                      "total": {"block": "b54", "text": "TOTAL | 775,397", "value": "775397"},
                      "dpp": {"block": "b64", "text": "TOTAL", "value": "normalised"},
                      "po_number": {"block": "b55", "text": "VAT AMOUNT", "value": "3390240.00"}}}
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, ["ppn", "total", "dpp", "po_number"], [])
    assert fa["ppn"]["source_text"] == "80,924.00" and fa["total"]["source_text"] == "775,397"
    assert fa["dpp"] is None and fa["po_number"] is None               # a value its source doesn't contain
    assert {d["field"] for d in mapping["dropped"]} == {"dpp", "po_number"}


def test_bare_values_and_spacing_are_found_on_the_page():
    """Page 6 answered bare values with no block; page 16's vendor code is printed "0000000398 / OS-055" and its
    name came back with the spaces stripped. Both are on the page: found by their characters. A value that is
    nowhere on the page stays dropped."""
    blocks = transcript.normalise_blocks([
        {"id": "b1", "kind": "printed", "text": "TOTAL"}, {"id": "b2", "kind": "printed", "text": "775,397"},
        {"id": "b3", "kind": "printed", "text": "Vendor : 0000000398 / OS-055"},
        {"id": "b4", "kind": "printed", "text": "PT. SARANA ABADI MAKMUR BERSAMA"}])
    raw = {"fields": {"total": "775397.00", "vendor_code": {"block": "b4", "text": "0000000398 / OS-055",
                                                              "value": "0000000398/OS-055"},
                      "vendor_name": {"block": "b4", "value": "PT.SARANAABADIMAKMURBERSAMA"},
                      "dpp": "123456.00"}}
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, ["total", "vendor_code", "vendor_name", "dpp"], [])
    assert fa["total"]["source_text"] == "775,397"
    assert fa["vendor_code"]["source_text"] == "0000000398 / OS-055"
    assert fa["vendor_name"]["source_text"] == "PT. SARANA ABADI MAKMUR BERSAMA"
    assert fa["dpp"] is None and set(mapping["moved"]) == {"total", "vendor_code"}


def test_a_row_that_wraps_onto_a_numbers_line_is_one_row():
    """Page 12: the item row is printed over two lines, copied as two blocks. The text model named the first; its
    numbers are in the second. The numbers line directly below counts as the same row; a line of words, or a row the
    text model named itself, never does."""
    blocks = transcript.normalise_blocks([
        {"id": "b6", "kind": "printed", "text": "20034699 BIHUN JAGUNG BIHUN CAP TANAM JAGUNG PCK 320g BAL/10",
         "box": [300, 50, 315, 900]},
        {"id": "b7", "kind": "printed", "text": "1301370 0 64,955.00 389,730.00 1,386,139.70 13,987,409.70",
         "box": [316, 50, 331, 900]},
        {"id": "b8", "kind": "printed", "text": "DISC: 01:3.0008% |00 TOTAL SELURUHNYA 13,987,409.70",
         "box": [332, 50, 347, 900]}])
    raw = {"lines": [{"row": "b6", "description": "BIHUN JAGUNG BIHUN CAP TANAM JAGUNG PCK 320g BAL/10", "qty": "0",
                      "unit_price": "64,955.00", "samb_material_code": "1301370", "discount": "3.0008%"}]}
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, [], ["description", "qty", "unit_price",
                                                                "samb_material_code", "discount"])
    row = fa["lines"][0]
    assert row["qty"] == "0" and row["unit_price"] == "64,955.00" and row["samb_material_code"] == "1301370"
    assert row["discount"] is None                     # its line has words (TOTAL SELURUHNYA): not the row's
    assert mapping["rows"][0]["blocks"] == ["b6", "b7"] and row["row_text"].endswith("13,987,409.70")
    raw["lines"].append({"row": "b7", "description": None})       # the text model called b7 a row of its own
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, [], ["qty"])
    assert mapping["rows"][0]["blocks"] == ["b6"] and fa["lines"][0]["qty"] is None


def test_a_rows_cells_are_found_like_fields_and_a_header_is_no_row():
    blocks = transcript.normalise_blocks([
        {"id": "h", "kind": "table_header", "text": "", "cells": ["SKU", "ARTIKEL", "QTY", "HARGA"]},
        {"id": "r", "kind": "table_row", "text": "", "cells": ["1301370", "BIHUN", "6", "64,955.00"]}])
    raw = {"lines": [{"row": "h", "qty": "QTY"}, {"row": "r", "qty": "6", "unit_price": "64955.00"}]}
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, [], ["qty", "unit_price"])
    assert len(fa["lines"]) == 1 and fa["lines"][0]["unit_price"] == "64955.00"      # "64,955.00" as printed
    assert mapping["dropped"][0]["why"] == "a table's header line, not an item"


def test_two_mappings_keep_agreements_and_single_finds_and_drop_disagreements():
    A = ({"total": {"value": "775397.00", "source_text": "775,397"}, "ppn": None,
          "po_number": {"value": "PO.1", "source_text": "PO.1"},
          "lines": [{"qty": "2", "unit_price": None, "row_text": "r1"}]},
         {"fields": {"total": {"block": "b1"}, "po_number": {"block": "b3"}}, "rows": [{"block": "r1"}]}, [])
    B = ({"total": {"value": "775397.00", "source_text": "775,397"}, "ppn": {"value": "76841.00", "source_text": "76,841"},
          "po_number": {"value": "PO.7", "source_text": "PO.7"},
          "lines": [{"qty": "3", "unit_price": "10", "row_text": "r1"}, {"qty": "1", "row_text": "r2"}]},
         {"fields": {"total": {"block": "b1"}, "ppn": {"block": "b2"}, "po_number": {"block": "b4"}},
          "rows": [{"block": "r1"}, {"block": "r2"}]}, [])
    fa, mapping, _ = transcript.merge(A, B)
    assert fa["total"]["value"] == "775397.00" and fa["ppn"]["value"] == "76841.00"       # agreed; found once
    assert fa["po_number"] is None                                                          # they disagree
    assert fa["lines"][0] == {"qty": None, "unit_price": "10", "row_text": "r1"} and fa["lines"][1]["qty"] == "1"
    assert {c.get("field") or c.get("column") for c in mapping["conflicts"]} == {"po_number", "qty"}
    assert mapping["one_run"] == ["ppn"] and [w["block"] for w in mapping["rows"]] == ["r1", "r2"]


def test_a_date_is_found_by_its_meaning():
    """Page 5: both runs found the receipt date "14-Sep-2026" and gave it as "2026-09-14"; comparing characters
    refused it."""
    blocks = transcript.normalise_blocks([{"id": "b18", "kind": "printed", "text": "Tanggal : 14-Sep-2026"}])
    raw = {"fields": {"posting_date": {"block": "b18", "text": "14-Sep-2026", "value": "2026-09-14"},
                      "tanggal_transaksi": {"block": "b18", "text": "14-Sep-2026", "value": "2026-09-15"}}}
    fa, mapping, _ = transcript.to_fields_all(raw, blocks, ["posting_date", "tanggal_transaksi"], [],
                                              {"posting_date": "date", "tanggal_transaksi": "date"})
    assert fa["posting_date"]["source_text"] == "14-Sep-2026"
    assert fa["tanggal_transaksi"] is None and mapping["dropped"][0]["why"] == "not on the page"


def test_a_cell_box_tightens_to_its_ink_without_the_borders():
    """Page 9: the value's box was its whole table cell, borders and blank space included, and Tesseract zoomed on it
    read nothing. Tightened to the ink, borders removed, it reads the number."""
    import cv2
    import numpy as np
    from worker import zoom
    img = np.full((200, 800), 255, "uint8")
    cv2.rectangle(img, (100, 50), (700, 110), 0, 2)                 # the cell's borders
    cv2.putText(img, "PO.2026.09.32029", (120, 92), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    for y in range(60, 105, 9):                                     # a right border broken into dashes, and specks
        cv2.line(img, (690, y), (690, y + 4), 0, 2)
    img[70, 600] = img[90, 640] = 0
    x0, y0, x1, y1 = zoom.ink_rect(img, (95, 45, 705, 115))
    assert 110 <= x0 <= 125 and x1 < 450 and 55 <= y0 and y1 <= 108   # the text, not the cell
    assert zoom.ink_rect(np.full((50, 50), 255, "uint8"), (0, 0, 50, 50)) == (0, 0, 50, 50)   # nothing: unchanged


def test_a_look_again_is_carried_over_only_where_the_first_answer_is_unchanged():
    """Adopting re-reads every page: a look-again already paid for is kept where the new reading made the same first
    answer (its kept answer put back), dropped where the new reading already differs (p12: the new reading reads the
    PO number right, so the look-again that corrected the old one no longer applies)."""
    from worker import vf
    second = {"asked": ["po_number", "total"], "bundle_asks": ["posting_date"], "results": {
        "po_number": {"first": {"source_text": "AH9Q59089"}, "second": {"value": "AH9Q69089", "source_text": "AH9Q69089"},
                      "kept_second": True},
        "total": {"first": {"source_text": "13,987,409.78"}, "second": {"value": "13987409.70",
                  "source_text": "13,987,409.70"}, "kept_second": True}}}
    fa = {"po_number": {"value": "AH9Q69089", "source_text": "AH9Q69089"},
          "total": {"value": "13987409.78", "source_text": "13,987,409.78", "box": [1, 2, 3, 4]}}
    out = vf.carry_over(second, fa)
    assert out["asked"] == ["total"] and "po_number" not in out["results"] and out["bundle_asks"] == ["posting_date"]
    assert fa["total"] == {"value": "13987409.70", "source_text": "13,987,409.70", "box": [1, 2, 3, 4]}
    assert vf.carry_over(None, fa) is None


def test_a_mapping_made_twice_is_another_version(monkeypatch):
    from common import context
    from worker import classify, vf
    ctx = context.seed_content()
    monkeypatch.setattr(vf, "MAP_TWICE", True)
    a = vf.two_step_versions(ctx)
    monkeypatch.setattr(vf, "MAP_TWICE", False)
    b = vf.two_step_versions(ctx)
    assert a[:2] == b[:2] and a[2] != b[2] and a[2].startswith(a[1])
