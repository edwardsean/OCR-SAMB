"""Phase 7c, redesigned (verification redesign S2, the user 2026-09-26): one bundle's documents checked against
Satellite, and the bundle's status. Runs after every grouping, under its lock (grouper/group.py). Plain code, no model.

Two sides, each with its own reference; documents are never evidence for each other:
  the order side     PO ↔ Satellite's SO as ordered (satellite.paper: order_total/dpp/ppn). The FP is the paper copy of
                     that order, printed once with the goods; its page is never the reference.
  the delivery side  TTG ↔ what Satellite received (satellite.received: the goods receipt, CGR, and the invoice built
                     from it). Never the FP, never the order: a tolakan lowers what was received, not the FP.
Totals decide: a usable total (read, not cut) within the allowance of its reference passes, even when only the AI
read it (Tesseract failing is no reason for a person). The rows only explain a gap, unless a document has no usable
total: then its printed row amounts decide. A value beyond the allowance follows one rule (item 11): print backs the
reading → a real difference, a person now; only the AI read it → one look-again first (the page waits, `ask`), then
a person if it still doesn't fit. A row's quantity never decides: a row doesn't show which column a number came from
(Hero's receipts print the ordered quantity too; page 9's pack size equals what was received by chance).

  sor_in_satellite  the bundle's SO is in Satellite
  docs_complete     the documents the customer sends: customer_profile.expected_docs, else FP and TTG
  vendor_is_samb    information only: the PO is linked to SAMB's SO by its number (fails only when print names another)
  fp_po_total       the order side: the PO's total (with tax, or before tax) and its PPN against the SO as ordered, up
                    to Rp 5 of rounding (the user: "accept differences under a few rupiah"); no usable total → its rows
  fp_po_lines       information: each PO row against its SO line, explaining a gap
  received          the delivery side: the receipt's total against what Satellite received; no usable total → its
                    rows; a tolakan Satellite records is named with its reason. Waits while Satellite has no complete
                    goods receipt
  dates             each receipt's date: Satellite's goods receipt date, or in order (SO date ≤ it ≤ the scan day)
  fpj               FP against the Faktur Pajak: none in this stage

Status: grouping while a page or a check waits (for the AI OCR, or for Satellite's goods receipt); auto_ok when every
page is settled and every applicable check passes; else needs_review, with the reasons. On Review (7d) a person may
accept a difference with a reason (staging.bundle_decision: it holds while the check says exactly the same), and
approve a bundle when nothing is left (can_approve): `reviewed` then stays until its fingerprint changes.
"""
import hashlib
import json
import re

from psycopg.types.json import Json

from common import db, satellite, verify
from common.fields import TYPE_MAP
from grouper import matching

ROUNDING = 5.00    # rupiah: FP and PO totals this close are the same (the user, 2026-09-25: "accept differences under a
                   # few rupiah"). Hero rounds per carton line (0.02–2.72 apart), Boots per piece with PPN (5.00 apart)
LABEL = {"sor_in_satellite": "SO in Satellite", "docs_complete": "Documents complete",
         "vendor_is_samb": "PO addressed to SAMB", "fp_po_total": "FP ↔ PO total", "fp_po_lines": "FP ↔ PO lines",
         "received": "Received vs Satellite's CGR", "dates": "Dates in order", "fpj": "FP ↔ Faktur Pajak",
         "calibration": "Customer calibration", "store_named": "The page's store is the order's"}
STEPS = (5, 10, 15, 20, 25, 30, 50, 100)   # allowances to suggest (rupiah); beyond Rp 100 a gap isn't rounding
SAMB = "SARANAABADIMAKMUR"


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _money(x):
    return f"{x:,.2f}"


def result(status, why, **more):
    return {"status": status, "why": why, **more}


def _value(page, name):
    """(value, resolved?) of a header field on a page."""
    f = ((page or {}).get("fields") or {}).get(name) or {}
    v = (((page or {}).get("checks") or {}).get("header") or {}).get(name) or {}
    return f.get("value"), v.get("verdict") == "ok"


def distinct(pages, docs, name):
    """One document per number: copies of the same PO (7000363700-03 holds PO.2026.09.32029 four times, two prints and
    two terms pages) are one PO, and summing them made its total 775,397 × 4. A document whose number wasn't read
    stays on its own: counting a copy twice only makes the total not fit, which sends the bundle to Review."""
    seen, out = set(), []
    for n in docs:
        number = verify.flat(_value(pages.get(n), name)[0])
        if number and number in seen:
            continue
        seen.add(number) if number else None
        out.append(n)
    return out


def _percents(text):
    return sorted(round(float(x), 2) for x in re.findall(r"\d+(?:\.\d+)?", str(text or "")) if float(x))


def _so_percents(s):
    return sorted(round(float(d["value"]), 2) for d in (s.get("discounts") or {}).values()
                  if d.get("type") == "percentage" and d.get("value"))


PRINTED_BY = ("text", "zoom", "second_look")      # the witnesses that are print: Tesseract backs the reading
AMOUNT_TEXT = re.compile(r"\d[\d.,]*\d")


def _asked(page):
    """The fields the look-again already asked on this page (for this reading)."""
    s = (page or {}).get("second_look") or {}
    return set(s.get("asked") or []) | set((s.get("results") or {}).keys())


