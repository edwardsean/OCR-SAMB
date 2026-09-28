"""Two more witnesses beside print (phase 6): a person's confirmation, and Satellite's sales-order record.

Why Satellite may confirm, and even correct, an FP: SAMB prints the Faktur Penjualan FROM that record, so the FP's
SOR, Nomor CPO and customer code must equal it. Once the FP's SOR is known (QR code, print, or a person), the record
settles the rest: page 6's pen stroke made the AI read Nomor CPO 6213310 twice; Satellite says 5213310.
If print and Satellite disagree, that is a real discrepancy: a person decides.

The FP's SOR itself is taken from Satellite only as a PAIR: the SOR read AND the Nomor CPO read must both match one SO.
Page 8's faint SOR was misread as page 1's real SOR; its CPO reading doesn't match that SO, so nothing is confirmed.

A customer's paper (TTG, PO) is not printed from Satellite: its PO number is confirmed by Satellite only if it equals
exactly one SO's Nomor CPO AND no other SO's is one character away. Hero's PO numbers run in sequence (58415552,
58415554, 58415556, ...): a one-digit misread lands on a real neighbour, so for them print must decide.

Phase 7b, after the 7a rules (settle_record): an FP whose SOR is known takes its amounts and its item lines from the
record too, as ordered: SAMB prints the FP once, with the goods, and never again, so a later tolakan changes only
Satellite's invoice (paper()). Amounts cut at the scan's edge (page 10's "106.86" is 106.861,53), a faint total (page
8's .80 read .86), item codes misread as another line's (page 1 read 1000566 twice), quantities: all come from the
record, the AI's reading kept beside them. A value print backs in full that still differs is left to a person.
An FP whose SOR can't be read at all may be resolved from its other attributes (resolve_fp).

Satellite's records are the user's export (satellite.sor: one row per SO with its customer PO number and invoice
amounts; satellite.sor_item: one row per SO line), loaded by scripts/load_satellite.py. A person's confirmations live
in staging.field_confirmation and survive every re-check.
"""
import copy
import difflib
import time
import re
from datetime import date

from common import confusions, gates

PRINTED = ("text", "qr", "zoom", "second_look", "adds_up")    # verdicts backed by print
SOR_QR = re.compile(r"^SOR\d{11}$")                               # an FP's QR code: its SOR


def flat(s):
    return re.sub(r"[^0-9A-Z]", "", str(s or "").upper())


def same_id(a, b):
    return bool(flat(a)) and flat(a) == flat(b)


def same_name(a, b):
    return bool(flat(a)) and flat(a) == flat(b)


