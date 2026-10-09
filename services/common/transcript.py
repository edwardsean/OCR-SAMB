"""Read, then map (the mentor, 2026-09-29): the AI OCR copies EVERYTHING on a page with no field list, then a text
model maps that copy onto the field list and collects notes. This module is the pure part: it turns the two answers
into today's reading shape, so everything after it (Jev, verify, zoom, look-again, the 7a gates, Satellite,
grouping, cross-checks, Review, publish) is unchanged.

  transcript  blocks [{id, kind, text, cells?, box [ymin, xmin, ymax, xmax] 0-1000, about?}] (openai_vlm.transcribe)
  fields_all  {canon: {value, source_text, box} | None, "lines": [{<LINE_CANON cols>, row_text}]}   (as today)
  mapping     where each value came from: {"fields": {canon: {block, box_by}}, "rows": [{block, box}], "dropped": [...]}
  notes       [{kind, text, blocks, about, box}]: handwriting, stamps, marks, remarks. AI claims, never a witness.

The transcript is the AI's text, never a witness either: a value gets ✅ only from print (Tesseract) or Satellite,
exactly as before. What this module guarantees instead is GROUNDING: a mapped value is kept only when its characters
are in the block it names, so the text model can't invent a value the transcript doesn't have.
"""
import hashlib
import re

from common.models import vlm
from common import verify
from common.verify import flat

TRANSCRIBE_V, MAP_V = vlm.TRANSCRIBE_V, vlm.MAP_V
PROMPT_SHA = {            # prompt version -> sha1 of its text: tests/test_vf_transcript.py checks a change bumped it
    ("TRANSCRIBE", 1): "f495ece80311",
    ("MAP", 1): "c350a7de07e3",
    ("MAP", 2): "19451bf19327",
    ("MAP", 3): "5aecb2170711",
    ("MAP_NAMED", 1): "c9358d6dfb4d",
}
KINDS = {"printed", "table_header", "table_row", "handwriting", "stamp", "mark"}
NOTE_KINDS = {"handwriting", "stamp", "mark", "remark", "signature"}
SNAP_MARGIN = 8           # on 0-1000: how far around a block Tesseract's words may sit and still be that block's
SNAP_COVER = 0.6          # the value's own words must spell at least this share of it to tighten its box


def prompt_sha(text):
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def transcript_version(spec, prep_version):
    """Which transcription a stored transcript is: the model, the prompt and the page image it was made from."""
    return f"t{TRANSCRIBE_V}@{spec}#p{prep_version}"


def map_version(fields_hash, t_version, spec):
    """Which mapping a reading is: the field list, the transcript and the text model + prompt."""
    return f"{fields_hash}@{t_version}>m{MAP_V}@{spec}"


# ---------------------------------------------------------------------------------------------- the transcript

def normalise_blocks(blocks):
    """Unique ids (b1, b2, … where the model gave none or repeated one), known kinds, no empty blocks."""
    out, seen = [], set()
    for i, b in enumerate(blocks or [], 1):
        text = block_text(b)
        if not flat(text) and b.get("kind") not in ("mark",) and not (b.get("text") or "").strip():
            continue
        bid = b.get("id") if b.get("id") and b.get("id") not in seen else f"b{i}"
        while bid in seen:
            bid += "x"
        seen.add(bid)
        out.append({**b, "id": bid, "kind": b.get("kind") if b.get("kind") in KINDS else "printed"})
    return out


def cells_of(b):
    """A table row's cells, or None when no cell holds anything: the AI OCR can leave a copied line in no cell
    (BATCH-20261008-03 p28: PO.2026.09.32006, printed across two columns above the first row, came back with six empty
    cells, and the text model was handed an empty line)."""
    cells = b.get("cells")
    return cells if cells and any(str(c).strip() for c in cells) else None


def cells_miss(b):
    """The words of a row's text that none of its cells holds (the AI OCR left FILMA MARGARINE SALTED SACHET 200GR out
    of every cell of its row on b-d50bc72289 p1)."""
    cells = cells_of(b)
    if not cells:
        return []
    joined = flat(" ".join(str(c) for c in cells))
    return [w for w in str(b.get("text") or "").split() if flat(w) and flat(w) not in joined]


