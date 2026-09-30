"""Which customer a page is from, before grouping (read-then-map, Stage 2b; shadow only: nothing uses it yet).

  Pass B (Stage 2c) maps a page again with what was learned for its type AND its customer, right after Jev, before
  the page is grouped. So the customer has to come from the page itself. Grouping later names the order, and the
  order's customer (satellite.chain_of) is final.

  Signals, each for one chain (a customer: Satellite's ship_to_parent, else the store):
  - key: the PO number, the SOR (the QR code first) or an FP's Nomor CPO, found in Satellite. STRONG when it matches
    an SO exactly and every SO one character away is the same customer's (AEON EASTVARA's POs 10101000125411 … 48,
    Boots' 4505832720 … 29, Hero's DC). WEAK when an exact match has another customer's SO one character away (SOR
    numbers run in sequence across customers), or when only SOs one character away match and all are one customer's.
  - name: the customer's name as the page prints it (the mapped customer_name), close to (NAME_MATCH) a name of this
    customer and clearly closer than any other's (NAME_MARGIN): Hero's receipts print PT DFI RETAIL NUSANTARA, and
    another customer is PT. SURI RETAIL NUSANTARA (0.86 alike). The names are Satellite's store names (an FP prints
    the store; AEON's receipt prints PT AEON INDONESIA, one of AEON's ship-tos), STRONG; and the names printed on the
    PO and receipt pages of bundles already grouped (Boots: PANEN SELARAS ADIPERKASA; Hari Hari: PT SINAR SAHABAT
    INTIMAKMUR), STRONG once two bundles printed it, else WEAK.
  - vendor: SAMB's code at the customer as the page prints it (PO vendor code, receipt vendor number), equal to one
    printed on another grouped bundle of exactly one customer (Hero: S10232). WEAK: codes are short.
  An FP's decoded QR code is its SOR, exactly: STRONG whatever is one character away. An FP's customer code is not a
  signal: the codes run in sequence, so a misread one is another store's (7000363700-03 p4 read 1400000565 for …566).

  A customer is named on one strong signal or two weak ones, and only when no signal points to another customer.
  Otherwise "don't know": pass B then uses only what holds for any customer. Measured by `shadow` (each page's
  bundle set aside while learning, so a page never teaches itself), against the customer of the order its bundle
  was grouped to.
"""
import collections
import difflib
import re

from common import satellite
from common.satellite import flat

NAME_MATCH = 0.9          # difflib ratio of two names (letters and digits, legal forms dropped) that are one name
NAME_MARGIN = 0.1         # ... and at least this much closer than any other customer's name
LEGAL = {"PT", "TBK", "CV", "UD", "PERSERO"}
KEY_MIN = 6               # a key shorter than this names nobody (flat characters)
SAMB = "SARANAABADIMAKMUR"      # SAMB's own name: the AI OCR sometimes maps it as the customer's; never learned
CODE_MIN = 4              # a vendor code token shorter than this names nobody


def norm(name):
    """A name to compare: letters and digits, the legal form dropped, spaces removed (the AI OCR splits and joins
    words: PT SINARSARAHABAT INTIMAKMUR)."""
    return "".join(w for w in re.findall(r"[A-Z0-9]+", str(name or "").upper()) if w not in LEGAL)


def chain_names(sos):
    """{normalised store name: {chains}} from Satellite; cached with the process-wide SO list."""
    def build():
        out = collections.defaultdict(set)
        for s in sos.values():
            if s.get("customer_name") and norm(s["customer_name"]):
                out[norm(s["customer_name"])].add(satellite.chain_of(s))
        return dict(out)
    if sos is not satellite._cache["sos"]:
        return build()
    if "chain_names" not in satellite._index:
        satellite._index["chain_names"] = build()
    return satellite._index["chain_names"]


def learned(c, sos, skip_bundles=()):
    """What pages of grouped bundles printed, per customer: {chain: {"names": {norm name: {bundles}},
    "vendor": {code: {bundles}}}}. Only PO, receipt and continuation pages (an FP is SAMB's own paper); a bundle in
    skip_bundles teaches nothing (shadow: the page's own)."""
    out = collections.defaultdict(lambda: {"names": collections.defaultdict(set), "vendor": collections.defaultdict(set)})
    for r in c.execute("""SELECT b.id bundle, b.sor_no, p.fields_all FROM staging.bundle b
                            JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                            JOIN staging.document d ON d.id = bd.document_id
                            JOIN staging.page p ON p.batch_id = d.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                           WHERE b.sor_no IS NOT NULL AND p.doc_type IN ('PO', 'TTG', 'CONTINUATION')"""):
        if r["bundle"] in skip_bundles:
            continue
        chain = satellite.chain_of(sos.get(flat(r["sor_no"])))
        fa = r["fields_all"] or {}
        if not chain:
            continue
        n = norm(_value(fa, "customer_name"))
        if n and SAMB not in n:
            out[chain]["names"][n].add(r["bundle"])
        for tok in _codes(_value(fa, "vendor_code")):
            out[chain]["vendor"][tok].add(r["bundle"])
    return out


