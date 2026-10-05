"""Phase 6: grouping. Pages → documents → one bundle (and one storage folder) per SOR.

Only RESOLVED keys link: backed by print (Tesseract's reading, the QR code, the zoomed spot, a look-again print backs),
by Satellite's SO record, by the store printed on the page (S4: a key only the AI read whose SO's store the page
names, and none of the SOs one character away), or by a person (common/keys.py, common/satellite.py). A value that
is only read waits.
Page order is used for one thing: a CONTINUATION page belongs to the document on the page before it.

How a document finds its SOR (the FP is the hub: it carries the SOR and the Nomor CPO):
  FP    its own SOR
  TTG   the SOR it prints (No Ref; DO# or S/Fak without the letters SOR), or its PO number = an SO's Nomor CPO
  PO    its PO number = an SO's Nomor CPO
  FPJ   the SOR it prints (as billing/SOR), when Satellite's billing number for that SO is the one it prints too
  PEL   row by row, by each row's reference (later stage: held)
An SO's Nomor CPO comes from Satellite's record, or from an FP here whose SOR and CPO are both resolved.

Held, never guessed: an unread or unsure page · no resolved key · keys naming different SOs · a PO number matching
several SOs · two FPs with one SOR · an FP whose SOR isn't resolved · a bundle without its FP (fp_missing).
Re-runnable: each run replaces this batch's documents; pages join their bundle as their keys are resolved.
An order's documents may sit in several scans (each file its own scan; a receipt scanned days after its invoice):
documents link scan by scan, then the order-level holds (no FP, two FPs) are decided over every scan
(grouper/members.py), and the order's other scans are regrouped when that changes.

  python -m grouper.group <batch>              group now (vlm-first also regroups after every page)
  python -m grouper.group <batch> --recheck    first re-check every read page against today's Satellite records and
                                               people's confirmations (no model calls): after Satellite data changes
"""
import io
import json
import re
import sys

from minio.commonconfig import CopySource
from psycopg.types.json import Json

from common import config, db, satellite, storage

PREFIX = config.STORAGE_PREFIX
LINKABLE = ("FP", "TTG", "PO")
WHY = {
    "not_read": "the AI OCR hasn't read it yet",
    "type_unknown": "its type isn't decided: it waits for a label",
    "continuation_without_start": "a continuation, but the page before it isn't part of a document",
    "needs_sap_billing": "Satellite has no billing number for this SO yet (it isn't posted in SAP)",
    "fpj_needs_both": "a Faktur Pajak links when both the SOR and the billing number it prints are confirmed",
    "billing_disagrees": "the billing number it prints isn't Satellite's billing number for the SOR it prints",
    "later_stage": "Pelunasan rows link one by one, by their references (a later stage)",
    "not_grouped": "this type isn't grouped",
    "fp_sor_unresolved": "its SOR isn't resolved yet",
    "two_fps_one_sor": "two FPs have the same SOR",
    "no_resolved_key": "none of its keys is resolved yet",
    "so_unknown": "its resolved key isn't an SO in Satellite or on any FP here",
    "keys_disagree": "its keys name different SOs",
    "po_matches_several_sos": "its PO number is the Nomor CPO of several SOs",
    "fp_missing": "the SO's FP isn't among the grouped pages",
}


def sor_key(v):
    """SOR26110245292, whether printed with the letters or without (DO#, S/Fak)."""
    f = satellite.flat(v)
    return "SOR" + f if re.fullmatch(r"\d{11}", f) else f


def _key(p, name):
    k = (p.get("keys") or {}).get(name) or {}
    return k.get("value"), k.get("confirmed_by")


