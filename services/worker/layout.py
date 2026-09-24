"""Layout fingerprint: where the long ruling lines sit on the page.

SAMB prints every Faktur Penjualan from one template, so its item table has the same column lines
in the same places. Lines are thick and long, so they survive faint print that breaks the text.
"""
import cv2
import numpy as np

TOL = 0.008            # a line matches if within 0.8% of page width/height


def _positions(mask, axis, min_frac):
    """Centres of line runs along one axis, normalised 0..1."""
    prof = mask.sum(axis=axis) / 255.0
    length = mask.shape[axis]
    hits = np.where(prof >= min_frac * length)[0]
    if len(hits) == 0:
        return []
    groups, start, prev = [], hits[0], hits[0]
    for h in hits[1:]:
        if h - prev > 3:
            groups.append((start + prev) / 2); start = h
        prev = h
    groups.append((start + prev) / 2)
    n = mask.shape[1 - axis]
    return [round(g / n, 4) for g in groups]


def fingerprint(a):
    """a: upright page, 0 = ink. Returns vertical-line x positions and horizontal-line y positions (0..1)."""
    h, w = a.shape
    ink = ((a < 128) * 255).astype(np.uint8)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))             # bridge dotted, faint line pixels
    # faint pages break vertical lines into dashes: close vertically first, then keep only long runs
    vink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 41)))
    vert = cv2.morphologyEx(vink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 12)))
    horz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 6, 1)))
    return {"v": _positions(vert, 0, 0.12), "h": _positions(horz, 1, 0.25)}


def _match(tpl, got):
    if not tpl or not got:
        return 0.0
    hit_t = sum(any(abs(t - g) <= TOL for g in got) for t in tpl) / len(tpl)       # template lines found
    hit_g = sum(any(abs(t - g) <= TOL for t in tpl) for g in got) / len(got)       # page lines explained
    return 0.0 if hit_t + hit_g == 0 else 2 * hit_t * hit_g / (hit_t + hit_g)


def similarity(tpl, fp):
    """F1 of matched vertical lines, at the best horizontal shift: the feeder places pages a little left or
    right, so compare the spacing pattern, not absolute positions. Shift is tried line-to-line (±15% width)."""
    if not tpl["v"] or not fp["v"]:
        return 0.0
    shifts = {round(g - t, 4) for t in tpl["v"] for g in fp["v"] if abs(g - t) <= 0.15} | {0.0}
    best = max(_match([t + d for t in tpl["v"]], fp["v"]) for d in shifts)
    return round(best, 3)
