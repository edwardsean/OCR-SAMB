"""The knowledge wiki at work (read-then-map, Stages 2c and 3; the pure part is common/wiki.py).

  step     the knowledge for one page, after Jev: the page's customer (the order it was grouped to, else
           customer.identify), "Any customer" + that customer's section of its type's active page. Claims about the
           copy (label, column, position with its place, not printed): the text model maps the transcript again
           with them (pass B) and only the fields and table columns they name are taken from it. Visual claims
           (handwriting, stamps, marks): the AI OCR reads the region's crop. Every answer is kept
           (staging.knowledge_map): nothing is paid for twice.
  draft    claims the examples agree on (two pages in two bundles) → a proposal.
  teach    Stage 3: one correction at a time, the teacher (Z.ai GLM, text) writes one claim for it → a proposal →
           the gate. A second try when the first isn't kept; "needs pages" when no other stored page can prove it.
  propose  a person's edit → a proposal.
  gate     replay a proposal on the stored pages its change touches that have a known answer (wiki.truth_of: a
           person's practice correction, or Satellite), at most wiki.TEST_PAGES of them, leave-one-out; pages with a
           hidden-pile (exam) correction are replayed too and scored apart, never deciding. A proposal that passes
           becomes active by itself, whoever wrote it (the user, 2026-10-08: "i want automatic use for the knowledge
           tips as well"; until then only one from corrections marked on the paper did, and a person approved the rest).
  activate the gate, when the replay passes (or a person, for a proposal left from before); the pages of that type are
           mapped again (apply).
  apply    the active page again on the type's stored pages that still need it (wiki.needs_tip: a field it names
           isn't settled), reusing the test's answers: what changed is re-checked and regrouped.
  lint     a claim a person's later correction contradicts is taken out by itself (a 'lint' version), and what has no
           evidence is listed.
  regroup  after grouping: a page whose knowledge was chosen for another customer than its order's is done again.
  export   every active page to <folder>/<TYPE>.md (seed/knowledge in the repo): knowledge lives in the database, so
           this is how it travels with the code to another server.
  install  each <folder>/<TYPE>.md made the type's active page, as written: no replay, and only pages read from then
           on use it (the user, 2026-10-09: tips go in as written; a person runs a replay when they want one). It
           stops, unless --force, when the file would leave out a claim the server's active page has (knowledge
           learned there), and lists them.

  python -m worker.learn show <type> | draft <type> | teach [--limit N] | propose <type> <file.md> --by NAME
                         [--note …] | gate <type> <version> | approve <type> <version> --by NAME
                         | reject <type> <version> | apply <type> | lint [<type>] | backfill
                         | export <folder> | install <folder> --by NAME [--force]
"""
import os
import sys
import time

from psycopg.types.json import Json

from common import config, context, customer, db, knowledge, satellite, settings, trace, transcript, wiki
from common.fields import DOCS, TYPE_MAP
from common.verify import flat
from worker import classify, vf
from worker import main as v1

TEACH_MODEL = config.WIKI_TEACHER_MODEL   # the text model (the Teknis screen "Model & kunci API")


@settings.on_change
def _teacher_changed():
    global TEACH_MODEL
    TEACH_MODEL = config.WIKI_TEACHER_MODEL
TEACH_TRIES = 2          # the teacher gets a second try, told why its first claim wasn't kept


# ---------------------------------------------------------------------------------------------- reading the wiki

def active(c, doc_type):
    return c.execute("SELECT * FROM staging.knowledge_page WHERE doc_type=%s AND status='active'",
                     (doc_type,)).fetchone()


def labels(sos):
    """{chain: how people call it}: the words most of its store names start with, legal forms left out (AEON, HARI
    HARI, BOOTS, STOCK POINT INDOGROSIR), else the store it orders for most (Hero's DC)."""
    import collections
    names = {}
    for s in sos.values():
        ch = satellite.chain_of(s)
        if ch and s.get("customer_name"):
            names.setdefault(ch, collections.Counter())[" ".join(s["customer_name"].upper().split())] += 1
    out = {}
    for ch, ns in names.items():
        ws = [[w for w in x.replace(".", " ").split() if w not in customer.LEGAL] for x in ns]
        common = []
        for i in range(max(len(w) for w in ws)):
            first = collections.Counter(w[i] for w in ws if len(w) > i and w[:i] == common)
            if not first:
                break
            word, k = first.most_common(1)[0]
            if k * 2 <= len(ws) or (i and k < len([w for w in ws if w[:i] == common])):
                break
            common.append(word)
        out[ch] = " ".join(common) if common and len(ws) > 1 else ns.most_common(1)[0][0]
    return out


def _ctx(c):
    return context.ensure(c)[1]


def chain_of(c, bid, n, fields_all, qr, sos, known):
    """(chain or None, how): the order the page was grouped to, else what the page itself says (customer.identify)."""
    ch = knowledge.chain_of_page(c, bid, n)
    if ch:
        return ch, "grouped"
    got = customer.identify(fields_all or {}, qr, sos, known)
    return got["chain"], ("identified" if got["chain"] else f"don't know: {got['why'][:120]}")


def _kept(bid, n, sha, mv):
    with db.connect() as c:
        return c.execute("""SELECT raw, raw2 FROM staging.knowledge_map WHERE batch_id=%s AND page_no=%s
                              AND hints_sha=%s AND map_version=%s""", (bid, n, sha, mv)).fetchone()


def _keep(bid, n, sha, mv, raw, raw2=None):
    with db.connect() as c:
        c.execute("""INSERT INTO staging.knowledge_map (batch_id, page_no, hints_sha, map_version, raw, raw2)
                     VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (batch_id, page_no, hints_sha, map_version)
                     DO UPDATE SET raw=EXCLUDED.raw, raw2=EXCLUDED.raw2""",
                  (bid, n, sha, mv, Json(raw), Json(raw2) if raw2 is not None else None))


# ---------------------------------------------------------------------------------------------- applying it