def edits(a, b):
    """Levenshtein distance, for the short identifiers compared here."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


SO_COLS = ("sor_no, customer_code, customer_name, cpo_no, tgl_so, dpp, ppn, total, vat_pct, billing_no, cgr_date, "
           "order_dpp, order_ppn, order_total, customer_parent, status, cgr_no")
LINE_SUMS = """LEFT JOIN (SELECT sor_no, bool_and(invoice_qty IS NOT DISTINCT FROM qty_pcs) AS unchanged,
                                 bool_and(line_amount > 0 AND vat IS NOT NULL) AS priced,
                                 sum(line_amount) AS lines_dpp, sum(vat) AS lines_ppn
                            FROM satellite.sor_item GROUP BY sor_no) l USING (sor_no)"""
TTL = 600                                          # seconds a process keeps Satellite's SO list
_cache = {"at": 0.0, "sos": None}
_index = {}


def _record(r):
    r = dict(r)
    r["paper"] = paper(r)
    return r


def load(conn, sors=None):
    """{flat SOR: record}. With `sors`: only those SOs, read now. Without: every SO (~40K a month), kept for ten
    minutes per process: grouping and the near-neighbour rule need the whole CPO list. Each record carries `paper`:
    the amounts its FP printed."""
    if sors is not None:
        return {flat(r["sor_no"]): _record(r) for r in conn.execute(
            f"SELECT {SO_COLS}, l.* FROM satellite.sor {LINE_SUMS} WHERE sor_no = ANY(%s)", (list(sors),))}
    if _cache["sos"] is None or time.time() - _cache["at"] > TTL:
        _cache.update(sos={flat(r["sor_no"]): _record(r) for r in conn.execute(
            f"SELECT {SO_COLS}, l.* FROM satellite.sor {LINE_SUMS}")}, at=time.time())
        _index.clear()
    return _cache["sos"]


def paper(so, lines=None):
    """{dpp, ppn, total} the SO's FP printed, or None when that can't be known. SAMB prints the FP once, with the
    goods, and never again (the user, 2026-09-25): it shows the SO as ORDERED. Satellite keeps those amounts beside
    the invoice's (order_dpp/ppn/total; a tolakan changes only the invoice). They equal the lines' amounts and VAT
    summed on every SO whose lines carry amounts (31,512 unchanged, 6,049 changed after printing, Sep 2026), which is
    the fallback for a record without them. `lines`: the SO's lines (satellite.items) when known; otherwise the sums
    load() read with the record."""
    if so.get("order_total") is not None:
        return {"dpp": _num(so.get("order_dpp")), "ppn": _num(so.get("order_ppn")), "total": _num(so["order_total"])}
    if lines:
        unchanged = all(_num(s.get("invoice_qty")) == _num(s.get("qty_pcs")) for s in lines)
        priced = all(_num(s.get("line_amount")) and s.get("vat") is not None for s in lines)
        dpp_, ppn_ = (sum(float(s["line_amount"]) for s in lines), sum(float(s["vat"]) for s in lines)) \
            if priced else (None, None)
    else:
        unchanged, priced = so.get("unchanged") is not False, so.get("priced")
        dpp_, ppn_ = so.get("lines_dpp"), so.get("lines_ppn")
    if unchanged:
        return {k: _num(so.get(k)) for k in ("dpp", "ppn", "total")}
    if priced and dpp_ is not None and ppn_ is not None:
        d, p = round(float(dpp_), 2), round(float(ppn_), 2)
        return {"dpp": d, "ppn": p, "total": round(d + p, 2)}
    return None


INVOICED = ("INVOICE_GENERATED", "INVOICE_REQUESTED")


def chain_of(so):
    """The customer a calibration belongs to: its chain (ship_to_parent_customer_id; Satellite's customer group is
    empty), else the store itself."""
    return (so or {}).get("customer_parent") or (so or {}).get("customer_code")


def chain_name(so):
    """How the Review screen names a customer: its chain, by the store this SO goes to."""
    return f"chain {chain_of(so)} ({(so or {}).get('customer_name') or 'no name'})" if so else None


def free_goods(so):
    """An SOF order: Satellite's as-ordered amounts are 0 on all 409 of them (their lines too), yet some are invoiced
    with real amounts. What their papers print isn't known yet (a question for the mentors), so neither side of the
    bundle checks can use Satellite as its reference."""
    return str((so or {}).get("sor_no") or "").upper().startswith("SOF")


def received(so, lines):
    """The delivery side's reference (verification redesign, S2): what the customer received, in money. Satellite's
    goods receipt (CGR) holds each line's pieces received and rejected (ordered = received + rejected on every complete
    line); the invoice is built from it (invoice qty = CGR qty on all 155,647 lines that have one) and is 0 until the
    SO is billed. Never the FP, never the order. {state, why, dpp, ppn, total, lines: [{line_no, net, with_vat,
    received, ordered, rejected, reason}]}. state:
      invoiced       Satellite's own amounts: the invoice (sor.dpp / ppn / total; per line invoice_nett_amount, kept as
                     sor_item.invoice_amount), discounts applied, no rounding of ours
      reconstructed  the CGR is complete but not billed yet: each line's net amount as ordered × received / ordered
                     (it equals the invoice to Rp 0.01 on 2,119 of 2,122 rejected lines)
      waiting        no complete goods receipt yet: not delivered, not recorded, or lines at 0 received and 0 rejected
      unknown        Satellite can't be the reference: not in its export, cancelled, or free goods (SOF)"""
    def out(state, why, dpp=None, ppn=None, total=None, ls=()):
        return {"state": state, "why": why, "dpp": dpp, "ppn": ppn, "total": total, "lines": list(ls)}
    if not so:
        return out("unknown", "the SO isn't in Satellite's export")
    if so.get("status") == "SO_CANCELLED":
        return out("unknown", "Satellite says the SO was cancelled")
    if free_goods(so):
        return out("unknown", "a free-goods order (SOF): Satellite's order amounts are 0, and what its papers print "
                              "isn't known yet")
    rate = (_num(so.get("vat_pct")) or 11) / 100

    def line(s, net, got):
        return {"line_no": s["line_no"], "net": round(net, 2), "with_vat": round(net * (1 + rate), 2),
                "received": got, "ordered": _num(s.get("qty_pcs")), "rejected": _num(s.get("rejected_qty")) or 0,
                "reason": s.get("reject_reason")}
    if (_num(so.get("total")) or 0) > 0 and (so.get("status") in INVOICED or so.get("cgr_no")):
        return out("invoiced", f"Satellite's invoice ({so.get('billing_no') or 'billed'}), built from its goods receipt",
                   _num(so.get("dpp")), _num(so.get("ppn")), _num(so.get("total")),
                   [line(s, _num(s.get("invoice_amount")) or 0, _num(s.get("cgr_qty"))) for s in lines])
    complete = lines and all(s.get("cgr_qty") is not None and abs((_num(s["cgr_qty"]) or 0) + (_num(s.get("rejected_qty"))
                             or 0) - (_num(s.get("qty_pcs")) or 0)) < 0.001 for s in lines)
    if not so.get("cgr_no") or not complete:
        return out("waiting", f"Satellite has no complete goods receipt yet (the SO is {so.get('status') or 'open'}"
                              + (", its receipt lines aren't filled in" if so.get("cgr_no") else "") + ")")
    ls = [line(s, (_num(s.get("line_amount")) or 0) * (_num(s["cgr_qty"]) or 0) / (_num(s.get("qty_pcs")) or 1),
               _num(s["cgr_qty"])) for s in lines]
    vat = sum((_num(s.get("vat")) or 0) * (_num(s["cgr_qty"]) or 0) / (_num(s.get("qty_pcs")) or 1) for s in lines)
    dpp = round(sum(x["net"] for x in ls), 2)
    return out("reconstructed", "Satellite's goods receipt (not billed yet): each line as ordered × received / ordered",
               dpp, round(vat, 2), round(dpp + vat, 2), ls)


