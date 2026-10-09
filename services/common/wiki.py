"""What people taught, as a wiki (read-then-map, Stages 2c and 3; Karpathy's LLM-wiki pattern, kept behind our gate).
Pure: no database, no model call (worker/learn.py runs it).

  One markdown page per document type (staging.knowledge_page, versioned like Jev's context):

      # TTG

      ## Any customer
      - posting_date: the date the goods were received, never the PO's date. [label · TGL TERIMA]

      ## AEON (chain 1100002424)
      - purchase_order_no: AEON's receiving note has no PO field; its RECEIPT NO is the PO number. [label · RECEIPT NO · pages b-c80bbbde4d/14]
      - no_ref: AEON's receiving note prints no SOR reference; leave it empty. [not_printed · pages b-c80bbbde4d/3]
      - lines.qty: the column headed "QTY RCV". [column · QTY RCV · pages …]
      - posting_date: the date stamped in the top-right corner. [position · region 40,700,90,960 · pages …]
      - lines.qty: … handwritten … [visual · region … · pages …]

  A claim is one line: the type's field name (as on /fields; lines.<column> for a table column), what to do, and in
  brackets its kind, the printed anchor, the region (0-1000 of the upright page, [ymin, xmin, ymax, xmax]) and the
  pages it was learned from. Each kind is applied by the tool that suits it (the user, 2026-10-01: what is in the
  copy, the text model maps; what isn't, the AI OCR reads):
    - label, column, position, not_printed (or no kind): the text model's second mapping (pass B, after Jev), as
      hints. A position claim is told its region in the copy's own terms (every line of the copy has its position),
      so the text model chooses among the lines there: the date, not the receipt number printed beside it;
    - visual: what the copy can't carry (handwriting, a stamp, a mark): the AI OCR reads the region's crop,
      field-directed, like the look-again.
  Only the fields the page's claims name are taken from them; everything else stays as pass A mapped it, so
  knowledge never changes a page's type and never touches a field it says nothing about.

  The gate replays a proposal on stored pages: a value is right or wrong against a person's confirmation (practice
  pile only), or, for keys and an FP's amounts, the order the page was grouped to in Satellite (a key only where the
  page prints it). Never Satellite for a PO's or receipt's amounts or quantities. A page never counts for a claim
  learned from that page or its bundle (leave-one-out).
"""
import difflib
import hashlib
import json
import re

from common import verify
from common.fields import TYPE_MAP
from common.verify import flat

PASS_B_V = 2              # the pass-B wrapper's version: part of every hints sha (a change re-maps, never reuses);
                          # 2: position claims reach the text model with their place (until then code picked the line)
                          # (not bumped when pass B began asking only the named fields: learn.pass_b says why)
ANY = "Any customer"
KINDS = ("label", "column", "position", "visual", "not_printed")
REGIONED = ("position", "visual")   # claims that carry where the value is printed (a region marked on the paper)
BY_EYE = ("visual",)      # read from the region's crop by the AI OCR
CLAIM = re.compile(r"^\s*[-*]\s+`?([a-z][a-z_]*(?:\.[a-z_]+)?)`?\s*:\s*(.+?)\s*$")
BRACKET = re.compile(r"\s*\[([^\[\]]*)\]\s*$")
PAGE_REF = re.compile(r"(b-[0-9a-f]+)/(\d+)")
CHAINS = re.compile(r"\(chains?\s+([\w-]+(?:\s*,\s*[\w-]+)*)\)")     # (chain 1100002424) or (chain 1100002312, 1100002314)
REGION = re.compile(r"^region\s+(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$")
NOT_PRINTED = "(not printed)"
MIN_PAGES = 2             # a drafted claim needs examples on this many pages, in as many bundles, agreeing
SAME_REGION = 0.2         # two examples' regions agree when they overlap at least this much (intersection / union)
SOR_LIKE = re.compile(r"SOR\d{11}|(?<![0-9])2611\d{7}(?![0-9])")     # an SOR reference, with or without its letters


# ---------------------------------------------------------------------------------------------- the page

def parse(md):
    """{"title", "sections": [{"head", "chain" (None: any customer), "claims": [claim], "prose": [line]}]}; a claim
    is {"field", "text", "kind", "anchor", "region", "pages": [(batch, page)], "line"}. Lines that aren't claims are
    prose kept for people; only claims are applied."""
    out, cur = {"title": None, "sections": []}, None
    for line in (md or "").splitlines():
        if line.startswith("# ") and out["title"] is None:
            out["title"] = line[2:].strip()
        elif line.startswith("## "):
            head = line[3:].strip()
            m = CHAINS.search(head)
            chains = [x.strip() for x in m.group(1).split(",")] if m else []
            cur = {"head": head, "chain": chains[0] if chains else None, "chains": chains, "claims": [], "prose": []}
            out["sections"].append(cur)
        elif cur is not None:
            m = CLAIM.match(line)
            if m:
                cur["claims"].append(claim(m.group(1), m.group(2), line))
            elif line.strip():
                cur["prose"].append(line.rstrip())
    return out


def claim(field, body, line=None):
    kind = anchor = region = None
    pages = []
    b = BRACKET.search(body)
    if b:
        body = body[:b.start()].strip()
        for piece in (x.strip() for x in b.group(1).split("·")):
            r = REGION.match(piece)
            if piece in KINDS:
                kind = piece
            elif r:
                region = [int(x) for x in r.groups()]
            elif piece.startswith("pages") or PAGE_REF.search(piece):
                pages = [(m.group(1), int(m.group(2))) for m in PAGE_REF.finditer(piece)]
            elif piece:
                anchor = piece
    return {"field": field, "text": body, "kind": kind, "anchor": anchor, "region": region, "pages": pages,
            "line": (line or "").strip()}