def _value(fa, name):
    v = (fa or {}).get(name)
    return (v or {}).get("value") if isinstance(v, dict) else None


def _codes(value):
    """A vendor code's tokens worth comparing: AEON prints 0000000398/OS-073 (the code and something else)."""
    return {t for t in (flat(x) for x in re.split(r"[\s/|,;]+", str(value or ""))) if len(t) >= CODE_MIN}


def _chains_of(sors, sos):
    return {satellite.chain_of(sos.get(flat(s))) for s in sors} - {None}


def key_signals(fields_all, qr, sos):
    """[(chain, 'strong' | 'weak', why)] from the page's keys."""
    out, keys = [], []
    sor = (qr or "") if satellite.SOR_QR.match(flat(qr or "")) else _value(fields_all, "sor")
    if sor:
        v = flat(sor)
        v = "SOR" + v if v.isdigit() and len(v) == 11 else v
        keys.append(("sor_no", v, "the QR code's SOR" if qr and flat(qr) == v else "the SOR read"))
    if _value(fields_all, "po_number"):
        keys.append(("cpo_no", flat(_value(fields_all, "po_number")), "the PO number read"))
    for col, v, what in keys:
        if len(v) < KEY_MIN:
            continue
        exact, near = satellite.near_keys(v, satellite._cached_index(sos, col))
        ex, nr = _chains_of(exact, sos), _chains_of(near, sos)
        if what.startswith("the QR") and len(ex) == 1:
            out.append((next(iter(ex)), "strong", f"{what} {v} is its order"))
        elif len(ex) == 1 and nr <= ex:
            out.append((next(iter(ex)), "strong", f"{what} {v} is its order{'s' if len(exact) > 1 else ''} "
                                                  f"{', '.join(exact[:2])}" + (f", and the {len(near)} one character "
                                                  "away are its too" if near else "")))
        elif len(ex) == 1:
            out.append((next(iter(ex)), "weak", f"{what} {v} is its order {exact[0]}, but another customer's is one "
                                                "character away"))
        elif not ex and len(nr) == 1:
            out.append((next(iter(nr)), "weak", f"{what} {v} is one character from its order{'s' if len(near) > 1 else ''}"
                                                f" {', '.join(near[:2])}"))
    return out


def name_signals(fields_all, sos, known):
    """[(chain, strength, why)] from the name the page prints: the best customer, when it is clearly the best."""
    n = norm(_value(fields_all, "customer_name"))
    if len(n) < 5:
        return []
    best = dict(_satellite_best(n, sos))             # chain -> (ratio, strength, the name it matched)
    for ch, k in known.items():
        for name, bundles in k["names"].items():
            r = difflib.SequenceMatcher(None, n, name, autojunk=False).ratio()
            if r > best.get(ch, (0,))[0]:
                best[ch] = (r, "strong" if len(bundles) >= 2 else "weak",
                            f"{name}, printed on {len(bundles)} of its grouped bundle{'s' if len(bundles) > 1 else ''}")
    ranked = sorted(best.items(), key=lambda x: -x[1][0])
    if not ranked or ranked[0][1][0] < NAME_MATCH:
        return []
    (ch, (r, strength, what)), rival = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    if rival and rival[1][0] > r - NAME_MARGIN:
        return [(ch, "weak", f"the name {n} fits {what} ({r:.2f})"), (rival[0], "weak",
                f"the name {n} also fits {rival[1][2]} ({rival[1][0]:.2f})")]
    return [(ch, strength, f"the name {n} fits {what} ({r:.2f})")]


_best = {}                                           # (name, when the SO list was read) -> Satellite's closest stores


def _satellite_best(n, sos):
    """{chain: (ratio, 'strong', why)} for Satellite's store names within reach of NAME_MATCH (4,198 names: kept per
    name while the process-wide SO list is the same)."""
    key = (n, satellite._cache["at"]) if sos is satellite._cache["sos"] else None
    if key in _best:
        return _best[key]
    best = {}
    for name, chains in chain_names(sos).items():
        q = difflib.SequenceMatcher(None, n, name, autojunk=False)
        if q.real_quick_ratio() < NAME_MATCH - NAME_MARGIN or q.quick_ratio() < NAME_MATCH - NAME_MARGIN:
            continue
        r = q.ratio()
        for ch in chains:
            if r > best.get(ch, (0,))[0]:
                best[ch] = (r, "strong", f"Satellite's store {name}")
    if key:
        if len(_best) > 5000:
            _best.clear()
        _best[key] = best
    return best


def vendor_signals(fields_all, known):
    out = []
    for tok in _codes(_value(fields_all, "vendor_code")):
        chains = [ch for ch, k in known.items() if tok in k["vendor"]]
        if len(chains) == 1:
            out.append((chains[0], "weak", f"SAMB's code at the customer {tok} was printed on "
                                           f"{len(known[chains[0]]['vendor'][tok])} of its grouped bundles"))
    return out


