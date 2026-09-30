"""Extraction knowledge learned from people (read-then-map, Stage 2). Stage 2a: the EXAMPLES it will learn from.

  A person corrects a field on the page viewer by marking where it is printed on the paper (source 'marked'), or
  types it on Review (source 'typed'; its place is then found in the page's copy). Either way the page is fixed at
  once (staging.field_confirmation, as before) and an example is kept (staging.extract_example, schema/020): the
  region, what Tesseract and the AI OCR's copy say there, and what is printed beside it (the label to its left, the
  column header above). The knowledge (Stage 2c) is learned from examples, behind a replay gate: one example never
  teaches alone.

  Regions and boxes are [ymin, xmin, ymax, xmax] on 0-1000 of the upright page, like the AI OCR's boxes.
"""
import random
import re

from psycopg.types.json import Json

from common import transcript, verify
from common.verify import flat

EXAM_SHARE = 0.2          # examples drawn for the exam pile (never learned from), like type labels
SAME_LINE = 0.5           # a block beside the value shares at least this much of the value's height


def _inside(box, region, share=0.5):
    """At least `share` of the box lies inside the region."""
    if not box or not region:
        return False
    y0, x0, y1, x1 = max(box[0], region[0]), max(box[1], region[1]), min(box[2], region[2]), min(box[3], region[3])
    if y1 <= y0 or x1 <= x0:
        return False
    return (y1 - y0) * (x1 - x0) >= share * max(1, (box[2] - box[0]) * (box[3] - box[1]))


def words_in(region, words, size):
    """Tesseract's words whose centre lies in the region, in reading order. words: [text, conf, x, y, w, h] on the
    upright image; size: (width, height)."""
    if not region or not size:
        return []
    w, h = size
    y0, x0, y1, x1 = region
    got = [wd for wd in words or [] if len(wd) >= 6
           and x0 * w / 1000 <= wd[2] + wd[4] / 2 <= x1 * w / 1000 and y0 * h / 1000 <= wd[3] + wd[5] / 2 <= y1 * h / 1000]
    return sorted(got, key=lambda wd: (round((wd[3] + wd[5] / 2) / max(1, wd[5])), wd[2]))


def blocks_in(region, blocks):
    """The copy's blocks lying mostly in the region, or failing that, the ones the region lies in."""
    inside = [b for b in blocks or [] if _inside(b.get("box"), region)]
    return inside or [b for b in blocks or [] if b.get("box") and _inside(region, b["box"], 0.6)]


def portion(block, region):
    """The part of a block's text the region covers across: a line is copied whole, a person marks one value in it.
    Characters are taken to spread evenly over the block's width (the inverse of transcript.narrow), widened to
    whole tokens."""
    text, box = transcript.block_text(block), block.get("box")
    if not box or box[3] <= box[1] or region[1] <= box[1] and region[3] >= box[3]:
        return text
    n, w = len(text), box[3] - box[1]
    a = max(0, int((region[1] - box[1]) / w * n) - 1)
    z = min(n, int((region[3] - box[1]) / w * n) + 1)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while z < n and not text[z].isspace():
        z += 1
    return text[a:z].strip(" |:") or text


def region_contents(region, blocks, words, size):
    """What a marked region holds: {words, blocks, suggest}. The suggestion is the AI OCR's copy of the part marked
    (the copy reads characters better than Tesseract on these scans: 13,987,409.70 where Tesseract has .78), else
    Tesseract's words; both are shown, and the person checks the value against the paper before saving."""
    ws = " ".join(wd[0] for wd in words_in(region, words, size)).strip()
    bs = blocks_in(region, blocks)
    part = " ".join(portion(b, region) for b in bs if not b.get("cells")).strip()   # a table row's cells aren't
    # spread by their characters (columns have their own widths): inside a table, only Tesseract's words suggest
    return {"words": ws, "blocks": [{"id": b.get("id"), "kind": b.get("kind"), "text": transcript.block_text(b)}
                                    for b in bs], "suggest": part if flat(part) else ws}