def _read(pages, span, doc_type, canon):
    """A document's value as the AI read it, whatever its verdict (verification redesign, S2): from the last of its
    pages that has it, its own field (the type's projection) or, on a continuation page, that page's whole reading.
    None when no page has it. {num, value, page, first, canon, field, src, printed, cut, asked, second}:
      printed  Tesseract's text backs the reading (a print verdict, or its digits on that page's text)
      cut      the scan's edge cut it (its last digits aren't printed)
      asked    the look-again was asked for it on the document's first page; second = the number it read then"""
    name, first = TYPE_MAP[doc_type].get(canon, canon), span[0]
    for n in reversed(span):
        p = pages.get(n) or {}
        own = p.get("doc_type") == doc_type
        f, src = ((p.get("fields") or {}).get(name) if own else None) or {}, "fields"
        if f.get("value") in (None, ""):
            f, src = (p.get("fields_all") or {}).get(canon) or {}, "fields_all"
        if f.get("value") in (None, ""):
            continue
        v = (((p.get("checks") or {}).get("header") or {}).get(name) or {}) if own and src == "fields" else {}
        text = str(f.get("source_text") or f["value"])
        answer = ((((pages.get(first) or {}).get("second_look") or {}).get("results") or {}).get(canon) or {}) \
            .get("second") or {}
        again = answer.get("source_text") or answer.get("value")
        return {"num": _num(f["value"]), "value": f["value"], "page": n, "first": first, "canon": canon,
                "field": name if src == "fields" else canon, "src": src,
                "printed": (v.get("verdict") == "ok" and v.get("by") in PRINTED_BY)
                or verify.found(text, verify.windows(p.get("classical_text") or "")),
                "cut": bool(verify.CUT_OFF.search(text)), "asked": canon in _asked(pages.get(first)),
                "second": verify.amount(str(again)) if again not in (None, "") else None}
    return None


def _usable(reads):
    """Every document has the value, printed in full (not cut at the scan's edge)."""
    return bool(reads) and all(r and not r["cut"] for r in reads)


def _near(x, options, allow):
    """The reference (label, value) closest to x, when within `allow` rupiah; else None."""
    best = min(((abs(x - v), (label, v)) for label, v in options if v is not None and x is not None), default=None)
    return best[1] if best and best[0] <= allow + 0.005 else None


def _settle(reads, options, allow):
    """Item 11 on one amount, the documents' readings summed, against Satellite's references: (status, info).
      pass     within `allow` of a reference, as read or as the look-again read it (an AI reading counts: the user's
               decision 3; Tesseract failing to read it is no reason for a person)
      fail     beyond, and Tesseract's text backs every reading: a real difference, for a person now
      ask      beyond, and a reading only the AI made hasn't had its look-again: info["ask"] = [(page, field)]
      unknown  still beyond after the look-again: a person, with both readings"""
    got = round(sum(r["num"] for r in reads), 2)
    hit = _near(got, options, allow)
    if hit:
        return "pass", {"got": got, "ref": hit, "how": ""}
    if all(r["printed"] for r in reads):
        return "fail", {"got": got}
    pending = [r for r in reads if not r["printed"] and not r["asked"]]
    if pending:
        return "ask", {"got": got, "ask": [(r["first"], r["canon"]) for r in pending]}
    again = round(sum(r["second"] if not r["printed"] and r["second"] is not None else r["num"] for r in reads), 2)
    hit = _near(again, options, allow)
    if hit:
        return "pass", {"got": again, "ref": hit, "how": f", as the look-again read it (first read {_money(got)})"}
    return "unknown", {"got": got, "again": again}


def _rows(pages, span, doc_type):
    """The document's table rows as the AI read them, on every page: [(page, row, src, its text)]."""
    out = []
    for n in span:
        p = pages.get(n) or {}
        own = p.get("doc_type") == doc_type and (p.get("fields") or {}).get("lines")
        src = "fields" if own else "fields_all"
        out += [(n, i, src, r.get("row_text") or "") for i, r in enumerate((p.get(src) or {}).get("lines") or [])]
    return out


def _rows_settle(rows, wants, allow):
    """The fallback when a document has no usable total: its printed row amounts decide. Each wanted line
    (line_no, [its amounts]) must be among them within `allow`, each printed amount used once, so two equal lines need
    it printed twice. (line numbers not found, [the amounts used: (page, row, src, text, the line's amount)], the
    largest gap between a line and the nearest amount printed: what a customer's rounding would have to cover)."""
    pool = [(n, i, src, t, a) for n, i, src, text in rows for t in AMOUNT_TEXT.findall(text)
            for a in [verify.amount(t)] if a is not None]
    taken, missing, used, gap = set(), [], [], None
    for line_no, amounts in wants:
        best = min(((abs(a - w), k, w) for k, (_, _, _, _, a) in enumerate(pool) if k not in taken for w in amounts),
                   default=None)
        gap = max(gap or 0, best[0]) if best else gap
        if best and best[0] <= allow + 0.005:
            taken.add(best[1])
            n, i, src, t, _ = pool[best[1]]
            used.append((n, i, src, t, best[2]))
        else:
            missing.append(line_no)
    return missing, used, (round(gap, 2) if gap is not None else None)


def _uses(reads, got, want, allow):
    """`used` entries for a pass on summed readings: each one's own share of the reference."""
    return [{"page": r["page"], "first": r["first"], "field": r["field"], "src": r["src"], "kind": "amount",
             "ref": round(want - (got - r["num"]), 2), "allow": allow} for r in reads]


def _row_uses(used, allow):
    return [{"page": n, "row": i, "src": src, "kind": "row", "text": t, "ref": w, "allow": allow}
            for n, i, src, t, w in used]


def _unsettled(st, info, what, ref_say, extra=""):
    """A check result for an amount item 11 couldn't settle."""
    if st == "fail":
        return result("fail", f"{what} {_money(info['got'])}, backed by print; {ref_say}{extra}: a real difference")
    if st == "ask":
        return result("unknown", f"{what} {_money(info['got'])} as the AI read it; {ref_say}{extra}. The AI looks "
                                 "at it again first", ask=info["ask"])
    return result("unknown", f"{what} {_money(info['got'])}, then {_money(info['again'])} when it looked again; "
                             f"{ref_say}{extra}")


