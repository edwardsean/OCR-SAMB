"""Phase 5: check every value the AI OCR read against the page itself. Plain code, no AI.

A value is confirmed ("ok") when one of these holds:
  text     it is printed in Tesseract's text for the page: letters and digits compared, spaces/dots/commas ignored,
           within one line (or a line and the next, for values that wrap), never glued across the whole page
  qr       it is the FP's SOR and equals the page's QR code
  adds_up  it is an FP amount and DPP + PPN = Total, with PPN = 11% (or 12%) of DPP
Anything else is "check": a person looks at it (Review screen, phase 7). There is no "close enough":
on page 22 Tesseract read $10232 and the AI read 510232 where S10232 is printed; a rule that treats S, $ and 5 alike
would have approved a wrong value.

The value that gets stored is the one checked, so it must also agree with its own source_text
(source "1.126.011,00" -> value 1126011.00); if not, it is "check" too.
Line items: each row is first found in Tesseract's text (the line sharing most words with the AI's row_text);
a value counts only if it is on that line. Short values ("4", "EA") are only ever checked there, as whole words.
Stored as one staging.field_check row per value (store()); run() and rows() themselves never touch the database.
"""
import re
from datetime import date

from common.fields import DOCS

VERIFY_VERSION = 1
MIN_LEN = 5              # fewer letters+digits than this turn up by chance somewhere on any page ("4", "EA", "2026")
TOLERANCE = 1.0          # rupiah, for rounding of cents
PPN_RATES = (0.11, 0.12)
SOR = re.compile(r"^SOR\d{11}$")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
MONTHS.update({"MEI": 5, "AGU": 8, "AGT": 8, "OKT": 10, "DES": 12})


def flat(s):
    return re.sub(r"[^0-9A-Z]", "", str(s or "").upper())


def windows(text):
    """Each Tesseract line, and each line joined to the next (a value may wrap)."""
    ls = [l for l in (text or "").splitlines() if flat(l)]
    return ls + [a + "\n" + b for a, b in zip(ls, ls[1:])]


def found(source, wins):
    """The letters+digits of `source` appear in one window, and a number is not part of a longer number:
    1.126.011 must not match inside 1.126.011,00, nor 450583272 inside 4505832724. A space in between is a boundary."""
    s = flat(source)
    if len(s) < MIN_LEN:
        return False
    for w in wins:
        up = w.upper()
        pos = [i for i, ch in enumerate(up) if "0" <= ch <= "9" or "A" <= ch <= "Z"]
        f = "".join(up[i] for i in pos)

        def glued(a, b):        # two kept characters with no whitespace between them in the printed line
            return not any(ch.isspace() for ch in w[pos[a] + 1:pos[b]])
        i = f.find(s)
        while i != -1:
            j = i + len(s)
            longer_before = i > 0 and s[0].isdigit() and f[i - 1].isdigit() and glued(i - 1, i)
            longer_after = j < len(f) and s[-1].isdigit() and f[j].isdigit() and glued(j - 1, j)
            if not (longer_before or longer_after):
                return True
            i = f.find(s, i + 1)
    return False


def numbers(s):
    """Every way the printed number can be read: Indonesian 1.126.011,00 · English 1,126,011.00 · digits only."""
    s = re.sub(r"[^0-9.,]", "", str(s or ""))
    out = set()
    for dec, th in ((",", "."), (".", ",")):
        t = s.replace(th, "")
        if t.count(dec) <= 1:
            try:
                out.add(round(float(t.replace(dec, ".").rstrip(".") or "x"), 2))
            except ValueError:
                pass
    d = re.sub(r"\D", "", s)
    if d:
        out.add(float(d))
    return out


def dates(s):
    s = str(s or "").upper().strip()
    out = set()
    for a, b, c in re.findall(r"(\d{1,4})[-./ ]([A-Z]{3}|\d{1,2})[A-Z]*[-./ ](\d{2,4})", s):
        m = MONTHS.get(b) if b.isalpha() else int(b)
        for y, d in ((c, a), (a, c)):          # 04-Sep-2026 and 2026-09-04
            y = int(y) + (2000 if len(y) == 2 else 0)
            try:
                out.add(date(y, m, int(d)).isoformat())
            except (TypeError, ValueError):
                pass
    return out