def pass_b(bid, n, p, text, sha, ctx, up=None, fields=(), cols=()):
    """The text model's mapping of the page's transcript with these hints: (fields_all, mapping, flips among all
    fields). It is asked only for the fields (and table columns) the hints name (the user, 2026-10-09: about half
    the tokens of mapping the whole list again, and no rows or notes when no column is named). Answers are kept in
    staging.knowledge_map; a kept one is never asked again. One kept from before (the whole list) holds every field
    asked now, so it stays valid: wiki.PASS_B_V isn't bumped for this, or every stored page with knowledge would be
    mapped again on its next grouping (paid)."""
    schema = context.named_schema(context.vlm_schema(ctx), fields, cols)
    _, mv, _ = vf.two_step_versions(ctx)
    kept = _kept(bid, n, sha, mv)
    raw, raw2 = (kept or {}).get("raw"), (kept or {}).get("raw2")
    blocks = p["transcript"]
    if raw is None:
        raw, _ = vf.ai_call("map_b", bid, n, vf.map_named, blocks, schema, text)
    if vf.MAP_TWICE and raw2 is None:
        raw2, _ = vf.ai_call("map_b", bid, n, vf.map_named, blocks, schema, text)
    if not kept or (vf.MAP_TWICE and kept.get("raw2") is None):
        _keep(bid, n, sha, mv, raw, raw2)
    img = up if up is not None else v1.load(p["upright_path"])
    first = vf.mapped(raw, blocks, schema, p.get("ocr_words"), img, ctx)
    if raw2 is None:
        return first[0], first[1], []
    second = vf.mapped(raw2, blocks, schema, p.get("ocr_words"), img, ctx)
    fa, mapping, _ = transcript.merge(first, second)
    return fa, mapping, vf.flips(first[0], second[0])["fields"]


def visual(bid, n, p, doc_type, c, ctx, up=None):
    """A visual claim: the AI OCR reads the learned region's crop for this one field, told what the claim says
    (zonal, like the look-again; the full page's copy never gets hints). The answer is kept."""
    canon = wiki.canon_of(doc_type, c["field"])
    sha = wiki.region_sha(doc_type, c)
    kept = _kept(bid, n, sha, "visual")
    if kept:
        a = kept["raw"]
    else:
        img = up if up is not None else v1.load(p["upright_path"])
        crop = vf.crop_png(img, c["region"])
        if not crop:
            return None
        meaning = ((ctx.get("fields") or {}).get(canon) or {}).get("meaning") or canon
        answers, _ = vf.ai_call("visual", bid, n, vf.look, v1.png_bytes(img), [(canon, f"{meaning}. {c['text']}")],
                                [(canon, crop)])
        a = (answers or {}).get(canon) or {}
        _keep(bid, n, sha, "visual", a)
    if not a or a.get("unsure") or a.get("value") in (None, ""):
        return None
    return {"value": a.get("value"), "source_text": a.get("source_text") or a.get("value"),
            "box": a.get("box") or c["region"]}


def step(bid, n, doc_type, fields_all, mapping, qr, ctx, up=None, md=None, version=None):
    """The knowledge for one page (after Jev decided doc_type): (fields_all, mapping, info or None). Without claims
    for the page, pass A's reading as it was (whatever an earlier knowledge laid over is taken back). md/version: a
    proposal instead of the active page (the gate)."""
    fa_a, map_a = wiki.undo(fields_all, mapping)
    with db.connect() as c:
        if md is None:
            row = active(c, doc_type)
            md, version = (row["markdown"], row["version"]) if row else ("", None)
        if not md:
            return fa_a, map_a, None
        sos = satellite.load(c)
        chain, how = chain_of(c, bid, n, fa_a, qr, sos, customer.learned(c, sos))
        p = c.execute("SELECT transcript, ocr_words, upright_path FROM staging.page WHERE batch_id=%s AND page_no=%s",
                      (bid, n)).fetchone()
    claims = wiki.claims_for(wiki.parse(md), chain)
    fields, cols, regions = wiki.overlay_fields(doc_type, claims), wiki.line_columns(claims), wiki.by_region(claims)
    if not (fields or cols or regions) or not (p and p["transcript"]):
        return fa_a, map_a, None
    ksha = wiki.knowledge_sha(doc_type, claims)
    old = (mapping or {}).get("pass_b") or {}
    if old.get("sha") == ksha:
        return fields_all, mapping, old                  # laid over already, with this very knowledge
    info = {"sha": ksha, "version": version, "chain": chain, "chain_by": how, "flips": []}
    if fields or cols:
        text = wiki.hints(doc_type, claims)
        fa_b, map_b, flips = pass_b(bid, n, p, text, wiki.hints_sha(text), ctx, up, fields, cols)
        info["flips"] = [f for f in flips if f in fields]
        fa, m = wiki.overlay(fa_a, map_a, fa_b, map_b, fields, info, cols)
    else:
        fa, m = wiki.overlay(fa_a, map_a, None, None, [], info)
    values, how_by = {}, {}
    for c_ in regions:                                   # visual claims: the AI OCR reads the region's crop
        canon = wiki.canon_of(doc_type, c_["field"])
        got = visual(bid, n, p, doc_type, c_, ctx, up)
        if got:                                          # a region with nothing it can tell leaves the field alone
            values[canon], how_by[canon] = got, c_["kind"]
    if values:
        fa, m = wiki.put(fa, m, values, how_by)
    return fa, m, m["pass_b"]


# ---------------------------------------------------------------------------------------------- proposals

def propose(doc_type, md, source, by, note=None):
    """A new version of the type's page, built on the active one. Returns its version."""
    if doc_type not in DOCS:
        raise ValueError(f"no field list for {doc_type}")
    with db.connect() as c:
        now = active(c, doc_type)
        v = c.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM staging.knowledge_page WHERE doc_type=%s",
                      (doc_type,)).fetchone()["v"]
        c.execute("""INSERT INTO staging.knowledge_page (doc_type, version, parent, status, markdown, source, note,
                                                          created_by) VALUES (%s,%s,%s,'proposed',%s,%s,%s,%s)""",
                  (doc_type, v, now["version"] if now else None, md, source, note, by))
    return v


def _examples(c, doc_type=None, where="e.status='active' AND e.pile='practice'"):
    return c.execute(f"""SELECT e.*, bd.bundle_id FROM staging.extract_example e
                           LEFT JOIN staging.document d ON d.batch_id=e.batch_id AND e.page_no BETWEEN d.page_from AND d.page_to
                           LEFT JOIN staging.bundle_document bd ON bd.document_id=d.id
                          WHERE {where} AND (%s::text IS NULL OR e.doc_type=%s) ORDER BY e.made_at""",
                     (doc_type, doc_type)).fetchall()