def _order_side(pages, spans, ref, so_lines, allow=ROUNDING):
    """PO ↔ the SO as ordered (satellite.paper: what the FP printed; the FP page itself is never the reference).
    A usable total decides: with tax against the order's total, or before tax against its DPP, and a PPN the PO prints
    against the order's PPN. With no usable total the PO's printed rows decide: every SO line's amount (net, or with
    VAT) among them. Several POs are summed."""
    allow_all = allow * len(spans)
    totals = [_read(pages, s, "PO", "total") for s in spans]
    opts = [("the order's total", ref["total"]), ("the order's DPP (a total before tax)", ref["dpp"])]
    if _usable(totals):
        st, info = _settle(totals, opts, allow_all)
        if st != "pass":
            near = min(opts, key=lambda o: abs(info["got"] - o[1]) if o[1] is not None else 1e18)
            return {**_unsettled(st, info, "the PO's total", f"the order's total {_money(ref['total'])}, its DPP "
                                 f"{_money(ref['dpp'])}", f" ({_money(abs(info['got'] - near[1]))} from {near[0]})"),
                    "fp": near[1], "po": info["got"], "gap": round(abs(info["got"] - near[1]), 2), "allow": allow_all}
        label, want = info["ref"]
        gap = abs(info["got"] - want)
        say = f"the PO's total {_money(info['got'])} and {label} {_money(want)}" + \
            (" are equal" if gap < 0.005 else f": {_money(gap)} apart, rounding (up to Rp {allow_all:g})") + info["how"]
        used = _uses(totals, info["got"], want, allow_all) if not info["how"] else []
        ppns = [_read(pages, s, "PO", "ppn") for s in spans]
        if _usable(ppns):                     # every amount the PO prints must fit, whichever total matched
            st2, info2 = _settle(ppns, [("the order's PPN", ref["ppn"])], allow_all)
            gap = max(gap, abs(info2["got"] - ref["ppn"]))
            if st2 != "pass":
                return {**_unsettled(st2, info2, "the PO's PPN", f"the order's PPN {_money(ref['ppn'])}",
                                     f" (the totals agree: {say})"), "fp": want, "po": info["got"], "gap": round(gap, 2)}
            used += _uses(ppns, info2["got"], info2["ref"][1], allow_all) if not info2["how"] else []
            say += f"; its PPN {_money(info2['got'])} and the order's {_money(ref['ppn'])} agree"
        # what the PO's "total" turned out to be: a Hero PO whose TOTAL NETTO isn't on the page prints only its total
        # before tax, which is what gets published as such (phase 8), never as a total with tax
        return result("pass", say, fp=want, po=info["got"], used=used, gap=round(gap, 2), allow=allow_all,
                      meaning="with tax" if want == ref["total"] else "before tax")
    wants = [(s["line_no"], [_num(s["line_amount"]), round(_num(s["line_amount"]) + (_num(s.get("vat")) or 0), 2)])
             for s in so_lines if (_num(s.get("line_amount")) or 0) > 0]
    missing, used, gap = _rows_settle([r for s in spans for r in _rows(pages, s, "PO")], wants, allow)
    why = "the PO has no usable total (" + ("cut at the scan's edge" if any(t and t["cut"] for t in totals)
                                           else "none read") + ")"
    if not missing:
        return result("pass", f"{why}; every SO line's amount is on its rows", used=_row_uses(used, allow), gap=gap)
    absent = [s[0] for s, t in zip(spans, totals) if not t and "total" not in _asked(pages.get(s[0]))]
    if absent:            # rows can't be looked at again, but a total the AI missed may be printed
        return result("unknown", f"{why}, and SO lines {missing} aren't among its rows. The AI looks for its total "
                                 "first", ask=[(n, "total") for n in absent], gap=gap)
    return result("unknown", f"{why}, and SO lines {missing} aren't among its rows", gap=gap)


def _delivery_side(pages, spans, rec, so_lines, allow=ROUNDING, receipt_shows=None):
    """TTG ↔ what Satellite received (satellite.received: its goods receipt; the invoice is built from it). Never the
    FP, never the order. A usable total decides (with tax against the received total, before tax against the received
    DPP); with none, the receipt's printed rows: every received line's amount among them. Several receipts are
    summed (a split delivery). receipt_shows 'ordered' (decision 8: this customer's receipts print the whole order):
    a receipt can't show a tolakan, so one Satellite records goes to a person; without one, ordered = received."""
    allow_all = allow * len(spans)
    rejected = sum((x["rejected"] or 0) for x in rec["lines"])
    if rejected and receipt_shows == "ordered":
        return result("unknown", f"this customer's receipts print the whole order, so this one can't show the tolakan "
                                 f"of {rejected:g} pieces Satellite records: a person confirms it")
    shown = f"; it shows the tolakan Satellite records ({rejected:g} pieces)" if rejected else ""
    tries = []                   # every total the receipt prints must fit: one fitting never covers another that doesn't
    totals = [_read(pages, s, "TTG", "total") for s in spans]
    dpps = [_read(pages, s, "TTG", "dpp") for s in spans]
    received = [("what Satellite received, with tax", rec["total"]), ("what Satellite received, before tax", rec["dpp"])]
    if _usable(totals):
        tries.append(("the receipt's total", totals, received, _settle(totals, received, allow_all)))
    if _usable(dpps):
        tries.append(("the receipt's total before tax", dpps, received[1:], _settle(dpps, received[1:], allow_all)))
    gap = max((min(abs(info["got"] - v) for _, v in opts if v is not None) for *_, opts, (st, info) in tries),
              default=None)
    gap = round(gap, 2) if gap is not None else None
    if tries and all(st == "pass" for *_, (st, _) in tries):
        says, used = [], []
        for what, reads, _, (st, info) in tries:
            label, want = info["ref"]
            says.append(f"{what} {_money(info['got'])} and {label} {_money(want)}"
                        + (" are equal" if abs(info["got"] - want) < 0.005 else
                           f": {_money(abs(info['got'] - want))} apart, rounding (up to Rp {allow_all:g})") + info["how"])
            used += _uses(reads, info["got"], want, allow_all) if not info["how"] else []
        return result("pass", "; ".join(says) + shown, used=used, gap=gap)
    if tries:
        rank = {"fail": 0, "ask": 1, "unknown": 2}
        what, reads, _, (st, info) = min((t for t in tries if t[3][0] != "pass"), key=lambda t: rank[t[3][0]])
        extra = ""
        if rejected:
            extra = f" (Satellite records a tolakan of {rejected:g} pieces the receipt may not show)"
        asks = [a for *_, (s, i) in tries if s == "ask" for a in i["ask"]]
        out = _unsettled(st, info, what, f"Satellite received {_money(rec['total'])} with tax, "
                                         f"{_money(rec['dpp'])} before tax ({rec['state']})", extra)
        return {**out, "gap": gap, **({"ask": asks} if asks and st != "fail" else {})}
    wants = [(x["line_no"], [x["net"], x["with_vat"]]) for x in rec["lines"] if (x["net"] or 0) > 0]
    missing, used, gap = _rows_settle([r for s in spans for r in _rows(pages, s, "TTG")], wants, allow)
    why = "the receipt has no usable total (" + ("cut at the scan's edge" if any((t and t["cut"]) or (d and d["cut"])
                                                for t, d in zip(totals, dpps)) else "none read") + ")"
    if not missing:
        return result("pass", f"{why}; every line Satellite received is on its rows" + shown,
                      used=_row_uses(used, allow), gap=gap)
    absent = [s[0] for s, t, d in zip(spans, totals, dpps)
              if not t and not d and "total" not in _asked(pages.get(s[0]))]
    if absent:
        return result("unknown", f"{why}, and lines {missing} Satellite received aren't among its rows. The AI "
                                 "looks for its total first", ask=[(n, c) for n in absent for c in ("total", "dpp")],
                      gap=gap)
    return result("unknown", f"{why}, and lines {missing} Satellite received aren't among its rows", gap=gap)