def refresh():
    """Forget the cached SO list and lines (after loading an export)."""
    _cache["sos"] = None
    _index.clear()
    _items.clear()


def items(conn, sor):
    """The SO's lines, in line order."""
    return [dict(r) for r in conn.execute("SELECT * FROM satellite.sor_item WHERE sor_no=%s ORDER BY line_no", (sor,))]


_items = {}                                        # sor -> (when, lines)


def items_for(sor):
    """items(), for callers without a connection: kept ten minutes per process, like the SO list."""
    hit = _items.get(sor)
    if not hit or time.time() - hit[0] > TTL:
        from common import db
        with db.connect() as c:
            hit = _items[sor] = (time.time(), items(c, sor))
    return hit[1]


def _deletions(s):
    return {s} | {s[:i] + s[i + 1:] for i in range(len(s))}


def neighbour_index(pairs):
    """{one-deletion variant: {(flat candidate, sor)}}: two strings at most one edit apart always share a variant, so
    finding them needs no scan of the whole list."""
    idx = {}
    for cand, sor in pairs:
        f = flat(cand)
        if f:
            for d in _deletions(f):
                idx.setdefault(d, set()).add((f, sor))
    return idx


def _cached_index(sos, key):
    """Only the process-wide SO list is worth caching (and safe to: small ad-hoc lists are built fresh)."""
    if sos is not _cache["sos"]:
        return neighbour_index([(s.get(key), s["sor_no"]) for s in sos.values()])
    if key not in _index:
        _index[key] = neighbour_index([(s.get(key), s["sor_no"]) for s in sos.values()])
    return _index[key]


def confirmations(conn, batch_id, page_no):
    """{field: confirmation}. A line cell's field is 'lines[<row key>].<column>' (row_keys)."""
    return {r["field"]: dict(r) for r in conn.execute(
        "SELECT field, value, confirmed_by FROM staging.field_confirmation WHERE batch_id=%s AND page_no=%s",
        (batch_id, page_no))}


ROW_CODE = {"FP": "kode_material", "TTG": "item_code", "PO": "product_code"}
LINE_FIELD = re.compile(r"^lines\[(.+)\]\.(\w+)$")


def row_keys(doc_type, lines):
    """Each row's key for a person's confirmation: its own code as printed (the customer's; SAMB's on an FP), a
    barcode in brackets left out; '<code>#2' for the second row with that code; 'ROW3' for a row with none. Not its
    position: a later reading may list the rows in another order."""
    seen, out = {}, []
    for i, r in enumerate(lines or []):
        code = flat(str(r.get(ROW_CODE.get(doc_type)) or "").split("(")[0]) or f"ROW{i + 1}"
        seen[code] = seen.get(code, 0) + 1
        out.append(code if seen[code] == 1 else f"{code}#{seen[code]}")
    return out


def person_lines(doc_type, fields, line_verdicts, confirmed):
    """A person's confirmations of line cells, applied by the row's key: the value is set (the AI's kept in the row's
    ai_values) and the cell is ✅ by person. A confirmation whose row isn't in this reading waits for it."""
    items = [(m.group(1), m.group(2), c) for name, c in (confirmed or {}).items() for m in [LINE_FIELD.match(name)] if m]
    if not items:
        return fields, line_verdicts
    fields, verdicts = copy.deepcopy(fields), [dict(v) for v in line_verdicts or []]
    rows = fields.get("lines") or []
    keys = row_keys(doc_type, rows)
    for key, col, c in items:
        if key not in keys:
            continue
        i, old = keys.index(key), rows[keys.index(key)].get(col)
        if not same_id(old, c["value"]):
            rows[i].setdefault("ai_values", {}).setdefault(col, old)
            rows[i][col] = c["value"]
        while len(verdicts) <= i:
            verdicts.append({})
        verdicts[i][col] = {"verdict": "ok", "by": "person", "why": f"confirmed by {c['confirmed_by']}"
                            if same_id(old, c["value"]) else f"corrected by {c['confirmed_by']}: read {old!r}"}
    return fields, verdicts


def _ok(v):
    return (v or {}).get("verdict") == "ok"