def draft(doc_type, by="drafted from examples"):
    """Claims the practice-pile examples agree on, put into the type's page as a proposal. Returns (version or
    None, what was drafted, examples still waiting for a second page)."""
    with db.connect() as c:
        ex = _examples(c, doc_type)
        now = active(c, doc_type)
        sos = satellite.load(c)
    bundle_of = {(e["batch_id"], e["page_no"]): e["bundle_id"] for e in ex}
    drafted = wiki.draft(doc_type, ex, bundle_of, labels(sos))
    used = {(p, c_["field"]) for _, c_, _ in drafted for p in c_["pages"]}
    waiting = [e for e in ex if ((e["batch_id"], e["page_no"]), e["field"]) not in used]
    if not drafted:
        return None, [], waiting
    sections, new = wiki.merge_draft(wiki.parse(now["markdown"] if now else ""), drafted, labels(sos))
    if not new:
        return None, [], waiting
    source = "marked" if all(s == "marked" for _, c_, s in drafted if c_ in new) else "typed"
    v = propose(doc_type, wiki.render(doc_type, sections), source, by,
                "drafted: " + "; ".join(f"{c_['field']} ({c_['kind']}{', ' + c_['anchor'] if c_.get('anchor') else ''})"
                                        for c_ in new))
    return v, new, waiting


# ---------------------------------------------------------------------------------------------- the gate

def _printed(blocks):
    return "|".join(flat(transcript.block_text(b)) for b in blocks or [])


def _scope(c, doc_type, bid=None):
    return c.execute("""SELECT p.batch_id, p.page_no, p.status, p.fields_all, p.mapping, p.qr_text, p.transcript,
                               p.ocr_words, p.upright_path, b.sor_no, bd.bundle_id, b.status::text AS bundle_status,
                               s.received_at
                          FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
                          LEFT JOIN staging.document d ON d.batch_id=p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                          LEFT JOIN staging.bundle_document bd ON bd.document_id=d.id
                          LEFT JOIN staging.bundle b ON b.id=bd.bundle_id
                         WHERE p.doc_type=%s AND p.type_status IN ('decided', 'labelled') AND p.transcript IS NOT NULL
                           AND p.fields_all IS NOT NULL AND (%s::text IS NULL OR p.batch_id=%s)
                         ORDER BY p.batch_id, p.page_no""", (doc_type, bid, bid)).fetchall()


def _confirmed(c, bid, n):
    """{field: value}, split by pile: (practice, exam). A confirmation whose example sits in the exam pile is scored
    only after (the gate never learns from it); one without an example counts as practice."""
    rows = c.execute("""SELECT f.field, f.value, e.pile FROM staging.field_confirmation f
                          LEFT JOIN staging.extract_example e ON e.batch_id=f.batch_id AND e.page_no=f.page_no
                               AND e.confirmation=f.field AND e.status='active'
                         WHERE f.batch_id=%s AND f.page_no=%s""", (bid, n)).fetchall()
    return ({r["field"]: r["value"] for r in rows if r["pile"] != "exam"},
            {r["field"]: r["value"] for r in rows if r["pile"] == "exam"})


def _settled(c, bid, n):
    """The header fields of a page backed independently of which line the AI picked (wiki.INDEPENDENT)."""
    return {r["f"] for r in c.execute(
        """SELECT substr(field_path, 8) AS f FROM staging.field_check WHERE batch_id=%s AND page_no=%s
             AND status='ok' AND field_path LIKE 'header.%%' AND confirmed_by = ANY(%s)""",
        (bid, n, sorted(wiki.INDEPENDENT)))}


def _order_of(p, printed, doc_type, sos):
    """The Satellite order a page's known answers come from: the one it was grouped to, else (a Faktur Pajak, often
    held until its numbers are sure) the one SOR it prints."""
    if p["sor_no"]:
        return sos.get(flat(p["sor_no"]))
    return wiki.printed_order(printed, sos) if doc_type == "FPJ" else None


def _cells(doc_type, fa_a, fa, truth, cols):
    """[(field, was, now, truth)] for the table cells a person confirmed, in the claimed columns."""
    keys = satellite.row_keys(doc_type, (fa_a or {}).get("lines") or [])
    lt = wiki.line_truth(truth, keys)
    ra, rb = (fa_a or {}).get("lines") or [], (fa or {}).get("lines") or []
    return [(f"lines.{col}", ra[i].get(col) if i < len(ra) else None, rb[i].get(col) if i < len(rb) else None, v)
            for (i, col), v in sorted(lt.items()) if col in cols]


def gate(doc_type, version, show=print):
    """The replay of one proposal (_gate), in the trace as tip.test."""
    with trace.span("tip.test", doc_type=doc_type, version=version) as sp:
        g = _gate(doc_type, version, show)
        sp.note(passed=bool(g.get("passed")), why=g.get("why"), pages_mapped=g.get("mapped"),
                right=f"{g.get('right_before')} → {g.get('right_after')}" if "right_after" in g else None)
        return g


