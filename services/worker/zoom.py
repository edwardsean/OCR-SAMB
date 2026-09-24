"""vlm-first: Tesseract takes a second, closer look at the exact spot of a value.

The whole-page reading misses many values on these 1-bit scans: on pages 1–31, 25 of the 26 values v1 sent to a
person were spots where Tesseract had read nothing. Here Tesseract re-reads just that spot: cropped with a margin,
cleaned ("close" reconnects dotted print), enlarged 3×, read as one line (psm 7) and as a block (psm 6).
Tesseract is never told the value, so it stays an independent witness: the value counts as printed only if
verify.found finds it in what Tesseract read there (same rule as the whole page: no "close enough", never
inside a longer number).

Where is the spot?
  1. the AI OCR's box for that value (vlm-first asks Gemini where it read each value), else
  2. the Tesseract line holding the longest run (4+ letters/digits) of the value, else
  3. the Tesseract line holding one of the field's printed labels ("Nomor CPO", "No PO"…), from the label rightwards.

  python -m worker.zoom report <batch_id> [pages]   try it on v1's stored readings (read-only, no model calls)
"""
import io
import os
import re
import sys

import cv2
import numpy as np

from common import verify
from worker import enhance

ZOOM = 3
MIN_RUN = 4
PAD = (24, 14)          # px added left/right and top/bottom around the spot


def flat(s):
    return verify.flat(s)