def near_keys(value, pairs):
    """pairs: [(candidate, sor)], or a neighbour_index of them. (SORs whose candidate equals value, SORs whose
    candidate is one character away)."""
    idx = pairs if isinstance(pairs, dict) else neighbour_index(pairs)
    v = flat(value)
    close = {(c, sor) for d in _deletions(v) for c, sor in idx.get(d, ())}
    return sorted({sor for c, sor in close if c == v}), sorted({sor for c, sor in close if c != v and edits(c, v) <= 1})


def unique_match(value, pairs):
    """The SOR whose candidate equals value, if exactly one does and no other candidate is one character away; else
    (None, why)."""
    exact, near = near_keys(value, pairs)
    if len(exact) == 1 and not near:
        return exact[0], None
    if exact and near:
        return None, f"matches {exact[0]} in Satellite, but {near[0]} is one character away"
    if len(exact) > 1:
        return None, f"matches several SOs in Satellite: {exact}"
    return None, None


def store_words(name):
    """The words of a store's name, for S4: letters and digits, two or more (BOOTS HARAPAN INDAH AVENUE → 4 words)."""
    return set(re.findall(r"[A-Z0-9]{2,}", str(name or "").upper()))


def store_df(sos):
    """{word: in how many distinct store names in Satellite}; cached with the process-wide SO list. INDONESIA is in
    62 of 4,184, HARAPAN in 11, EASTVARA in 1: a common word names no store."""
    def build():
        import collections
        return collections.Counter(w for n in {s["customer_name"] for s in sos.values() if s.get("customer_name")}
                                   for w in store_words(n))
    if sos is not _cache["sos"]:
        return build()
    if "df" not in _index:
        _index["df"] = build()
    return _index["df"]


def store_can_tell(sor, near, sos):
    """S4: could the store printed on a page tell this SO from the SOs one character away? Only when every one of
    them ships to a store with a word this SO's store lacks. Not for one store's run of numbers (AEON EASTVARA's POs
    10101000125411 … 448, Hero's DC, Duta Buah BSD's PO.2026.09.32028 and …29): the question would be wasted."""
    mine = store_words((sos.get(flat(sor)) or {}).get("customer_name"))
    return bool(near) and bool(mine) and all(mine - store_words((sos.get(flat(n)) or {}).get("customer_name"))
                                             for n in near)


def _stores(sos):
    """{store name: its words}, every ship-to in Satellite; cached with the process-wide SO list."""
    def build():
        return {s["customer_name"]: store_words(s["customer_name"]) for s in sos.values() if s.get("customer_name")}
    if sos is not _cache["sos"]:
        return build()
    if "stores" not in _index:
        _index["stores"] = build()
    return _index["stores"]


def by_store(ship_to, sor, near, sos):
    """S4: (True, why) when the store the page prints names this SO's ship-to, two ways:
      - against the SOs one character away (`near`): every store word on the page that fits one of them fits this
        SO's store too;
      - against every store in Satellite (so also `near`): this SO's store shares more words with the page's than
        any other store does. A number misread by two characters lands outside `near`; this still catches it.
    Boots ships one order to ten stores (POs 4505832720 … 29): page 4's 'BOOTS HARAPAN INDAH BEKASI' fits …24
    (BOOTS HARAPAN INDAH AVENUE) by BOOTS HARAPAN INDAH; the others share only BOOTS. A misread …23 would point to
    BINTARO XCHANGE 2, which the page's store doesn't fit: held. 'BOOTS' alone fits every Boots store: held."""
    printed = (ship_to or {}).get("source_text") or (ship_to or {}).get("value")
    if not printed or (ship_to or {}).get("unsure"):
        return False, "the AI OCR found no store on the page it was sure of"
    page = store_words(printed)
    so = sos.get(flat(sor)) or {}
    name = so.get("customer_name")
    mine = page & store_words(name)
    theirs = {n: page & store_words((sos.get(flat(n)) or {}).get("customer_name")) for n in near}
    better = next((n for n, t in theirs.items() if not t <= mine), None)
    if better:
        return False, (f"the page's store {printed!r} fits {better} ({(sos.get(flat(better)) or {}).get('customer_name')}),"
                       f" one character away: misread?")
    rival = next((s for s, w in _stores(sos).items() if s != name and len(page & w) >= len(mine)), None) \
        if mine else "no store"
    if rival:
        return False, (f"the page's store {printed!r} fits {rival} at least as well as {name}" if mine else
                       f"the page's store {printed!r} doesn't fit {name}")
    return True, (f"the page's store {printed!r} names {name} by {' '.join(sorted(mine))}; none of the {len(near)} "
                  f"SO{'s' if len(near) > 1 else ''} one character away ships there, and no other store in Satellite "
                  "fits it as well")


def _rows_decide(name, value, index, doc_type, fields, verdicts, why, items_of):
    """A key only the AI read that names exactly one SO, with others one character away: the page's rows decide
    when they fit that SO clearly better than every neighbour (grouper.matching.rows_tell). No model call, so it goes
    before the store question. On both batches: 22 correct keys settle; a key bent to each neighbour's number (164)
    never does."""
    exact, near = near_keys(value, index)
    if len(exact) != 1 or not near or not items_of or not (fields.get("lines") or []):
        return why
    from grouper import matching                          # grouper sits on common; imported when first needed
    ok, said = matching.rows_tell(doc_type, fields, exact[0], near, items_of)
    if ok:
        verdicts[name] = {"verdict": "ok", "by": "rows", "why": f"equals the key of {exact[0]} in Satellite; {said}"}
        return None
    return f"{why}; {said}"