def block_text(b):
    """A block's text: a table row's cells joined in order (so handwriting beside a row never enters it); its text when
    no cell holds anything."""
    cells = cells_of(b)
    return " ".join(str(c) for c in cells if str(c).strip()) if cells else (b.get("text") or "")


def shown(b):
    """A block as the text model and the page view show it: a table row's cells; its text when no cell holds anything;
    its cells and then the whole line when the cells miss words of it."""
    cells = cells_of(b)
    if not cells:
        return b.get("text") or ""
    row = " | ".join(str(c) for c in cells)
    return f"{row}  (whole line: {b['text']})" if cells_miss(b) else row


def render(blocks):
    """The transcript as the text model reads it: one line per block, with its kind and position."""
    lines = []
    for b in blocks:
        box = b.get("box")
        where = f"(x {box[1]}-{box[3]}, y {box[0]}-{box[2]})" if box else "(position unknown)"
        text = shown(b)
        on = f" [on {b['about']}]" if b.get("about") else ""
        lines.append(f"[{b['id']}] {b['kind']} {where}{on} {text}")
    return "\n".join(lines)


TAUGHT = ("LEARNED FROM PEOPLE for this document type (use each only where this page shows what it names: its label, "
          "its column or its place):")


def map_prompt(blocks, heads, cols, hints=None):
    """The text model's prompt: the task, the field list (heads/cols from openai_vlm._field_list), what people taught
    for this page's type and customer (pass B only), and the transcript."""
    taught = f"\n\n{TAUGHT}\n{hints}" if hints else ""
    return (f"{vlm.MAP}\n\nFIELDS (key: meaning):\n{heads}\n\nLINE ITEM COLUMNS: {cols}{taught}\n\n"
            f"TRANSCRIPT:\n{render(blocks)}")


def map_named_prompt(blocks, heads, cols, hints):
    """Pass B's prompt (vlm.MAP_NAMED): only the fields its tips name (heads), a table's rows only when they name a
    column (cols), the tips, and the whole transcript (a tip says where on the page to look)."""
    lines = vlm.MAP_NAMED_LINES if cols else ""
    answer = 'Answer with ONE JSON object: {"fields": {<field>: {...} or null}' + (', "lines": [...]' if cols else "") + "}."
    return (f"{vlm.MAP_NAMED}{lines}{answer}\n\nFIELDS (key: meaning):\n{heads}"
            + (f"\n\nLINE ITEM COLUMNS: {cols}" if cols else "")
            + f"\n\n{TAUGHT}\n{hints}\n\nTRANSCRIPT:\n{render(blocks)}")


# ---------------------------------------------------------------------------------------------- the mapping

