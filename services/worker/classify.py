"""Phase 3: what kind of document is this page?  (§06 capability 1, Klasifikasi)

Independent votes, then a decision that prefers "unsure" to a guess:
  keyword  document title words in the top of the page (no AI)
  jev      TypeSafe Jev Choice over the page text (text only, never the image)
  layout   similarity to SAMB's Faktur Penjualan print layout (survives faint print)
  qr       a decoded SOR QR code: only a Faktur Penjualan carries one

A decided type is safe to group on. "unsure" breaks the chain in grouping (hold rule 2) and is shown
to a person with the best guess.
"""
import json
import os
import re
import time

import httpx

from worker import layout

CLASSIFY_VERSION = 2      # 2: Jev leads; FP needs a second witness; title words fixed (2026-09-24)
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
LAYOUT_FP = 0.70          # calibrated on sample pages 1-32 (FP 0.74-0.85, others <= 0.63); a vote, never decisive alone
JEV_ALONE = 0.85          # Jev decides alone at this confidence if no printed title contradicts it (simulated on all 288 pages)
JEV_MIN = 0.50            # below this the docs say route to a human
SOR_RE = re.compile(r"^SOR\d{11}$")

FP_TEMPLATE = json.load(open(os.path.join(os.path.dirname(__file__), "fp_layout.json")))

# Title words per type. Customer names for the same document differ (see CLAUDE.md: TTG has many names).
KEYWORDS = {
    "FP":  ["FAKTUR PENJUALAN"],
    "TTG": ["RECEIVING SLIP", "GOODS RECEIVE", "GOOD RECEIPT", "GOODS RECEIPT", "TANDA TERIMA", "BUKTI PENERIMAAN BARANG",
            "RECEIVING NOTE", "RECEIVED NOTE", "PRODUCT RECEIPT"],
    "PO":  ["PURCHASE ORDER", "SURAT PESANAN"],
    "SJ":  ["SURAT JALAN", "DELIVERY NOTE", "DELIVERY ORDER"],
    "FPJ": ["FAKTUR PAJAK"],
    "PEL": ["BUKTI PEMBAYARAN", "PAYMENT ADVICE", "REMITTANCE", "PELUNASAN"],
}

JEV_TYPES = {  # Jev option key -> our doc_type
    "faktur_penjualan": "FP", "tanda_terima": "TTG", "purchase_order": "PO", "surat_jalan": "SJ",
    "faktur_pajak": "FPJ", "pelunasan": "PEL", "continuation": "CONTINUATION", "other": "OTHER",
}
JEV_QUESTION = {
    "doc_type": {
        "type": "choice",
        "instructions": (
            "This is the OCR text of ONE scanned page from an Indonesian distributor's accounts-receivable paperwork "
            "(the distributor is PT Sarana Abadi Makmur Bersama, SAMB). `title_zone` is the text near the top of the "
            "page, `page_text` is the whole page, `footer` is the bottom line. OCR text can be noisy. "
            "Which kind of document is this page?"
        ),
        "criteria": {
            "faktur_penjualan": {"what": "SAMB's own sales invoice: title FAKTUR PENJUALAN, a Sales Order [SO] # starting SOR, "
                                         "Kepada (customer), item table with Kode, Nama Produk, Kemasan, Harga, Disc columns"},
            "tanda_terima": {"what": "The customer's receipt of goods from SAMB",
                             "examples": ["Receiving Slip Order", "Goods Receive Note", "Good Receipt", "Tanda Terima"]},
            "purchase_order": {"what": "The customer's purchase order to SAMB",
                               "examples": ["Purchase Order", "Surat Pesanan"]},
            "surat_jalan": {"what": "Delivery note that travelled with the goods", "examples": ["Surat Jalan", "Delivery Note"]},
            "faktur_pajak": {"what": "Indonesian tax invoice", "not_for": "a page that only mentions faktur pajak in small print"},
            "pelunasan": {"what": "Customer payment / remittance document listing invoices paid"},
            "continuation": {"what": "A later page of a multi-page document: it has NO document title or header of its own, "
                                     "only continued table rows or totals",
                             "not_for": "a page that has its own title, even if its footer says Page 5 of 35"},
            "other": {"what": "Anything else, or too unreadable to tell"},
        },
    }
}