def claim_line(c):
    meta = [x for x in (c.get("kind"), c.get("anchor")) if x]
    if c.get("region"):
        meta.append("region " + ",".join(str(int(x)) for x in c["region"]))
    if c.get("pages"):
        meta.append("pages " + ", ".join(f"{b}/{n}" for b, n in c["pages"]))
    return f"- {c['field']}: {c['text']}" + (f" [{' · '.join(meta)}]" if meta else "")


def render(doc_type, sections):
    """The page's markdown from its sections: "Any customer" first, then the customers by name."""
    out = [f"# {doc_type}", ""]
    order = sorted(sections, key=lambda s: (s["chain"] is not None, s["head"]))
    for s in order:
        out += [f"## {s['head']}"] + list(s.get("prose") or []) + [claim_line(c) for c in s["claims"]] + [""]
    return "\n".join(out).rstrip() + "\n"


def section_head(chain, label):
    return ANY if chain is None else f"{label or 'customer'} (chain {chain})"


def dropped(old_md, new_md):
    """The claims of one page that another leaves out: [(section head, claim line)]. A claim is the same when its
    section names the same customers and its field and text read the same (its brackets may differ). Installing a page
    from the repo on a server (learn.install) checks this, so knowledge learned on that server isn't lost unseen."""
    def key(s, c):
        return tuple(s.get("chains") or ()) or ("any",), c["field"], flat(c["text"])
    have = {key(s, c) for s in parse(new_md)["sections"] for c in s["claims"]}
    return [(s["head"], c["line"]) for s in parse(old_md)["sections"] for c in s["claims"] if key(s, c) not in have]


def covers(section, chain):
    """Is this customer's section for this chain? A section may name several (Alfamart's DCs, a chain whose every
    store is its own customer in Satellite): "## Alfamart (chain 1100002312, 1100002314)"."""
    return bool(chain) and chain in (section.get("chains") or ([section["chain"]] if section.get("chain") else []))


def claims_for(parsed, chain):
    """What a page of this customer is given: "Any customer", then the customer's own section (a claim there on the
    same field replaces the general one). A page whose customer isn't known gets "Any customer" only."""
    general, own = [], []
    for s in (parsed or {}).get("sections") or []:
        if s["chain"] is None and s["head"].lower().startswith("any"):
            general += s["claims"]
        elif covers(s, chain):
            own += s["claims"]
    mine = {c["field"] for c in own}
    return [c for c in general if c["field"] not in mine] + own


def canon_of(doc_type, field):
    """The combined list's name of a type's field (the text model maps onto the combined list): TTG
    purchase_order_no → po_number. A line column stays lines.<column>."""
    if field.startswith("lines."):
        return field
    back = {v: k for k, v in (TYPE_MAP.get(doc_type) or {}).items()}
    return back.get(field, field)


def by_text(claims):
    """The claims the text model applies: everything about the copy (label, column, position, not_printed, or no
    kind). A position claim needs its region, and never holds a table column (rows move from page to page)."""
    return [c for c in claims if c.get("kind") not in BY_EYE
            and not (c.get("kind") == "position" and (not c.get("region") or c["field"].startswith("lines.")))]


def by_region(claims):
    """The header claims the AI OCR reads from a region's crop (visual): what the copy can't carry. A region claim on
    a line column is left for later: rows move from page to page."""
    return [c for c in claims if c.get("kind") in BY_EYE and c.get("region")
            and not c["field"].startswith("lines.")]


def hints(doc_type, claims):
    """The text claims as the text model reads them, keyed by the combined list's names. A position claim says where,
    in the transcript's own terms (x 700-960, y 40-90), so the text model can tell which line there is the value."""
    out = []
    for c in by_text(claims):
        canon = canon_of(doc_type, c["field"])
        name = canon if canon == c["field"] else f"{canon} (this {doc_type}'s {c['field']})"
        where = ""
        if c.get("kind") == "position":
            y0, x0, y1, x1 = c["region"]
            where = f" Where: around x {x0}-{x1}, y {y0}-{y1} (positions as in the transcript)."
        out.append(f"- {name}: {c['text']}{where}")
    return "\n".join(out)


def hints_sha(text):
    return hashlib.sha1(f"{PASS_B_V}\n{text}".encode()).hexdigest()[:12]


def region_sha(doc_type, c):
    """A region claim's own key, for its kept answer (a visual read) and for knowing the page is up to date."""
    return hashlib.sha1(json.dumps([PASS_B_V, doc_type, c["field"], c.get("kind"), c.get("region"), c.get("text")])
                        .encode()).hexdigest()[:12]


def knowledge_sha(doc_type, claims):
    """Everything a page is given, in one key: when it changes, the page is mapped again (the pass-B wrapper's version
    too: a page laid over with an older wrapper is done again)."""
    return hashlib.sha1(json.dumps([PASS_B_V, hints(doc_type, claims)] + sorted(region_sha(doc_type, c)
                                                                      for c in by_region(claims))).encode()).hexdigest()[:12]


def overlay_fields(doc_type, claims):
    """The combined list's header fields the text claims name: only these are taken from pass B."""
    return sorted({canon_of(doc_type, c["field"]) for c in by_text(claims) if not c["field"].startswith("lines.")})