def _gate(doc_type, version, show=print):
    """Replay the proposal on the stored pages of its type whose knowledge it changes; store the result. First,
    without any call: which of them have a value to score it on (a truth, not learned from that page). None: it
    can't be proven yet, and nothing is mapped. A marked proposal that passes becomes active (and is applied).
    Returns the gate."""
    with db.connect() as c:
        row = c.execute("SELECT * FROM staging.knowledge_page WHERE doc_type=%s AND version=%s",
                        (doc_type, version)).fetchone()
        if not row or row["status"] != "proposed":
            raise ValueError(f"{doc_type} #{version} is not a proposal")
        now = active(c, doc_type)
        if (now["version"] if now else None) != row["parent"]:
            raise ValueError(f"{doc_type} #{version} was built on #{row['parent']}, but "
                             f"#{now['version'] if now else '-'} is active now: draft or write it again")
        ctx = _ctx(c)
        sos = satellite.load(c)
        known = customer.learned(c, sos)
        pages = _scope(c, doc_type)
        exam_ex = {(r["batch_id"], r["page_no"], r["field"]) for r in c.execute(
            "SELECT batch_id, page_no, field FROM staging.extract_example WHERE pile='exam' AND status='active'")}
    cand, base = wiki.parse(row["markdown"]), wiki.parse(now["markdown"] if now else "")
    bundle_of = {(p["batch_id"], p["page_no"]): p["bundle_id"] for p in pages}
    names = TYPE_MAP.get(doc_type) or {}
    plan = []                                            # 1: without calls, what each changed page can be scored on
    for p in pages:
        bid, n = p["batch_id"], p["page_no"]
        fa_a, _ = wiki.undo(p["fields_all"], p["mapping"])
        with db.connect() as c:
            chain, how = chain_of(c, bid, n, fa_a, p["qr_text"], sos, known)
            practice, exam = _confirmed(c, bid, n)
        cc, bc = wiki.claims_for(cand, chain), wiki.claims_for(base, chain)
        if wiki.knowledge_sha(doc_type, cc) == wiki.knowledge_sha(doc_type, bc):
            continue                                    # this proposal changes nothing for this page
        named = {c_["field"] for c_ in cc + bc}
        printed = _printed(p["transcript"])
        so = _order_of(p, printed, doc_type, sos)
        truth = wiki.truth_of(doc_type, practice, so, printed)
        truth_exam = wiki.truth_of(doc_type, exam, None, printed) if exam else {}
        ok = wiki.counts(cc, (bid, n), bundle_of)
        heads = [f for f in named if not f.startswith("lines.")]
        cols = sorted({f.split(".", 1)[1] for f in named if f.startswith("lines.")})
        line_t = [f for f in truth if wiki.LINE_FIELD.match(f) and wiki.LINE_FIELD.match(f).group(2) in cols]
        countable = [f for f in heads if f in truth and ok.get(f, True)] + \
            [f for f in line_t if ok.get("lines." + wiki.LINE_FIELD.match(f).group(2), True)]
        plan.append({"p": p, "chain": chain, "how": how, "cc": cc, "heads": heads, "cols": cols, "truth": truth,
                     "exam": truth_exam, "ok": ok, "countable": countable,
                     "exam_fields": [f for f in heads if f in truth_exam and ok.get(f, True)],
                     "person": any(f in practice for f in heads), "at": p["received_at"]})
    tested = {id(x) for x in wiki.test_pages([x for x in plan if x["countable"]])}
    for x in plan:                                       # over the limit: not mapped, said so
        if x["countable"] and id(x) not in tested:
            x["countable"], x["over_cap"] = [], True
    rows, exam_rows, detail, flips, calls = [], [], {}, 0, 0
    to_map = sum(1 for x in plan if x["countable"] or x["exam_fields"])
    _progress(doc_type, version, step="testing", done=0, of=to_map)
    for x in plan:                                       # 2: map again only the pages that can be scored
        p, bid, n = x["p"], x["p"]["batch_id"], x["p"]["page_no"]
        page_rows = []
        fa_a, _ = wiki.undo(p["fields_all"], p["mapping"])
        after_fa, pb = None, {}
        if x["countable"] or x["exam_fields"]:          # an exam-pile page is mapped to be scored apart
            after_fa, _, pb = step(bid, n, doc_type, p["fields_all"], p["mapping"], p["qr_text"], ctx,
                                   md=row["markdown"], version=version)
            calls += 1
            _progress(doc_type, version, step="testing", done=calls, of=to_map)
            for f in x["heads"]:
                canon = wiki.canon_of(doc_type, f)
                was = ((p["fields_all"] or {}).get(canon) or {}).get("value")
                now_v = ((after_fa or {}).get(canon) or {}).get("value")
                for t, out in ((x["truth"], rows), (x["exam"], exam_rows)):
                    if f not in t:
                        continue
                    if not x["ok"].get(f, True):         # leave-one-out, for the exam pile too
                        if out is rows:
                            page_rows.append({"field": f, "was": was, "now": now_v, "truth": str(t[f]),
                                              "counted": False, "why": "learned from this page or its bundle"})
                        continue
                    r = {"page": f"{bid}/{n}", "field": f, "before": wiki.score(f, was, t[f]),
                         "after": wiki.score(f, now_v, t[f])}
                    out.append(r)
                    if out is rows:
                        page_rows.append({**r, "was": was, "now": now_v, "truth": str(t[f]), "counted": True})
                        flips += canon in ((pb or {}).get("flips") or [])
            for f, was, now_v, t in _cells(doc_type, p["fields_all"], after_fa, x["truth"], x["cols"]):
                if not x["ok"].get(f, True):
                    continue
                r = {"page": f"{bid}/{n}", "field": f, "before": wiki.score(f, was, t), "after": wiki.score(f, now_v, t)}
                rows.append(r)
                page_rows.append({**r, "was": was, "now": now_v, "truth": str(t), "counted": True})
        else:
            for f in x["heads"]:
                if f in x["truth"]:
                    page_rows.append({"field": f, "was": ((p["fields_all"] or {}).get(wiki.canon_of(doc_type, f))
                                                          or {}).get("value"), "now": None,
                                      "truth": str(x["truth"][f]), "counted": False,
                                      "why": f"over the test's limit of {wiki.TEST_PAGES} pages" if x.get("over_cap")
                                      else "learned from this page or its bundle (not mapped again)"})
        detail[f"{bid}/{n}"] = {"chain": x["chain"], "chain_by": x["how"], "fields": x["heads"] + x["cols"],
                                "rows": page_rows, "unscored": [f for f in x["heads"] if f not in x["truth"]]}
        show(f"{bid} p{n} ({x['chain'] or 'customer unknown'}, {x['how']}): " + ("; ".join(
            f"{r['field']} {r.get('was')!r} → {r.get('now')!r}" + (f" [{r['before']} → {r['after']}]" if r.get("counted")
                                                                    else " [not counted: " + r.get("why", "") + "]")
            for r in page_rows) or f"nothing to score ({', '.join(x['heads'] + x['cols'])})"))
    cites_exam = sorted({f"{cl['field']} ({b}/{n})" for sec in cand["sections"] for cl in sec["claims"]
                         for b, n in cl["pages"] if (b, n, cl["field"]) in exam_ex})
    g = {**wiki.verdict(rows, flips), "pages": detail, "pages_changed": len(detail), "mapped": calls,
         "exam": wiki.verdict(exam_rows) if exam_rows else None, "cites_exam": cites_exam,
         "needs_pages": not any(x["countable"] for x in plan)}
    with db.connect() as c:
        c.execute("UPDATE staging.knowledge_page SET gate=%s WHERE doc_type=%s AND version=%s",
                  (Json(g), doc_type, version))
    _progress(doc_type, version, step="tested", done=calls, of=to_map, passed=bool(g["passed"]))
    if cites_exam:
        show(f"  learned from an exam-pile example: {', '.join(cites_exam)} (its exam score is left out)")
    show(f"{doc_type} #{version}: {'PASSED' if g['passed'] else 'not passed'}: {g['why']} "
         f"(scored {g['scored']}: right {g['right_before']} → {g['right_after']}, wrong {g['wrong_before']} → "
         f"{g['wrong_after']}, flips {g['flips']}, pages mapped {calls})")
    if g["passed"]:                       # used at once, no person approves it (the user, 2026-10-08)
        try:
            activate(doc_type, version, context.AUTO, show=show)
        except ValueError as e:           # another page of this type became active since it was built: never used
            reject(doc_type, version, f"the gate: {e}"[:200])
            show(f"  not used: {e}")
    return g


