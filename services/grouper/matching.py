"""Phase 7c: which SO line each customer line is (a PO or TTG row).

SAMB's order names a product with its own material code (1001187 ELLIPS MOROCCAN 20 GR NUTRICOLOR); the customer's
paper with the customer's own code (Hero's PLU 3078035), a barcode (8993417489938) and its own wording ("ELLIPS HAIR
MASK NUTRI COLOR 20GR"). Satellite keeps no barcode, and no table maps one to the other (§07). So, in this order:

  person     a pair a person confirmed or refused for this row (Review, 7d)
  map        satellite.product_code_map: the chain's code (customer_parent), or the printed barcode, confirmed before
  only_line  the SO has one line and the document one row
  numbers    a PO row: the only SO line with its quantity in pieces AND a price that fits (per piece or per carton,
             with or without PPN, up to the customer's rounding to whole rupiah), wanted by no other row
  amount     a PO row: the only SO line whose amount (net, or with PPN) its printed amount fits, to 0.03%: money
             doesn't depend on how each side counts pieces (Duta Buah's PO: 48 PCS; SAMB's line: 2). Two rows at one
             amount (ALPENLIEBE KARAMEL and STRAWBERRY, both 32,760) are told apart by name, clearly, or not at all
  po_row     a TTG row takes the line of the PO row with the same code (one customer, one system)
  ai         proposed by a text model on Z.ai (propose()), checked on the numbers; a person confirms it once
The first six decide; an AI pair is a proposal until a person confirms it (7d), and that confirmation fills the map,
so the same product then matches with no AI and no person. Boots' three Vaseline 425ML variants share quantity and
price: only their names tell them apart, which is exactly what an AI can get wrong confidently.

  python -m grouper.matching propose <batch> [<sor>]   AI proposals for rows nothing else matched (vf-teacher: it
                                                        holds ZAI_API_KEY). One call per document, MATCH_PAUSE (30 s)
                                                        apart: Z.ai's free tier refused a 4th call within 2 minutes
                                                        (1302). Stops at the rate limit; re-run it later. Rows already
                                                        proposed aren't asked again. Nothing is confirmed.
"""
import os
import re
import sys
import time

from common import db, satellite, verify

AI_MODEL = os.environ.get("MATCH_MODEL", "glm-4.7-flash")        # text only, free on Z.ai; not Groq's AI OCR budget
PAUSE = int(os.environ.get("MATCH_PAUSE", "30"))                   # seconds between calls
COLS = {"TTG": ("item_code", "material_description"), "PO": ("product_code", "product_description")}
CARTON = {"KTN", "CT", "CTN", "CAR", "CRT", "KRT", "KARTON", "CASE", "CS", "DUS", "BOX"}
BARCODE = re.compile(r"(?<!\d)(\d{12,13})(?!\d)")                 # EAN-13 / UPC-A; 8-digit SKUs aren't barcodes here


def barcode_ok(s):
    """A barcode whose check digit is right (EAN-13, UPC-A): a misread barcode almost never passes."""
    d = [int(c) for c in s]
    total = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(reversed(d[:-1])))
    return (10 - total % 10) % 10 == d[-1]


def _bonus(r):
    """A free row: Hari Hari prints a bonus carton as its own row, price 0 and amount 0 ('… 1 KTN 0 0')."""
    nums = re.findall(r"\d[\d.,]*", str(r.get("row_text") or ""))
    return len(nums) >= 2 and all(verify.amount(x) == 0 for x in nums[-2:])


def rows_of(doc_type, fields):
    """The customer's rows as the matcher sees them: its own code (the barcode taken out), the barcodes on the row,
    the wording, the quantity and price as read, and whether it is a bonus row."""
    code_c, desc_c = COLS[doc_type]
    out = []
    for i, r in enumerate((fields or {}).get("lines") or []):
        raw = str(r.get(code_c) or "")
        codes = {b for b in BARCODE.findall(f"{raw} {r.get('row_text') or ''}") if barcode_ok(b)}
        own = verify.flat(raw.split("(")[0])
        amounts = [a for a in (verify.amount(x) for x in re.findall(r"\d[\d.,]*\d", str(r.get("row_text") or "")))
                   if a is not None]
        out.append({"i": i, "code": "" if own in codes else own, "barcodes": codes, "desc": r.get(desc_c) or "",
                    "qty": r.get("qty"), "uom": r.get("uom"), "price": r.get("unit_price"),
                    "discount": r.get("discount"), "bonus": _bonus(r),
                    "amount": amounts[-1] if amounts else None})       # the row's printed amount: its last one
    return out