def line_columns(claims):
    """The table columns the text claims name: taken from pass B's rows, paired with pass A's."""
    return sorted({c["field"].split(".", 1)[1] for c in by_text(claims) if c["field"].startswith("lines.")})


# ---------------------------------------------------------------------------------------------- pass B over pass A

def pass_a(fields_all, mapping):
    """The page's reading as pass A made it: the fields and cells the knowledge replaced put back. Jev only ever
    sees this."""
    pb = (mapping or {}).get("pass_b") or {}
    if not fields_all or not (pb.get("before") or pb.get("before_lines")):
        return fields_all
    fa = dict(fields_all)
    for k, v in (pb.get("before") or {}).items():
        if v is None:
            fa.pop(k, None)
        else:
            fa[k] = v
    if pb.get("before_lines"):
        rows = [dict(r) for r in fa.get("lines") or []]
        for i, cells in pb["before_lines"].items():
            if int(i) < len(rows):
                rows[int(i)].update(cells)
        fa["lines"] = rows
    return fa


def _row_pairs(map_a, map_b):
    """{pass A row index: pass B row index}, rows made from the same line of the copy."""
    b_of = {}
    for j, r in enumerate((map_b or {}).get("rows") or []):
        b_of.setdefault(r.get("block"), j)
    return {i: b_of[r.get("block")] for i, r in enumerate((map_a or {}).get("rows") or []) if r.get("block") in b_of}


def overlay(fa_a, map_a, fa_b, map_b, fields, info, cols=()):
    """Pass A's reading with these fields taken from pass B (None where pass B found nothing: a "not printed" claim
    empties a field), and these table columns taken from pass B's row made from the same line of the copy (a row
    pass B didn't make keeps pass A's cell). What was replaced is kept in mapping.pass_b (before, before_map,
    before_lines), so pass_a() and undo() can take it back. Returns (fields_all, mapping)."""
    fa, mapping = dict(fa_a or {}), dict(map_a or {})
    mf = dict(mapping.get("fields") or {})
    before, before_map, before_lines = {}, {}, {}
    for k in fields:
        before[k], before_map[k] = fa.get(k), mf.get(k)
        if (fa_b or {}).get(k):
            fa[k] = fa_b[k]
        else:
            fa.pop(k, None)
        if ((map_b or {}).get("fields") or {}).get(k):
            mf[k] = map_b["fields"][k]
        else:
            mf.pop(k, None)
    if cols:
        rows, rows_b = [dict(r) for r in fa.get("lines") or []], (fa_b or {}).get("lines") or []
        for i, j in _row_pairs(map_a, map_b).items():
            if i < len(rows) and j < len(rows_b):
                old = {c: rows[i].get(c) for c in cols}
                new = {c: rows_b[j].get(c) for c in cols}
                if old != new:
                    before_lines[str(i)] = old
                    rows[i].update(new)
        fa["lines"] = rows
    mapping["fields"] = mf
    mapping["pass_b"] = {**info, "fields": list(fields), "cols": list(cols), "before": before,
                         "before_map": before_map, "before_lines": before_lines}
    return fa, mapping


def put(fa, mapping, values, how):
    """Visual claims' values laid over (after pass B): {canon: {value, source_text, box} or None}. Their earlier
    values join mapping.pass_b.before, so undo() takes them back too."""
    fa, mapping = dict(fa or {}), dict(mapping or {})
    pb = dict(mapping.get("pass_b") or {"fields": [], "cols": [], "before": {}, "before_map": {}, "before_lines": {}})
    before, mf = dict(pb.get("before") or {}), dict(mapping.get("fields") or {})
    before_map = dict(pb.get("before_map") or {})
    for k, v in values.items():
        if k not in before:
            before[k], before_map[k] = fa.get(k), mf.get(k)
        if v:
            fa[k] = v
            mf[k] = {"box_by": how[k]}
        else:
            fa.pop(k, None)
            mf.pop(k, None)
    mapping["fields"] = mf
    mapping["pass_b"] = {**pb, "before": before, "before_map": before_map,
                         "regions": sorted(set(pb.get("regions") or []) | set(values))}
    return fa, mapping


def undo(fields_all, mapping):
    """(pass A's reading, pass A's mapping): whatever the knowledge laid over, taken back."""
    pb = (mapping or {}).get("pass_b")
    if not pb:
        return fields_all, mapping
    m = dict(mapping)
    mf = dict(m.get("fields") or {})
    for k, v in (pb.get("before_map") or {}).items():
        if v is None:
            mf.pop(k, None)
        else:
            mf[k] = v
    m["fields"] = mf
    m.pop("pass_b", None)
    return pass_a(fields_all, mapping), m


def changed(fa_old, fa_new, fields):
    """The fields whose value differs between two readings."""
    return [k for k in fields if flat(((fa_old or {}).get(k) or {}).get("value")) !=
            flat(((fa_new or {}).get(k) or {}).get("value"))]


def forget(second, fields):
    """A look-again's answers for fields whose first answer the knowledge has just changed no longer apply."""
    if not second or not fields:
        return second
    res = {k: v for k, v in (second.get("results") or {}).items() if k not in fields}
    return {**second, "results": res, "asked": [a for a in second.get("asked") or [] if a not in fields]}


def iou(a, b):
    if not a or not b:
        return 0.0
    y0, x0, y1, x1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, y1 - y0) * max(0, x1 - x0)
    area = lambda r: max(0, r[2] - r[0]) * max(0, r[3] - r[1])      # noqa: E731
    u = area(a) + area(b) - inter
    return inter / u if u else 0.0