def amount(s):
    """The number a printed amount means, by rupiah conventions. '.' groups thousands and ',' marks decimals
    (1.126.011,00), or the English way (9,410,527.00): with both marks, the last one is the decimal mark; with one
    kind, a mark followed by exactly 3 digits groups thousands (111.586 = 111586), otherwise it marks decimals.
    One reading only: 111.586 is never one hundred eleven. None if there is no number."""
    t = re.sub(r"[^0-9.,]", "", str(s or "")).strip(".,")
    if not re.search(r"\d", t):
        return None
    last = max(t.rfind("."), t.rfind(","))
    if last < 0:
        return float(t)
    if "." in t and "," in t:
        whole, frac = re.sub(r"[.,]", "", t[:last]), t[last + 1:]
    else:
        parts = t.split(t[last])
        if len(parts) > 2 or len(parts[-1]) == 3:
            whole, frac = "".join(parts), ""
        else:
            whole, frac = parts[0], parts[1]
    return float(f"{whole or 0}.{frac or 0}")


CUT_OFF = re.compile(r"[.,]\d?\s*$")   # "1.014.424,5" or "1.078.330,": an amount whose last digits the scan cut off


def agrees(kind, value, source):
    """Does the stored value say what the printed source_text says?"""
    if kind == "amount":
        try:
            return amount(source) is not None and abs(round(float(value), 2) - amount(source)) < 0.005
        except (TypeError, ValueError):
            return False
    if kind == "qty":
        try:
            v = round(float(value), 2)
        except (TypeError, ValueError):
            return False
        return any(abs(v - n) < 0.005 for n in numbers(source))
    if kind == "date":
        return str(value) in dates(source)
    return flat(value) == flat(source)


def _num(f):
    try:
        return float((f or {}).get("value"))
    except (TypeError, ValueError):
        return None


def adds_up(fields, usable):
    """FP: DPP + PPN = Total and PPN = 11% (or 12%) of DPP. Only values that agree with their printed source are used."""
    if not all(usable.get(k) for k in ("dpp", "ppn", "total")):
        return False
    d, p, t = (_num(fields.get(k)) for k in ("dpp", "ppn", "total"))
    if None in (d, p, t) or d <= 0:
        return False
    return abs(d + p - t) <= TOLERANCE and any(abs(d * r - p) <= TOLERANCE for r in PPN_RATES)


def ok(by):
    return {"verdict": "ok", "by": by}


def check(why):
    return {"verdict": "check", "why": why}


def header(doc_type, fields, text, qr_text, sums=True):
    """sums=False leaves the FP adds-up rule out: vlm-first applies its stricter one after every witness
    (common/gates.py)."""
    kinds = {f["name"]: f["kind"] for f in DOCS[doc_type]["header"]}
    wins = windows(text)
    qr = qr_text if qr_text and SOR.match(qr_text) else None
    out, usable = {}, {}
    for name, kind in kinds.items():
        f = fields.get(name) or {}
        value, source = f.get("value"), f.get("source_text")
        if value in (None, ""):
            out[name] = {"verdict": "empty"}
            continue
        if not source:
            out[name] = check("no printed text given for it")
            continue
        if not agrees(kind, value, source):
            out[name] = check(f"value {value} doesn't match what it says is printed ({source})")
            continue
        if kind == "amount" and CUT_OFF.search(source):
            # Measured on page 3: the scan cut off "1.014.424,3x"; Tesseract AND the AI OCR both read the half digit
            # as 5. Two readers agreeing on a half-printed digit is not proof.
            out[name] = {**check(f"{source!r} looks cut off at the edge of the scan (one decimal or none left)"),
                         "cut": True}           # its missing digits aren't printed: a look-again can't find them
            continue
        usable[name] = True
        if name == "sor" and qr:
            out[name] = ok("qr") if flat(value) == flat(qr) else check(f"QR code says {qr}")
        elif found(source, wins):
            out[name] = ok("text")
        elif len(flat(source)) < MIN_LEN:
            out[name] = check("too short to find in Tesseract's text reliably")
        else:
            out[name] = check("not in Tesseract's text")
    if sums and doc_type == "FP" and adds_up(fields, usable):
        for name in ("dpp", "ppn", "total"):
            if out[name]["verdict"] == "check":
                out[name] = ok("adds_up")
    return out


def row_line(row, raw_lines, taken):
    """The Tesseract line that shares most words with the AI's row_text (at least 2, and a third of them)."""
    toks = {flat(t) for t in str(row.get("row_text") or "").split()} - {""}
    toks = {t for t in toks if len(t) >= 3}
    best, at = 0, None
    for i, l in enumerate(raw_lines):
        if i in taken:
            continue
        hit = len(toks & {flat(t) for t in l.split()})
        if hit > best:
            best, at = hit, i
    return at if toks and best >= max(2, len(toks) / 3) else None