def title_segments(words, height, zone=0.35, max_words=6):
    """Short line segments in the top of the page. A title is a short line; body sentences are long.
    Words are grouped into lines by vertical centre, then lines are split where a big horizontal gap
    separates columns (headers often share a row with an address block)."""
    top = [w for w in words if w[3] < height * zone]
    if not top:
        return []
    med_h = sorted(w[5] for w in top)[len(top) // 2] or 20
    lines = []   # [mean centre, words]
    for w in sorted(top, key=lambda w: w[3] + w[5] / 2):
        cy = w[3] + w[5] / 2
        if lines and abs(lines[-1][0] - cy) <= max(med_h, w[5]) * 0.6:
            ws = lines[-1][1]; ws.append(w)
            lines[-1][0] = sum(x[3] + x[5] / 2 for x in ws) / len(ws)   # running centre, not the first word's
        else:
            lines.append([cy, [w]])
    segs = []
    for _, ws in lines:
        ws.sort(key=lambda w: w[2])
        cur = [ws[0]]
        for a, b in zip(ws, ws[1:]):
            if b[2] - (a[2] + a[4]) > max(a[5], b[5], med_h) * 3:   # big title fonts have wide word gaps
                segs.append(cur); cur = []
            cur.append(b)
        segs.append(cur)
    return [" ".join(w[0] for w in seg) for seg in segs if len(seg) <= max_words]


def keyword_vote(words, height):
    """Title words only: short line segments in the top 35%. Body sentences mention other documents
    (Hari Hari's receiving slip says 'dilampirkan dengan faktur asli dan faktur pajak')."""
    hits = set()
    for seg in title_segments(words, height):
        if ":" in seg:          # "Purchase Order : 3014006217" is a field label, not a title
            continue
        t = re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", seg.upper()))
        hits |= {k for k, keys in KEYWORDS.items() if any(x in t for x in keys)}
    # Two different titles means we can't trust the title words on this page: abstain rather than conflict.
    return hits.pop() if len(hits) == 1 else None


def jev_vote(words, height, text, footer):
    state = {
        "title_zone": " ".join(w[0] for w in words if w[3] < height * 0.35)[:1500],
        "page_text": (text or "")[:3000],
        "footer": footer or "",
    }
    return jev_ask(state)


def jev_ask(state, questions=None):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {"skipped": "no TYPESAFE_API_KEY"}
    body = {"model": JEV_MODEL, "state": state, "questions": questions or JEV_QUESTION}   # vlm-first passes its own
    for attempt in range(5):
        r = httpx.post(JEV_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        if r.status_code in (429, 529, 502, 503):
            time.sleep(2 ** attempt); continue          # docs: throttling, back off
        r.raise_for_status()
        a = r.json()["answers"]["doc_type"]
        return {"choice": JEV_TYPES[a["choice"]], "confidence": round(a["confidence"], 3),
                "probabilities": {JEV_TYPES[k]: round(v, 3) for k, v in a["probabilities"].items()},
                "model": r.json().get("model"), "tokens": r.json().get("usage")}
    return {"error": f"HTTP {r.status_code} after retries"}


def decide(votes, flags):
    """Returns (status, type, guess, reason). Jev leads; nothing is guessed.
    Simulated on all 288 sample pages before adoption (2026-09-24): unsure 73 -> 54, 0 wrong on pages 1-32.

    An FP starts a bundle, so it needs TWO witnesses: Jev, plus the QR, the FP print layout, or the printed
    title FAKTUR PENJUALAN. Measured why: Jev said FP at 0.88 on SAMB's own handwritten SALES ORDER form (page 68)."""
    kw, jev, lay, qr = votes["keyword"], votes["jev"], votes["layout"], votes["qr_sor"]
    jc, jconf = jev.get("choice"), jev.get("confidence", 0.0)
    fp_layout = lay >= LAYOUT_FP

    # 1. A decoded SOR QR code: only a Faktur Penjualan has one. A text vote naming another type -> unsure.
    if qr:
        if jc in (None, "FP", "OTHER"):
            return "decided", "FP", "FP", "SOR QR code"
        return "unsure", None, "FP", f"QR says FP but Jev says {jc}"
    # 2. FP needs Jev plus a non-Jev witness.
    if jc == "FP" or kw == "FP":
        if jc == "FP" and jconf >= JEV_MIN and (fp_layout or kw == "FP"):
            return "decided", "FP", "FP", "Jev + " + ("FP layout" if fp_layout else "FAKTUR PENJUALAN title")
        return "unsure", None, "FP", "FP needs Jev plus the QR, the FP layout or the FP title"
    # 3. Looks like SAMB's invoice, but Jev says something else.
    if fp_layout:
        return "unsure", None, jc, f"FP layout ({lay}) but Jev says {jc}"
    # 4. Printed title and Jev agree.
    if kw and kw == jc and jconf >= JEV_MIN:
        return "decided", kw, kw, "title + Jev agree"
    # 5. Jev confident, and no printed title says otherwise.
    if jc and jconf >= JEV_ALONE and kw in (None, jc):
        return "decided", jc, jc, f"Jev ≥ {JEV_ALONE}"
    # 6. Title alone, only when Jev is unavailable.
    if kw and not jc:
        return "decided", kw, kw, "title (Jev unavailable)"
    return "unsure", None, (jc or kw), "Jev not confident enough and no title to confirm it"


def classify(up, words, text, qr_text, flags):
    """up: upright page array. Returns dict for the page row."""
    h = up.shape[0]
    footer = " ".join(w[0] for w in words if w[3] > h * 0.93)[:200]
    votes = {
        "keyword": keyword_vote(words, h),
        "layout": layout.similarity(FP_TEMPLATE, layout.fingerprint(up)),
        "qr_sor": bool(qr_text and SOR_RE.match(qr_text)),
        "jev": jev_vote(words, h, text, footer),
    }
    status, doc_type, guess, reason = decide(votes, flags)
    votes["reason"] = reason
    return {"type_status": status, "doc_type": doc_type, "type_guess": guess,
            "doc_type_conf": votes["jev"].get("confidence"), "layout_score": votes["layout"],
            "footer": footer, "type_votes": votes, "classify_version": CLASSIFY_VERSION}


def second_try(first, read, flags):
    """Unsure pages only: ask Jev again with the AI OCR's reading instead of Tesseract's text.
    Deliberately NOT given the AI OCR's 'issuer' (it named SAMB as issuer of an Indomaret PO) — only what's printed."""
    title = ((read.get("title") or {}).get("value") or "") if isinstance(read.get("title"), dict) else ""
    state = {"title_zone": (title + " \n" + (read.get("transcript") or "")[:700]).strip(),
             "page_text": (read.get("transcript") or "")[:3000],
             "footer": ((read.get("page_marker") or {}) or {}).get("value") or ""}
    kw = None
    t = re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", title.upper()))
    hits = {k for k, keys in KEYWORDS.items() if any(x in t for x in keys)}
    if len(hits) == 1:
        kw = hits.pop()
    votes = {"keyword": kw, "layout": first["layout"], "qr_sor": first["qr_sor"], "jev": jev_ask(state)}
    # the AI OCR reading is clean text, so the 'weak text' guard from Tesseract no longer applies
    status, doc_type, guess, reason = decide(votes, [f for f in flags if f not in ("faint", "poor_quality")])
    votes["reason"] = "second try (AI OCR text): " + reason
    return status, doc_type, guess, votes
