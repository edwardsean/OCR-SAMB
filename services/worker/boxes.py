"""The page viewer's clickable boxes, made once per page in the worker (common/boxes.py pairs; this file looks at the
image). Stored in staging.page.pick for the transcript it was made from; the viewer only reads it.

  1. Tesseract once more on the page with its ruling lines removed (psm 6, 2x): on these 1-bit scans the table
     borders and underlines swallow the cells, and the page's own reading (psm 3) finds little in tables (p3's
     AEON receiving note: 147 words on the page, none in its four rows; 643 without the lines).
  2. Each reading's words shrunk to their ink (a word several columns wide dropped), then the copy's tokens paired
     with both readings (common/boxes.match).
  3. A gap still left in a copied line, beside a token that has its box: Tesseract zoomed on that stretch of the
     printed line alone (psm 7, 3x). p3's quantities touch their column's border and print as dots: the page reads
     "z" for 2.00, the line alone reads "200". What it reads is paired like any reading (the characters must agree).
  4. One token between two boxed neighbours on the same printed line, with exactly one piece of ink between them as
     wide as that token: the box is that ink. Only this narrow case is placed without Tesseract reading it:
     counting ink along a whole row slipped a column on p3 (a row number and a dashed line cancelled out).
  5. Every box shrunk to the ink inside it (a dotted underline made p3's "10" reach across its column's border).

  python -m worker.boxes <batch_id> [pages]          make them for stored pages (no model call)
  python -m worker.boxes report <batch_id> [pages]   how many of the copy's tokens have a box, per kind of line
"""
import sys

import cv2
import numpy as np

from common import boxes, db
from common.verify import flat
from worker import enhance

VERSION = 1                   # bump when the boxes change: stored ones are made again
RULE_RUN = 80                 # ink running this many pixels straight is a ruling line, not text
LINE_ZOOM = 3
SPECK = 12                    # pieces of ink smaller than this (pixels) are scan noise


def version(transcript_version):
    return f"{transcript_version}|b{VERSION}"


def ink_of(up):
    """(the page with its dark bands masked and its ruling lines removed, 0 = ink, for Tesseract; its ink as 0/1 with
    dashed borders removed too and dotted strokes closed, for placing and shrinking boxes). Tesseract reads the page
    with its dashed borders: without them it read 7 fewer printed words on pp. 1, 3, 5, 12."""
    g = up if up.ndim == 2 else cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    g = enhance.mask_bands(g, *enhance.measure(g)[2:])
    ink = (g < 128).astype("uint8") * 255
    lines = _run(ink, (RULE_RUN, 1)) | _run(ink, (1, RULE_RUN))
    text = cv2.subtract(ink, cv2.dilate(lines, np.ones((3, 3), "uint8")))
    bare = cv2.subtract(text, cv2.dilate(dashed(text), np.ones((3, 3), "uint8")))
    closed = cv2.morphologyEx(bare, cv2.MORPH_CLOSE, np.ones((3, 3), "uint8"))
    return 255 - text, (closed > 0).astype("uint8")


def _run(img, size):
    return cv2.morphologyEx(img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))


DASH_GAP = 11                 # dashes of a broken border lie at most this far apart