def identify(fields_all, qr, sos, known):
    """{"chain": chain or None, "why": …, "signals": [(chain, strength, why)]}."""
    sig = key_signals(fields_all, qr, sos) + name_signals(fields_all, sos, known) + vendor_signals(fields_all, known)
    chains = {ch for ch, _, _ in sig}
    if not sig:
        return {"chain": None, "why": "nothing on the page names a customer", "signals": sig}
    if len(chains) > 1:
        return {"chain": None, "why": "the signals name different customers: " + ", ".join(sorted(chains)), "signals": sig}
    ch = next(iter(chains))
    strong, weak = sum(s == "strong" for _, s, _ in sig), sum(s == "weak" for _, s, _ in sig)
    if strong or weak >= 2:
        return {"chain": ch, "why": "; ".join(w for _, _, w in sig), "signals": sig}
    return {"chain": None, "why": f"one weak signal for {ch}: " + sig[0][2], "signals": sig}


def bends(fields_all):
    """The page's reading with one key or vendor code bent: every digit in turn replaced by every other digit (the
    way the scans misread: 5/6/8, 1/7, 3/8). None of them may name the wrong customer."""
    for name in ("sor", "po_number", "vendor_code"):
        v = _value(fields_all, name)
        for i, ch in enumerate(str(v or "")):
            if ch.isdigit():
                for d in "0123456789".replace(ch, ""):
                    fa = dict(fields_all)
                    fa[name] = dict(fields_all[name], value=v[:i] + d + v[i + 1:])
                    yield name, fa


def shadow(bid, pages=None, learn=True, bend=False, show=print):
    """Name each page's customer as the pipeline would, before grouping, and compare with the customer of the order
    its bundle was grouped to. Each page's own bundle is set aside while learning. `bend`: also every one-digit
    misread of each key and vendor code (with the QR code set aside, so the reading has to decide). Writes nothing."""
    from common import db
    with db.connect() as c:
        sos = satellite.load(c)
        rows = c.execute("""SELECT p.page_no, p.doc_type, p.qr_text, p.fields_all, b.id bundle, b.sor_no
                              FROM staging.page p
                              LEFT JOIN staging.document d ON d.batch_id = p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                              LEFT JOIN staging.bundle_document bd ON bd.document_id = d.id
                              LEFT JOIN staging.bundle b ON b.id = bd.bundle_id
                             WHERE p.batch_id = %s AND (%s::int[] IS NULL OR p.page_no = ANY(%s))
                               AND p.fields_all IS NOT NULL ORDER BY p.page_no""",
                         (bid, pages and list(pages), pages and list(pages))).fetchall()
        cache, tally = {}, collections.Counter()
        for r in rows:
            if learn and r["bundle"] not in cache:
                cache[r["bundle"]] = learned(c, sos, skip_bundles={r["bundle"]})
            got = identify(r["fields_all"], r["qr_text"], sos, cache.get(r["bundle"], {}) if learn else {})
            truth = satellite.chain_of(sos.get(flat(r["sor_no"]))) if r["sor_no"] else None
            verdict = ("no order to compare" if not truth else "don't know" if not got["chain"] else
                       "right" if got["chain"] == truth else "WRONG")
            tally[verdict] += 1
            tally[(r["doc_type"], verdict)] += 1
            show(f"p{r['page_no']:<3} {r['doc_type'] or '-':<12} {verdict:<11} {got['chain'] or '-':<11} {got['why']}")
            if bend and truth:
                for name, fa in bends(r["fields_all"]):
                    b = identify(fa, None, sos, cache.get(r["bundle"], {}) if learn else {})["chain"]
                    tally["bent " + ("wrong" if b and b != truth else "right" if b else "don't know")] += 1
                    if b and b != truth:
                        show(f"     WRONG when {name} reads {fa[name]['value']}: {b}")
        show(f"{bid}: " + ", ".join(f"{k} {v}" for k, v in tally.items() if isinstance(k, str)))
        for t in ("FP", "PO", "TTG", "CONTINUATION"):
            per = {v: n for (tt, v), n in ((k, n) for k, n in tally.items() if isinstance(k, tuple)) if tt == t}
            if per:
                show(f"  {t}: " + ", ".join(f"{k} {v}" for k, v in per.items()))
        return tally


if __name__ == "__main__":
    import sys
    from worker.clone import pages_arg
    if sys.argv[1:2] == ["shadow"] and len(sys.argv) > 2:
        args = [a for a in sys.argv[3:] if not a.startswith("--")]
        shadow(sys.argv[2], pages_arg(args[0]) if args else None, learn="--no-learning" not in sys.argv,
               bend="--bend" in sys.argv)
    else:
        print("usage: python -m common.customer shadow <batch> [pages] [--no-learning] [--bend]")