def activate(doc_type, version, by, show=print, lint=False, written=False):
    """The one active page of its type; the old one retired. Refused unless it's a proposal built on the active page
    and its replay passed (the lint's takings-out need no replay: they only take knowledge away; a page a person
    installs as written (install) needs none either). Then its type's pages are done again (apply), except an
    installed page: only pages read from then on use it."""
    with db.connect() as c:
        r = c.execute("SELECT * FROM staging.knowledge_page WHERE doc_type=%s AND version=%s",
                      (doc_type, version)).fetchone()
        if not r or r["status"] != "proposed":
            raise ValueError(f"{doc_type} #{version} is not a proposal")
        now = active(c, doc_type)
        if (now["version"] if now else None) != r["parent"]:
            raise ValueError(f"{doc_type} #{version} was built on #{r['parent']}, but #{now['version']} is active now")
        if not (lint and r["source"] == "lint") and not (written and r["source"] == "person") \
                and not (r["gate"] or {}).get("passed"):
            raise ValueError(f"{doc_type} #{version}: its replay hasn't passed")
        c.execute("UPDATE staging.knowledge_page SET status='retired' WHERE doc_type=%s AND status='active'",
                  (doc_type,))
        c.execute("""UPDATE staging.knowledge_page SET status='active', approved_by=%s, approved_at=now()
                     WHERE doc_type=%s AND version=%s""", (by, doc_type, version))
        c.execute("""UPDATE staging.extract_example SET lesson_status='learned', lesson_at=now()
                      WHERE lesson_doc=%s AND lesson_version=%s AND lesson_status='proposed'""", (doc_type, version))
    trace.event("tip.active", doc_type=doc_type, version=version, was=r["parent"], by=by, source=r["source"])
    return None if written else apply(doc_type, show=show)


def export(folder, show=print):
    """Every type's active page to <folder>/<TYPE>.md."""
    os.makedirs(folder, exist_ok=True)
    with db.connect() as c:
        for t in DOCS:
            now = active(c, t)
            if now:
                with open(os.path.join(folder, f"{t}.md"), "w") as f:
                    f.write(now["markdown"].rstrip() + "\n")
                show(f"{t}: #{now['version']} → {folder}/{t}.md")


def install(folder, by, force=False, show=print):
    """Each <folder>/<TYPE>.md as the type's active page (see the module's note). Returns {type: what happened}."""
    done = {}
    for t in DOCS:
        path = os.path.join(folder, f"{t}.md")
        if not os.path.exists(path):
            continue
        md = open(path).read()
        with db.connect() as c:
            now = active(c, t)
        if now and now["markdown"].strip() == md.strip():
            done[t] = f"already active (#{now['version']})"
        else:
            lost = wiki.dropped(now["markdown"], md) if now else []
            if lost and not force:
                done[t] = "not installed: the file leaves out claims this server's page has; put them in the file " \
                          "or use --force:\n" + "\n".join(f"    {head}: {line}" for head, line in lost)
            else:
                v = propose(t, md, "person", by, f"installed from {path}")
                activate(t, v, by, written=True)
                done[t] = f"#{v} active" + (f", {len(lost)} claims of #{now['version']} left out" if lost else "")
        show(f"{t}: {done[t]}")
    return done


def reject(doc_type, version, by=None):
    with db.connect() as c:
        c.execute("""UPDATE staging.knowledge_page SET status='rejected', approved_by=%s, approved_at=now()
                     WHERE doc_type=%s AND version=%s AND status='proposed'""", (by, doc_type, version))
        c.execute("""UPDATE staging.extract_example SET lesson_status='no_change', lesson_at=now(),
                            lesson = coalesce(lesson, '{}'::jsonb) || jsonb_build_object('rejected_by', %s::text)
                      WHERE lesson_doc=%s AND lesson_version=%s AND lesson_status='proposed'""",
                  (by or "a person", doc_type, version))


def _store(bid, n, p, fa, m, show, why):
    """A page the knowledge changed: stored, its look-again answers for the changed fields forgotten, re-checked.
    Returns what changed."""
    moved = wiki.changed(p["fields_all"], fa, sorted((set(fa or {}) | set(p["fields_all"] or {})) - {"lines"}))
    if (fa or {}).get("lines") != (p["fields_all"] or {}).get("lines"):
        moved.append("lines")
    with db.connect() as c:
        sl = c.execute("SELECT second_look FROM staging.page WHERE batch_id=%s AND page_no=%s",
                       (bid, n)).fetchone()["second_look"]
        sl = wiki.forget(sl, moved)
        c.execute("UPDATE staging.page SET fields_all=%s, mapping=%s, second_look=%s WHERE batch_id=%s AND page_no=%s",
                  (Json(fa), Json(m), Json(sl) if sl else None, bid, n))
    oc = vf.recheck(bid, n)
    show(f"{bid} p{n}: {', '.join(moved) or 'knowledge recorded, no value changed'} → {oc} ({why})")
    return moved


def apply(doc_type, show=print, bid=None):
    """The active page on the stored pages (_apply), in the trace as tip.apply. After a grouping (one batch) only a
    run that changed a page is written: it runs for every type on every grouping, mostly with nothing to do."""
    if bid is not None:
        t0 = time.monotonic()
        out = _apply(doc_type, show, bid)
        if out:
            trace.event("tip.apply", batch=bid, ms=int((time.monotonic() - t0) * 1000), doc_type=doc_type,
                        pages_changed=len(out))
        return out
    with trace.span("tip.apply", doc_type=doc_type) as sp:
        out = _apply(doc_type, show, bid)
        sp.note(pages_changed=len(out) if hasattr(out, "__len__") else None)
        return out