def plan(pages, sos):
    """Pure. pages: [{page_no, doc_type, type_status, keys}] (any order); sos: satellite.load(). Returns
    {"documents": [...], "bundles": {sor: {"documents": [...], "hold": reason or None}}}."""
    docs, at = [], {}

    def doc(p, hold=None):
        d = {"type": p["doc_type"], "pages": [p["page_no"]], "keys": {}, "read": {}, "sor": None,
             "linked_by": None, "evidence": [], "hold": hold, "suggest": None}
        docs.append(d)
        at[p["page_no"]] = d
        return d
    for p in sorted(pages, key=lambda p: p["page_no"]):
        n, t, status = p["page_no"], p["doc_type"], p["type_status"]
        if status is None:
            doc(p, "not_read")
        elif status not in ("decided", "labelled"):
            doc(p, "type_unknown")
        elif t == "CONTINUATION":
            prev = at.get(n - 1)
            if prev is None:
                doc(p, "continuation_without_start")
            else:                                         # the only use of page order
                prev["pages"].append(n)
                at[n] = prev
                prev["evidence"].append(f"page {n} continues it (the page before it)")
        elif t in LINKABLE or t == "FPJ":
            d = doc(p)
            for name in (("sor", "billing_no") if t == "FPJ" else ("sor", "po_no")):
                value, by = _key(p, name)
                d["read"][name] = value
                if value and by:
                    d["keys"][name] = {"value": value, "by": by}
        else:
            doc(p, {"PEL": "later_stage"}.get(t, "not_grouped"))

    cpo = {}                                              # Nomor CPO → SORs: Satellite's record ...
    for rec in sos.values():
        if rec.get("cpo_no"):
            cpo.setdefault(satellite.flat(rec["cpo_no"]), set()).add(sor_key(rec["sor_no"]))
    fps = {}
    for d in docs:                                        # the FPs: each is its SOR's hub
        if d["type"] != "FP" or d["hold"]:
            continue
        if "sor" not in d["keys"]:
            d["hold"] = "fp_sor_unresolved"
            continue
        fps.setdefault(sor_key(d["keys"]["sor"]["value"]), []).append(d)
    for s, ds in fps.items():
        for d in ds:
            if len(ds) > 1:
                d["hold"] = "two_fps_one_sor"
                continue
            d["sor"], d["linked_by"] = s, "sor"
            d["evidence"].insert(0, f"its SOR {s} ({d['keys']['sor']['by']})")
            if "po_no" in d["keys"]:                      # ... and resolved FPs here
                cpo.setdefault(satellite.flat(d["keys"]["po_no"]["value"]), set()).add(s)

    for d in docs:                                        # TTG and PO: to an SO, by a resolved key
        if d["type"] not in ("TTG", "PO") or d["hold"]:
            continue
        found = {}                                        # SOR → [(which key, why)]
        if "sor" in d["keys"]:
            found.setdefault(sor_key(d["keys"]["sor"]["value"]), []).append(
                ("sor", f"it prints the SOR {d['keys']['sor']['value']} ({d['keys']['sor']['by']})"))
        if "po_no" in d["keys"]:
            po = d["keys"]["po_no"]
            hits = cpo.get(satellite.flat(po["value"]), set())
            if len(hits) > 1:
                d["hold"] = "po_matches_several_sos"
                continue
            for s in hits:
                found.setdefault(s, []).append(("po_no", f"its PO number {po['value']} ({po['by']}) is {s}'s Nomor CPO"))
        if not d["keys"]:
            d["hold"] = "no_resolved_key"
            raw = d["read"].get("po_no")
            hits = cpo.get(satellite.flat(raw), set()) if raw else set()
            d["suggest"] = next(iter(hits)) if len(hits) == 1 else None
        elif not found:
            d["hold"] = "so_unknown"
        elif len(found) > 1:
            d["hold"] = "keys_disagree"
            d["evidence"] += [why for ws in found.values() for _, why in ws]
        else:
            (s, ws), = found.items()
            d["sor"], d["linked_by"] = s, ws[0][0]
            d["evidence"] = [why for _, why in ws] + d["evidence"]

    for d in docs:                                        # FPJ: by the SOR it prints, checked by its billing number
        if d["type"] != "FPJ" or d["hold"]:
            continue
        _link_fpj(d, sos)

    bundles = {}
    for d in docs:
        if d["sor"] and not d["hold"]:
            bundles.setdefault(d["sor"], {"documents": [], "hold": None})["documents"].append(d)
    for s, b in bundles.items():
        if not any(d["type"] == "FP" for d in b["documents"]):
            b["hold"] = "fp_missing"
    _suggest_for_fps(docs, bundles, sos)
    return {"documents": docs, "bundles": bundles}


def _fpj_parts(v):
    """(SOR, billing number) from what a Faktur Pajak prints as its reference: '7000300001/SOR26110200001', either
    part alone, or read into the wrong field."""
    f = satellite.flat(v)
    m = re.search(r"SO[RF]\d{11}", f)                  # an SOR, or an SOF (free goods) order
    bill = re.search(r"(?<!\d)\d{10}(?!\d)", f.replace(m.group(0), "|") if m else f)
    return (m.group(0) if m else None), (bill.group(0) if bill else None)