def lines(doc_type, rows, text):
    cols = [f["name"] for f in DOCS[doc_type]["lines"]]
    raw = [l for l in (text or "").splitlines() if flat(l)]
    out, taken = [], set()
    for row in rows or []:
        at = row_line(row, raw, taken)
        res = {}
        if at is not None:
            taken.add(at)
            here = raw[at]
            wins = [here] + ([here + "\n" + raw[at + 1]] if at + 1 < len(raw) else [])
            words = {flat(t) for t in here.split()}
        for c in cols:
            v = row.get(c)
            if v in (None, ""):
                res[c] = {"verdict": "empty"}
            elif at is None:
                res[c] = check("row not found in Tesseract's text")
            elif flat(v) in words or found(v, wins):
                res[c] = ok("text")
            else:
                res[c] = check("not on this row in Tesseract's text")
        out.append(res)
    return out


def run(doc_type, fields, text, qr_text, sums=True):
    """Verdicts for every header value and every line-item value of one page."""
    if doc_type not in DOCS or not fields:
        return None
    h = header(doc_type, fields, text, qr_text, sums)
    ls = lines(doc_type, fields.get("lines"), text) if DOCS[doc_type]["lines"] else []

    def count(vs):
        return {k: sum(v["verdict"] == k for v in vs) for k in ("ok", "check", "empty")}
    return {"version": VERIFY_VERSION, "header": h, "lines": ls,
            "summary": {"header": count(h.values()), "lines": count([v for r in ls for v in r.values()])}}


def rows(fields, result):
    """One staging.field_check row per value: header.<name> and lines[i].<column>. When a witness changed the value
    (Satellite's record, a person), vlm_value keeps the AI's reading and adjudicated_value holds the new one."""
    out = []
    for name, v in result["header"].items():
        f = fields.get(name) or {}
        changed = "ai_value" in f
        out.append((f"header.{name}", f["ai_value"] if changed else f.get("value"), f.get("source_text"), v,
                    f.get("value") if changed else None))
    for i, (row, res) in enumerate(zip(fields.get("lines") or [], result["lines"])):
        ai = row.get("ai_values") or {}                   # a cell Satellite corrected (phase 7b) keeps the AI's
        for col, v in res.items():
            out.append((f"lines[{i}].{col}", ai[col] if col in ai else row.get(col), None, v,
                        row.get(col) if col in ai else None))
    return [{"field_path": path, "vlm_value": None if val is None else str(val), "source_text": src,
             "classical_match": v.get("by") == "text", "status": v["verdict"], "confirmed_by": v.get("by"),
             "reason": v.get("why"), "adjudicated_value": None if adj is None else str(adj),
             "adjudicator": v.get("by") if adj is not None else None} for path, val, src, v, adj in out]


def store(conn, batch_id, page_no, fields, result):
    """Replace the page's machine verdicts. (Phase 7 will have to keep a person's corrections when re-checking.)"""
    conn.execute("DELETE FROM staging.field_check WHERE batch_id=%s AND page_no=%s", (batch_id, page_no))
    rs = rows(fields, result) if result else []
    if rs:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO staging.field_check
                               (batch_id, page_no, field_path, vlm_value, source_text, classical_match, status, confirmed_by,
                                reason, adjudicated_value, adjudicator)
                               VALUES (%(b)s, %(n)s, %(field_path)s, %(vlm_value)s, %(source_text)s, %(classical_match)s,
                                       %(status)s, %(confirmed_by)s, %(reason)s, %(adjudicated_value)s, %(adjudicator)s)""",
                            [{**r, "b": batch_id, "n": page_no} for r in rs])
    return len(rs)


def load(conn, batch_id, page_no):
    """The page's verdicts back in run()'s shape: {header: {name: v}, lines: [{col: v}], summary}."""
    h, ls = {}, {}
    for r in conn.execute("SELECT field_path, status, confirmed_by, reason FROM staging.field_check "
                          "WHERE batch_id=%s AND page_no=%s", (batch_id, page_no)):
        v = {"verdict": r["status"], **({"by": r["confirmed_by"]} if r["confirmed_by"] else {}),
             **({"why": r["reason"]} if r["reason"] else {})}
        m = re.match(r"^lines\[(\d+)\]\.(\w+)$", r["field_path"])
        if m:
            ls.setdefault(int(m.group(1)), {})[m.group(2)] = v
        else:
            h[r["field_path"].split(".", 1)[1]] = v
    if not h and not ls:
        return None
    lines_ = [ls.get(i, {}) for i in range(max(ls) + 1)] if ls else []

    def count(vs):
        return {k: sum(v["verdict"] == k for v in vs) for k in ("ok", "check", "empty")}
    return {"header": h, "lines": lines_,
            "summary": {"header": count(h.values()), "lines": count([v for r in lines_ for v in r.values()])}}