def dashed(text):
    """Borders the 1-bit scan broke into dashes (p3's column borders, dotted underlines), 255 where they are: pieces
    of ink one to three pixels thin, chained end to end over a ruling line's length. A letter is thicker than that,
    or never part of such a chain."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(text, 8)
    ids = np.arange(n)
    thin = np.isin(lab, ids[(st[:, 2] <= 3) & (ids > 0)]).astype("uint8") * 255
    flat_ = np.isin(lab, ids[(st[:, 3] <= 3) & (ids > 0)]).astype("uint8") * 255
    v = _run(cv2.morphologyEx(thin, cv2.MORPH_CLOSE, np.ones((DASH_GAP, 1), "uint8")), (1, RULE_RUN))
    f = _run(cv2.morphologyEx(flat_, cv2.MORPH_CLOSE, np.ones((1, 2 * DASH_GAP), "uint8")), (RULE_RUN, 1))
    return (v & thin) | (f & flat_)


def read_unruled(un):
    """Step 1: Tesseract on the page without its ruling lines."""
    d = enhance.ocr_data(enhance.upscale(enhance.v_close(un)), 6)
    return enhance.summarize(d, enhance.UPSCALE)[0]


def pieces(ink, x0, y0, x1, y1, letter):
    """Pieces of ink in the rectangle (page pixels), dashes dropped: [(x0, y0, x1, y1)]."""
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    crop = ink[y0:max(y0, int(y1)), x0:max(x0, int(x1))]
    if crop.size == 0:
        return []
    out = []
    for x, y, w, h, a in cv2.connectedComponentsWithStats(crop, 8)[2][1:]:
        if a < SPECK or (h <= max(3, 0.2 * letter) and w >= 2 * h + 2):
            continue                                  # noise, or a flat dash of a broken border, underline or dotted
                                                      # rule (p5's rules between rows close up 4-5 pixels tall)
        if w <= 4 and not 0.6 * letter <= h <= 1.4 * letter:
            continue                                  # a thin dash of a vertical border (a "1" is letter-tall)
        out.append((x0 + int(x), y0 + int(y), x0 + int(x + w), y0 + int(y + h)))
    return out


def clusters(ps, letter, gap):
    """Pieces merged left to right while at most `gap` apart; a cluster needs a piece half a letter tall (a lone dot
    or comma is no word)."""
    out = []
    for p in sorted(ps):
        tall = p[3] - p[1] >= 0.5 * letter
        if out and p[0] - out[-1][2] <= gap:
            c = out[-1]
            out[-1] = (min(c[0], p[0]), min(c[1], p[1]), max(c[2], p[2]), max(c[3], p[3]), c[4] or tall)
        else:
            out.append((*p, tall))
    return [c[:4] for c in out if c[4]]


def tighten(box, ink, W, H, letter=None, need=None):
    """A box (0-1000) shrunk to the ink inside it; unchanged when it holds none. A thin upright piece whose ink goes
    on above and below the box is a border crossing it (p3's 02701936 reached its column's dashed border), not a
    letter: a "1" ends where its line does. With the page's letter height, a box much taller than a letter keeps its
    fullest row of ink (p12: Tesseract's MERCHANDISING took in the NO.PO below it); with the width its text needs
    (pixels), it never shrinks below half of that (p12: a pen stroke through BIHUN left only its B)."""
    x0, y0, x1, y1 = box[1] * W / 1000, box[0] * H / 1000, box[3] * W / 1000, box[2] * H / 1000
    ps = pieces(ink, x0 - 2, y0 - 2, x1 + 2, y1 + 2, max(8, (y1 - y0) * 0.6))
    ps = [p for p in ps if x0 - 2 <= (p[0] + p[2]) / 2 <= x1 + 2 and y0 - 2 <= (p[1] + p[3]) / 2 <= y1 + 2
          and not (p[2] - p[0] <= 5 and _goes_on(ink, p, y0, y1))]
    if letter and ps and max(p[3] for p in ps) - min(p[1] for p in ps) > 1.6 * letter:
        rows = []                                     # pieces in rows by their middles, the fullest row kept
        for p in sorted(ps, key=lambda p: (p[1] + p[3]) / 2):
            if rows and (p[1] + p[3]) / 2 - rows[-1][-1] <= 0.6 * letter:
                rows[-1][0].append(p)
                rows[-1][-1] = (p[1] + p[3]) / 2
            else:
                rows.append([[p], (p[1] + p[3]) / 2])
        ps = max((r[0] for r in rows), key=lambda r: sum((p[2] - p[0]) * (p[3] - p[1]) for p in r))
    if not ps:
        return box
    tx0, ty0 = max(x0, min(p[0] for p in ps)), max(y0, min(p[1] for p in ps))
    tx1, ty1 = min(x1, max(p[2] for p in ps)), min(y1, max(p[3] for p in ps))
    if tx1 - tx0 < 2 or ty1 - ty0 < 2:
        return box
    if need and tx1 - tx0 < 0.5 * need:
        tx0, tx1 = x0, x1                             # most of its ink went: keep its width
    return [int(ty0 * 1000 / H), int(tx0 * 1000 / W), -(-int(ty1 * 1000) // H), -(-int(tx1 * 1000) // W)]


def on_ink(words, ink):
    """Tesseract's words shrunk to their ink before they are paired (p5's own reading gives a right-aligned 40.00 a
    box three times its width, the blank before it). A word whose ink lies in pieces a column's gap apart is several
    words run together (p5's "30000" was 30.00 30.00 0.00 across three columns): it is left out."""
    H, W = ink.shape[:2]
    out = []
    for w in words or []:
        if len(w) < 6 or w[4] <= 0 or w[5] <= 0:
            continue
        x0, y0, x1, y1 = w[2], w[3], w[2] + w[4], w[3] + w[5]
        ps = [p for p in pieces(ink, x0 - 2, y0 - 2, x1 + 2, y1 + 2, max(8, 0.6 * w[5]))
              if x0 - 2 <= (p[0] + p[2]) / 2 <= x1 + 2 and y0 - 2 <= (p[1] + p[3]) / 2 <= y1 + 2
              and not (p[2] - p[0] <= 5 and _goes_on(ink, p, y0, y1))]
        if not ps:
            out.append(w)
            continue
        tall = sorted(p[3] - p[1] for p in ps)[len(ps) // 2]
        cl = clusters(ps, tall, 2 * tall)
        if len(cl) > 1:
            continue
        tx0, ty0, tx1, ty1 = max(x0, cl[0][0] if cl else x0), max(y0, min(p[1] for p in ps)), \
            min(x1, cl[0][2] if cl else x1), min(y1, max(p[3] for p in ps))
        out.append([w[0], w[1], tx0, ty0, max(1, tx1 - tx0), max(1, ty1 - ty0)] if tx1 > tx0 and ty1 > ty0 else w)
    return out


def _goes_on(ink, p, y0, y1):
    """Ink in the piece's column both just above and just below the box."""
    a, z = max(0, p[0] - 1), p[2] + 1
    above = ink[max(0, int(y0) - 10):max(0, int(y0) - 3), a:z]
    below = ink[int(y1) + 3:int(y1) + 10, a:z]
    return above.size and below.size and above.any() and below.any()


def gaps(units, blocks, W, H):
    """Runs of a copied line's tokens that have no box, beside a token that has one on the same printed line:
    [(block, token indices, (x0, y0, x1, y1) in page pixels, the neighbours' boxes in pixels)]."""
    have = {(u["block"], u["i"]): u for u in units if u.get("block")}
    px = lambda b: (b[1] * W / 1000, b[0] * H / 1000, b[3] * W / 1000, b[2] * H / 1000)
    out = []
    for b in blocks:
        if not b.get("box"):
            continue
        toks = boxes.tokens(b)
        idx = [i for i, (t, _, _) in enumerate(toks) if flat(t)]
        k = 0
        while k < len(idx):
            if (b["id"], idx[k]) in have:
                k += 1
                continue
            j = k
            while j + 1 < len(idx) and (b["id"], idx[j + 1]) not in have:
                j += 1
            left = have.get((b["id"], idx[k - 1])) if k else None
            right = have.get((b["id"], idx[j + 1])) if j + 1 < len(idx) else None
            run, k = idx[k:j + 1], j + 1
            near = [px(u["box"]) for u in (left, right) if u]
            if not near:
                continue
            h = float(np.median([n[3] - n[1] for n in near]))
            cys = [(n[1] + n[3]) / 2 for n in near]
            if max(cys) - min(cys) > 0.5 * h:
                continue                              # the neighbours are on different printed lines
            bx = px(b["box"])
            x0 = near[0][2] + 1 if left else bx[0] - 0.01 * W
            x1 = near[-1][0] - 1 if right else bx[2] + 0.01 * W
            if x1 - x0 >= h:
                cy = sum(cys) / len(cys)
                out.append((b, run, (x0, cy - 0.9 * h, x1, cy + 0.9 * h), near))
    return out


def read_gap(rect, un):
    """Step 3: Tesseract zoomed on one gap's stretch of its printed line, as words in page pixels."""
    x0, y0, x1, y1 = (max(0, int(v)) for v in rect)
    crop = un[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    big = cv2.resize(enhance.v_close(crop), None, fx=LINE_ZOOM, fy=LINE_ZOOM, interpolation=cv2.INTER_CUBIC)
    return [[w[0], w[1], w[2] + x0, w[3] + y0, w[4], w[5]]
            for w in enhance.summarize(enhance.ocr_data(big, 7), LINE_ZOOM)[0]]


def between(gs, units, ink, reads, W, H, letter):
    """Step 4: one token between two boxed neighbours on its printed line, and exactly one piece of ink there, as
    tall as a letter and as wide as the token should be (the neighbours give the width of a character). Only ink
    centred on the line counts: p5's rows are a letter and a half apart. A confident reading there that says
    something else vetoes it."""
    spots = [u["box"] for u in units]
    words = [w for r in reads for w in r if len(w) >= 6 and w[1] >= 60 and flat(w[0])]
    out = []
    for b, run, (x0, y0, x1, y1), near in gs:
        if len(run) != 1 or len(near) != 2 or not letter:
            continue
        toks = boxes.tokens(b)
        t, s, e = toks[run[0]]
        cy = (y0 + y1) / 2
        have = {u["i"]: u for u in units if u.get("block") == b["id"]}
        per = [(n[2] - n[0]) / max(1, len(u["tess"] or u["text"])) for n, u in
               zip(near, (have.get(i) for i in _neighbours(toks, run[0], have)))]
        cw = float(np.median(per)) if per else letter * 0.6
        ps = [p for p in pieces(ink, x0, cy - letter, x1, cy + letter, letter)
              if abs((p[1] + p[3]) / 2 - cy) <= 0.5 * letter]
        cl = [c for c in clusters(ps, letter, 1.2 * cw)
              if not any(boxes.overlaps([c[1] * 1000 / H, c[0] * 1000 / W, c[3] * 1000 / H, c[2] * 1000 / W], x)
                         for x in spots)]
        if len(cl) != 1 or not 0.6 <= (cl[0][2] - cl[0][0]) / (len(t) * cw) <= 1.6 \
                or cl[0][3] - cl[0][1] > 1.4 * letter:
            continue
        c = cl[0]
        if any(c[0] <= w[2] + w[4] / 2 <= c[2] and c[1] <= w[3] + w[5] / 2 <= c[3] and not _fits(w[0], t)
               for w in words):
            continue
        box = [int(c[1] * 1000 / H), int(c[0] * 1000 / W), -(-c[3] * 1000 // H), -(-c[2] * 1000 // W)]
        spots.append(box)
        out.append({"block": b["id"], "i": run[0], "s": s, "e": e, "text": t, "tess": "", "match": "ink", "box": box})
    return out


def _neighbours(toks, i, have):
    left = max((k for k in have if k < i), default=None)
    right = min((k for k in have if k > i), default=None)
    return [k for k in (left, right) if k is not None]


def _fits(word, token):
    """A reading that doesn't contradict the token: part of it, or like it."""
    a, z = boxes.canon(word), boxes.canon(token)
    return a in z or z in a or boxes.sim(word, token) > 0


def sizes(units, W, H):
    """The page's letter height and character width (pixels), from the boxes whose reading equals the copy."""
    eq = [u for u in units if u["match"] == "equal" and len(u["text"]) >= 3] or units
    if not eq:
        return None, None
    hs = sorted((u["box"][2] - u["box"][0]) * H / 1000 for u in eq)
    ws = sorted((u["box"][3] - u["box"][1]) * W / 1000 / len(u["text"]) for u in eq)
    return hs[len(hs) // 2], ws[len(ws) // 2]


def _lettered(box, letter, H):
    """Its ink is at least half a letter tall: a box shrunk to a flat line of dots holds no word."""
    return not letter or (box[2] - box[0]) * H / 1000 >= 0.5 * letter


def make(up, blocks, words):
    """The page's clickable units and how many of each kind: {units, made}."""
    H, W = up.shape[:2]
    blocks = [b for b in blocks or [] if b.get("box") and b.get("kind") not in boxes.DESCRIBED]
    un, ink = ink_of(up)
    reads = [on_ink(words, ink), on_ink(read_unruled(un), ink)]
    units = boxes.match(blocks, reads, (W, H))
    for u in units:                                   # on their ink: a line's height is its letters', not the
        u["box"] = tighten(u["box"], ink, W, H)       # underline Tesseract's box took in
    letter, cw = sizes(units, W, H)
    for u in units:
        u["box"] = tighten(u["box"], ink, W, H, letter, len(u["text"]) * cw)
    units = [u for u in units if _lettered(u["box"], letter, H)]
    spots, gap_words = [u["box"] for u in units], []
    for b, run, rect, _ in gaps(units, blocks, W, H):
        ws = read_gap(rect, un)
        gap_words += ws
        for u in boxes.place(b, run, ws, (W, H), spots):
            u["box"] = tighten(u["box"], ink, W, H, letter, len(u["text"]) * cw)
            if _lettered(u["box"], letter, H):
                units.append(u)
    units += between(gaps(units, blocks, W, H), units, ink, reads + [gap_words], W, H, letter)
    for n, u in enumerate(units):
        u["id"] = f"w{n}"
    made = {}
    for u in units:
        made[u["match"]] = made.get(u["match"], 0) + 1
    return {"units": units, "made": made}


def coverage(blocks, units):
    """How many of the copy's tokens have a box: {kind of line: [with a box, tokens]} (printed text and table
    headers as 'printed', table rows apart)."""
    got = {(u["block"], u["i"]) for u in units if u.get("block")}
    out = {}
    for b in blocks or []:
        if not b.get("box"):
            continue
        k = {"table_header": "printed"}.get(b.get("kind"), b.get("kind") or "printed")
        for i, (t, _, _) in enumerate(boxes.tokens(b)):
            if flat(t):
                c = out.setdefault(k, [0, 0])
                c[0] += (b["id"], i) in got
                c[1] += 1
    return out


def store(bid, pages=None):
    """Make the boxes for stored pages that have a transcript (no model call), and save them."""
    from psycopg.types.json import Json
    from worker import main as v1
    with db.connect() as c:
        ps = c.execute("""SELECT page_no, upright_path, transcript, transcript_version, ocr_words FROM staging.page
                           WHERE batch_id=%s AND transcript IS NOT NULL AND ocr_words IS NOT NULL
                             AND (%s::int[] IS NULL OR page_no = ANY(%s)) ORDER BY page_no""",
                       (bid, pages, pages)).fetchall()
    for p in ps:
        up = v1.load(p["upright_path"])
        got = make(up, p["transcript"], p["ocr_words"])
        with db.connect() as c:
            c.execute("UPDATE staging.page SET pick=%s WHERE batch_id=%s AND page_no=%s",
                      (Json({"v": version(p["transcript_version"]), **got}), bid, p["page_no"]))
        print(f"p{p['page_no']}: {len(got['units'])} boxes {got['made']}", flush=True)


def report(bid, pages=None):
    """Coverage of the copy's tokens by the stored boxes, per page and in all."""
    with db.connect() as c:
        ps = c.execute("""SELECT page_no, transcript, pick FROM staging.page WHERE batch_id=%s AND pick IS NOT NULL
                            AND (%s::int[] IS NULL OR page_no = ANY(%s)) ORDER BY page_no""",
                       (bid, pages, pages)).fetchall()
    tot = {}
    for p in ps:
        cov = coverage(p["transcript"], p["pick"]["units"])
        for k, (a, n) in cov.items():
            tot.setdefault(k, [0, 0])
            tot[k][0] += a
            tot[k][1] += n
        print(f"p{p['page_no']}: " + "  ".join(f"{k} {a}/{n}" for k, (a, n) in sorted(cov.items())))
    print("all: " + "  ".join(f"{k} {a}/{n} = {a / max(1, n):.0%}" for k, (a, n) in sorted(tot.items())))


def _pages(s):
    return sorted({n for part in s.split(",") for n in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1)
                                                       if "-" in part else [int(part)])}) if s else None


if __name__ == "__main__":
    if sys.argv[1] == "report":
        report(sys.argv[2], _pages(sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        store(sys.argv[1], _pages(sys.argv[2] if len(sys.argv) > 2 else None))