def _link_fpj(d, sos):
    """A Faktur Pajak prints SAMB's billing number and the SOR ('7000300001/SOR26110200001'). It links to that SOR
    only when both are confirmed (print, a person) and Satellite's billing number for that SO is the one it prints:
    two independent witnesses that must agree. Satellite has the billing number once the SO is posted in SAP."""
    parts = [_fpj_parts(k["value"]) for k in d["keys"].values()]
    sors, bills = {s for s, _ in parts if s}, {b for _, b in parts if b}
    sor, bill = (next(iter(sors)) if len(sors) == 1 else None), (next(iter(bills)) if len(bills) == 1 else None)
    if not d["keys"]:
        d["hold"] = "no_resolved_key"
    elif len(sors) > 1 or len(bills) > 1:
        d["hold"] = "keys_disagree"
    elif not sor or not bill:
        d["hold"] = "fpj_needs_both"
    elif sor not in sos:
        d["hold"] = "so_unknown"
    elif not sos[sor].get("billing_no"):
        d["hold"] = "needs_sap_billing"
    elif satellite.flat(sos[sor]["billing_no"]) != bill:
        d["hold"] = "billing_disagrees"
    else:
        d["sor"], d["linked_by"] = sor, "billing_no"
        by = ", ".join(sorted({k["by"] for k in d["keys"].values()}))
        d["evidence"].insert(0, f"it prints {bill}/{sor} ({by}); {bill} is Satellite's billing number for {sor}")
    if d["hold"]:
        raw = " ".join(str(v) for v in d["read"].values() if v)
        d["suggest"] = _fpj_parts(raw)[0]


def _suggest_for_fps(docs, bundles, sos):
    """A hint for a person, never a link: an FP whose SOR isn't resolved probably belongs to an SO that has documents
    here but no FP. Pick the one closest to what was read (its SOR, or its Nomor CPO against the SO's). Never an SO
    that already has its FP: page 8's faint SOR reads as page 1's real SOR."""
    orphans = [s for s, b in bundles.items() if b["hold"] == "fp_missing"]
    for d in docs:
        if d["hold"] != "fp_sor_unresolved":
            continue
        d["suggest"] = None
        raw_sor, raw_cpo = d["read"].get("sor"), satellite.flat(d["read"].get("po_no"))

        def distance(s):
            a = satellite.edits(sor_key(raw_sor), s) if raw_sor else 99
            want = satellite.flat((sos.get(s) or {}).get("cpo_no"))
            return min(a, satellite.edits(raw_cpo, want) if raw_cpo and want else 99)
        ranked = sorted(orphans, key=distance)
        if ranked and distance(ranked[0]) <= 3 and (len(ranked) == 1 or distance(ranked[1]) > distance(ranked[0])):
            d["suggest"] = ranked[0]


# ---------------------------------------------------------------------------------------------------- storage

def folder(sor, hold):
    return f"{PREFIX}bundles/{'_held/' if hold else ''}{sor}/"


def file_name(bid, n, doc_type):
    return f"{bid}-p{n:03d}-{doc_type or 'unread'}.png"


def sync_folders(bid, pages, out):
    """One folder per SOR: {PREFIX}bundles/<SOR>/ holds the pages of a complete bundle, {PREFIX}bundles/_held/<SOR>/ a
    bundle still missing its FP, {PREFIX}bundles/_held/unplaced/ every page not in a bundle yet. Each folder has a
    manifest saying how every page got there (or why not). Only this batch's files are touched."""
    c, bucket = storage.client(), storage.bucket()
    up = {p["page_no"]: p.get("upright_path") for p in pages}
    want, manifests = {}, {}
    for s, b in out["bundles"].items():
        f = folder(s, b["hold"])
        manifests[f + f"{bid}.json"] = {"sor": s, "hold": b["hold"], "why": WHY.get(b["hold"]), "documents": [
            {"type": d["type"], "pages": d["pages"], "linked_by": d["linked_by"], "evidence": d["evidence"]}
            for d in b["documents"]]}
        for d in b["documents"]:
            for n in d["pages"]:
                want[f + file_name(bid, n, d["type"] if n == d["pages"][0] else "CONTINUATION")] = up.get(n)
    held = [d for d in out["documents"] if d["hold"] or not d["sor"]]
    manifests[f"{PREFIX}bundles/_held/unplaced/{bid}.json"] = {"documents": [
        {"type": d["type"], "pages": d["pages"], "hold": d["hold"], "why": WHY.get(d["hold"]),
         "suggested_sor": d["suggest"], "evidence": d["evidence"]} for d in held]}
    for d in held:
        for n in d["pages"]:
            want[f"{PREFIX}bundles/_held/unplaced/{file_name(bid, n, d['type'])}"] = up.get(n)
    have = {o.object_name for o in c.list_objects(bucket, prefix=f"{PREFIX}bundles/", recursive=True)
            if f"/{bid}-p" in o.object_name or o.object_name.endswith(f"/{bid}.json")}
    for name in have - set(want) - set(manifests):
        c.remove_object(bucket, name)
    for name, src in want.items():
        if src and name not in have:
            c.copy_object(bucket, name, CopySource(bucket, src))
    for name, m in manifests.items():
        body = json.dumps(m, indent=1, ensure_ascii=False).encode()
        c.put_object(bucket, name, io.BytesIO(body), len(body), content_type="application/json")
    return len(want)