def pieces(row, s):
    """The row's quantity in pieces, with the SO line's pieces per carton; None when it isn't a quantity ('1 x 48'
    is a pack size)."""
    q = str(row.get("qty") or "").upper()
    nums = re.findall(r"\d[\d.,]*", q)
    if re.search(r"\d\s*[X×]\s*\d", q) or len(nums) != 1:
        return None
    n = verify.amount(nums[0])
    unit = re.sub(r"[^A-Z]", "", q) or verify.flat(row.get("uom"))
    return n * float(s.get("pcs_per_uom") or 1) if unit in CARTON else n


def price_fits(price, s):
    """Does a printed unit price equal the SO line's, in any of the ways a customer prints it: per piece or per
    carton, with or without 11% PPN, rounded to whole rupiah? (Hero 518,919.00 per carton; Boots 63,361 =
    57,082.43 × 1.11; Hari Hari 5,630.63 per piece.) None when no price was read."""
    p = verify.amount(price) if price not in (None, "") else None
    if p is None:
        return None
    per, pc = float(s.get("pcs_per_uom") or 1), float(s.get("price_pcs") or 0)
    uom = float(s.get("price_uom") or 0) or pc * per
    return any(abs(p - x) <= 1 for base in (pc, uom) for x in (base, base * 1.11))


AMOUNT_FIT = 0.0003     # per-piece price rounding: Duta Buah's rows are 0.06 to 17.45 from SAMB's lines (32K to 288K)


def amount_fits(amount, s):
    """Does a row's printed amount equal the SO line's amount as ordered, net or with PPN, to 0.03% (at least Rp 1)?"""
    if not amount:
        return False
    net = float(s.get("line_amount") or 0)
    if net <= 0:
        return False
    return any(abs(amount - x) <= max(1.0, AMOUNT_FIT * x) for x in (net, net + float(s.get("vat") or 0)))


def load_map(c, chain):
    """{("code", customer code): SAMB item, ("barcode", barcode): SAMB item} for the SO's chain; a barcode from any."""
    out = {}
    for r in c.execute("""SELECT customer_code, customer_item_code, customer_barcode, samb_material_code
                            FROM satellite.product_code_map WHERE customer_code = %s OR customer_barcode IS NOT NULL""",
                       (chain,)):
        if r["customer_code"] == chain:
            out[("code", verify.flat(r["customer_item_code"]))] = r["samb_material_code"]
        if r["customer_barcode"]:
            out[("barcode", r["customer_barcode"])] = r["samb_material_code"]
    return out


def r_amount(rows, i):
    return next((r["amount"] for r in rows if r["i"] == i), None)


def amount_str(a):
    return f"{a:,.2f}" if a is not None else "—"