def _apply(doc_type, show=print, bid=None):
    """The active page again on the type's stored pages (kept answers reused): a page whose values changed is
    stored, re-checked, and its batch regrouped. A page whose new value needs the AI OCR to look again waits for it
    (`again`, the sweep). Only pages that are 'read' (a page with a ticket is done by its worker), and never a page
    whose order is already published (wiki.to_apply). Its progress is shown on the status bar. {page: changed}."""
    with db.connect() as c:
        ctx = _ctx(c)
        stored = wiki.to_apply([p for p in _scope(c, doc_type, bid) if p["status"] == "read"])
        now = active(c, doc_type)
        pages, settled_n = _needing(c, doc_type, stored, now)
    if settled_n:
        show(f"{doc_type}: {settled_n} stored page(s) skipped: what the knowledge names is already settled there")
    version = now["version"] if now is not None and bid is None else None   # after_grouping's per-batch passes
    _progress(doc_type, version, step="applying", done=0, of=len(pages), changed=0)  # don't report (version None)
    out, batches = {}, set()
    for i, p in enumerate(pages, 1):
        b, n = p["batch_id"], p["page_no"]
        fa, m, _ = step(b, n, doc_type, p["fields_all"], p["mapping"], p["qr_text"], ctx)
        if fa != p["fields_all"] or m != p["mapping"]:
            out[f"{b}/{n}"] = _store(b, n, p, fa, m, show, "knowledge")
            batches.add(b)
        _progress(doc_type, version, step="applying", done=i, of=len(pages), changed=len(out))
    if bid is None:
        for b in sorted(batches):
            vf.regroup(b)
    _progress(doc_type, version, step="applied", done=len(pages), of=len(pages), changed=len(out))
    return out


def _needing(c, doc_type, pages, now):
    """(the stored pages a switched-on page of knowledge still has to be used on, how many were skipped). A page whose
    every field the knowledge names (for its customer) is settled is skipped: backed independently of the AI's choice,
    or equal to its known answer. Taking knowledge away (none active) goes over every page."""
    parsed = wiki.parse(now["markdown"]) if now else None
    if not parsed:
        return pages, 0
    sos = satellite.load(c)
    known = customer.learned(c, sos)
    out = []
    for p in pages:
        bid, n = p["batch_id"], p["page_no"]
        fa_a, _ = wiki.undo(p["fields_all"], p["mapping"])
        chain, _ = chain_of(c, bid, n, fa_a, p["qr_text"], sos, known)
        named = {c_["field"] for c_ in wiki.claims_for(parsed, chain)}
        practice, _ = _confirmed(c, bid, n)
        printed = _printed(p["transcript"])
        truth = wiki.truth_of(doc_type, practice, _order_of(p, printed, doc_type, sos), printed)
        right = {f for f in named if f in truth and wiki.score(
            f, ((p["fields_all"] or {}).get(wiki.canon_of(doc_type, f)) or {}).get("value"), truth[f]) == "right"}
        if wiki.needs_tip(named, _settled(c, bid, n) | right):
            out.append(p)
    return out, len(pages) - len(out)


def after_grouping(bid, show=print):
    """vf-grouper, after a batch is grouped: a page whose knowledge was chosen for another customer (identified from
    the page, or not known) than its order's is done again with its order's customer. Returns the pages changed
    (the caller regroups once more)."""
    changed = {}
    with db.connect() as c:
        types = [r["doc_type"] for r in c.execute("SELECT DISTINCT doc_type FROM staging.knowledge_page WHERE "
                                                   "status='active'")]
    for t in types:
        changed.update(apply(t, show=show, bid=bid))
    return changed


# ---------------------------------------------------------------------------------------------- the lint

def lint(doc_type=None, show=print):
    """A claim that produced a value a person later corrected (practice pile) is taken out by itself: a 'lint'
    version, active at once (taking knowledge away never makes a value confidently wrong), and the type's pages are
    done again. Also listed: claims with no page behind them, and customers with a section but nothing the page can
    be recognised by. Returns the report."""
    report = {"demoted": [], "unbacked": [], "unrecognised": []}
    with db.connect() as c:
        pages = c.execute("SELECT * FROM staging.knowledge_page WHERE status='active' AND (%s::text IS NULL OR "
                          "doc_type=%s)", (doc_type, doc_type)).fetchall()
        sos = satellite.load(c)
        known = customer.learned(c, sos)
    for kp in pages:
        t, parsed = kp["doc_type"], wiki.parse(kp["markdown"])
        with db.connect() as c:
            scope = [{"page": (p["batch_id"], p["page_no"]), "pass_b": (p["mapping"] or {}).get("pass_b"),
                      "fields_all": p["fields_all"], "practice": _confirmed(c, p["batch_id"], p["page_no"])[0]}
                     for p in _scope(c, t) if (p["mapping"] or {}).get("pass_b")]
        drop, why = wiki.contradictions(t, parsed, scope)
        for s in parsed["sections"]:
            for cl in s["claims"]:
                if not cl["pages"]:
                    report["unbacked"].append(f"{t} · {s['head']} · {cl['field']}")
            if s["chain"] and not any((known.get(ch) or {}).get("names") for ch in s.get("chains") or [s["chain"]]):
                report["unrecognised"].append(f"{t} · {s['head']}")
        if drop:
            v = propose(t, wiki.render(t, wiki.without(parsed, drop)), "lint", "the lint",
                        "taken out, contradicted: " + "; ".join(why))
            activate(t, v, "the lint", show=show, lint=True)
            report["demoted"].append({"type": t, "version": v, "why": why})
            show(f"{t} #{v}: taken out: {'; '.join(why)}")
    return report


# ---------------------------------------------------------------------------------------------- the teacher (Stage 3)

def _field_value(fa, doc_type, field, row_key):
    """What the page's reading has for an example's field (a header field, or a row's cell by its key)."""
    if field.startswith("lines."):
        col = field.split(".", 1)[1]
        rows = (fa or {}).get("lines") or []
        keys = satellite.row_keys(doc_type, rows)
        return next((r.get(col) for r, k in zip(rows, keys) if k == row_key), None)
    return ((fa or {}).get(wiki.canon_of(doc_type, field)) or {}).get("value")


def _meaning(ctx, doc_type, field):
    if field.startswith("lines."):
        col = field.split(".", 1)[1]
        f = next((x for x in DOCS[doc_type]["lines"] if x["name"] == col), {})
        return f.get("desc") or f.get("label") or col
    canon = wiki.canon_of(doc_type, field)
    return ((ctx.get("fields") or {}).get(canon) or {}).get("meaning") or field


def _section_text(parsed, chain):
    for s in (parsed or {}).get("sections") or []:
        if (chain and wiki.covers(s, chain)) or (not chain and s["chain"] is None):
            return "\n".join(s.get("prose") or []) + ("\n" if s.get("prose") else "") + \
                "\n".join(wiki.claim_line(c) for c in s["claims"])
    return ""


