"""Phase 2: make a scanned page readable and read it with Tesseract (no AI).

Steps, in order:
  measure   black %, solid black bands, dotted-print score
  upright   Tesseract orientation (0/90/180/270), double-checked when it proposes a turn
  straight  small-angle deskew by projection profile
  mask      blank solid black feeder bands so they don't turn into garbage text
  variants  a few cleanups, each scored on the header area; the best one reads the page
  qr        decode the QR code if the page has one
"""
import re
import time

import cv2
import numpy as np
import pytesseract
from PIL import Image

from worker import orient

ENHANCE_VERSION = 2      # 2: sideways test on text lines (worker/orient.py). Bump when enhance/OCR changes, so re-runs redo it; otherwise re-runs reuse the stored result
LANG = "ind+eng"
CONFIDENT = 70            # a word read at >= this confidence counts as "confident"

# Thresholds calibrated on the sample; see calibrate.py and the phase 2 notes.
DARK_BAND_FLAG = 0.06     # >= 6% of rows solid black
FAINT_CONF = 55           # mean word confidence below this = faint: Tesseract can't reliably see the print
POOR_CONF = 40            # mean word confidence below this …
POOR_CHARS = 120          # … or fewer confident characters than this → poor_quality
SIDEWAYS_RATIO = 1.2      # column/row profile variance above this = text runs sideways
SKEW_FLAG = 1.0           # smaller tilts are still corrected, just not flagged


# ------------------------------------------------------------------ measure

def measure(a):
    ink = a < 128
    black_ratio = float(ink.mean())
    band_rows = ink.mean(axis=1) > 0.85
    band_cols = ink.mean(axis=0) > 0.85
    return black_ratio, float(band_rows.mean()), band_rows, band_cols


def speckle_ratio(a):
    """Faint print scanned in black-and-white breaks into dots. Share of ink blobs that are tiny."""
    ink = (a < 128).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    areas = areas[areas < 5000]            # ignore big blobs: bands, logos, lines
    if len(areas) < 200:
        return 0.0
    return float((areas <= 6).mean())


def mask_bands(a, band_rows, band_cols):
    b = a.copy()
    b[band_rows, :] = 255
    b[:, band_cols] = 255
    return b


# ------------------------------------------------------------------ orientation + skew

def osd(a):
    """Tesseract's suggestion: degrees to rotate clockwise, and its confidence."""
    small = cv2.resize(a, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    try:
        out = pytesseract.image_to_osd(Image.fromarray(small), config="--psm 0 -c min_characters_to_try=10")
        rot = int(re.search(r"Rotate: (\d+)", out).group(1))
        conf = float(re.search(r"Orientation confidence: ([\d.]+)", out).group(1))
        return rot, conf
    except Exception:
        return 0, 0.0


def rotate_cw(a, deg):
    return {0: a, 90: cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE),
            180: cv2.rotate(a, cv2.ROTATE_180), 270: cv2.rotate(a, cv2.ROTATE_90_COUNTERCLOCKWISE)}[deg]