def _dates(pages, spans, so, scan_day):
    """Each receipt's date (decision 7): equal to Satellite's goods receipt date, or in order (the SO's date ≤ it ≤
    the scan day; the SO's date is only a bound, never a reference for the receipt). Out of order or missing: item 11."""
    so_day, cgr = so and so.get("tgl_so"), so and so.get("cgr_date")
    lo, hi = so_day and so_day.isoformat(), scan_day and scan_day.isoformat()
    says, used, asks, worst = [], [], [], "pass"
    rank = {"pass": 0, "unknown": 1, "fail": 2}

    def inorder(d):
        return bool(d) and (not lo or lo <= d) and (not hi or d <= hi)
    for span in spans:
        r = _read(pages, span, "TTG", "posting_date")
        day = r and str(r["value"])
        if day and cgr and day == cgr.isoformat():
            says.append(f"received {day}, Satellite's goods receipt date")
        elif inorder(day):
            says.append(f"received {day}, in order (SO {so_day or '?'}, scanned {scan_day or '?'}), kept as read")
        else:
            again = ((((pages.get(span[0]) or {}).get("second_look") or {}).get("results") or {})
                     .get("posting_date") or {}).get("second") or {}
            if day and r["printed"]:
                st, say = "fail", f"received {day}, backed by print, but the SO is of {so_day} and the stack was " \
                                  f"scanned {scan_day}: out of order"
            elif "posting_date" not in _asked(pages.get(span[0])):
                st, say = "unknown", ("no receipt date read" if not day else f"received {day} as the AI read it: out "
                                      "of order") + ". The AI looks at it again first"
                asks.append((span[0], "posting_date"))
            elif inorder(str(again.get("value") or "")):
                st, say = "pass", f"received {again['value']}, in order, as the look-again read it"
            else:
                st, say = "unknown", f"received {day or 'not read'}, then {again.get('value') or 'not read'} when " \
                                     "it looked again: out of order"
            worst = max(worst, st, key=rank.get)
            says.append(say)
            continue
        used.append({"page": r["page"], "first": span[0], "field": r["field"], "src": r["src"], "kind": "date",
                     "lo": lo, "hi": hi})
    return result(worst, "; ".join(says), **({"used": used} if worst == "pass" else {}), **({"ask": asks} if asks else {}))


def allowance_for(gap):
    """The allowance to suggest for a customer whose amounts were up to `gap` rupiah from Satellite's: the smallest
    round step that covers it. None beyond Rp 100: that isn't rounding (a person looks at the difference)."""
    return next((s for s in STEPS if gap is None or gap <= s + 0.005), None)


def _calibration(prof, so, so_lines, ttgs, out):
    """The two looks a person gives each new customer (chain), once each (the user, decision 5): its allowance, on its
    first bundle; what its receipt prints after a rejection, on its first bundle Satellite records a tolakan on. Until
    then the bundle waits on Review; afterwards only anomalies reach a person."""
    chain, name = prof.get("chain"), prof.get("name") or "this customer"
    if not chain:
        return result("n/a", "no customer in Satellite to calibrate")
    gaps = [out[k]["gap"] for k in ("fp_po_total", "received") if (out.get(k) or {}).get("gap") is not None]
    asks = []
    if prof.get("allowance") is None:
        biggest = max(gaps, default=None)
        asks.append({"what": "allowance", "gap": biggest, "suggest": allowance_for(biggest),
                     "why": f"{name}'s first bundle: a person confirms how far its amounts may be from Satellite's "
                            + (f"(here up to {_money(biggest)}; " if biggest is not None else "(")
                            + f"Rp {ROUNDING:g} until then)"})
    rejected = sum(float(s.get("rejected_qty") or 0) for s in so_lines)
    if ttgs and rejected and not prof.get("receipt_shows"):
        rec, got = satellite.received(so, so_lines), out.get("received") or {}
        asks.append({"what": "receipt", "suggest": "received" if got.get("status") == "pass" else None,
                     "why": f"{name}'s first bundle with a tolakan ({rejected:g} pieces): does its receipt print what "
                            f"was received ({_money(rec['total'] or 0)} with tax) or the whole order?"})
    if asks:
        return result("unknown", "; ".join(a["why"] for a in asks), calibrate=asks)
    return result("pass", f"{name} is calibrated: amounts may differ by up to Rp {float(prof['allowance']):g}"
                          + (f"; its receipts print {'what was received' if prof['receipt_shows'] == 'received' else 'the whole order'}"
                             if prof.get("receipt_shows") else ""))


def _own_words(name, stores):
    """The words only this store's name has among its customer's stores (Boots: HARAPAN INDAH AVENUE; BOOTS is every
    store's)."""
    mine = satellite.store_words(name)
    return mine - set().union(*(satellite.store_words(s) for s in stores if s != name)) if stores else mine


def _weight(word, df):
    """How much a word says about which store: rare in Satellite's store names, a lot; common (INDONESIA, BEKASI, PT),
    nothing. Measured over 4,184 names: HARAPAN 11, BINTARO 20, INDONESIA 62."""
    n = (df or {}).get(word, 0)
    return 1.0 if n <= 12 else 0.5 if n <= 25 else 0.0