def match(docs, so_lines, pmap, decisions):
    """docs: [(page_no, doc_type, rows_of(...))]; pmap: load_map(); decisions: {(page_no, row): staging.line_match
    row} (a person's pairs, the AI's proposals). Returns {(page_no, row): {line: SO line index | None, how, status:
    matched | proposed | refused | bonus | none, why}}. One document never uses an SO line twice."""
    line_of = {verify.flat(s["item_code"]): j for j, s in enumerate(so_lines)}
    by_no = {s["line_no"]: j for j, s in enumerate(so_lines)}
    out, used = {}, {}

    def take(page, i, j, how, status, why):
        out[(page, i)] = {"line": j, "how": how, "status": status, "why": why}
        if j is not None and status == "matched":
            used.setdefault(page, set()).add(j)

    def free(page, j):
        return j not in used.get(page, set())

    for page, dt, rows in docs:                                          # a person, then the map
        for r in rows:
            d = decisions.get((page, r["i"]))
            if r["bonus"]:
                take(page, r["i"], None, "bonus", "bonus", "a free (bonus) row: not an SO line")
            elif d and d["how"] == "person":
                j = by_no.get(d["so_line_no"])
                take(page, r["i"], j, "person", "matched" if d["status"] == "matched" and j is not None else "refused",
                     d.get("reason"))
            else:
                item = pmap.get(("code", r["code"])) if r["code"] else None
                item = item or next((pmap[("barcode", b)] for b in sorted(r["barcodes"]) if ("barcode", b) in pmap), None)
                j = line_of.get(verify.flat(item)) if item else None
                if j is not None and free(page, j):
                    take(page, r["i"], j, "map", "matched", f"SAMB's {item} (product map)")
    for page, dt, rows in docs:                                          # the SO's only line
        live = [r for r in rows if not r["bonus"]]
        if len(so_lines) == 1 and len(live) == 1 and (page, live[0]["i"]) not in out and free(page, 0):
            take(page, live[0]["i"], 0, "only_line", "matched", "the SO's only line, the document's only row")
    for page, dt, rows in docs:                                          # a PO row by its numbers
        if dt != "PO":
            continue
        fits = {}
        for r in rows:
            if (page, r["i"]) not in out:
                fits[r["i"]] = [j for j, s in enumerate(so_lines) if free(page, j) and pieces(r, s) is not None
                                and abs(pieces(r, s) - float(s.get("qty_pcs") or 0)) < 0.001 and price_fits(r["price"], s)]
        for i, js in fits.items():
            if len(js) == 1 and not any(js[0] in o for k, o in fits.items() if k != i):
                s = so_lines[js[0]]
                take(page, i, js[0], "numbers", "matched",
                     f"the only SO line of {float(s['qty_pcs']):g} pieces at this price")
    for page, dt, rows in docs:                                          # a PO row by its printed amount
        if dt != "PO":
            continue
        desc = {r["i"]: r["desc"] for r in rows}
        claims = {}
        for r in rows:
            if (page, r["i"]) in out or r["bonus"]:
                continue
            js = [j for j, s in enumerate(so_lines) if free(page, j) and amount_fits(r["amount"], s)]
            if len(js) == 1:
                claims.setdefault(js[0], []).append(r["i"])
        for j, who in claims.items():
            s = so_lines[j]
            likes = sorted(((satellite._like(desc[i], s["description"]), i) for i in who), reverse=True)
            if len(who) > 1 and likes[0][0] - likes[1][0] < satellite.NAME_CLEAR:
                continue                                                 # two rows, one amount, no clear name
            i = likes[0][1]
            take(page, i, j, "amount", "matched", f"its printed amount {amount_str(r_amount(rows, i))} is SO line "
                 f"{s['line_no']}'s ({float(s['line_amount']):,.2f})" + (" and the name is the closest" if len(who) > 1 else ""))
    po_line = {r["code"]: out[(page, r["i"])]["line"] for page, dt, rows in docs if dt == "PO" for r in rows
               if r["code"] and out.get((page, r["i"]), {}).get("status") == "matched"}
    for page, dt, rows in docs:                                          # a TTG row by its PO row
        for r in rows:
            j = po_line.get(r["code"]) if dt == "TTG" and r["code"] else None
            if (page, r["i"]) not in out and j is not None and free(page, j):
                take(page, r["i"], j, "po_row", "matched", f"the PO's row with the same code {r['code']}")
    for page, dt, rows in docs:                                          # the AI's proposals, then nothing
        for r in rows:
            if (page, r["i"]) in out:
                continue
            d = decisions.get((page, r["i"]))
            if d and d["how"] == "ai" and d["status"] == "proposed" and by_no.get(d["so_line_no"]) is not None:
                take(page, r["i"], by_no[d["so_line_no"]], "ai", "proposed", d.get("reason"))
            else:
                take(page, r["i"], None, "none", "none", "no SO line matched yet")
    return out


PROMPT = """You match the lines of a customer's {doc} to the lines of SAMB's sales order for the same delivery.
They are the same goods named two ways: the customer uses its own item codes and wording, SAMB its material codes and
product names. Quantities and prices are given for context only; the customer may print them per carton or per piece.

Customer lines ({doc}):
{rows}

SAMB's sales order lines:
{lines}

Answer with ONLY a JSON object: {{"pairs": [{{"row": <customer row number>, "line": <SAMB line number, or null>,
"why": "<the words or numbers that show it>"}}]}}. One entry per customer row. Each SAMB line at most once. Use null
when you are not sure: a wrong pair is worse than none."""


def _prompt(doc_type, rows, so_lines):
    rs = "\n".join(f"row {r['i']}: code {r['code'] or '-'}, barcode {', '.join(sorted(r['barcodes'])) or '-'}, "
                   f"\"{r['desc']}\", quantity {r['qty'] or '-'} {r['uom'] or ''}, price {r['price'] or '-'}"
                   for r in rows)
    ls = "\n".join(f"line {s['line_no']}: material {s['item_code']}, \"{s['description']}\", "
                   f"{float(s['pcs_per_uom'] or 1):g} pieces per carton, {float(s['qty_pcs'] or 0):g} pieces ordered"
                   for s in so_lines)
    return PROMPT.format(doc="purchase order" if doc_type == "PO" else "goods receipt", rows=rs, lines=ls)