def _store_decides(name, value, index, sos, ship_to, verdicts, why):
    """S4, for a key only the AI read that matches exactly one SO while others are one character away: the store
    printed on the page decides, when it can (store_can_tell). Not asked yet (ship_to None): the verdict carries
    `store` (the SO it would name), so the page asks once. Returns the reason left, or None once settled."""
    exact, near = near_keys(value, index)
    if len(exact) != 1 or not near or not store_can_tell(exact[0], near, sos):
        return why
    if ship_to is None:
        if verdicts.get(name) is not None:
            verdicts[name]["store"] = exact[0]
        return f"{why}; the store printed on the page can tell them apart (not asked yet)"
    ok, swhy = by_store(ship_to, exact[0], near, sos)
    if ok:
        verdicts[name] = {"verdict": "ok", "by": "ship_to", "why": f"equals the key of {exact[0]} in Satellite; {swhy}"}
        return None
    return f"{why}; {swhy}"


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _like(a, b):
    return difflib.SequenceMatcher(None, flat(a), flat(b)).ratio() if flat(a) and flat(b) else 0.0


NAME_ALIKE, NAME_CLEAR = 0.8, 0.05


def pair_lines(rows, so_lines, trusted=()):
    """Which SO line each FP row prints: ([(row index, SO line index)], SO lines no row matched).
    The name decides: SAMB prints the SO line's name, so a row pairs with the line its name matches best, when that
    line is clearly the best (by NAME_CLEAR) and alike (NAME_ALIKE). A code the AI read never outweighs the name:
    page 1 read 1000566 on two rows, and row 5's 1000564 is another line's code. Only where names tie (one product in
    variants: Boots' LUMINOUS / SMOOTH / YOUTHFUL GLUTAGLOW) does the code decide, and only a code print backs
    (`trusted`: row indexes). Anything less stays unpaired."""
    cands = []
    for i, r in enumerate(rows):
        likes = [_like(r.get("nama_produk"), s.get("description")) for s in so_lines]
        coded = [j for j, s in enumerate(so_lines) if same_id(r.get("kode_material"), s.get("item_code"))]
        best = max(likes, default=0.0)
        top = [j for j, x in enumerate(likes) if best - x < NAME_CLEAR] if best >= NAME_ALIKE else []
        if len(top) == 1:
            cands.append((best, i, top[0]))
        elif i in trusted and len(coded) == 1 and (not top or coded[0] in top):
            cands.append((likes[coded[0]], i, coded[0]))
    pairs, rows_used, lines_used = [], set(), set()
    for _, i, j in sorted(cands, reverse=True):
        if i not in rows_used and j not in lines_used:
            pairs.append((i, j))
            rows_used.add(i)
            lines_used.add(j)
    return sorted(pairs), [j for j in range(len(so_lines)) if j not in lines_used]


def _by_total(sos):
    """{the total its FP printed: [SO]}, cached with the process-wide SO list."""
    def build():
        idx = {}
        for s in sos.values():               # the order side only: what the FP printed, never the invoice
            t = _num(((s["paper"] if "paper" in s else paper(s)) or {}).get("total"))
            if t:
                idx.setdefault(t, []).append(s)
        return idx
    if sos is not _cache["sos"]:
        return build()
    if "total" not in _index:
        _index["total"] = build()
    return _index["total"]