def _initials(name, page):
    """The page prints a store's initials: BSD for BUMI SERPONG DAMAI (Duta Buah's 'CABANG : BSD')."""
    w = [x for x in re.findall(r"[A-Z]+", str(name or "").upper()) if len(x) >= 2]
    runs = {"".join(x[0] for x in w[i:j]) for i in range(len(w)) for j in range(i + 3, len(w) + 1)}
    return bool(runs & page)


def _score(name, own, page, df):
    """How clearly the page names this store: its own words on the page, weighted by rarity, plus its initials."""
    return sum(_weight(w, df) for w in own & page) + (1.0 if _initials(name, page) else 0.0)


def _store_named(pages, docs, so, stores, df=None):
    """A second net for a wrong-order link: the customer's paper usually names the store the goods go to. Fail when a
    page names ANOTHER store of this customer and not this order's (Boots ships one order to ten stores: the totals
    would all fit). Pass when it names this order's store. Info when it names none: a head-office PO.
    A store is named at a score of 1 (_score): its own words (only it has them among the customer's stores) weighted
    by how rare they are in all of Satellite's store names, or its initials. Never by the page's own issuer (Satellite
    lists PT. AEON INDONESIA, the company, among AEON's ship-tos; its POs are issued by it). An address word can
    still name a store (Duta Buah's head office is on Jl. Jalur SUTRA; ALAM SUTRA is one of its stores): a false
    alarm sends a bundle to Review, it never links anything."""
    name = (so or {}).get("customer_name")
    if not name:
        return result("n/a", "the SO has no ship-to name")
    mine = _own_words(name, stores)
    named, wrong = [], []
    for n, t in docs:
        if t not in ("PO", "TTG"):
            continue
        p = pages.get(n) or {}
        text = " ".join([str(p.get("classical_text") or "")] + [str((f or {}).get("value") or "") for k, f in
                        ((p.get("fields_all") or {}).items()) if k != "lines" and isinstance(f, dict)])
        words = satellite.store_words(text)
        issuer = satellite.store_words(((p.get("fields_all") or {}).get("customer_name") or {}).get("value"))
        if _score(name, mine, words, df) >= 1:
            named.append(n)
            continue
        other = next((s for s in stores if s != name for own in [_own_words(s, stores)]
                      if own and not own <= issuer and _score(s, own, words, df) >= 1), None)
        if other:
            wrong.append((n, other))
    if wrong:
        return result("fail", "; ".join(f"page {n} names {s}, another store of this customer, not {name}"
                                        for n, s in wrong) + ": linked to the wrong order?")
    if named:
        return result("pass", f"page{'s' if len(named) > 1 else ''} {', '.join(map(str, named))} name{'' if len(named) > 1 else 's'} "
                              f"{name}")
    return result("info", f"no page names a store of this customer (a head-office order?); {name} is kept as the SO says")