def _progress(doc_type, version, **p):
    """What the teacher is doing with a version now (testing it, applying it), for the status bar after a fix and the
    top bar (schema/023). Best-effort: before that migration, or on any error, it is skipped; learning never waits
    for it."""
    if version is None:
        return
    try:
        with db.connect() as c:
            c.execute("UPDATE staging.knowledge_page SET progress = %s::jsonb || jsonb_build_object('at', now()) "
                      "WHERE doc_type=%s AND version=%s", (Json(p), doc_type, version))
    except Exception:
        pass


def _teaching(eid):
    """The lesson's tip is being written (schema/023), for the status bar. Best-effort, like _progress."""
    try:
        with db.connect() as c:
            c.execute("UPDATE staging.extract_example SET lesson_status='teaching', lesson_at=now() "
                      "WHERE id=%s AND lesson_status IN ('waiting', 'needs_pages')", (eid,))
    except Exception:
        pass


def _lesson(eid, status, extra=None, version=None, doc=None):
    with db.connect() as c:
        c.execute("""UPDATE staging.extract_example SET lesson_status=%s, lesson_at=now(),
                            lesson = coalesce(lesson, '{}'::jsonb) || %s::jsonb, lesson_doc=coalesce(%s, lesson_doc),
                            lesson_version=coalesce(%s, lesson_version) WHERE id=%s""",
                  (status, Json(extra or {}), doc, version, eid))


def teach_one(e, ask=None, show=print):
    """One lesson (_teach_one), in the trace as lesson.tip: how it ended."""
    with trace.span("lesson.tip", batch=e["batch_id"], page=e["page_no"], doc_type=e["doc_type"],
                    field=e["field"]) as sp:
        status = _teach_one(e, ask, show)
        sp.note(result=status)
        if status in ("retry", "wait"):
            sp.set("wait")
        return status


def _teach_one(e, ask=None, show=print):
    """One lesson: a practice-pile correction. Closed without asking when the page's reading already has the value
    (a knowledge made active since got it right); else the teacher writes one claim, code checks it, it becomes a
    proposal and is replayed; one that passes is active at once ('learned'). Not kept: a second try, told why. No
    other stored page to prove it on: "needs pages" (asked again when its scope has more pages). Returns the lesson's
    status ('proposed', a passed tip that couldn't be used, stops the round)."""
    from common.models import teacher
    real = ask is None                                   # only the real teacher's calls go in the ledger
    ask = ask or (lambda prompt: teacher.ask_text(prompt, TEACH_MODEL))
    bid, n, t = e["batch_id"], e["page_no"], e["doc_type"]
    with db.connect() as c:
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        now = active(c, t)
        pending = c.execute("SELECT version FROM staging.knowledge_page WHERE doc_type=%s AND status='proposed'",
                            (t,)).fetchone()
        ctx = _ctx(c)
        sos = satellite.load(c)
        others = c.execute("""SELECT batch_id, page_no, kind, value, anchor FROM staging.extract_example
                               WHERE doc_type=%s AND field=%s AND chain IS NOT DISTINCT FROM %s AND pile='practice'
                                 AND status='active' AND id <> %s ORDER BY made_at LIMIT 8""",
                           (t, e["field"], e["chain"], e["id"])).fetchall()
        scope_n = len(_scope(c, t))
    if not p or t not in DOCS or not p.get("transcript"):
        _lesson(e["id"], "no_change", {"why": "the page has no copy to learn from"})
        return "no_change"
    have = _field_value(p["fields_all"], t, e["field"], e.get("row_key"))
    right = (not flat(have)) if e["kind"] == "not_printed" else flat(have) == flat(e["value"])
    if right:
        _lesson(e["id"], "already_right", {"why": f"the page's reading already has {have!r}"})
        return "already_right"
    if pending:
        return "wait"                                    # one proposal at a time per type
    _teaching(e["id"])                                   # the status bar: the teacher is writing a tip
    parsed = wiki.parse(now["markdown"] if now else "")
    label = labels(sos).get(e["chain"]) if e["chain"] else None
    what = ((ctx.get("types") or {}).get(t) or {}).get("what") or t
    mapped = (p["fields_all"] or {}).get(wiki.canon_of(t, e["field"])) if not e["field"].startswith("lines.") else have
    tried = []
    for attempt in range(TEACH_TRIES):
        prompt = wiki.teach_prompt(t, what, _meaning(ctx, t, e["field"]), dict(e), label,
                                   _section_text(parsed, e["chain"]), _section_text(parsed, None),
                                   "\n".join(f"- {o['batch_id']}/{o['page_no']}: "
                                             + ("not printed" if o["kind"] == "not_printed" else repr(o["value"]))
                                             + (f", beside {(o['anchor'] or {}).get('left')!r}"
                                                if (o["anchor"] or {}).get("left") else "") for o in others),
                                   transcript.render(p["transcript"]), mapped)
        if tried:
            prompt += ("\n\nYOUR EARLIER CLAIM WASN'T KEPT: " + tried[-1]["claim"] + "\nWhy: " + tried[-1]["why"]
                       + "\nWrite a different claim, or answer no_change.")
        try:
            answer, meta = ask(prompt)
            if real:
                vf.ledger(teacher.provider_of(TEACH_MODEL), "teach_wiki", bid, n, True, {"model": meta.get("model"), "ms": meta.get("ms"),
                                                              "tokens_in": meta.get("prompt_tokens"),
                                                              "tokens_out": meta.get("completion_tokens")})
        except Exception as err:
            if real:
                vf.ledger(teacher.provider_of(TEACH_MODEL), "teach_wiki", bid, n, False, {"model": TEACH_MODEL},
                          f"{type(err).__name__}: {err}"[:300])
            _lesson(e["id"], "waiting", {"error": f"{type(err).__name__}: {err}"[:300]})
            return "retry"
        cl, scope = wiki.teacher_claim(answer, dict(e), p["transcript"])
        if not cl and isinstance(answer, dict) and answer.get("no_change"):
            _lesson(e["id"], "no_change", {"answers": tried + [{"answer": answer, "why": scope}]})
            show(f"{bid} p{n} {e['field']}: no claim ({scope})")
            return "no_change"
        if not cl:                                       # refused by code: the second try is told why
            tried.append({"claim": str((answer or {}).get("claim") if isinstance(answer, dict) else answer),
                          "why": scope, "answer": answer})
            show(f"{bid} p{n} {e['field']}: claim refused ({scope})")
            continue
        chain = e["chain"] if scope == "customer" else None
        sections = [{**s, "claims": [x for x in s["claims"] if not (cl.get("replaces") and
                                                                    flat(x["text"]) == flat(cl["replaces"]))]}
                    for s in parsed["sections"]]
        sections, new = wiki.merge_draft({"sections": sections}, [(chain, cl, e["source"])], labels(sos))
        if not new:
            _lesson(e["id"], "no_change", {"answers": tried + [{"answer": answer, "why": "the page already says it"}]})
            return "no_change"
        v = propose(t, wiki.render(t, sections), e["source"], f"teacher ({TEACH_MODEL})",
                    f"from {bid} p{n} {e['field']}: {answer.get('why') or ''}"[:300])
        _lesson(e["id"], "proposed", {"answers": tried + [{"answer": answer, "version": v}]}, v, t)
        g = gate(t, v, show=show)
        if g["passed"]:
            with db.connect() as c:
                st = c.execute("SELECT status FROM staging.knowledge_page WHERE doc_type=%s AND version=%s",
                               (t, v)).fetchone()["status"]
            if st == "active":                           # passed: used at once
                _lesson(e["id"], "learned", {}, v, t)
                return "learned"
            if st == "rejected":                         # passed, but built on a page no longer active: not used
                return "no_change"
            return "proposed"
        reject(t, v, "the gate")
        if g.get("needs_pages"):
            _lesson(e["id"], "needs_pages", {"why": g["why"], "scope_pages": scope_n}, v, t)
            show(f"{bid} p{n} {e['field']}: needs another stored page to prove it on")
            return "needs_pages"
        tried.append({"claim": wiki.claim_line(cl), "why": g["why"], "version": v})
    _lesson(e["id"], "no_change", {"answers": tried, "why": "not kept after two tries"})
    return "no_change"