def quick_score(a):
    """Confident characters on a downscaled page: cheap way to compare two orientations."""
    small = cv2.resize(a, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    d = pytesseract.image_to_data(Image.fromarray(small), lang="eng", config="--psm 3",
                                  output_type=pytesseract.Output.DICT)
    return sum(len(w) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= CONFIDENT)


def line_direction(a):
    """Text lines make the row profile spiky. If columns are spikier than rows, the text runs sideways."""
    small = cv2.resize(a, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    ink = (small < 128).astype(np.float32)
    rows, cols = float(np.var(ink.sum(axis=1))), float(np.var(ink.sum(axis=0)))
    return rows, cols


def upright(a):
    """0/90/180/270. Sideways-or-not comes from the text-line test (worker/orient.py): measured on all 288 sample pages,
    upright pages scored <= 1.24 and sideways pages >= 6.0. The old whole-page ink test turned 10 upright pages
    sideways (table ruling lines fooled it) and missed 2 sideways ones. A quick read then picks 90 vs 270, which it
    separates well (page 11: 46 vs 771 confident chars). Upside down only if OSD is confident and it reads better."""
    rot, conf = osd(a)
    if orient.text_direction(a)[2] > orient.SIDEWAYS:
        best = max((90, 270), key=lambda r: quick_score(rotate_cw(a, r)))
        return rotate_cw(a, best), best, conf
    if rot == 180 and conf >= 1.5 and quick_score(rotate_cw(a, 180)) > quick_score(a):
        return rotate_cw(a, 180), 180, conf
    return a, 0, conf


def deskew(a):
    small = cv2.resize(a, (1000, int(a.shape[0] * 1000 / a.shape[1])), interpolation=cv2.INTER_AREA)
    ink = (small < 128).astype(np.uint8) * 255
    h, w = ink.shape
    best, best_score = 0.0, -1.0
    for ang in np.arange(-4.0, 4.01, 0.25):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        r = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        s = float(np.var(r.sum(axis=1)))
        if s > best_score:
            best, best_score = float(ang), s
    if abs(best) < 0.3:
        return a, 0.0
    m = cv2.getRotationMatrix2D((a.shape[1] / 2, a.shape[0] / 2), best, 1.0)
    return cv2.warpAffine(a, m, (a.shape[1], a.shape[0]), flags=cv2.INTER_LINEAR, borderValue=255), best


# ------------------------------------------------------------------ variants
# Measured on the sample (pages 1, 4, 8, 11, 14): read at 2x, "close" was best or near-best on every page,
# and scoring variants on the header at 1x did NOT predict the full-page winner (page 11: header tie,
# full page 73 vs 48 vs 38). So: always read "close"; only on a weak read also try "smooth" and keep the better.

def v_original(a):
    return a


def v_smooth(a):
    return cv2.GaussianBlur(a, (0, 0), 1.0)


def v_close(a):
    k = np.ones((3, 3), np.uint8)
    return cv2.GaussianBlur(cv2.dilate(cv2.erode(a, k), k), (0, 0), 1.0)   # erode grows ink: reconnects dots


VARIANTS = {"original": v_original, "smooth": v_smooth, "close": v_close}
UPSCALE = 2


def upscale(a):
    return cv2.resize(a, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)


def ocr_data(img, psm):
    return pytesseract.image_to_data(Image.fromarray(img), lang=LANG, config=f"--psm {psm}",
                                     output_type=pytesseract.Output.DICT)


def summarize(d, scale):
    words = []
    for t, c, x, y, w, h in zip(d["text"], d["conf"], d["left"], d["top"], d["width"], d["height"]):
        c = float(c)
        if t.strip() and c >= 0:
            words.append([t, round(c), round(x / scale), round(y / scale), round(w / scale), round(h / scale)])
    # Confidence over real words only (2+ letters/digits). Table borders read as "|" or "—" score low and
    # dragged table-heavy pages below the faint threshold even when their text read well.
    real = [w for w in words if len(re.sub(r"[^0-9A-Za-z]", "", w[0])) >= 2]
    conf = sum(w[1] for w in real) / len(real) if real else 0.0
    chars = sum(len(w[0]) for w in real if w[1] >= CONFIDENT)
    return words, conf, chars


def read_page(a):
    """Read with "close" at 2x; if that read is weak, also try "smooth" and keep whichever has more confident chars."""
    tried = {}
    for name in ("close", "smooth"):
        clean = VARIANTS[name](a)
        d = ocr_data(upscale(clean), 3)
        words, conf, chars = summarize(d, UPSCALE)
        tried[name] = {"clean": clean, "d": d, "words": words, "conf": conf, "chars": chars}
        if conf >= FAINT_CONF:
            break
    best = max(tried, key=lambda n: (tried[n]["chars"], tried[n]["conf"]))
    scores = {n: {"confident_chars": t["chars"], "mean_conf": round(t["conf"], 1)} for n, t in tried.items()}
    return best, scores, tried[best]


def text_from_words(d):
    """Rebuild reading-order text with line breaks from Tesseract's block/par/line numbering."""
    lines, cur, key = [], [], None
    for t, b, p, l in zip(d["text"], d["block_num"], d["par_num"], d["line_num"]):
        k = (b, p, l)
        if k != key and cur:
            lines.append(" ".join(cur)); cur = []
        key = k
        if t.strip():
            cur.append(t)
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(x for x in lines if x.strip())


# ------------------------------------------------------------------ qr

def qr(a):
    """The Faktur Penjualan prints its QR in the top quarter, beside the Sales Order block.
    Its modules are dotted like the text, so one decoder on the raw scan found only about half.
    Measured on pages 1, 3, 10, 13, 17, 20, 23, 26: trying these in order (stop at first hit) decoded all 8."""
    h, _ = a.shape
    region = a[: h // 4, :]
    std, aruco = cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()
    closed = v_close(region)
    attempts = (
        (std, region), (std, cv2.GaussianBlur(region, (0, 0), 1.2)), (std, closed), (aruco, closed),
        (std, upscale(closed)), (aruco, upscale(closed)),
        (std, upscale(cv2.GaussianBlur(region, (0, 0), 1.5))), (aruco, upscale(cv2.GaussianBlur(region, (0, 0), 1.5))),
    )
    for det, img in attempts:
        try:
            txt, _, _ = det.detectAndDecode(img)
        except cv2.error:
            txt = ""
        if txt:
            return txt
    return None


# ------------------------------------------------------------------ the whole page

def prepare(original):
    """Image preparation only: dark bands, upright, straighten, QR. Tesseract is used here only for the quick
    orientation check (90° vs 270°), never as a reading. Returns (upright image, work image for reading, result)."""
    t0 = time.time()
    black_ratio, band_ratio, band_rows, band_cols = measure(original)
    work = mask_bands(original, band_rows, band_cols)

    work, rotation, osd_conf = upright(work)
    up_img = rotate_cw(original, rotation) if rotation else original
    work, skew = deskew(work)
    if skew:
        m = cv2.getRotationMatrix2D((up_img.shape[1] / 2, up_img.shape[0] / 2), skew, 1.0)
        up_img = cv2.warpAffine(up_img, m, (up_img.shape[1], up_img.shape[0]), borderValue=255)

    flags = []
    if rotation: flags.append("rotated")
    if abs(skew) >= SKEW_FLAG: flags.append("skewed")
    if band_ratio >= DARK_BAND_FLAG: flags.append("dark_band")
    result = {
        "rotation": rotation, "osd_conf": round(osd_conf, 2), "skew_angle": round(skew, 2),
        "black_ratio": round(black_ratio, 3), "dark_band_ratio": round(band_ratio, 3),
        "speckle_ratio": round(speckle_ratio(work), 3), "qr_text": qr(work), "quality_flags": flags,
        "ms_prepare": int((time.time() - t0) * 1000),
    }
    return up_img, work, result


def read(work):
    """The Tesseract reading of a prepared page. Returns (cleaned image, result)."""
    t0 = time.time()
    best, scores, r = read_page(work)
    conf, chars = r["conf"], r["chars"]
    flags = []
    if conf < FAINT_CONF: flags.append("faint")
    if conf < POOR_CONF or chars < POOR_CHARS: flags.append("poor_quality")
    return r["clean"], {"ocr_variant": best, "variant_scores": scores, "ocr_conf": round(conf, 2),
                        "confident_chars": chars, "ocr_words": r["words"], "classical_text": text_from_words(r["d"]),
                        "quality_flags": flags, "ms_read": int((time.time() - t0) * 1000)}


def process(original):
    """v1: prepare + read in one step. original: 2-D uint8 array, 0 = ink. Returns (upright image, cleaned image, result)."""
    t0 = time.time()
    up_img, work, prep = prepare(original)
    clean, rd = read(work)
    result = {k: v for k, v in {**prep, **rd}.items() if k not in ("ms_prepare", "ms_read")}
    result["quality_flags"] = prep["quality_flags"] + rd["quality_flags"]
    result["ms_enhance_ocr"] = int((time.time() - t0) * 1000)
    return up_img, clean, result