def check_bundle(sor, docs, pages, so, so_lines, matches, expected=("FP", "TTG"), scan_day=None, spans=None,
                 profile=None, stores=None, df=None):
    """The checks of one bundle. docs: [(page_no, doc_type)] (first pages); pages: {page_no: {doc_type, fields,
    checks, outcome, fields_all, classical_text, second_look}}; matches: matching.match(); spans: {first page: every
    page of that document} (default: the first page alone); profile: the customer's calibration {chain, name,
    allowance, receipt_shows} (satellite.customer_profile; without one the checks run at Rp 5 and nothing is
    calibrated). Pure.

    Two sides, each with its own reference in Satellite (the user, 2026-09-26): the order side, PO ↔ the SO as
    ordered (what the FP printed); the delivery side, TTG ↔ the goods receipt (what was received). Documents are never
    evidence for each other. Totals decide; rows only explain, unless a document has no usable total."""
    out = {}
    by_type = {}
    for n, t in docs:
        by_type.setdefault(t, []).append(n)
    pos, ttgs = by_type.get("PO") or [], by_type.get("TTG") or []
    spans, prof = spans or {}, profile or {}
    allow = float(prof["allowance"]) if prof.get("allowance") is not None else ROUNDING

    out["sor_in_satellite"] = result("pass", f"{sor} is in Satellite") if so else \
        result("fail", f"{sor} isn't in Satellite's export (older than a month?)")
    missing = [t for t in expected if t not in by_type]
    out["docs_complete"] = result("fail" if missing else "pass", f"no {' or '.join(missing)} in the bundle" if missing
                                  else f"{', '.join(t for t in expected)} present")
    if not pos:
        out["vendor_is_samb"] = result("n/a", "no PO in the bundle")
    else:           # information (verification redesign): the PO joined SAMB's SO by its number, and the AI's
        name, ok = _value(pages[pos[0]], "vendor_name")    # instructions name SAMB, so its reading proves nothing
        if ok and name and SAMB not in verify.flat(name):
            out["vendor_is_samb"] = result("fail", f"the PO is addressed to {name!r} (print says so), not SAMB")
        else:
            out["vendor_is_samb"] = result("info", (f"the PO's vendor reads {name!r}" + ("" if ok else ", kept as read")
                                                    if name else "the PO's vendor wasn't read")
                                           + "; the PO is linked to SAMB's SO by its number")

    # the order side: PO ↔ Satellite's SO as ordered
    if not pos:
        out["fp_po_total"] = result("n/a", "no PO in the bundle")
    elif not so:
        out["fp_po_total"] = result("unknown", "the SO isn't in Satellite's export: nothing to compare the PO with")
    elif satellite.free_goods(so):
        out["fp_po_total"] = result("unknown", "a free-goods order (SOF): Satellite's order amounts are 0, and what its "
                                               "papers print isn't known yet")
    else:
        ref = satellite.paper(so, so_lines)
        out["fp_po_total"] = _order_side(pages, [spans.get(n) or [n] for n in distinct(pages, pos, "purchase_order_no")],
                                         ref, so_lines, allow) if ref else \
            result("unknown", "what the order printed can't be known (it changed after printing, and a line carries "
                              "no amount)")

    def per_row(n, dt, compare):
        """Each row against its SO line, in words: [(row, why)]. An explanation, never a verdict: the totals decide
        (a row doesn't show which column a number came from; Hero's receipts print the ordered quantity too)."""
        rows = []
        for r in matching.rows_of(dt, pages[n]["fields"]):
            m = matches.get((n, r["i"])) or {"status": "none", "why": "not matched"}
            if m["status"] == "bonus":
                continue
            if m["status"] != "matched":
                rows.append((r["i"], "the AI proposed its SO line (a person may confirm it on Review)"
                             if m["status"] == "proposed" else f"no SO line matched ({m['why']})"))
                continue
            rows.append((r["i"], compare(r, so_lines[m["line"]])))
        return rows

    def po_row(r, s):
        q = matching.pieces(r, s)
        say = [f"quantity {r['qty']!r} isn't a quantity" if q is None else f"{q:g} pieces as ordered"
               if abs(q - float(s["qty_pcs"] or 0)) < 0.001 else f"{q:g} pieces, the SO ordered {float(s['qty_pcs']):g}"]
        fits = matching.price_fits(r["price"], s)
        say.append("no price read" if fits is None else f"price {r['price']} fits {float(s['price_pcs']):,.2f} a piece"
                   if fits else f"price {r['price']} ≠ the SO's {float(s['price_pcs']):,.2f} a piece "
                                f"({float(s['price_uom'] or 0):,.2f} a carton)")
        if r["discount"] not in (None, ""):
            say.append(f"discounts {r['discount']} as the SO's" if _percents(r["discount"]) == _so_percents(s)
                       else f"discounts {r['discount']} ≠ the SO's {_so_percents(s) or 'none'}")
        return f"line {s['line_no']}: " + ", ".join(say)

    def ttg_row(r, s):
        q, got = matching.pieces(r, s), s.get("cgr_qty")
        ordered, rejected = float(s["qty_pcs"] or 0), float(s.get("rejected_qty") or 0)
        tolak = f"; tolakan: {rejected:g} of {ordered:g} pieces ({s.get('reject_reason') or 'no reason given'})" \
            if rejected else ""
        if got is None:
            return f"line {s['line_no']}: Satellite has no goods receipt for it yet"
        if q is None:
            return f"line {s['line_no']}: quantity {r['qty']!r} isn't a quantity; Satellite received {float(got):g}"
        return (f"line {s['line_no']}: {q:g} pieces, as Satellite received" if abs(q - float(got)) < 0.001 else
                f"line {s['line_no']}: the row reads {q:g} pieces, Satellite received {float(got):g}") + tolak

    rows = [x for n in pos for x in per_row(n, "PO", po_row)]
    out["fp_po_lines"] = result("info", (("; ".join(f"row {i + 1}: {w}" for i, w in rows) or "no PO rows read")[:900]
                                         + " (the rows explain; the totals decide)")) if pos else \
        result("n/a", "no PO in the bundle")

    # the delivery side: TTG ↔ what Satellite received (its goods receipt), never the FP
    tolakan = [f"line {s['line_no']}: {float(s['rejected_qty']):g} pieces ({s.get('reject_reason')})"
               for s in so_lines if float(s.get("rejected_qty") or 0)]
    explain = [f"row {i + 1}: {w}" for n in ttgs for i, w in per_row(n, "TTG", ttg_row)]
    if not ttgs:
        out["received"] = result("n/a", "no TTG in the bundle")
    else:
        rec = satellite.received(so, so_lines)
        if rec["state"] in ("waiting", "unknown"):
            out["received"] = result(rec["state"], rec["why"], tolakan=tolakan, rows=explain)
        else:
            out["received"] = {**_delivery_side(pages, [spans.get(n) or [n] for n in distinct(pages, ttgs, "document_no")],
                                                rec, so_lines, allow,
                                                prof.get("receipt_shows")), "tolakan": tolakan, "rows": explain}
    out["dates"] = _dates(pages, [spans.get(n) or [n] for n in ttgs], so, scan_day) if ttgs else \
        result("n/a", "no TTG in the bundle")
    out["fpj"] = result("n/a", "no Faktur Pajak in this stage")
    out["store_named"] = _store_named(pages, docs, so, stores or [], df)
    out["calibration"] = _calibration(prof, so, so_lines, ttgs, out) if so and not satellite.free_goods(so) else \
        result("n/a", "nothing to calibrate against: " + ("the SO isn't in Satellite" if not so else "free goods (SOF)"))
    return out


def check_print(c):
    """What a check said: a person's acceptance holds only while it says exactly this."""
    return hashlib.sha1(json.dumps([c.get("status"), c.get("why")]).encode()).hexdigest()[:16]


def accept(checks, decisions):
    """Checks a person accepted on Review (staging.bundle_decision) become 'accepted', the reason shown, while their
    result is unchanged. Every check also carries its print, for the Review form."""
    out = {}
    for k, c in checks.items():
        c = {**c, "print": check_print(c)}
        d = decisions.get(k)
        if c["status"] in ("fail", "unknown") and d and d["input_print"] == c["print"] and k != "calibration":
            # (a customer's calibration is settled by its own form on Review, never accepted away)
            c = {**c, "status": "accepted", "was": c["status"],
                 "why": f"accepted by {d['decided_by']} ({d['reason']}" + (f": {d['note']}" if d.get("note") else "")
                        + f"): {c['why']}"}
        out[k] = c
    return out


def can_approve(checks, pages):
    """(yes?, what is left). A person may approve a bundle only when every check passed or was accepted and every
    page has settled what it decides itself (vf.page_settled): nothing waits for the AI OCR, and no page's type, key
    or FP value still waits for a person. A value kept as read never holds a bundle; the bundle's checks judge the
    rest. A bundle never checked (held without its FP: no checks at all) can't be approved (S5)."""
    if not checks:
        return False, ["the bundle hasn't been checked: it is held (its FP isn't here, or grouping hasn't run)"]
    left = [f"{LABEL[k]}: {c['why']}" for k, c in checks.items() if c["status"] in ("fail", "unknown", "waiting")]
    waiting = sorted(n for n, p in pages.items() if p.get("outcome") == "waiting_ai")
    person = sorted(n for n, p in pages.items() if p.get("outcome") in ("needs_person", "held_unsure"))
    left += ([f"pages {waiting} wait for the AI OCR"] if waiting else []) + \
            ([f"pages {person}: their type, key or FP values wait for a person"] if person else [])
    return not left, left