def resolve_fp(fields, sos, items_of):
    """An FP whose SOR couldn't be read: (SO, why) when, among the SOs whose total the printed total could be (as
    read, or one confusable character different), exactly one also agrees with something else on the page, exactly:
    the customer code, the Nomor CPO, the customer's name, or every item code printed. Else (None, why or None).
    SAMB ships one order to many stores: eight Boots SOs of 1,126,011.00 on 4 Sep 2026, and page 8's 754,022.80 is
    also Hari Hari Ciledug's, same two items. Only the store tells them apart, so a total never decides alone, and
    a misread store (page 8's 'BINTANG TANOSSEL') decides nothing."""
    printed = (fields.get("total") or {}).get("source_text") or (fields.get("total") or {}).get("value")
    if not printed:
        return None, None
    idx, seen = _by_total(sos), {}
    for amount, change in confusions.amounts(printed).items():
        for s in idx.get(amount, ()):
            seen.setdefault(s["sor_no"], (s, change))
    if not seen:
        return None, None
    codes = {flat(r.get("kode_material")) for r in fields.get("lines") or []} - {""}
    agreed = {}
    for sor, (so, change) in seen.items():
        agree = [name for name, got, want in (
            ("the customer code", (fields.get("customer_code") or {}).get("value"), so.get("customer_code")),
            ("the Nomor CPO", (fields.get("nomor_cpo") or {}).get("value"), so.get("cpo_no")),
            ("the customer's name", (fields.get("customer_name") or {}).get("value"), so.get("customer_name")))
            if same_id(got, want)]
        if codes and codes == {flat(x.get("item_code")) for x in items_of(so["sor_no"])}:
            agree.append("every item code")
        agreed[sor] = agree
    # what agrees with every candidate tells none apart (two stores, one order: the items agree with both)
    common = set.intersection(*(set(a) for a in agreed.values())) if len(seen) > 1 else set()
    survivors = [(seen[sor][0], seen[sor][1], a) for sor, a in agreed.items() if set(a) - common]
    if len(survivors) != 1:
        return None, (f"the total {printed!r} fits only {next(iter(seen))}, but nothing else on the page agrees"
                      if len(seen) == 1 else
                      f"the total {printed!r} could be {len(seen)} SOs in Satellite, and nothing read on the page "
                      "tells them apart" if not survivors else
                      f"the total {printed!r} could be {len(seen)} SOs in Satellite, and the page points to "
                      f"{len(survivors)} of them")
    so, change, agree = survivors[0]
    return so, (f"the total {printed!r}" + (f" ({change})" if change else "") + f" fits {len(seen)} SO"
                + ("s" if len(seen) > 1 else "") + f" in Satellite, and only {so['sor_no']} matches "
                + (", ".join(agree[:-1]) + " and " if len(agree) > 1 else "") + agree[-1] + " read")


def _so_of_customer_doc(fields, verdicts, sos):
    """The one SO a TTG or PO names through a backed key: its printed SOR (No Ref), else its PO number when exactly
    one SO carries it (a split order has several: none is chosen)."""
    ref = (fields.get("no_ref") or {}).get("value")
    if ref and _ok(verdicts.get("no_ref")):
        f = flat(ref)
        return sos.get(f if f.startswith("SOR") else "SOR" + f)
    po = (fields.get("purchase_order_no") or {}).get("value")
    if po and _ok(verdicts.get("purchase_order_no")):
        hits = [s for s in sos.values() if same_id(s.get("cpo_no"), po)] if sos is not _cache["sos"] else \
            [sos[flat(sor)] for c, sor in _cached_index(sos, "cpo_no").get(flat(po), ()) if c == flat(po)]
        return hits[0] if len(hits) == 1 else None
    return None