# ---------------------------------------------------------------------------------------------------- database

def run(bid, folders=True):
    """Group one batch. Serialised per batch (a person's confirmation, the worker and `again` may all regroup)."""
    with db.connect(autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"group:{bid}",))
        try:
            out = _run(bid, folders)
            try:                                          # phase 7c: each bundle's checks and status
                from grouper import crosscheck
                crosscheck.run(bid)
            except Exception as e:                        # never fails the grouping
                print(f"cross-checking {bid} failed: {type(e).__name__}: {e}", flush=True)
            return out
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"group:{bid}",))


def _run(bid, folders):
    with db.connect() as c:
        pages = [dict(r) for r in c.execute("""SELECT page_no, doc_type::text AS doc_type, type_status, keys,
                                                       upright_path FROM staging.page WHERE batch_id=%s""", (bid,))]
        sos = satellite.load(c)
    out = plan(pages, sos)
    with db.connect() as c:                               # this batch's last grouping goes; other batches' stay
        before = {r["sor_no"] for r in c.execute(
            """SELECT DISTINCT b.sor_no FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                 JOIN staging.document d ON d.id = bd.document_id WHERE d.batch_id = %s""", (bid,))}
        was = {r["sor_no"]: r["hold_reason"] for r in c.execute(   # each order's hold before this scan's grouping
            "SELECT sor_no, hold_reason FROM staging.bundle WHERE status <> 'published' AND sor_no = ANY(%s)",
            (sorted(before | set(out["bundles"])),))}
        c.execute("""DELETE FROM staging.bundle_document bd USING staging.document d
                     WHERE bd.document_id = d.id AND d.batch_id = %s""", (bid,))
        c.execute("DELETE FROM staging.document WHERE batch_id=%s", (bid,))
        c.execute("""DELETE FROM staging.bundle b WHERE b.status NOT IN ('reviewed', 'published')
                     AND NOT EXISTS (SELECT 1 FROM staging.bundle_document bd WHERE bd.bundle_id = b.id)""")
        ids = {}
        for d in out["documents"]:
            if not d["type"]:                             # an unread page is not a document yet
                continue
            ids[id(d)] = c.execute("""
                INSERT INTO staging.document (batch_id, doc_type, page_from, page_to, key_sor, key_po_no, resolved_sor,
                                              linked_by, keys, evidence, hold_reason, suggested_sor)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (bid, d["type"], d["pages"][0], d["pages"][-1], d["read"].get("sor"), d["read"].get("po_no"),
                 d["sor"], d["linked_by"], Json(d["keys"]), Json(d["evidence"]), d["hold"], d["suggest"])).fetchone()["id"]
        for s, b in out["bundles"].items():
            done = c.execute("SELECT id FROM staging.bundle WHERE sor_no=%s AND status='published' ORDER BY id DESC "
                             "LIMIT 1", (s,)).fetchone()     # published (phase 8): its pages go back to it, never to
            if done:                                         # a new bundle that would come to Review again
                for d in b["documents"]:
                    c.execute("INSERT INTO staging.bundle_document (bundle_id, document_id) VALUES (%s, %s)",
                              (done["id"], ids[id(d)]))
                continue
            bundle = c.execute("""
                INSERT INTO staging.bundle (sor_no, status, hold_reason, folder)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sor_no) WHERE status <> 'published'
                DO UPDATE SET hold_reason = EXCLUDED.hold_reason, folder = EXCLUDED.folder,
                              status = CASE WHEN staging.bundle.status IN ('reviewed') THEN staging.bundle.status
                                            ELSE EXCLUDED.status END
                RETURNING id""", (s, "needs_review" if b["hold"] else "grouping", b["hold"], folder(s, b["hold"]))
            ).fetchone()["id"]
            for d in b["documents"]:
                c.execute("INSERT INTO staging.bundle_document (bundle_id, document_id) VALUES (%s, %s)",
                          (bundle, ids[id(d)]))
        others = _order_holds(c, bid, sorted(before | set(out["bundles"])), out, was)
    files = sync_folders(bid, pages, out) if folders else 0
    _wake(others)
    return out, files


def _order_holds(c, bid, sors, out, was=None):
    """The order-level holds over every scan, for the orders this batch's documents are (or were) in: an order is
    complete when its FP sits in any scan. out's bundles get the final hold (for their folders). Returns the other
    scans to regroup, those of orders whose hold changed since before this grouping (`was`; their folders and
    checks follow)."""
    was = was or {}
    from grouper import members
    wake = set()
    for s in sors:                                        # in SOR order: two batches never wait on each other
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"bundle:{s}",))
        b = c.execute("""SELECT id, hold_reason, status::text AS status FROM staging.bundle WHERE sor_no=%s
                           AND status <> 'published' ORDER BY id DESC LIMIT 1""", (s,)).fetchone()
        if not b:
            continue
        rows = members.of_bundle(c, b["id"])
        hold = members.hold_of([r["t"] for r in rows])
        if hold != b["hold_reason"]:                      # this scan's own grouping wrote its scan-only hold
            status = "needs_review" if hold else ("grouping" if b["status"] == "needs_review" else b["status"])
            c.execute("UPDATE staging.bundle SET hold_reason=%s, status=%s, folder=%s WHERE id=%s",
                      (hold, status, folder(s, hold), b["id"]))
        if hold != was.get(s, hold):
            wake |= {r["batch_id"] for r in rows} - {bid}
        if s in out["bundles"]:
            out["bundles"][s]["hold"] = hold
    return wake


def _wake(batches):
    """Regroup the order's other scans (vf-grouper): their folders and checks follow the order's new state."""
    if not batches:
        return
    try:
        from common import queue
        for b in sorted(batches):
            queue.wake_grouper(b, "an order across scans changed")
    except Exception as e:                                # the next wake-up or the sweep regroups them anyway
        print(f"could not wake the grouper for {sorted(batches)}: {e}", flush=True)


def summary(out):
    b = out["bundles"]
    complete = sorted(s for s, x in b.items() if not x["hold"])
    held = [d for d in out["documents"] if d["hold"]]
    return (f"{len(complete)} bundles: " + ", ".join(f"{s} (pages {sorted(n for d in b[s]['documents'] for n in d['pages'])})"
                                                     for s in complete) +
            f"\n{len(b) - len(complete)} held bundles: " + ", ".join(f"{s} ({x['hold']})" for s, x in b.items() if x["hold"]) +
            f"\n{len(held)} documents not placed: " + ", ".join(f"p{d['pages'][0]} {d['type'] or ''} ({d['hold']})"
                                                                 for d in held))


if __name__ == "__main__":
    if "--recheck" in sys.argv:
        from worker import vf
        with db.connect() as c:
            read = [r["page_no"] for r in c.execute(
                "SELECT page_no FROM staging.page WHERE batch_id=%s AND classical_text IS NOT NULL ORDER BY 1",
                (sys.argv[1],))]
        for n in read:
            vf.recheck(sys.argv[1], n)
        print(f"re-checked {len(read)} pages")
    out, files = run(sys.argv[1])
    print(summary(out))
    print(f"{files} page files in the bundle folders")
    with db.connect() as c:                               # phase 7c: what the cross-checks decided
        for b in c.execute("""SELECT DISTINCT b.sor_no, b.status::text AS status, b.checks->'reasons' AS reasons
                                FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
                                JOIN staging.document d ON d.id = bd.document_id WHERE d.batch_id = %s
                               ORDER BY 1""", (sys.argv[1],)):
            print(f"{b['sor_no']}: {b['status']}" + (f" · {len(b['reasons'] or [])} reasons: "
                                                     f"{(b['reasons'] or [''])[0][:150]}" if b["reasons"] else ""))