def decide(checks, pages, old_status=None, old_print=None, new_print=None):
    """(status, reasons). Reasons: failed checks, then open ones, then pages a person must finish. An accepted check
    counts as passed."""
    waiting = sorted(n for n, p in pages.items() if p.get("outcome") == "waiting_ai")
    person = sorted(n for n, p in pages.items() if p.get("outcome") in ("needs_person", "held_unsure"))
    failed = [f"{LABEL[k]}: {c['why']}" for k, c in checks.items() if c["status"] == "fail"]
    open_ = [f"{LABEL[k]}: {c['why']}" for k, c in checks.items() if c["status"] == "unknown"]
    later = [f"{LABEL[k]}: {c['why']}" for k, c in checks.items() if c["status"] == "waiting"]   # Satellite's receipt
    reasons = failed + open_ + ([f"pages {person}: their type, key or FP values wait for a person"] if person else [])
    if old_status == "reviewed":
        if old_print == new_print:
            return "reviewed", reasons
        reasons.insert(0, "it was reviewed, then its documents or Satellite's record changed")
    if waiting or later:
        return "grouping", ([f"pages {waiting} wait for the AI OCR"] if waiting else []) + later + reasons
    return ("auto_ok" if not reasons else "needs_review"), reasons


def fingerprint(pages, so, so_lines, matches):
    """What a review looked at: every value and verdict of the bundle's pages, the SO record, the pairs."""
    state = {"pages": {n: {"fields": p.get("fields"), "checks": p.get("checks")} for n, p in sorted(pages.items())},
             "so": {k: str(v) for k, v in (so or {}).items() if k not in ("paper",)},
             "lines": [{k: str(v) for k, v in s.items()} for s in so_lines],
             "matches": {f"{p}:{i}": m for (p, i), m in sorted(matches.items())}}
    return hashlib.sha1(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def inputs(c, bid):
    """Everything the checks of the batch's complete bundles read from the database, one dict per bundle, with what
    is stored for it now (`stored`: checks, reasons, pairs; `status`; `fingerprint`)."""
    bundles = c.execute("""SELECT DISTINCT b.id, b.sor_no, b.status::text AS status, b.fingerprint, b.checks
                             FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                             JOIN staging.document d ON d.id = bd.document_id
                            WHERE d.batch_id = %s AND b.hold_reason IS NULL ORDER BY b.sor_no""", (bid,)).fetchall()
    sos = satellite.load(c, [b["sor_no"] for b in bundles])
    decisions = {(r["page_no"], r["row_index"]): r for r in c.execute(
        "SELECT * FROM staging.line_match WHERE batch_id=%s", (bid,))}
    day = c.execute("SELECT coalesce(scanned_day, received_at::date) AS d FROM staging.scan_batch WHERE id=%s",
                    (bid,)).fetchone()
    out = []
    for b in bundles:
        ranges = c.execute(
            """SELECT d.page_from, d.page_to, d.doc_type::text AS t FROM staging.document d
                 JOIN staging.bundle_document bd ON bd.document_id = d.id
                WHERE bd.bundle_id = %s AND d.batch_id = %s ORDER BY d.page_from""", (b["id"], bid)).fetchall()
        docs = [(d["page_from"], d["t"]) for d in ranges]
        pages = {r["page_no"]: dict(r) for r in c.execute(
            """SELECT p.page_no, p.doc_type::text AS doc_type, p.fields, p.outcome, p.fields_all, p.classical_text,
                      p.second_look FROM staging.page p
                 JOIN staging.document d ON d.batch_id = p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                 JOIN staging.bundle_document bd ON bd.document_id = d.id
                WHERE bd.bundle_id = %s AND p.batch_id = %s""", (b["id"], bid))}
        for n in pages:
            pages[n]["checks"] = verify.load(c, bid, n)
        so = sos.get(verify.flat(b["sor_no"]))
        chain = satellite.chain_of(so)
        profile = c.execute("""SELECT expected_docs::text[] AS e, rounding_allowance, receipt_shows
                                 FROM satellite.customer_profile WHERE customer_code=%s""", (chain,)).fetchone()
        stores = sorted({s["customer_name"] for s in satellite.load(c).values()
                         if s.get("customer_name") and chain and satellite.chain_of(s) == chain})
        out.append({"id": b["id"], "sor": b["sor_no"], "status": b["status"], "fingerprint": b["fingerprint"],
                    "stores": stores, "df": satellite.store_df(satellite.load(c)),
                    "stored": b["checks"] or {}, "docs": docs, "pages": pages, "so": so,
                    "spans": {d["page_from"]: list(range(d["page_from"], d["page_to"] + 1)) for d in ranges},
                    "profile": {"chain": chain, "name": satellite.chain_name(so),
                                "allowance": _num((profile or {}).get("rounding_allowance")),
                                "receipt_shows": (profile or {}).get("receipt_shows")} if chain else {},
                    "lines": satellite.items(c, b["sor_no"]), "expected": tuple(profile["e"]) if profile else ("FP", "TTG"),
                    "pmap": matching.load_map(c, so and so.get("customer_parent")), "decisions": decisions,
                    "accepted": {r["check_name"]: r for r in c.execute(
                        "SELECT * FROM staging.bundle_decision WHERE sor_no=%s", (b["sor_no"],))},
                    "scan_day": day and day["d"]})
    return out


def evaluate(x):
    """One bundle's checks, reasons, pairs, status and fingerprint from its inputs(). Pure: it reads and writes
    nothing, so a rule change can be measured (shadow) before it is adopted."""
    rows = [(n, t, matching.rows_of(t, x["pages"][n]["fields"])) for n, t in x["docs"] if t in ("PO", "TTG")]
    m = matching.match(rows, x["lines"], x["pmap"], x["decisions"])
    checks = accept(check_bundle(x["sor"], x["docs"], x["pages"], x["so"], x["lines"], m, x["expected"],
                                 x["scan_day"], x.get("spans"), x.get("profile"), x.get("stores"), x.get("df")),
                    x["accepted"])
    asks = {}                    # item 11: a value only the AI read, beyond its reference: the page looks again first
    for c in checks.values():
        for n, field in (c.get("ask") or []) if c["status"] == "unknown" else []:
            asks.setdefault(n, set()).add(field)
    pages = {n: {**p, "outcome": "waiting_ai"} if n in asks else p for n, p in x["pages"].items()}
    fp = fingerprint(x["pages"], x["so"], x["lines"], m)
    status, reasons = decide(checks, pages, x["status"], x["fingerprint"], fp)
    pairs = [{"page": p, "row": i + 1, "line": x["lines"][y["line"]]["line_no"] if y["line"] is not None else None,
              "how": y["how"], "status": y["status"], "why": y["why"]} for (p, i), y in sorted(m.items())]
    return {"checks": checks, "reasons": reasons, "pairs": pairs, "status": status, "fingerprint": fp,
            "asks": {n: sorted(v) for n, v in sorted(asks.items())}}


def calibrate(chain, name, by, allowance=None, receipt_shows=None):
    """A person's once-per-customer answer on Review (verification redesign S3, decision 5): how far the customer's
    amounts may be from Satellite's (its rounding), and/or what its receipts print after a rejection. Stored on its
    profile (satellite.customer_profile, by and when). Returns the batches holding any of its bundles: all of them
    are checked again (the caller regroups them), so one answer settles every bundle of that customer."""
    if receipt_shows not in (None, "received", "ordered"):
        raise ValueError(f"receipt_shows is 'received' or 'ordered', not {receipt_shows!r}")
    with db.connect() as c:
        c.execute("""INSERT INTO satellite.customer_profile (customer_code, customer_name) VALUES (%s, %s)
                     ON CONFLICT (customer_code) DO NOTHING""", (chain, name or f"chain {chain}"))
        if allowance is not None:
            c.execute("""UPDATE satellite.customer_profile SET rounding_allowance=%s, allowance_by=%s, allowance_at=now()
                         WHERE customer_code=%s""", (allowance, by, chain))
        if receipt_shows:
            c.execute("""UPDATE satellite.customer_profile SET receipt_shows=%s, receipt_by=%s, receipt_at=now()
                         WHERE customer_code=%s""", (receipt_shows, by, chain))
        return [r["batch_id"] for r in c.execute(
            """SELECT DISTINCT d.batch_id FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                 JOIN staging.document d ON d.id = bd.document_id JOIN satellite.sor s ON s.sor_no = b.sor_no
                WHERE coalesce(s.customer_parent, s.customer_code) = %s ORDER BY 1""", (chain,))]


def ask_again(c, bid, page, fields):
    """Item 11 at the bundle: the page's look-again is owed for these fields (canon names). The page waits for the AI
    (`python -m worker.vf again` runs it, the page's own look-again asks them: vf.second_look_asks), then the bundle
    is checked again with what it read."""
    p = c.execute("SELECT second_look FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, page)).fetchone()
    s = dict((p or {}).get("second_look") or {})
    want = sorted(set(s.get("bundle_asks") or []) | set(fields))
    s.update(bundle_asks=want, waiting=s.get("waiting") or "the bundle's checks ask the AI to look again at: "
             + ", ".join(want))
    c.execute("UPDATE staging.page SET second_look=%s, outcome='waiting_ai' WHERE batch_id=%s AND page_no=%s",
              (Json(s), bid, page))


def run(bid):
    """Check every complete bundle of the batch and store checks, status, reasons and fingerprint."""
    with db.connect() as c:
        done = {}
        for x in inputs(c, bid):
            r = evaluate(x)
            c.execute("""UPDATE staging.bundle SET checks=%s, status=%s, fingerprint=%s, checked_at=now()
                         WHERE id=%s""", (Json({"checks": r["checks"], "reasons": r["reasons"], "pairs": r["pairs"]}),
                                          r["status"], r["fingerprint"], x["id"]))
            for page, fields in r["asks"].items():
                ask_again(c, bid, page, fields)
            done[x["sor"]] = r["status"]
    return done


def shadow(bid, show=print):
    """What today's rules would say about every bundle of the batch, against what is stored: status, each check,
    and what a person would have left (can_approve). Writes nothing. Returns {sor: (stored status, new status)}."""
    with db.connect() as c:
        xs = inputs(c, bid)
    out, left_before, left_after = {}, 0, 0
    for x in xs:
        r = evaluate(x)
        was = x["stored"].get("checks") or {}
        before = can_approve(was, x["pages"])[1]
        after = can_approve(r["checks"], x["pages"])[1]
        left_before, left_after = left_before + len(before), left_after + len(after)
        out[x["sor"]] = (x["status"], r["status"])
        moved = [(k, (was.get(k) or {}).get("status"), c_["status"], c_["why"]) for k, c_ in r["checks"].items()
                 if (was.get(k) or {}).get("status") != c_["status"] or (was.get(k) or {}).get("why") != c_["why"]]
        pages = ",".join(str(n) for n, _ in x["docs"])
        show(f"{x['sor']} (pages {pages}): {x['status']} → {r['status']} · left {len(before)} → {len(after)}"
             + ("" if moved or x["status"] != r["status"] else " · unchanged")
             + "".join(f" · would ask the AI to look again: p{n} {', '.join(v)}" for n, v in r["asks"].items()))
        for k, a, b_, why in moved:
            show(f"   {LABEL[k]}: {a} → {b_}: {why[:160]}")
    tally = lambda i: {s: sum(1 for v in out.values() if v[i] == s) for s in sorted({v[i] for v in out.values()})}
    show(f"{len(out)} bundles · status {tally(0)} → {tally(1)} · items left for a person {left_before} → {left_after}")
    return out


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["shadow"] and len(sys.argv) > 2:
        shadow(sys.argv[2])
    else:
        print("usage: python -m grouper.crosscheck shadow <batch>")