# ---------------------------------------------------------------------------------------------- the gate

AMOUNTS = {"dpp", "ppn", "total"}
LINE_FIELD = re.compile(r"^lines\[(.+)\]\.(\w+)$")


def truth_of(doc_type, confirmed, so, printed):
    """{type field: the value it must have} on one page, from what can be trusted without the AI:
      - a person's confirmation (the caller passes practice-pile ones only; a line cell's as lines[<row key>].<col>);
      - the order the page was grouped to, in Satellite: a PO number or SOR reference where the page prints it
        (flat, in the AI OCR's copy), an FP's SOR, Nomor CPO, customer code and printed amounts (paper()). A
        receipt that prints no SOR reference at all (none like SOR… or 2611…, its order's included) has none;
      - a Faktur Pajak's billing number: Satellite's for its order (grouped to, or the one SOR it prints:
        printed_order) where the page prints it (2026-10-06: before, only people's corrections could score one).
    Never a PO's or receipt's amounts or quantities from Satellite: comparing those with SAMB's record is the point."""
    out = {}
    so = so or {}
    if doc_type in ("PO", "TTG") and so.get("cpo_no") and flat(so["cpo_no"]) in printed:
        out["purchase_order_no"] = so["cpo_no"]
    if doc_type == "TTG" and so.get("sor_no"):
        digits = flat(so["sor_no"]).replace("SOR", "")
        if digits and digits in printed:
            out["no_ref"] = so["sor_no"]
        elif not SOR_LIKE.search(printed):
            out["no_ref"] = NOT_PRINTED
    if doc_type == "FPJ" and so.get("billing_no") and flat(so["billing_no"]) in printed:
        out["billing_number"] = so["billing_no"]
    if doc_type == "FP":
        for k, v in (("sor", so.get("sor_no")), ("nomor_cpo", so.get("cpo_no")),
                     ("customer_code", so.get("customer_code"))):
            if v:
                out[k] = v
        for k, v in (so.get("paper") or {}).items():
            if k in AMOUNTS and v is not None:
                out[k] = v
    out.update(confirmed or {})
    return out


def printed_order(printed, sos):
    """The one Satellite order whose SOR the page prints (with or without its letters), or None when it prints none,
    or several, or one Satellite doesn't know. A misread SOR names no order, or another order whose billing number
    the page doesn't print (truth_of asks for that too)."""
    found = {"SOR" + m[-11:] for m in SOR_LIKE.findall(printed or "")}
    hits = [sos[k] for k in found if k in (sos or {})]
    return hits[0] if len(hits) == 1 else None


BILLING = re.compile(r"(?<![0-9])[0-9]{10}(?![0-9])")


def _billing(v):
    """A Faktur Pajak prints '<billing number>/<SOR>': the billing number is what a value is scored on."""
    m = BILLING.search(re.sub(r"\s+", "", str(v or "")))
    return m.group(0) if m else flat(v)


def line_truth(truth, keys):
    """{(row index, column): value} from confirmations of line cells, by the rows' keys (satellite.row_keys)."""
    at = {k: i for i, k in enumerate(keys)}
    out = {}
    for f, v in truth.items():
        m = LINE_FIELD.match(f)
        if m and m.group(1) in at:
            out[(at[m.group(1)], m.group(2))] = v
    return out


def score(field, value, truth):
    """right | wrong | empty, for one value against its truth."""
    empty = value is None or not flat(value) or str(value).strip() == NOT_PRINTED
    if str(truth).strip() == NOT_PRINTED:
        return "right" if empty else "wrong"
    if empty:
        return "empty"
    if field == "billing_number":
        return "right" if _billing(value) == _billing(truth) else "wrong"
    if field in AMOUNTS:
        a = verify.amount(value)
        b = verify.amount(truth) if isinstance(truth, str) else float(truth)
        return "right" if a is not None and b is not None and abs(float(a) - float(b)) < 0.005 else "wrong"
    return "right" if flat(value) == flat(truth) else "wrong"


def counts(claims, page, bundle_of):
    """{field: True} where this page may count in the replay: every claim naming the field was learned from another
    page in another bundle (leave-one-out), or cites no page (written by a person)."""
    out = {}
    mine = bundle_of.get(page)
    for c in claims:
        others = [p for p in c.get("pages") or [] if p != page and (mine is None or bundle_of.get(p) != mine)]
        ok = not c.get("pages") or bool(others)
        out[c["field"]] = out.get(c["field"], True) and ok
    return out


def verdict(rows, flips=0):
    """rows: [{page, field, before, after}] (right | wrong | empty each). A change passes when it makes at least one
    value right, none newly wrong, loses no right value (to wrong or to empty), and the gain beats the text model's
    own noise (flips: fields its two mappings disagreed on, among those scored)."""
    gained = [r for r in rows if r["after"] == "right" and r["before"] != "right"]
    lost = [r for r in rows if r["before"] == "right" and r["after"] != "right"]
    new_wrong = [r for r in rows if r["after"] == "wrong" and r["before"] != "wrong"]
    why = []
    if not rows:
        why.append("no stored page it changes has a known value to score it on")
    elif not gained:
        why.append("it makes no value right")
    if lost:
        why.append(f"{len(lost)} right value(s) lost")
    if new_wrong:
        why.append(f"{len(new_wrong)} value(s) newly wrong")
    if gained and len(gained) <= flips:
        why.append(f"the gain ({len(gained)}) doesn't beat the text model's own noise ({flips} flips)")
    return {"passed": not why, "gained": len(gained), "lost": len(lost), "new_wrong": len(new_wrong),
            "flips": flips, "scored": len(rows),
            "right_before": sum(r["before"] == "right" for r in rows),
            "right_after": sum(r["after"] == "right" for r in rows),
            "wrong_before": sum(r["before"] == "wrong" for r in rows),
            "wrong_after": sum(r["after"] == "wrong" for r in rows),
            "why": "; ".join(why) or "more right, nothing lost, nothing newly wrong"}