def lines_of(words):
    """Tesseract words [text, conf, x, y, w, h] grouped into visual lines, left to right."""
    if not words:
        return []
    med_h = sorted(w[5] for w in words)[len(words) // 2] or 20
    lines = []                                           # [centre y, [words]]
    for w in sorted(words, key=lambda w: w[3] + w[5] / 2):
        cy = w[3] + w[5] / 2
        if lines and abs(lines[-1][0] - cy) <= med_h * 0.6:
            lines[-1][1].append(w)
            lines[-1][0] = sum(x[3] + x[5] / 2 for x in lines[-1][1]) / len(lines[-1][1])
        else:
            lines.append([cy, [w]])
    return [sorted(ws, key=lambda w: w[2]) for _, ws in lines]


def rect_of(ws, x0=None, x1=None):
    return (min(w[2] for w in ws) if x0 is None else x0, min(w[3] for w in ws),
            max(w[2] + w[4] for w in ws) if x1 is None else x1, max(w[3] + w[5] for w in ws))


def longest_run(a, b):
    """Length of the longest common substring of two strings."""
    best, prev = 0, [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def by_ai_box(box, shape):
    """Gemini's box: [ymin, xmin, ymax, xmax] on a 0–1000 scale."""
    if not box or len(box) != 4:
        return None
    h, w = shape[:2]
    y0, x0, y1, x1 = (max(0, min(1000, int(v))) for v in box)
    if y1 <= y0 or x1 <= x0:
        return None
    return (x0 * w // 1000, y0 * h // 1000, x1 * w // 1000, y1 * h // 1000)


def by_words(words, source):
    s = flat(source)
    best, at = 0, None
    for ws in lines_of(words):
        for w in ws:
            run = longest_run(s, flat(w[0]))
            if run > best:
                best, at = run, ws
    return rect_of(at) if at and best >= MIN_RUN else None


def by_label(words, labels, page_width):
    for label in labels:
        fl = flat(re.sub(r"\(.*?\)|\[.*?\]", "", label))
        if len(fl) < 3:
            continue
        for ws in lines_of(words):
            if fl in flat("".join(w[0] for w in ws)):
                start = next((w for w in ws if flat(w[0]) and fl.startswith(flat(w[0])[:3])), ws[0])
                return rect_of(ws, x0=start[2], x1=page_width)
    return None


def locate(img, words, source, labels, box=None):
    """(rect, how) for the spot of one value, or (None, None)."""
    r = by_ai_box(box, img.shape)
    if r:
        return r, "ai_box"
    r = by_words(words, source)
    if r:
        return r, "tesseract_words"
    r = by_label(words, labels, img.shape[1])
    if r:
        return r, "label"
    return None, None


def reread(img, rect):
    """What Tesseract reads at this spot, zoomed: one line (psm 7) and a block (psm 6)."""
    x0, y0, x1, y1 = rect
    h, w = img.shape[:2]
    crop = img[max(0, y0 - PAD[1]):min(h, y1 + PAD[1]), max(0, x0 - PAD[0]):min(w, x1 + PAD[0])]
    if crop.size == 0:
        return []
    big = cv2.resize(enhance.v_close(crop), None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_CUBIC)
    out = []
    for psm in (7, 6):
        d = enhance.ocr_data(big, psm)
        out.append(enhance.text_from_words(d) if psm == 6 else " ".join(t for t in d["text"] if t.strip()))
    return out


def lev(a, b):
    """Edit distance."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def loose(s):
    """Letters and digits, plus the symbols Tesseract writes for broken letters ('$' or '§' for S), so that
    '$10232' stays a different reading of '510232' instead of shrinking to '10232'."""
    return re.sub(r"[^0-9A-Z$§]", "", str(s or "").upper())


def disagreement(source, tokens):
    """A reading of the same slot that is ALMOST this value: same length give or take one character, 1–2 characters
    different ('$10232' for '510232', '9043773365' for '5043773365'). A reading that merely contains the value, or is
    part of it (a neighbouring word, a crop that cut the end off), is not a disagreement. Returns it, or None."""
    s = loose(source)
    if len(s) < MIN_RUN:
        return None
    n = len(str(source).split())
    for size in range(max(1, n - 1), n + 2):     # the value read as one word more or fewer
        for k in range(len(tokens) - size + 1):
            t = loose("".join(tokens[k:k + size]))
            if t and s not in t and t not in s and abs(len(t) - len(s)) <= 1 and lev(s, t) <= 2:
                return " ".join(tokens[k:k + size])
    return None


def band(words, rect):
    """The whole-page reading's words at this spot, left to right."""
    x0, y0, x1, y1 = rect
    return [w[0] for w in sorted(words or [], key=lambda w: w[2])
            if w[3] + w[5] / 2 >= y0 - PAD[1] and w[3] + w[5] / 2 <= y1 + PAD[1]
            and w[2] + w[4] >= x0 - PAD[0] and w[2] <= x1 + PAD[0]]


def conflict(source, texts, whole_page_band):
    """Tesseract read this spot two different ways: one of its readings is almost, but not exactly, the value.
    Measured on page 22: printed S10232, Gemini read 510232, the zoomed read also 510232, but the whole-page read
    '$10232'. Two readers can share a mistake, so the zoomed read only counts when no reading of the spot disagrees.
    (The other way round is not applied: zoomed reads misread often (p1 3190721 for 5190721, p9 214436 for 214438),
    so a zoomed read never cancels a value the whole-page reading already backs.)"""
    for tokens in [whole_page_band] + [t.split() for t in texts or []]:
        other = disagreement(source, tokens)
        if other:
            return other
    return None


def confirm(source, texts, whole_page_band=()):
    """Pure. (ok, why): the value's printed form is in what Tesseract read zoomed in at its spot, and no reading of
    that spot (zoomed, or the whole-page reading there) disagrees with it."""
    if not verify.found(source, verify.windows("\n".join(texts or []))):
        return False, "not found zoomed in either"
    other = conflict(source, texts, whole_page_band)
    if other:
        return False, f"Tesseract read this spot two ways: {other!r} and {source!r}"
    return True, "found zoomed in, and no reading of the spot disagrees"


# ---------------------------------------------------------------------------------------- trial on v1's readings

def _bend(s):
    for i, ch in enumerate(s):
        if ch.isdigit():
            return s[:i] + str((int(ch) + 1) % 10) + s[i + 1:]
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ("C" if ch.upper() == "B" else "B") + s[i + 1:]
    return s + "1"


def report(batch_id, pages=None):
    import psycopg
    from PIL import Image
    from psycopg.rows import dict_row

    from common import storage
    from common.fields import CANON, TYPE_MAP

    with psycopg.connect(os.environ["MAIN_DATABASE_URL"], row_factory=dict_row) as m:   # read-only
        rows = m.execute("""SELECT p.page_no, p.doc_type::text AS t, p.fields, p.ocr_words, p.upright_path,
                                   f.field_path, f.status, f.confirmed_by
                              FROM staging.field_check f JOIN staging.page p USING (batch_id, page_no)
                             WHERE f.batch_id=%s AND f.field_path LIKE 'header.%%' AND f.status='check'
                               AND (%s OR p.page_no = ANY(%s)) ORDER BY p.page_no, f.field_path""",
                         (batch_id, not pages, pages or [])).fetchall()
    images, turned, stayed, bent_ok = {}, [], [], []
    for r in rows:
        name = r["field_path"].split(".", 1)[1]
        canon = {v: k for k, v in TYPE_MAP[r["t"]].items()}[name]
        f = r["fields"][name]
        if r["page_no"] not in images:
            o = storage.client().get_object(storage.bucket(), r["upright_path"])
            images[r["page_no"]] = np.array(Image.open(io.BytesIO(o.read())).convert("L"))
        img = images[r["page_no"]]
        rect, how = locate(img, r["ocr_words"], f["source_text"], CANON[canon]["printed_as"])
        texts = reread(img, rect) if rect else []
        near = band(r["ocr_words"], rect) if rect else []
        line = f"p{r['page_no']:>2} {r['t']:<3} {name:<18} {f['source_text']!s:<32} spot: {how or 'not found':<15} "
        ok, why = confirm(f["source_text"], texts, near) if rect else (False, "no spot found")
        if ok:
            turned.append((r["page_no"], name))
            caught = not confirm(_bend(f["source_text"]), texts, near)[0]
            if not caught:
                bent_ok.append((r["page_no"], name))
            print(line + f"✅ {why} (one digit changed: {'caught' if caught else 'NOT caught'})")
        else:
            stayed.append((r["page_no"], name))
            print(line + f"⚠ {why}" + (f" · zoomed read: {texts[0][:50]!r}" if texts and "not found" in why else ""))
    print(f"\nwere ⚠: {len(turned) + len(stayed)} · now ✅ by the zoomed read: {len(turned)} · still ⚠: {len(stayed)} "
          f"· one-digit changes that still passed: {len(bent_ok)}")
    return {"turned": turned, "stayed": stayed, "bent_ok": bent_ok}


if __name__ == "__main__":
    if sys.argv[1] == "report":
        from worker.clone import pages_arg
        report(sys.argv[2], pages_arg(sys.argv[3]) if len(sys.argv) > 3 else None)