def teach(limit=20, show=print, ask=None):
    """The teacher's round: waiting lessons oldest first, and those that needed pages once their type has more
    stored pages. A tip that passes is used at once, so the next lesson starts from it. Stops at a proposal still
    open (one change at a time) or when the model can't be reached. A lesson still 'teaching' when a round starts was cut off (one teacher, so nothing else is writing it):
    it waits again."""
    try:
        with db.connect() as c:
            c.execute("UPDATE staging.extract_example SET lesson_status='waiting' WHERE lesson_status='teaching'")
    except Exception:
        pass
    with db.connect() as c:
        waiting = _examples(c, None, "e.status='active' AND e.pile='practice' AND e.lesson_status='waiting'")
        later = _examples(c, None, "e.status='active' AND e.pile='practice' AND e.lesson_status='needs_pages'")
        counts = {t: len(_scope(c, t)) for t in {x["doc_type"] for x in later}}
    later = [x for x in later if counts.get(x["doc_type"], 0) > ((x["lesson"] or {}).get("scope_pages") or 0)]
    done = []
    for e in (waiting + later)[:limit]:
        status = teach_one(e, ask=ask, show=show)
        done.append((e["batch_id"], e["page_no"], e["field"], status))
        show(f"lesson {e['batch_id']} p{e['page_no']} {e['field']}: {status}")
        if status in ("proposed", "retry"):
            break
    return done


# ---------------------------------------------------------------------------------------------- by hand

def backfill(by=None):
    """Examples for confirmations made before examples were kept (Review answers from before Stage 2a)."""
    with db.connect() as c:
        rows = c.execute("""SELECT f.* FROM staging.field_confirmation f WHERE NOT EXISTS (
                              SELECT 1 FROM staging.extract_example e WHERE e.batch_id=f.batch_id
                                 AND e.page_no=f.page_no AND e.confirmation=f.field)""").fetchall()
        made = []
        for r in rows:
            got = knowledge.save_example(c, r["batch_id"], r["page_no"], r["field"], r["value"], r["shown"],
                                         r["confirmed_by"], "typed", row_key=r.get("row_key"))
            made.append((r["batch_id"], r["page_no"], r["field"], got))
    return made


def show_page(doc_type):
    with db.connect() as c:
        rows = c.execute("SELECT version, status, source, note, created_by FROM staging.knowledge_page WHERE doc_type=%s "
                         "ORDER BY version", (doc_type,)).fetchall()
        now = active(c, doc_type)
    for r in rows:
        print(f"#{r['version']} {r['status']:<9} {r['source']:<7} {r['created_by']}: {r['note'] or ''}")
    print(now["markdown"] if now else f"(no active page for {doc_type})")


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k: a[a.index(k) + 1] if k in a else None          # noqa: E731
    if a[:1] == ["show"] and len(a) > 1:
        show_page(a[1])
    elif a[:1] == ["draft"] and len(a) > 1:
        v, new, waiting = draft(a[1])
        print(f"proposal #{v}: {[wiki.claim_line(c) for c in new]}" if v else "nothing to draft")
        for e in waiting:
            print(f"  waiting for a second page: {e['batch_id']} p{e['page_no']} {e['field']} ({e['kind']}, "
                  f"{(e['anchor'] or {}).get('left') or (e['anchor'] or {}).get('header_cell') or '-'})")
    elif a[:1] == ["teach"]:
        teach(int(opt("--limit") or 20))
    elif a[:1] == ["propose"] and len(a) > 2 and opt("--by"):
        print("proposal #", propose(a[1], open(a[2]).read(), "person", opt("--by"), opt("--note")))
    elif a[:1] == ["gate"] and len(a) > 2:
        gate(a[1], int(a[2]))
    elif a[:1] == ["approve"] and len(a) > 2 and opt("--by"):
        print(activate(a[1], int(a[2]), opt("--by")))
    elif a[:1] == ["reject"] and len(a) > 2:
        reject(a[1], int(a[2]), opt("--by"))
    elif a[:1] == ["apply"] and len(a) > 1:
        print(apply(a[1]))
    elif a[:1] == ["lint"]:
        print(lint(a[1] if len(a) > 1 else None))
    elif a[:1] == ["backfill"]:
        for x in backfill():
            print(x)
    elif a[:1] == ["export"] and len(a) > 1:
        export(a[1])
    elif a[:1] == ["install"] and len(a) > 1 and opt("--by"):
        install(a[1], opt("--by"), force="--force" in a)
    else:
        print(__doc__)