# ---------------------------------------------------------------------------------------------- drafts from examples

def kind_of(e):
    """(kind, anchor) an example can teach, set by code (the plan's rule): not printed; a stamp, handwriting or mark
    under it → visual; a table column's header → column; a printed label on its left → label; otherwise → position.
    A line cell is taught only by its column's header."""
    if e["kind"] == "not_printed":
        return ("not_printed", None)
    a = e.get("anchor") or {}
    if e["field"].startswith("lines."):
        return ("column", a["header_cell"].strip()) if a.get("header_cell") else None
    if a.get("under_kind") in ("handwriting", "stamp", "mark"):
        return ("visual", None) if e.get("region") else None
    if a.get("left"):
        return ("label", a["left"].strip())
    return ("position", None) if e.get("region") else None


def _text(doc_type, field, kind, anchor, who):
    if kind == "not_printed":
        return f"{who} {doc_type} prints no {field}; leave it empty."
    if kind == "column":
        return f"the column headed \"{anchor}\"."
    if kind == "position":
        return f"printed in the same place on {who} {doc_type}s."
    if kind == "visual":
        return f"written or stamped in the same place on {who} {doc_type}s: read it there."
    return f"the value printed right of the label \"{anchor}\"."


def _regions(es):
    """Examples whose regions agree (each overlaps the first by SAME_REGION): [(examples, the union region)]."""
    left, out = list(es), []
    while left:
        head, rest = left[0], left[1:]
        group = [head] + [e for e in rest if iou(e["region"], head["region"]) >= SAME_REGION]
        left = [e for e in rest if e not in group]
        rs = [e["region"] for e in group]
        out.append((group, [min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)]))
    return out


def draft(doc_type, examples, bundle_of, labels):
    """Claims the examples agree on: the same field, kind and anchor (compared flat; for a region, overlapping) on
    MIN_PAGES pages in as many bundles. Held by one customer: that customer's section; by two or more and no rival
    for the field: "Any customer". Returns [(chain or None, claim, source)], source 'marked' when every example was
    marked on the paper."""
    groups = {}
    for e in examples:
        k = kind_of(e)
        if not k or e.get("doc_type") != doc_type:
            continue
        groups.setdefault((e["field"], k[0], flat(k[1] or "")), []).append(e)
    split = []
    for (field, kind, a), es in groups.items():
        if kind in REGIONED:
            split += [((field, kind, a), g, r) for g, r in _regions(es)]
        else:
            split.append(((field, kind, a), es, None))

    def enough(xs):
        pages = {(x["batch_id"], x["page_no"]) for x in xs}
        return len(pages) >= MIN_PAGES and len({bundle_of.get(p, p) for p in pages}) >= MIN_PAGES

    out = []
    for (field, kind, _), es, region in split:
        known = {}
        for e in es:
            if e.get("chain"):
                known.setdefault(e["chain"], []).append(e)
        rivals = [1 for (f, _, _), xs, _ in split if f == field and xs is not es]
        anchor = kind_of(es[0])[1]
        chains = [(None, es)] if len(known) >= 2 and enough(es) and not rivals else \
            [(ch, xs) for ch, xs in known.items() if enough(xs)]
        for ch, xs in chains:
            who = "this customer's" if ch else "a"
            c = {"field": field, "text": _text(doc_type, field, kind, anchor, who), "kind": kind,
                 "anchor": anchor if kind in ("label", "column") else None, "region": region,
                 "pages": sorted({(x["batch_id"], x["page_no"]) for x in xs})}
            out.append((ch, c, "marked" if all(x["source"] == "marked" for x in xs) else "typed"))
    return out


def merge_draft(parsed, drafted, labels):
    """The page with the claims put in (a new claim replaces its section's claim on the same field, when that one
    says something else). Returns (sections, [claims added or changed])."""
    sections = [{"head": s["head"], "chain": s["chain"], "chains": list(s.get("chains") or []),
                 "claims": list(s["claims"]), "prose": list(s.get("prose") or [])}
                for s in (parsed or {}).get("sections") or []]
    new = []
    for ch, c, _ in drafted:
        s = next((x for x in sections if (covers(x, ch) if ch else
                                          x["chain"] is None and x["head"].lower().startswith("any"))), None)
        if s is None:
            s = {"head": section_head(ch, labels.get(ch)), "chain": ch, "claims": [], "prose": []}
            sections.append(s)
        old = next((x for x in s["claims"] if x["field"] == c["field"]), None)
        if old and flat(old["text"]) == flat(c["text"]) and old.get("kind") == c.get("kind"):
            continue
        if old:
            s["claims"].remove(old)
        s["claims"].append(c)
        new.append(c)
    if not any(s["chain"] is None for s in sections):
        sections.insert(0, {"head": ANY, "chain": None, "claims": [], "prose": []})
    return sections, new


