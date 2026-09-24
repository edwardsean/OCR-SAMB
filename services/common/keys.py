"""Keys for grouping (phase 6), derived by plain code from the AI OCR fields and the QR code.

The AI OCR invents plausible digits on faint pages (measured: page 8 → SOR20110258010, real SOR26110256810),
so every key says HOW it is confirmed. Grouping may only use confirmed keys.
  confirmed_by = "qr"         the page's SOR QR code says the same
               = "ocr_text"   the value also appears in Tesseract's independent reading
               = None         unconfirmed: nobody else saw it
"""
import re

from common import verify

SOR = re.compile(r"^SOR\d{11}$")


def flat(s):
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


def in_text(value, classical_text):
    """Is this identifier also in Tesseract's text? Same rule as phase 5 (common/verify.py): letters+digits only,
    within one line or a line and the next."""
    return verify.found(value, verify.windows(classical_text))


def val(fields, name):
    x = (fields or {}).get(name)
    return (x or {}).get("value") if isinstance(x, dict) else None


def key(value, classical_text, qr=None, kind=None):
    if not value:
        return None
    v = re.sub(r"\s", "", value)
    k = {"value": v, "confirmed_by": None, "problems": []}
    if kind == "sor" and not SOR.match(v):
        k["problems"].append("not SOR + 11 digits")
    if qr and kind == "sor":
        if v == qr:
            k["confirmed_by"] = "qr"
        else:
            k["problems"].append(f"QR says {qr}")
    if not k["confirmed_by"] and in_text(v, classical_text):
        k["confirmed_by"] = "ocr_text"
    return k


def derive(doc_type, fields, classical_text, qr_text):
    qr = qr_text if qr_text and SOR.match(qr_text) else None
    out = {}
    if doc_type == "FP":
        out["sor"] = key(val(fields, "sor"), classical_text, qr, "sor") or ({"value": qr, "confirmed_by": "qr", "problems": []} if qr else None)
        out["po_no"] = key(val(fields, "nomor_cpo"), classical_text)
    elif doc_type == "TTG":
        ref = val(fields, "no_ref")
        out["sor"] = key(ref, classical_text, kind="sor") if ref and flat(ref).startswith("SOR") else None
        out["po_no"] = key(val(fields, "purchase_order_no"), classical_text)
        out["document_no"] = key(val(fields, "document_no"), classical_text)
    elif doc_type == "PO":
        out["po_no"] = key(val(fields, "purchase_order_no"), classical_text)
    elif doc_type == "FPJ":
        s = val(fields, "sor")
        out["sor"] = key(s, classical_text, kind="sor") if s else None
        out["billing_no"] = key(val(fields, "billing_number"), classical_text)
    return {k: v for k, v in out.items() if v}