def _spaced(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def grounded(text, block):
    """The value is in that block as it is written, as whole tokens: never a piece of a longer number or word (a
    handwritten "2" beside a row is not the "2" inside "425ML"; "1.126.011" is not "1.126.011,00" cut short).
    A table row is matched against its cells, one cell at a time or across neighbouring cells, and against its whole
    line too when the cells miss words of it (cells_miss)."""
    t = _spaced(text)
    if not flat(t):
        return False
    hay = [_spaced(block_text(block))] + [_spaced(c) for c in cells_of(block) or []] + \
        ([_spaced(block.get("text"))] if cells_miss(block) else [])
    pat = re.compile(r"(?<![0-9A-Z])(?<![0-9][.,])" + re.escape(t) + r"(?![0-9A-Z]|[.,][0-9])")
    return any(pat.search(h) for h in hay)


TOKEN = re.compile(r"[0-9A-Za-z](?:[0-9A-Za-z./\-]|[.,](?=[0-9]))*(?:[.,](?![0-9]))?")


def source_of(value, text, block, kind=None):
    """The value's printed characters in the block the text model named, or None. The value itself is looked for in
    the block first: the text model sometimes names the value's block but copies the label beside it ("VAT AMOUNT"
    for block "80,924.00"). Otherwise its copied text must be in the block AND contain the value. A value its source
    doesn't contain ("normalised", a total with the source "TOTAL") is never kept."""
    span = locate(value, block_text(block), kind)
    if span and grounded(span, block):
        return span
    if text and grounded(text, block):
        span = locate(value, text, kind)
        if span:
            return span
    return None


def locate(value, text, kind=None):
    """The value's own token(s) in the text, or None: the same characters (identifiers), the same amount ("80924.00"
    is "80,924.00"), or the same words (a name)."""
    fv = flat(value)
    if not fv or not text:
        return None
    ms = list(TOKEN.finditer(text))
    for i in range(len(ms)):                          # the same characters over one or more neighbouring tokens
        acc = ""                                      # ("0000000398 / OS-055", "PT. SARANA ABADI …")
        for j in range(i, min(i + 12, len(ms))):
            acc += flat(ms[j].group(0))
            if acc == fv:
                return text[ms[i].start():ms[j].end()]
            if not fv.startswith(acc):
                break
    toks = [m.group(0) for m in ms]
    try:
        v = float(str(value).replace(",", ""))
    except ValueError:
        v = None
    if v is not None:
        nums = [t for t in toks if re.search(r"\d", t) and verify.amount(t) is not None
                and abs(verify.amount(t) - v) < 0.005]
        if nums:
            return nums[0]
    if kind == "date":                                # a date by meaning: "2026-09-14" is "14-Sep-2026",
        for size in range(3):                         # the shortest span that is it ("14 Sep 2026" is three tokens)
            for i in range(len(ms) - size):
                cand = text[ms[i].start():ms[i + size].end()]
                if str(value) in verify.dates(cand):
                    return cand
    words = re.search(r"(?<![0-9A-Za-z])" + re.escape(str(value).strip()) + r"(?![0-9A-Za-z])", text, re.I)
    return words.group(0) if words else None


def narrow(box, text, span):
    """A block's box is its whole line ("NOMER ORDER | PO.2026.09.32029"): the value's share of it, by where its
    characters sit in the line, so the zoomed check reads the value and not its label. Snapping to Tesseract's
    words (snap_boxes) tightens it further where Tesseract read them."""
    if not box or not text or not span:
        return box
    i = text.upper().find(span.upper())
    if i < 0 or len(span) >= 0.7 * len(text):
        return box
    y0, x0, y1, x1 = box
    w = x1 - x0
    pad = max(4, w * 0.03)
    return [y0, int(max(x0, x0 + w * i / len(text) - pad)), y1, int(min(x1, x0 + w * (i + len(span)) / len(text) + pad))]


def own_span(value, text):
    """The value's own characters within what the text model copied: when it copied the whole line ("NO.PO :
    AH9Q69089 DIV : T"), the token that is the value ("AH9Q69089"), so the print check compares the value, not its
    label. An amount is found by its number ("13987409.70" in "TOTAL SELURUHNYA 13,987,409.70"). Several words that
    together make the value (a name) stay as copied when no single token is it."""
    fv = flat(value)
    toks = TOKEN.findall(text or "")
    if len(toks) <= 1 or not fv:
        return text
    same = [t for t in toks if flat(t) == fv]
    if same:
        return same[0]
    try:
        v = float(str(value).replace(",", ""))
        nums = [t for t in toks if re.search(r"\d", t) and verify.amount(t) is not None and abs(verify.amount(t) - v) < 0.005]
        if nums:
            return nums[0]
    except ValueError:
        pass
    words = re.search(re.escape(str(value).strip()), text or "", re.I)
    return words.group(0) if words else text


WRAP_LINES = 2           # at most this many numbers-only lines under a table row count as that row, wrapped


def numbers_only(text):
    """A line that is (almost) all numbers: amounts, codes, quantities, no words."""
    toks = TOKEN.findall(text or "")
    return bool(toks) and sum(1 for t in toks if re.search(r"\d", t)) >= 0.7 * len(toks)


def row_blocks(b, blocks, named):
    """A table row and the lines it wraps onto: a printed row too long for one line continues on the next, and the AI
    OCR copies it as two blocks (page 12: b6 "20034699 BIHUN JAGUNG … BAL/10", b7 "1301370 0 64,955.00 …"). The row's
    cells then sit in both. A following block joins the row only when it is numbers only, directly below it, and not
    itself a row the text model named."""
    i = next((k for k, x in enumerate(blocks) if x is b), None)
    out = [b]
    if i is None:
        return out
    for x in blocks[i + 1:i + 1 + WRAP_LINES]:
        prev = out[-1]
        if x.get("id") in named or x.get("kind") not in ("printed", "table_row") or not numbers_only(block_text(x)):
            break
        pb, xb = prev.get("box"), x.get("box")
        if pb and xb and not (pb[0] <= xb[0] <= pb[2] + max(15, 1.5 * (pb[2] - pb[0]))):
            break                                     # not the line directly below
        out.append(x)
    return out


def union(boxes):
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def _s(v):
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return s or None


def to_fields_all(raw, blocks, field_names, line_cols, kinds=None):
    """The text model's answer + the transcript → (fields_all, mapping, notes). A value, cell or note that isn't in the
    block it names is dropped (listed in mapping["dropped"]): the text model may only point at the transcript."""
    by_id = {b["id"]: b for b in blocks}
    kinds = kinds or {}
    answer = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
    fields_all, mapping = {}, {"fields": {}, "rows": [], "dropped": []}
    for name in field_names:
        m = answer.get(name)
        if isinstance(m, (str, int, float)) and not isinstance(m, bool):
            m = {"value": str(m)}                     # a bare value, no block named: found by its characters below
        if not isinstance(m, dict):
            fields_all[name] = None
            continue
        named_b, text = by_id.get(_s(m.get("block"))), _s(m.get("text"))
        text = re.sub(r"\s*\|\s*", " ", text) if text else text          # the cells' separator we show it
        value = _s(m.get("value")) or text
        kind, b = kinds.get(name), named_b
        src = source_of(value, text, b, kind) if b and value else None
        if not src and value:                         # no block, or the wrong one: the block that holds the value
            b = next((x for x in blocks if locate(value, block_text(x), kind)), None)
            src = locate(value, block_text(b), kind) if b else None
            if src:
                mapping.setdefault("moved", []).append(name)
        if not src:
            fields_all[name] = None
            if text or value:
                mapping["dropped"].append({"field": name, "text": text, "value": value, "block": _s(m.get("block")),
                                           "why": "not on the page" if named_b else "no such block"})
            continue
        fields_all[name] = {"value": value, "source_text": src, "box": narrow(b.get("box"), block_text(b), src)}
        mapping["fields"][name] = {"block": b["id"], "box_by": "block"}
    rows = []
    named = {_s(r.get("row")) for r in raw.get("lines") or [] if isinstance(r, dict)}
    for r in raw.get("lines") or []:
        if not isinstance(r, dict):
            continue
        b = by_id.get(_s(r.get("row")))
        if not b or b.get("kind") == "table_header":
            mapping["dropped"].append({"row": _s(r.get("row")),
                                       "why": "no such block" if not b else "a table's header line, not an item"})
            continue
        parts = row_blocks(b, blocks, named)          # the row, and a numbers line it wraps onto (page 12)
        row = {"row_text": " ".join(block_text(x) for x in parts)}
        for col in line_cols:
            v = _s(r.get(col))
            row[col] = v if v and any(grounded(v, x) or locate(v, block_text(x)) for x in parts) else None
            if v and row[col] is None:
                mapping["dropped"].append({"row": b["id"], "column": col, "text": v, "why": "not in its row"})
        rows.append(row)
        mapping["rows"].append({"block": b["id"], "blocks": [x["id"] for x in parts],
                                "box": union(x.get("box") for x in parts)})
    fields_all["lines"] = rows
    notes = []
    for n in raw.get("notes") or []:
        if not isinstance(n, dict):
            continue
        ids = [i for i in (n.get("blocks") or []) if isinstance(i, str) and i in by_id]
        if not ids:
            continue                                          # a note must point at something on the page
        kind = n.get("kind") if n.get("kind") in NOTE_KINDS else "remark"
        notes.append({"kind": kind, "text": _s(n.get("text")) if kind != "signature" else None, "blocks": ids,
                      "about": _s(n.get("about")), "box": union(by_id[i].get("box") for i in ids)})
    return fields_all, mapping, notes


# ---------------------------------------------------------------------------------------------- two mappings

def merge(first, second):
    """Two mappings of one transcript (each already grounded: (fields_all, mapping, notes)) → one. The text model
    leaves a field empty on one run and fills it on the next (10 of 16 pages differed on 7000363700-03):
      both give the same value      kept
      only one gives a value        kept (it is on the page, in the block it names)
      they give different values    empty ("don't know" beats picking a run), listed in mapping["conflicts"]
    Rows are matched by the block they were read from; a cell follows the same three rules."""
    (fa, ma, na), (fb, mb, nb) = first, second
    out, mapping = {}, {"fields": {}, "rows": [], "dropped": ma.get("dropped", []), "moved": ma.get("moved", []),
                        "conflicts": [], "one_run": []}
    for name in sorted(set(fa) | set(fb)):
        if name == "lines":
            continue
        a, b = fa.get(name), fb.get(name)
        if a and b and flat(a["value"]) != flat(b["value"]):
            out[name] = None
            mapping["conflicts"].append({"field": name, "values": [a["value"], b["value"]]})
            continue
        out[name] = a or b
        if (a or b) and not (a and b):
            mapping["one_run"].append(name)
        where = (ma.get("fields") or {}).get(name) if a else (mb.get("fields") or {}).get(name)
        if out[name] and where:
            mapping["fields"][name] = where
    ra = {w["block"]: (row, w) for row, w in zip(fa.get("lines") or [], ma.get("rows") or [])}
    rb = {w["block"]: (row, w) for row, w in zip(fb.get("lines") or [], mb.get("rows") or [])}
    order = [w["block"] for w in ma.get("rows") or []] + [w["block"] for w in mb.get("rows") or []
                                                          if w["block"] not in ra]
    rows = []
    for blk in order:
        (x, wx), (y, wy) = ra.get(blk, (None, None)), rb.get(blk, (None, None))
        row = dict(x or y)
        if x and y:
            for col in set(x) | set(y):
                if col == "row_text":
                    continue
                u, v = x.get(col), y.get(col)
                if u and v and flat(u) != flat(v):
                    row[col] = None
                    mapping["conflicts"].append({"row": blk, "column": col, "values": [u, v]})
                else:
                    row[col] = u or v
        rows.append(row)
        mapping["rows"].append(wx or wy)
    out["lines"] = rows
    return out, mapping, na or nb


# ---------------------------------------------------------------------------------------------- boxes

def snap_boxes(fields_all, mapping, words, shape):
    """Tighten each value's box from its whole block to the Tesseract words that print it, where Tesseract found them
    inside the block (zoom and the look-again crop a tight spot; a whole line may hold labels and other values).
    words: staging.page.ocr_words [text, conf, x, y, w, h] on the upright image; shape: (h, w). Only words that are
    part of the value count (a label beside it on the same line never widens the box), and together they must cover
    most of it (SNAP_COVER), else the block's box stays."""
    h, w = shape[:2]
    for name, where in mapping.get("fields", {}).items():
        f = fields_all.get(name)
        if not f or not f.get("box"):
            continue
        y0, x0, y1, x1 = f["box"]
        m = SNAP_MARGIN
        inside = [wd for wd in words or [] if len(wd) >= 6
                  and (x0 - m) * w / 1000 <= wd[2] + wd[4] / 2 <= (x1 + m) * w / 1000
                  and (y0 - m) * h / 1000 <= wd[3] + wd[5] / 2 <= (y1 + m) * h / 1000]
        src = flat(f["source_text"])
        part = [wd for wd in inside if len(flat(wd[0])) >= 2 and flat(wd[0]) in src]
        if part and sum(len(flat(wd[0])) for wd in part) >= SNAP_COVER * len(src):
            rx0, ry0 = min(wd[2] for wd in part), min(wd[3] for wd in part)
            rx1, ry1 = max(wd[2] + wd[4] for wd in part), max(wd[3] + wd[5] for wd in part)
            f["box"] = [int(ry0 * 1000 / h), int(rx0 * 1000 / w), int(ry1 * 1000 / h), int(rx1 * 1000 / w)]
            where["box_by"] = "tesseract"
    return fields_all