def contradictions(doc_type, parsed, pages):
    """The lint's finding: claims that produced a value a person later said is wrong. pages: [{"page": (batch, n),
    "pass_b": mapping.pass_b, "fields_all", "practice": {field: value}}]. A page a claim was learned from never
    counts against it. Returns ({(section chain, field)}, [why])."""
    drop, why = set(), []
    for p in pages:
        pb = p.get("pass_b") or {}
        if not pb:
            continue
        laid = set(pb.get("fields") or []) | set(pb.get("regions") or [])
        for cl in claims_for(parsed, pb.get("chain")):
            canon = canon_of(doc_type, cl["field"])
            if cl["field"] not in p["practice"] or canon not in laid or tuple(p["page"]) in [tuple(x) for x in cl["pages"]]:
                continue
            now_v = ((p.get("fields_all") or {}).get(canon) or {}).get("value")
            if score(cl["field"], now_v, p["practice"][cl["field"]]) == "wrong":
                sec = next(s["chain"] for s in parsed["sections"] if cl in s["claims"])
                drop.add((sec, cl["field"]))
                why.append(f"{cl['field']} gave {now_v!r} on {p['page'][0]}/{p['page'][1]}, a person says "
                           f"{p['practice'][cl['field']]!r}")
    return drop, why


def without(parsed, drop):
    """The page's sections with these claims taken out (the lint): drop = [(chain, field)]."""
    sections = []
    for s in (parsed or {}).get("sections") or []:
        sections.append({"head": s["head"], "chain": s["chain"], "chains": list(s.get("chains") or []),
                         "prose": list(s.get("prose") or []),
                         "claims": [c for c in s["claims"] if (s["chain"], c["field"]) not in drop]})
    return sections


def diff(a, b):
    """The lines a proposal changes, for people (/knowledge): '+ …' and '- …'."""
    return [x for x in difflib.ndiff((a or "").splitlines(), (b or "").splitlines()) if x[:2] in ("+ ", "- ")]


# ---------------------------------------------------------------------------------------------- the teacher (Stage 3)

TEACH_V = 2          # 2 (2026-10-06): one position format (x/y), the axes explained, the place given in words: FPJ #2
                     # said "bottom right" for x 35-220 (the left fifth), reading [top, left, bottom, right] as x first


def place(region):
    """Where a region sits on the page, in words: 'lower left, about 72% down the page'. region = [ymin, xmin, ymax,
    xmax] on 0-1000 of the upright page (0,0 = the top-left corner)."""
    y0, x0, y1, x1 = region
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    across = x1 - x0 > 667
    side = "across the page" if across else "left" if cx < 333 else "right" if cx > 667 else "centre"
    height = "top" if cy < 200 else "upper" if cy < 400 else "middle" if cy < 600 else "lower" if cy < 800 else "bottom"
    words = f"{height} part, across the page" if across else "centre" if (height, side) == ("middle", "centre") \
        else f"{height} {side}"
    return f"{words}, about {round(cy / 10)}% down the page"


def xy(region):
    """A region in the copy's own terms: 'x 34-118, y 719-727'."""
    y0, x0, y1, x1 = region
    return f"x {x0}-{x1}, y {y0}-{y1}"


def teach_prompt(doc_type, what, meaning, example, customer, section, general, others, copy, mapped):
    """The teacher's question for one correction: write ONE claim for the wiki, or say none helps. It sees the
    correction, the page as the AI OCR copied it (with positions), what the text model mapped, the customer's section
    and "Any customer", and other people's corrections of this field (practice pile only)."""
    a = example.get("anchor") or {}
    r = example.get("region")
    where = f"{place(r)} ({xy(r)})" if r else "not marked on the page"
    said = ("the page prints no such value" if example["kind"] == "not_printed"
            else f"the right value is {example['value']!r}")
    beside = "; ".join(f"{k}: {v!r}" for k, v in (("label on its left", a.get("left")), ("printed above", a.get("above")),
                                                   ("its column's header", a.get("header_cell")),
                                                   ("what is printed there", a.get("under_kind"))) if v)
    return f"""You keep a small wiki that tells a text model where values are printed on one kind of business document,
per customer. The text model reads a copy of each page (every printed line with its position) and fills a field list.
A person has just corrected one value. Write ONE claim that would make the text model get it right on this customer's
other pages, or say that no claim helps.

DOCUMENT TYPE: {doc_type} ({what})
CUSTOMER: {customer or 'not known'}
FIELD: {example['field']} ({meaning})
THE CORRECTION: the reading had {example.get('shown')!r}; the person says {said}.
WHERE IT IS PRINTED: {where}{'; ' + beside if beside else ''}.
WHAT THE TEXT MODEL MAPPED FOR IT: {mapped!r}

THE WIKI NOW, for this customer:
{section or '(nothing yet)'}
and for any customer:
{general or '(nothing yet)'}

OTHER CORRECTIONS OF THIS FIELD (other pages):
{others or '(none)'}

THE PAGE AS COPIED (block id, kind, position, text):
{copy}

POSITIONS: every position above is on a 0-1000 grid over the upright page. x runs from the left edge (0) to the right
edge (1000); y runs from the top edge (0) to the bottom edge (1000). So x below 333 is the left third and above 667
the right third; y below 200 is the top of the page and above 800 the bottom.

Rules for the claim:
- It says where or how the value is printed on this customer's {doc_type}s, in plain words. Never this page's value
  itself: the claim must hold on the customer's next page, with other numbers.
- kind is one of: label (the value is beside a printed label: give the label exactly as printed, as "anchor"),
  column (a table column: give its header as printed), position (a fixed place on the page, no label), visual
  (handwritten, stamped or marked), not_printed (the customer's {doc_type} never prints this field).
- scope is "customer" (only this customer's {doc_type}s) or "any" (every customer's {doc_type}s). Use "any" only if
  nothing in it is particular to this customer.
- If the wiki already has a claim for this field that the correction shows is wrong, replace it: say which in
  "replaces" (its text).
- If you say where on the page it is, use the words given in WHERE IT IS PRINTED (e.g. "lower left"), never your own
  reading of the numbers, and give no coordinates: they are added from the place the person marked.

Answer with ONE JSON object, nothing else:
{{"claim": "…", "kind": "label", "anchor": "RECEIPT NO", "scope": "customer", "replaces": null, "why": "…"}}
or {{"no_change": "why no claim would help"}}."""