def settle(doc_type, fields, verdicts, sos, confirmed=None, items_of=None, ship_to=None, qr=None):
    """(fields, verdicts) after a person's confirmations, the page's SOR QR code and Satellite's record. Both are the
    type's names (the projection). Copies; the AI's own reading is kept as ai_value wherever a value changes. ship_to:
    the page's answer to the store question (S4), None when it wasn't asked. qr: the page's decoded QR text."""
    fields, verdicts = copy.deepcopy(fields or {}), copy.deepcopy(verdicts or {})

    def put(name, value, by, why):
        f = fields[name] = fields.get(name) or {}         # a field the AI didn't read comes as None
        if not same_id(f.get("value"), value) or f.get("value") is None:
            f.setdefault("ai_value", f.get("value"))
            f["value"] = value
        verdicts[name] = {"verdict": "ok", "by": by, "why": why}

    for name, c in (confirmed or {}).items():            # 1. a person has the last word
        if LINE_FIELD.match(name):                        # a line cell: person_lines()
            continue
        old = (fields.get(name) or {}).get("value")
        put(name, c["value"], "person", f"confirmed by {c['confirmed_by']}" if same_id(old, c["value"])
            else f"corrected by {c['confirmed_by']}: read {old!r}")

    def mine(name):
        return (verdicts.get(name) or {}).get("by") == "person"

    if doc_type == "FP" and qr and SOR_QR.match(qr) and not mine("sor"):   # 1b. the QR code is the SOR, decoded
        got = (fields.get("sor") or {}).get("value")      # digitally: page 13 read SOR26110254669, its QR 264669
        if same_id(got, qr):
            verdicts["sor"] = {"verdict": "ok", "by": "qr"}
        else:
            put("sor", qr, "qr", f"read {got!r}; the QR code says {qr}" if got else f"not read; the QR code says {qr}")

    if doc_type == "FP":                                  # 2. the FP is printed from Satellite's record
        sor = (fields.get("sor") or {}).get("value")
        so = sos.get(flat(sor))
        cpo = (fields.get("nomor_cpo") or {}).get("value")
        if so and not _ok(verdicts.get("sor")) and same_id(cpo, so["cpo_no"]):
            why = "the SOR and the Nomor CPO read both match one SO in Satellite"
            verdicts["sor"] = {"verdict": "ok", "by": "satellite", "why": why}
            if not mine("nomor_cpo"):
                verdicts["nomor_cpo"] = {"verdict": "ok", "by": "satellite", "why": why}
        if not _ok(verdicts.get("sor")) and items_of:      # 2b. no SOR read: the page's other attributes (7b)
            so, why = resolve_fp(fields, sos, items_of)
            if so:
                put("sor", so["sor_no"], "satellite", why)
            elif why and verdicts.get("sor"):
                verdicts["sor"]["why"] = f"{verdicts['sor'].get('why') or 'not backed by print'}; {why}"
            so = sos.get(flat((fields.get("sor") or {}).get("value")))
        if so and _ok(verdicts.get("sor")):
            for name, key in (("nomor_cpo", "cpo_no"), ("customer_code", "customer_code")):
                want, got, v = so.get(key), (fields.get(name) or {}).get("value"), verdicts.get(name) or {}
                if not want or mine(name):
                    continue
                if same_id(got, want):
                    if not _ok(v):
                        verdicts[name] = {"verdict": "ok", "by": "satellite", "why": "matches Satellite's SO record"}
                elif _ok(v) and v.get("by") in PRINTED:   # print and the record disagree: a person decides
                    verdicts[name] = {"verdict": "check", "why": f"printed {got!r}, but Satellite's SO record says {want}",
                                      "conflict": "satellite"}
                else:
                    put(name, want, "satellite", f"read {got!r}; Satellite's SO record says {want}" if got
                        else f"not read; Satellite's SO record says {want}")
            want, got = so.get("customer_name"), (fields.get("customer_name") or {}).get("value")
            v = verdicts.get("customer_name") or {}
            if want and not _ok(v) and not mine("customer_name"):
                if same_name(got, want):
                    verdicts["customer_name"] = {"verdict": "ok", "by": "satellite", "why": "matches Satellite's SO record"}
                elif v:                                   # a ship-to name may differ in form: a hint, not a correction
                    v["why"] = f"{v.get('why') or 'not backed by print'}; Satellite has {want!r}"

    if doc_type in ("TTG", "PO"):                         # 3. a customer's paper: exact, and no near neighbour,
        po, v = (fields.get("purchase_order_no") or {}).get("value"), verdicts.get("purchase_order_no")
        if po and not _ok(v) and not mine("purchase_order_no"):   # or else the store printed on it (S4)
            index = _cached_index(sos, "cpo_no")
            sor, why = unique_match(po, index)
            if sor:
                verdicts["purchase_order_no"] = {"verdict": "ok", "by": "satellite", "why":
                                                 f"equals the Nomor CPO of {sor} in Satellite; no other SO's is one "
                                                 "character away"}
            elif why:
                why = _rows_decide("purchase_order_no", po, index, doc_type, fields, verdicts, why, items_of)
                why = why and _store_decides("purchase_order_no", po, index, sos, ship_to, verdicts, why)
                if why and v:
                    v["why"] = f"{v.get('why') or 'not backed by print'}; {why}"
    if doc_type == "TTG":
        ref, v = (fields.get("no_ref") or {}).get("value"), verdicts.get("no_ref")
        if ref and not _ok(v) and not mine("no_ref"):
            index = _cached_index(sos, "sor_no")
            f = flat(ref)
            sor, why = unique_match(f if f.startswith("SOR") else "SOR" + f, index)
            if sor:
                verdicts["no_ref"] = {"verdict": "ok", "by": "satellite",
                                      "why": "is an SO in Satellite; no other SO is one character away"}
            elif why and not _ok(verdicts.get("purchase_order_no")):
                key = f if f.startswith("SOR") else "SOR" + f
                why = _rows_decide("no_ref", key, index, doc_type, fields, verdicts, why, items_of)
                why = why and _store_decides("no_ref", key, index, sos, ship_to, verdicts, why)
                if why and v:
                    v["why"] = f"{v.get('why') or 'not backed by print'}; {why}"
        so = _so_of_customer_doc(fields, verdicts, sos)       # 4. the receipt date vs Satellite's CGR date (7b)
        day, v = str((fields.get("posting_date") or {}).get("value") or ""), verdicts.get("posting_date") or {}
        if so and so.get("cgr_date") and gates.ISO_DAY.match(day) and not mine("posting_date"):
            apart = abs((so["cgr_date"] - date.fromisoformat(day)).days)
            if apart == 0 and not _ok(v):
                verdicts["posting_date"] = {"verdict": "ok", "by": "satellite",
                                            "why": f"equals the goods receipt date of {so['sor_no']} in Satellite"}
            elif apart > CGR_DAYS and _ok(v) and v.get("by") in PRINTED:
                verdicts["posting_date"] = {"verdict": "check", "why": f"{day} is {apart} days from the goods receipt "
                                            f"date of {so['sor_no']} in Satellite ({so['cgr_date']}); misread?",
                                            "conflict": "satellite"}
    return fields, verdicts


CGR_DAYS = 7     # a receipt date this far from Satellite's CGR date is suspect (page 2: 31 Aug vs 1 Sep, 1 day)