def checked(doc_type, rows, so_lines, pairs):
    """The AI's pairs that survive the numbers it doesn't control: one-to-one; a PO row's quantity in pieces equals
    the line's ordered pieces and its price fits; a TTG row received no more than was ordered. [(row, line_no, why)]"""
    by_no, rows_i = {s["line_no"]: s for s in so_lines}, {r["i"]: r for r in rows}
    seen, out = set(), []
    for p in pairs or []:
        r, s = rows_i.get(p.get("row")), by_no.get(p.get("line"))
        if r is None or s is None or s["line_no"] in seen or r["i"] in {x[0] for x in out}:
            continue
        n = pieces(r, s)
        if doc_type == "PO" and ((n is not None and abs(n - float(s["qty_pcs"] or 0)) >= 0.001)
                                 or price_fits(r["price"], s) is False):
            continue
        if doc_type == "TTG" and n is not None and n > float(s["qty_pcs"] or 0) + 0.001:
            continue
        seen.add(s["line_no"])
        out.append((r["i"], s["line_no"], str(p.get("why") or "")[:300]))
    return out


def bundle_docs(c, bid, sor):
    """[(page_no, doc_type, rows)] for the bundle's PO and TTG documents (their first page holds the reading)."""
    docs = []
    for d in c.execute("""SELECT d.page_from, d.doc_type::text AS t, p.fields FROM staging.document d
                            JOIN staging.bundle_document bd ON bd.document_id = d.id
                            JOIN staging.bundle b ON b.id = bd.bundle_id
                            JOIN staging.page p ON p.batch_id = d.batch_id AND p.page_no = d.page_from
                           WHERE d.batch_id = %s AND b.sor_no = %s AND d.doc_type IN ('PO', 'TTG')
                           ORDER BY d.page_from""", (bid, sor)):
        docs.append((d["page_from"], d["t"], rows_of(d["t"], d["fields"])))
    return docs


def propose(bid, sor=None):
    """AI proposals for the rows of each bundle that nothing else matched. Stored in staging.line_match as
    'proposed'; the checks don't use them until a person confirms (7d)."""
    from common.models import teacher
    with db.connect() as c:
        sors = [sor] if sor else [r["sor_no"] for r in c.execute(
            """SELECT DISTINCT b.sor_no FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
               JOIN staging.document d ON d.id = bd.document_id WHERE d.batch_id = %s AND b.hold_reason IS NULL
               ORDER BY 1""", (bid,))]
        sos = satellite.load(c, sors)
        decisions = {(r["page_no"], r["row_index"]): r for r in c.execute(
            "SELECT * FROM staging.line_match WHERE batch_id=%s", (bid,))}
    asked = 0
    for s in sors:
        so = sos.get(verify.flat(s))
        with db.connect() as c:
            docs, lines = bundle_docs(c, bid, s), satellite.items(c, s)
            pmap = load_map(c, so and so.get("customer_parent"))
        m = match(docs, lines, pmap, decisions)
        for page, dt, rows in docs:
            todo = [r for r in rows if m[(page, r["i"])]["status"] == "none"]
            if not todo or not lines:
                continue
            if asked:
                time.sleep(PAUSE)                            # Z.ai's free tier refuses bursts (1302 after 3 calls)
            asked += 1
            try:
                answer, meta = teacher.ask_text(_prompt(dt, todo, lines), AI_MODEL)
            except Exception as e:
                print(f"{s} p{page}: the model couldn't be reached: {type(e).__name__}: {e}"[:300], flush=True)
                if "429" in str(e):                          # the rate limit: every next call would wait out too
                    print("stopped at Z.ai's rate limit; run again later (what was proposed is kept)", flush=True)
                    return
                continue
            kept = checked(dt, todo, lines, answer.get("pairs"))
            with db.connect() as c:
                for i, line_no, why in kept:
                    r = next(x for x in todo if x["i"] == i)
                    c.execute("""INSERT INTO staging.line_match (batch_id, page_no, row_index, sor_no, so_line_no, how,
                                   status, reason, customer_code, ean)
                                 VALUES (%s, %s, %s, %s, %s, 'ai', 'proposed', %s, %s, %s)
                                 ON CONFLICT (batch_id, page_no, row_index) DO UPDATE SET so_line_no = EXCLUDED.so_line_no,
                                   reason = EXCLUDED.reason, proposed_at = now()
                                 WHERE staging.line_match.how = 'ai'""",
                              (bid, page, i, s, line_no, f"{meta.get('model')}: {why}", r["code"] or None,
                               min(r["barcodes"]) if r["barcodes"] else None))
            print(f"{s} p{page} {dt}: {len(todo)} rows to match, the model paired {len(answer.get('pairs') or [])}, "
                  f"{len(kept)} kept after the number checks · {meta.get('ms')} ms", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "propose":
        propose(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