def teacher_claim(answer, example, blocks):
    """The teacher's answer checked by code: (claim, scope) or (None, why). The claim must be about this field, of a
    known kind; a label or column must be printed on the page (in the copy) and isn't the value itself; the claim
    never contains the page's own value (it must hold on other pages)."""
    if not isinstance(answer, dict):
        return None, "the teacher's answer isn't a JSON object"
    if answer.get("no_change"):
        return None, f"the teacher: {answer['no_change']}"
    text, kind = str(answer.get("claim") or "").strip(), answer.get("kind")
    anchor = str(answer.get("anchor") or "").strip() or None
    if not text:
        return None, "no claim in the teacher's answer"
    if kind not in KINDS:
        return None, f"unknown kind {kind!r}"
    line = example["field"].startswith("lines.")
    if kind == "column" and not line:
        return None, f"{example['field']} is a single value, not a table column: a column claim can't hold it"
    if line and kind in ("label", "position"):
        return None, f"{example['field']} is a table cell: say its column (or that it's handwritten or stamped)"
    value = flat(example.get("value"))
    if len(value) >= 4 and value in flat(text):
        return None, "the claim names this page's value: it wouldn't hold on the next page"
    if kind in ("label", "column"):
        if not anchor:
            return None, f"a {kind} claim needs the label as printed"
        copy = flat(" ".join(str(b.get("text") or "") + " " + " ".join(str(x) for x in b.get("cells") or [])
                             for b in blocks or []))
        if flat(anchor) not in copy:
            return None, f"the label {anchor!r} isn't printed on this page"
        if value and flat(anchor) == value:
            return None, "the label is the value itself"
    if kind in REGIONED and not example.get("region"):
        return None, f"a {kind} claim needs where the value is (the correction wasn't marked on the paper)"
    scope = "any" if answer.get("scope") == "any" else "customer"
    c = {"field": example["field"], "text": text.replace("[", "(").replace("]", ")"), "kind": kind,
         "anchor": anchor if kind in ("label", "column") else None,
         "region": [int(x) for x in example["region"]] if kind in REGIONED else None,
         "pages": [(example["batch_id"], example["page_no"])], "replaces": answer.get("replaces")}
    return c, scope


# ---------------------------------------------------------------------------------------------- the status bar

STEPS = ("Fixed on this page", "Kept as a lesson", "Waiting for the teacher", "The teacher writes a tip",
         "Tested on other pages", "Switched on", "Applied to stored pages")


def _tip(lesson):
    """The tip the teacher wrote for this lesson (its last answer's claim), or None."""
    for a in reversed((lesson or {}).get("answers") or []):
        claim = (a.get("answer") or {}).get("claim") if isinstance(a.get("answer"), dict) else None
        if claim:
            return str(claim)
    return None