def anchor_of(region, value, blocks):
    """What is printed beside the value: the label left of it on the same line (inside its own block, "Ref. PO No. :
    4505832724", or the nearest block to its left), the nearest table header above it and the header's cell over it,
    and the kind of block it sits in (printed, table_row, handwriting, stamp, mark)."""
    if not region:
        return {}
    under = blocks_in(region, blocks)
    out = {"under_kind": under[0].get("kind") if under else None}
    for b in under:                                   # the label inside its own line
        text = transcript.block_text(b)
        span = transcript.locate(value, text) if value else None
        if span:
            before = text[:text.upper().find(span.upper())].strip(" :|-")
            if re.search(r"[A-Za-z]{2}", before):
                out["left"] = " ".join(before.split()[-5:])
                break
    ry0, rx0, ry1, rx1 = region
    rh = max(1, ry1 - ry0)
    if "left" not in out:                             # the nearest block to its left on the same line
        beside = [b for b in blocks or [] if b.get("box") and b["box"][3] <= rx0 + 5
                  and min(ry1, b["box"][2]) - max(ry0, b["box"][0]) >= SAME_LINE * rh
                  and re.search(r"[A-Za-z]{2}", transcript.block_text(b))]
        if beside:
            out["left"] = transcript.block_text(max(beside, key=lambda b: b["box"][3]))
    heads = [b for b in blocks or [] if b.get("kind") == "table_header" and b.get("box") and b["box"][2] <= ry0 + 5]
    if heads:
        hd = max(heads, key=lambda b: b["box"][2])
        out["above"] = transcript.block_text(hd)
        cells = [c for c in hd.get("cells") or []]
        hx0, hx1 = hd["box"][1], hd["box"][3]
        if cells and hx1 > hx0:                       # the header's cell over the value, by where it sits across
            i = int(((rx0 + rx1) / 2 - hx0) / (hx1 - hx0) * len(cells))
            out["header_cell"] = cells[max(0, min(len(cells) - 1, i))]
    return out


NOISE_CONF = 40           # Tesseract's own confidence below which a word only it read is dropped as specks


def pick_units(blocks, words, size):
    """Everything on the page a person can click to take as a value. POSITIONS come from Tesseract's words (it knows
    exactly where each word is printed); TEXT comes from the AI OCR's copy, which reads characters better: within each
    line of the copy, its words are aligned in order with Tesseract's words in that line (difflib), so a word Tesseract
    misread still gets the copy's characters. A word only Tesseract read keeps Tesseract's text; a word only the copy
    has gets no box (its place isn't known: estimating it from its line put boxes on the wrong columns, 2026-09-30;
    finding table cells from the ruling lines slipped a column on p3, where a row number and a dashed line cancelled
    out). A table row is fixed from its copied cells instead (the viewer lists them; the person matches by eye).
    [{id, block, i, text, box [ymin, xmin, ymax, xmax] 0-1000, tess, match: equal | misread | tesseract}]"""
    import difflib
    W, H = size or (0, 0)
    if not W or not H:
        return []
    ws = [dict(t=wd[0], c=wd[1], x0=wd[2], y0=wd[3], x1=wd[2] + wd[4], y1=wd[3] + wd[5]) for wd in words or []
          if len(wd) >= 6 and flat(wd[0])]
    used, out = set(), []

    def unit(w, text, block, i, match):
        out.append({"id": f"w{len(out)}", "block": block, "i": i, "text": text, "tess": w["t"], "match": match,
                    "box": [int(w["y0"] * 1000 / H), int(w["x0"] * 1000 / W), int(w["y1"] * 1000 / H),
                            int(w["x1"] * 1000 / W)]})

    for b in blocks or []:
        box = b.get("box")
        if not box:
            continue
        pad = max(4, (box[2] - box[0]) * 0.35)        # Tesseract's words whose centre lies on this line of the copy
        mine = [k for k, w in enumerate(ws) if k not in used
                and (box[0] - pad) * H / 1000 <= (w["y0"] + w["y1"]) / 2 <= (box[2] + pad) * H / 1000
                and (box[1] - 15) * W / 1000 <= (w["x0"] + w["x1"]) / 2 <= (box[3] + 15) * W / 1000]
        if not mine:
            continue
        mine.sort(key=lambda k: (round(((ws[k]["y0"] + ws[k]["y1"]) / 2) / max(1, ws[k]["y1"] - ws[k]["y0"])), ws[k]["x0"]))
        text = " ".join(str(c) for c in b["cells"]) if b.get("cells") else (b.get("text") or "")
        toks = [m.group(0) for m in transcript.TOKEN.finditer(text)]
        sm = difflib.SequenceMatcher(None, [flat(t) for t in toks], [flat(ws[k]["t"]) for k in mine], autojunk=False)
        for op, a0, a1, b0, b1 in sm.get_opcodes():
            for j in range(b1 - b0):
                k = mine[b0 + j]
                if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
                    unit(ws[k], toks[a0 + j], b["id"], a0 + j, "equal" if op == "equal" else "misread")
                elif not noise(ws[k]):
                    unit(ws[k], ws[k]["t"], b["id"], 1000 + b0 + j, "tesseract")
                used.add(k)
    for k, w in enumerate(ws):                        # words on no line of the copy: Tesseract's text alone
        if k not in used and not noise(w):
            unit(w, w["t"], None, k, "tesseract")
    return out