def settle_record(doc_type, fields, res, sos, items_of=None):
    """Phase 7b, after the 7a rules: an FP whose SOR is resolved takes DPP, PPN, Total and its item lines (code,
    name, cartons / pieces) from Satellite's record, the one it was printed from, AS ORDERED (paper()): the FP is
    printed once, with the goods, and a tolakan later changes only the invoice. A value that already matches keeps
    its ✅ (or gets one, by satellite); a different one is corrected, the AI's reading kept (ai_value, a row's
    ai_values). If print backs the different value in full, a person decides. An SO whose printed amounts can't be
    known (changed, with a line carrying no amount) corrects no amount. Returns (fields, verdicts); res gets
    `so_lines`: the SO lines no row matched (a row the reading missed)."""
    if doc_type != "FP" or not res or not _ok(res["header"].get("sor")):
        return fields, res
    so = sos.get(flat((fields.get("sor") or {}).get("value")))
    if not so:
        return fields, res
    lines = items_of(so["sor_no"]) if items_of else []
    printed = paper(so, lines) or {}                    # the SO as ordered: what this FP printed (see paper())
    changed = printed and any(_num(printed[k]) != _num(so.get(k)) for k in ("dpp", "ppn", "total"))
    note = " as ordered (the SO changed after this FP was printed)" if changed else ""
    fields, h = copy.deepcopy(fields), {k: dict(v) for k, v in res["header"].items()}
    for name in ("dpp", "ppn", "total"):
        want, v = printed.get(name), h.get(name) or {}
        f = fields[name] = fields.get(name) or {}                 # an amount the AI didn't read comes as None
        got = _num(f.get("value"))
        if want is None or v.get("by") == "person":
            continue
        if got == want:
            if not _ok(v):
                h[name] = {"verdict": "ok", "by": "satellite", "why": f"matches Satellite's SO record{note}"}
        elif _ok(v) and v.get("by") in PRINTED and gates.complete(f.get("source_text")):
            h[name] = {"verdict": "check", "why": f"printed {f.get('source_text')!r}, but Satellite's SO record says "
                                                  f"{want:,.2f}: changed after printing?", "conflict": "satellite"}
        else:
            f.setdefault("ai_value", f.get("value"))
            f["value"] = f"{want:.2f}"
            read = f.get("source_text") or f.get("ai_value")
            h[name] = {"verdict": "ok", "by": "satellite", "why": (f"read {read!r}" if read else "not read")
                       + f"; Satellite's SO record says {want:,.2f}{note}"}
    rows, verdicts = [dict(r) for r in fields.get("lines") or []], [dict(x) for x in res.get("lines") or []]
    trusted = {i for i, rv in enumerate(verdicts) if (rv.get("kode_material") or {}).get("by") in ("text", "person")}
    pairs, missing = pair_lines(rows, lines, trusted)
    for i, j in pairs:
        s, row, rv = lines[j], rows[i], verdicts[i] if i < len(verdicts) else {}
        per = int(s.get("pcs_per_uom") or 1)
        ordered = int(_num(s.get("qty_pcs")) or 0)           # the FP prints what was ordered, never re-printed
        for col, want in (("kode_material", s.get("item_code")), ("nama_produk", s.get("description")),
                          ("qty_crt", str(ordered // per)), ("qty_pcs", str(ordered % per))):
            got, v = row.get(col), rv.get(col) or {"verdict": "empty"}
            if want in (None, "") or v.get("by") == "person":
                continue
            if (_num(got) == _num(want)) if col.startswith("qty") else same_name(got, want):
                if not _ok(v):
                    rv[col] = {"verdict": "ok", "by": "satellite", "why": f"matches line {s['line_no']} of the SO"}
            elif _ok(v) and v.get("by") == "text":
                rv[col] = {"verdict": "check", "why": f"its row prints {got!r}, but line {s['line_no']} of the SO "
                                                      f"says {want!r}"}
            else:
                row.setdefault("ai_values", {}).setdefault(col, got)
                row[col] = want
                rv[col] = {"verdict": "ok", "by": "satellite", "why": ("not read" if got in (None, "") else
                                                                       f"read {got!r}")
                           + f"; line {s['line_no']} of the SO says {want!r}"}
        k, v = str(row.get("kemasan") or "").upper(), rv.get("kemasan") or {}
        if _ok(v) and per not in {int(x) for x in re.findall(r"\d+", k)} and re.search(r"\d+\s*[X×]\s*\d", k):
            rv["kemasan"] = {"verdict": "check", "why": f"{row.get('kemasan')!r}, but line {s['line_no']} of the SO "
                                                        f"packs {per} a carton"}
    fields["lines"] = rows

    def count(vs):
        return {k: sum(v["verdict"] == k for v in vs) for k in ("ok", "check", "empty")}
    return fields, {**res, "header": h, "lines": verdicts,
                    "so_lines": {"sor": so["sor_no"], "paired": len(pairs), "missing": [lines[j]["line_no"] for j in missing]},
                    "summary": {"header": count(h.values()), "lines": count([v for r in verdicts for v in r.values()])}}