def lesson_progress(example, page=None, ahead=0, pending=False):
    """What a person's fix is doing now, for the status bar after Save (the page viewer, a Review card), in plain
    words (the user, 2026-10-01). example: its staging.extract_example row, or None when none was kept; page: the
    knowledge_page row of the tip it proposed (lesson_doc, lesson_version), or None; ahead: lessons waiting before it;
    pending: an earlier tip of its document type is still an open proposal. Returns {steps: [(label, state)], headline,
    tip, final}; a state is done, now, todo, skip or stop; final: nothing more will happen by itself (stop asking)."""
    def bar(n_done, now=None, rest="todo"):
        states = ["done"] * n_done + ([now] if now else [])
        return list(zip(STEPS, states + [rest] * (len(STEPS) - len(states))))

    if not example:
        return {"steps": bar(1, "stop", "skip"), "tip": None, "final": True,
                "headline": "Fixed. Not kept as a lesson: the value wasn't found in the page's copy, so there's "
                            "nothing to learn about where it is printed."}
    if example.get("pile") == "exam":
        return {"steps": bar(2, None, "skip"), "tip": None, "final": True,
                "headline": "Fixed. Kept in the test pile: it is never taught, only used to measure how well the "
                            "system has learned."}
    lesson, st = example.get("lesson") or {}, example.get("lesson_status")
    tip, prog = _tip(lesson), (page or {}).get("progress") or {}
    if st == "waiting":
        if lesson.get("error"):
            head = "Waiting: the teacher couldn't reach its AI. It tries again by itself every 30 minutes."
        elif pending:
            head = ("Waiting: an earlier tip for this kind of document is still open on the Knowledge screen. One "
                    "change at a time, so this lesson waits until it is used or rejected.")
        else:
            head = f"Waiting for the teacher ({ahead} lesson{'s' if ahead != 1 else ''} ahead)." if ahead else \
                "Waiting for the teacher to start."
        return {"steps": bar(2, "now"), "headline": head, "tip": None, "final": False}
    if st == "teaching":
        return {"steps": bar(3, "now"), "headline": "The teacher is writing a tip from your fix…", "tip": None,
                "final": False}
    if st == "needs_pages":
        return {"steps": bar(4, "stop"), "tip": tip, "final": True,
                "headline": "The tip is written, but there's no other stored page of this kind to test it on yet. "
                            "It is tried again by itself when more pages arrive."}
    if st == "proposed":
        gate = (page or {}).get("gate") or {}
        if prog.get("step") == "testing" and not gate:
            of, done = prog.get("of") or 0, prog.get("done") or 0
            return {"steps": bar(4, "now"), "tip": tip, "final": False,
                    "headline": f"Testing the tip on other pages ({done} of {of})…" if of else
                                "Testing the tip on other pages…"}
        if gate.get("passed"):                       # switched on at once (the user, 2026-10-08): a moment
            return {"steps": bar(5, "now"), "tip": tip, "final": False,
                    "headline": "The tip passed its test. Switching it on…"}
        if gate:
            return {"steps": bar(4, "now"), "tip": tip, "final": False,
                    "headline": "The first tip didn't pass its test. The teacher is trying again…"}
        return {"steps": bar(4, "now"), "headline": "Testing the tip on other pages…", "tip": tip, "final": False}
    if st == "learned":
        if prog.get("step") == "applying":
            of, done = prog.get("of") or 0, prog.get("done") or 0
            return {"steps": bar(6, "now"), "tip": tip, "final": False,
                    "headline": f"Learned. Applying the tip to stored pages ({done} of {of})…"}
        if prog.get("step") == "applied":
            n, ch = prog.get("of") or 0, prog.get("changed") or 0
            return {"steps": bar(7), "tip": tip, "final": True,
                    "headline": f"Learned. The tip is in use: {ch} of {n} stored page{'s' if n != 1 else ''} "
                                "changed, and new pages of this kind use it too."}
        return {"steps": bar(7), "headline": "Learned. The tip is in use.", "tip": tip, "final": True}
    if st == "already_right":
        return {"steps": bar(3, None, "skip"), "tip": None, "final": True,
                "headline": "Nothing to learn: the system already reads this value right."}
    if st == "no_change":
        who = lesson.get("rejected_by")
        why = lesson.get("why") or next((a.get("why") for a in reversed(lesson.get("answers") or [])
                                         if a.get("why")), None)
        return {"steps": bar(4, "stop", "skip"), "tip": tip, "final": True,
                "headline": f"The tip was rejected by {who}." if who else
                            "No tip was kept" + (f": {why}" if why else ".")}
    return {"steps": bar(2, "stop", "skip"), "tip": tip, "final": True,
            "headline": f"The lesson stopped: {lesson.get('error') or st or 'unknown state'}."}


def teacher_badge(teaching=None, progress=None, waiting=0, approvals=0):
    """The top bar's one line about the teacher, or None when it has nothing to do. teaching: the document type of a
    lesson being written; progress: (document type, {step, done, of}) of a tip being tested or applied; waiting:
    lessons waiting; approvals: tips that passed and wait for a person."""
    if progress:
        t, p = progress
        what = "testing" if p.get("step") == "testing" else "applying"
        return f"Teacher: {what} a {t} tip ({p.get('done') or 0}/{p.get('of') or 0})"
    if teaching:
        return f"Teacher: writing a {teaching} tip"
    if approvals:
        return f"{approvals} tip{'s' if approvals != 1 else ''} waiting for your approval"
    if waiting:
        return f"Teacher: {waiting} lesson{'s' if waiting != 1 else ''} waiting"
    return None


TEST_PAGES = 30            # at most this many pages decide a proposal's test (2026-10-06: cost follows the help needed)


def test_pages(plan, cap=TEST_PAGES):
    """The pages that decide a proposal's test, at most cap: those a person corrected first (the answers people
    gave), then the newest. plan entries: {"person": bool, "at": when the scan arrived}."""
    def when(x):
        return x["at"].timestamp() if hasattr(x.get("at"), "timestamp") else 0
    return sorted(plan, key=lambda x: (not x["person"], -when(x)))[:cap]


INDEPENDENT = {"person", "qr", "satellite", "ship_to", "rows", "receipt_no"}   # backed whichever line the AI chose


def needs_tip(named, settled):
    """Whether a stored page still needs a switched-on tip: some field the tip names isn't settled. Settled = backed
    by something independent of which line the AI picked (a person, the QR code, Satellite: INDEPENDENT), or equal to
    a known right answer. A print ✅ alone isn't: it proves the characters are printed, not that they are this field.
    A table column is never settled as a whole, so a tip about one always applies."""
    return any(f.startswith("lines.") or f not in settled for f in named)


def to_apply(pages):
    """The stored pages a newly switched-on tip is applied to: one row per page, and never a page whose order is
    already published (the user, 2026-10-01: a published order is never checked again, so re-reading it would only
    cost text-model calls; new pages use the tip anyway)."""
    seen, published, out = set(), set(), []
    for p in pages:
        if p.get("bundle_status") == "published":
            published.add((p["batch_id"], p["page_no"]))
    for p in pages:
        k = (p["batch_id"], p["page_no"])
        if k in seen or k in published:
            continue
        seen.add(k)
        out.append(p)
    return out