def noise(w):
    """A word only Tesseract read that is too short or too unsure to be anything but specks ("ie", "eee" over a table
    border): it would sit on top of real words and catch their clicks."""
    return len(flat(w["t"])) < 3 or (w.get("c") is not None and w["c"] < NOISE_CONF)


def chain_of_page(c, bid, n):
    """The customer of the page's order, when grouping has linked it (satellite.chain_of), else None."""
    r = c.execute("""SELECT s.customer_parent, s.customer_code FROM staging.document d
                       JOIN staging.bundle_document bd ON bd.document_id = d.id JOIN staging.bundle b ON b.id = bd.bundle_id
                       JOIN satellite.sor s ON s.sor_no = b.sor_no
                      WHERE d.batch_id=%s AND %s BETWEEN d.page_from AND d.page_to LIMIT 1""", (bid, n)).fetchone()
    return (r["customer_parent"] or r["customer_code"]) if r else None


def save_example(c, bid, n, confirmation, value, shown, by, source, region=None, row_key=None):
    """One example from a correction already saved as a field_confirmation (same transaction). The page's copy and
    Tesseract's words give what is printed in and beside the region; a typed correction's region is where the value is
    found in the copy (none found: no example, the page is still fixed). Earlier examples of the same field on this
    page are set aside (superseded). Returns the example's id, or None."""
    p = c.execute("""SELECT doc_type::text AS doc_type, transcript, ocr_words, upright_path, fields
                       FROM staging.page WHERE batch_id=%s AND page_no=%s""", (bid, n)).fetchone()
    if not p or not p["doc_type"]:
        return None
    m = re.match(r"lines\[(.+)\]\.(\w+)$", confirmation)
    field = f"lines.{m.group(2)}" if m else confirmation
    row_key = row_key or (m.group(1) if m else None)
    blocks = p["transcript"] or []
    not_printed = value.strip() == "(not printed)"
    size = _size(p)
    if not region and not not_printed:                # typed: where the copy prints it
        b = next((x for x in blocks if transcript.locate(value, transcript.block_text(x))), None)
        if b and b.get("box"):
            region = transcript.narrow(b["box"], transcript.block_text(b), transcript.locate(value, transcript.block_text(b)))
    if not region and not not_printed:
        return None                                   # not found on the page: nothing to learn where it is
    got = region_contents(region, blocks, p["ocr_words"], size) if region else {"words": "", "blocks": []}
    c.execute("""UPDATE staging.extract_example SET status='superseded'
                  WHERE batch_id=%s AND page_no=%s AND confirmation=%s AND status='active'""", (bid, n, confirmation))
    return c.execute("""INSERT INTO staging.extract_example (batch_id, page_no, confirmation, doc_type, chain, field,
                          row_key, kind, value, shown, region, tess_words, blocks, anchor, printed, source, pile, made_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                     (bid, n, confirmation, p["doc_type"], chain_of_page(c, bid, n), field, row_key,
                      "not_printed" if not_printed else "value", None if not_printed else value, shown or None,
                      Json(region) if region else None, got["words"] or None, Json(got["blocks"]),
                      Json(anchor_of(region, value, blocks) if region and not not_printed else {}),
                      bool(region) and not not_printed and bool(flat(value)) and flat(value) in flat(got["words"]),
                      source, "exam" if random.random() < EXAM_SHARE else "practice", by)).fetchone()["id"]


def _size(p):
    """The upright image's (width, height), from its stored PNG header (as the UI's word boxes use it)."""
    try:
        from common import storage
        o = storage.client().get_object(storage.bucket(), p["upright_path"], offset=0, length=24)
        head = o.read(); o.close(); o.release_conn()
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        return None
