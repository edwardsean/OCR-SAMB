"""Phase 7a: rules on one page's verdicts, run after every witness has spoken (print, the zoomed spot, the look-again,
Satellite, a person). They only take ✅ away, except `sums`, the one way arithmetic may still confirm an FP amount.
Pure code: no database, no image, no model, and nothing about any customer. The rules come from what the fields mean
and from SAMB's own invoice format; a customer's layout is never written here (the user, 2026-09-25).

  complete   An FP amount counts as printed only in full, the way SAMB prints it: 1.126.011,00. The scan's right edge
             cuts some: page 10's PPN 106.861,53 shows as "106.86", its DPP as "971.468".
  sums       DPP + PPN = Total confirms one FP amount only when the other two are backed by something besides the AI
             (print, the QR code, Satellite, a person), exactly (±0.005), with PPN 11% or 12% of DPP (±Rp 1). On page
             29 the AI read two cut digits wrong in step (.95 → .90, .28 → .23), so its three values added up exactly:
             a sum over the AI's own values proves nothing. (v1 keeps its looser rule: verify.header(sums=True).)
  identity   Three whole FP amounts that don't add up: the ones backed only by print lose ✅. Page 8's look-again read
             a faint .80 as .86. Nothing is removed on the PPN rate: SAMB's PPN misses 11% of DPP by more than a sen
             on about 1 SO in 20 (Satellite, 2026-09-25).
  with_tax   A customer's PO total is its total including PPN, the amount an FP total is compared with. It keeps ✅
             only when the page's PPN is backed too and total − PPN, taxed at 11% or 12%, gives that PPN (±Rp 1).
             Hero's POs 18, 24 and 27 end before their TOTAL NETTO; the TOTAL NET PURCHASE above it (before tax) had
             been taken as the total.
  columns    A quantity or unit found on its table row doesn't show which column it came from. Page 12's row prints
             the cartons ordered (2) and the pieces received (144); Hari Hari prints the pack size (40.00 PC) beside
             the cartons received (1 KTN). The row's text alone never confirms one; a person or Satellite can.
  dates      No date after the day the stack was scanned.
"""
import re

from common.fields import DOCS

AMOUNTS = ("dpp", "ppn", "total")
RATES = (0.11, 0.12)
BACKED = ("text", "zoom", "second_look", "qr", "satellite", "person")   # a witness besides the AI's own reading
PRINT = ("text", "zoom", "second_look")                                 # backed by print, and by print alone
SETTLED = ("satellite", "person")                                       # never overruled here
SAMB_AMOUNT = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")                 # 1.126.011,00
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLUMN_CELLS = ("qty", "qty_crt", "qty_pcs", "uom")


def complete(source):
    """Is this FP amount printed in full, the way SAMB prints it (1.126.011,00)?"""
    return bool(SAMB_AMOUNT.match(re.sub(r"\s", "", str(source or ""))))


def _num(f):
    try:
        return float((f or {}).get("value"))
    except (TypeError, ValueError):
        return None


def _ok(v, by=None):
    return v.get("verdict") == "ok" and (by is None or v.get("by") in by)


def _drop(v, why):
    """Take a ✅ away, saying why and what had given it."""
    return {"verdict": "check", "why": f"{why} (it had ✅ by {v.get('by')})"}


def _fp_amounts(fields, h):
    for k in AMOUNTS:                                                    # complete
        v, src = h.get(k) or {}, (fields.get(k) or {}).get("source_text")
        if _ok(v, PRINT) and not complete(src):
            h[k] = _drop(v, f"{src!r} isn't a whole amount: SAMB prints two decimals (1.126.011,00), so the scan's "
                            "edge may have cut it")
    d, p, t = (_num(fields.get(k)) for k in AMOUNTS)
    exact = None not in (d, p, t) and abs(d + p - t) <= 0.005 and any(abs(d * r - p) <= 1 for r in RATES)
    for k in AMOUNTS:                                                    # sums
        v = h.get(k) or {}
        if v.get("verdict") != "check":
            continue
        if exact and all(_ok(h.get(o) or {}, BACKED) for o in AMOUNTS if o != k):
            h[k] = {"verdict": "ok", "by": "adds_up"}
        elif exact:
            h[k] = {**v, "why": f"{v.get('why')}; DPP + PPN = Total, but only the AI read the others: not proof"}
    if None not in (d, p, t) and abs(d + p - t) > 0.005 and \
            all(complete((fields.get(k) or {}).get("source_text")) for k in AMOUNTS):    # identity
        for k in AMOUNTS:
            if _ok(h.get(k) or {}, PRINT):
                h[k] = _drop(h[k], f"DPP + PPN = {d + p:,.2f}, not the Total {t:,.2f}: one of them is misread")


def _with_tax(fields, h):
    v, pv = h.get("total") or {}, h.get("ppn") or {}
    if not _ok(v) or v.get("by") in SETTLED:
        return
    t, p = _num(fields.get("total")), _num(fields.get("ppn"))
    if not _ok(pv, BACKED) or None in (t, p):
        h["total"] = _drop(v, "a PO total includes PPN, and no PPN on this page is backed to show that this one does")
    elif not any(abs(r * (t - p) - p) <= 1 for r in RATES):
        h["total"] = _drop(v, f"a PO total includes PPN: {t:,.2f} is the amount the PPN {p:,.2f} was computed on"
                              if any(abs(r * t - p) <= 1 for r in RATES) else
                              f"a PO total includes PPN: {t:,.2f} minus the PPN {p:,.2f} doesn't give that PPN back")


def _dates(doc_type, fields, h, scan_day):
    kinds = {f["name"]: f["kind"] for f in DOCS[doc_type]["header"]}
    for name, v in h.items():
        value = str((fields.get(name) or {}).get("value") or "")
        if kinds.get(name) == "date" and _ok(v) and v.get("by") not in SETTLED and ISO_DAY.match(value) \
                and value > scan_day.isoformat():
            h[name] = _drop(v, f"{value} is after the day the stack was scanned ({scan_day})")


def columns(lines):
    """Line verdicts after the column rule: a quantity or a unit backed only by its row's text loses ✅."""
    return [{c: (_drop(v, "a quantity or unit found on its row doesn't show which column it came from (ordered, "
                          "received or the pack size)") if c in COLUMN_CELLS and _ok(v, ("text",)) else v)
             for c, v in row.items()} for row in lines or []]


def apply(doc_type, fields, res, scan_day=None):
    """The page's verdicts after the rules. fields: the type's projection, as checked. scan_day: a date, or None to
    skip the date rule. Returns new verdicts; the ones given are left as they were."""
    if not res or doc_type not in DOCS:
        return res
    h = {k: dict(v) for k, v in res["header"].items()}
    if doc_type == "FP":
        _fp_amounts(fields, h)
    if doc_type == "PO":
        _with_tax(fields, h)
    if scan_day:
        _dates(doc_type, fields, h, scan_day)
    ls = columns(res.get("lines"))

    def count(vs):
        return {k: sum(v["verdict"] == k for v in vs) for k in ("ok", "check", "empty")}
    return {**res, "header": h, "lines": ls,
            "summary": {"header": count(h.values()), "lines": count([v for r in ls for v in r.values()])}}
