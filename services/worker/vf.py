"""vlm-first page worker (PIPELINE=vlm-first). One ticket = one page:

  1 prepare the image   enhance.prepare: dark bands, upright, straighten, QR. No Tesseract reading.
  2 AI OCR, all fields  Gemini reads the page against the WHOLE combined field list (and where each value is)
  3 Jev                 classifies from that reading, knowing every type's fields (common/context.py)
  4 decide              Jev >= 0.85. An SOR QR means FP; an FP also needs the QR or the FP layout (both from the
                        image, never from the AI). Otherwise unsure: the page waits for a person's label.
  5 Tesseract reads     only now, after classification (decided or labelled pages)
  6 check               each value: printed in Tesseract's text / = the QR / FP sums (common/verify.py); then
                        Tesseract re-reads each unconfirmed value's spot, zoomed in (worker/zoom.py)
  7 look again          Gemini, blind: told which fields Tesseract didn't back, never what Tesseract read.
                        A new answer is kept only if print backs it.
  8 outcome             clear (every §6.1 field backed by print) · needs_person · held_unsure

  python -m worker.vf once <batch> <pages> [--v1-reading]   run pages now, without the queue
      --v1-reading   dry run: instead of calling Gemini, use v1's stored reading renamed to the combined list
                     (no boxes, no second look). For trying the flow on days the Gemini quota is used up.
"""
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from common import context, db, verify
from common import keys as keymod
from common.fields import DOCS, TYPE_MAP, lift, project
from common.models import vlm
from worker import classify, enhance, layout, zoom
from worker import main as v1

PREP_VERSION = 1
JEV_DECIDE = 0.85
PREFIX = os.environ.get("STORAGE_PREFIX", "")
DAILY_GEMINI_CAP = int(os.environ.get("VF_GEMINI_DAILY_CAP", "40"))
REQUIRED = {code: [f["name"] for f in d["header"] if f["source"] == "6.1"] for code, d in DOCS.items()}
PREP_COLS = ("rotation", "osd_conf", "skew_angle", "black_ratio", "dark_band_ratio", "speckle_ratio", "qr_text",
             "layout_score")


class OutOfBudget(Exception):
    pass


# ---------------------------------------------------------------------------------------------- bookkeeping

def pacific_day():
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def ledger(provider, purpose, bid, n, ok, meta=None, error=None):
    meta = meta or {}
    with db.connect(autocommit=True) as c:
        c.execute("""INSERT INTO staging.model_call (pacific_day, provider, model, purpose, batch_id, page_no, ok, ms,
                                                     tokens, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (pacific_day(), provider, meta.get("model"), purpose, bid, n, ok, meta.get("ms"),
                   Json({k: meta.get(k) for k in ("tokens_in", "tokens_out", "tokens_thinking")}), error))


def gemini_left():
    with db.connect() as c:
        used = c.execute("SELECT count(*) AS n FROM staging.model_call WHERE provider='gemini' AND pacific_day=%s",
                         (pacific_day(),)).fetchone()["n"]
    return DAILY_GEMINI_CAP - used


def gemini(purpose, bid, n, call, *args):
    if gemini_left() <= 0:
        raise OutOfBudget(f"vlm-first's Gemini cap for today ({DAILY_GEMINI_CAP}) is used up")
    try:
        out, meta = call(*args)
    except Exception as e:
        ledger("gemini", purpose, bid, n, False, error=f"{type(e).__name__}: {e}"[:300])
        raise
    ledger("gemini", purpose, bid, n, True, meta)
    return out, meta


# ---------------------------------------------------------------------------------------------- the steps

def prepare(ticket, prev, bid, n):
    """Step 1. Reused when this page was already prepared by the same code."""
    if prev and prev["prep_version"] == PREP_VERSION and (prev["upright_path"] or "").startswith(PREFIX or "\0"):
        return v1.load(prev["upright_path"]), {k: prev[k] for k in PREP_COLS}, prev["upright_path"], \
            prev["thumb_upright_path"], [f for f in prev["quality_flags"] or [] if f in ("rotated", "skewed", "dark_band")]
    if not PREFIX:
        raise RuntimeError("STORAGE_PREFIX must be set: vlm-first never writes v1's image keys")
    up, _, prep = enhance.prepare(v1.load(ticket["image_key"]))       # v1's render: read, never written
    prep["layout_score"] = layout.similarity(classify.FP_TEMPLATE, layout.fingerprint(up))
    base = f"{PREFIX}pages/{bid}"
    up_key, thumb_key = f"{base}/upright/p{n:03d}.png", f"{base}/thumb_upright/p{n:03d}.jpg"
    v1.put_png(up_key, up)
    v1.put_thumb(thumb_key, up)
    return up, {k: prep[k] for k in PREP_COLS}, up_key, thumb_key, prep["quality_flags"]


def jev_state(fields_all, ctx):
    """What Jev sees: only the AI OCR's reading (values as printed), never Tesseract."""
    found, clue = {}, {"document_title": None, "page_marker": None}
    for name, f in (fields_all or {}).items():
        if name == "lines" or not isinstance(f, dict) or f.get("value") in (None, ""):
            continue
        text = f.get("source_text") or f.get("value")
        if name in clue:
            clue[name] = text
        else:
            found[name] = text
    lines = (fields_all or {}).get("lines") or []
    return {"found": found, "not_found": sorted(n for n in ctx["fields"] if n not in found and n not in clue),
            **clue, "line_rows": len(lines), "first_rows": [r.get("row_text") for r in lines[:2]]}


def decide(jev, qr_sor, layout_score):
    """(type_status, doc_type, guess, reason). Jev decides at >= 0.85; two vetoes from the image keep a wrong FP out."""
    jc, conf = jev.get("choice"), jev.get("confidence") or 0.0
    if not jc:
        return "unsure", None, None, "Jev unavailable"
    fp_witness = qr_sor or (layout_score or 0) >= classify.LAYOUT_FP
    if conf < JEV_DECIDE:
        return "unsure", None, jc, f"Jev not sure enough ({jc} {conf:.2f} < {JEV_DECIDE})"
    if qr_sor and jc != "FP":
        return "unsure", None, jc, f"the SOR QR code says FP, Jev says {jc}"
    if jc == "FP" and not fp_witness:
        return "unsure", None, "FP", "an FP needs the QR code or the FP layout as well"
    return "decided", jc, jc, f"Jev {jc} {conf:.2f}" + (" + QR" if qr_sor else " + FP layout" if jc == "FP" else "")


def label_of(bid, n):
    with db.connect() as c:
        r = c.execute("SELECT label::text AS label FROM staging.type_label WHERE batch_id=%s AND page_no=%s",
                      (bid, n)).fetchone()
    return r and r["label"]


def check(doc_type, fields_all, rd, qr_text, up, ctx):
    """Step 6: verify.run on the type's fields, then a zoomed Tesseract look at each value's spot that the
    whole-page reading didn't back. Returns (verdicts, zoom evidence)."""
    fields = project(fields_all, doc_type)
    res = verify.run(doc_type, fields, rd["classical_text"], qr_text)
    ev = {}
    if not res:
        return res, ev
    canon_of = {v: k for k, v in TYPE_MAP[doc_type].items()}
    for name, v in res["header"].items():
        if v["verdict"] != "check" or not v["why"].startswith(("not in", "too short")):
            continue
        f = fields_all.get(canon_of[name]) or {}
        printed = ctx["fields"].get(canon_of[name], {}).get("printed_as", [])
        rect, how = zoom.locate(up, rd["ocr_words"], f.get("source_text"), printed, f.get("box"))
        if not rect:
            ev[name] = {"how": None, "ok": False, "why": "no spot found"}
            continue
        texts, near = zoom.reread(up, rect), zoom.band(rd["ocr_words"], rect)
        ok, why = zoom.confirm(f.get("source_text"), texts, near)
        ev[name] = {"rect": list(rect), "how": how, "texts": texts, "band": near, "ok": ok, "why": why}
        res["header"][name] = {"verdict": "ok", "by": "zoom"} if ok else {**v, "why": f"{v['why']}; zoomed in: {why}"}
    return res, ev


def crop_png(up, box):
    rect = zoom.by_ai_box(box, up.shape)
    if not rect:
        return None
    x0, y0, x1, y1 = rect
    h, w = up.shape[:2]
    c = up[max(0, y0 - 40):min(h, y1 + 40), max(0, x0 - 60):min(w, x1 + 60)]
    return v1.png_bytes(cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)) if c.size else None


def second_look_asks(doc_type, res, ctx):
    """Header values still unconfirmed, and required (§6.1) values that came back empty."""
    canon_of = {v: k for k, v in TYPE_MAP[doc_type].items()}
    asks = []
    for name, v in res["header"].items():
        if v["verdict"] == "check" or (v["verdict"] == "empty" and name in REQUIRED[doc_type]):
            c = canon_of[name]
            asks.append((c, ctx["fields"][c]["meaning"]))
    return asks


def settle(first, second, backed):
    """Pure: what a second look means for one field. first/second: the two readings; backed: print backs `second`."""
    if not second or second.get("unsure") or second.get("value") in (None, ""):
        return False, "second look: unsure or not found"
    if backed:
        return True, "backed by print after a second look"
    if verify.flat((first or {}).get("source_text")) == verify.flat(second.get("source_text")):
        return False, "the AI OCR read the same twice; Tesseract doesn't back it"
    return False, (f"the AI OCR changed its reading ({(first or {}).get('source_text')!r} then "
                   f"{second.get('source_text')!r}); Tesseract backs neither")


def look_again(doc_type, fields_all, res, rd, qr_text, up, ctx, bid, n):
    """Step 7. Returns (fields_all, verdicts, zoom evidence, second-look evidence)."""
    asks = second_look_asks(doc_type, res, ctx)
    if not asks:
        return fields_all, res, None, None
    crops = [(c, png) for c, _ in asks for png in [crop_png(up, (fields_all.get(c) or {}).get("box"))] if png]
    answers, meta = gemini("second_look", bid, n, vlm.second_look, v1.png_bytes(up), asks, crops)
    trial = dict(fields_all)
    for c, _ in asks:
        a = (answers or {}).get(c)
        if a and not a.get("unsure") and a.get("value") not in (None, ""):
            trial[c] = {k: a.get(k) for k in ("value", "source_text", "box")}
    res2, ev2 = check(doc_type, trial, rd, qr_text, up, ctx)
    out, results = dict(fields_all), {}
    name_of = TYPE_MAP[doc_type]
    for c, _ in asks:
        backed = res2["header"].get(name_of[c], {}).get("verdict") == "ok"
        keep, why = settle(fields_all.get(c), (answers or {}).get(c), backed)
        results[c] = {"first": fields_all.get(c), "second": (answers or {}).get(c), "kept_second": keep, "why": why}
        if keep:
            out[c] = trial[c]
    res3, ev3 = check(doc_type, out, rd, qr_text, up, ctx)
    for c, r in results.items():                     # say why each asked field ended as it did
        v = res3["header"].get(name_of[c])
        if v and v["verdict"] == "ok" and r["kept_second"]:
            res3["header"][name_of[c]] = {"verdict": "ok", "by": "second_look"}
        elif v and v["verdict"] != "ok":
            v["why"] = f"{v.get('why', 'empty')}; {r['why']}"
    return out, res3, ev3, {"asked": [c for c, _ in asks], "crops": [c for c, _ in crops], "results": results,
                            "meta": meta}


def outcome(type_status, doc_type, res):
    if type_status not in ("decided", "labelled"):
        return "held_unsure"
    if doc_type == "OTHER":
        return "needs_person"
    if not res:                                      # CONTINUATION, SJ, PEL: no field list to check
        return "clear"
    return "clear" if all(res["header"].get(f, {}).get("verdict") == "ok" for f in REQUIRED[doc_type]) \
        else "needs_person"


# ---------------------------------------------------------------------------------------------- one page

def handle(ticket, v1_reading=None):
    bid, n = ticket["batch_id"], ticket["page_no"]
    run = ticket.get("run", 1)
    if run != v1.current_run(bid):
        return "stale"
    with db.connect() as c:
        prev = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        ctx_v, ctx = context.ensure(c, classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS,
                                    classify.JEV_TYPES)
    fv = context.fields_version(ctx)
    fv_saved = fv if v1_reading is None else "dry-run"   # a dry-run reading is never reused as if Gemini made it

    up, prep, up_key, thumb_key, prep_flags = prepare(ticket, prev, bid, n)                        # 1

    x = {"fields_all": None, "extract_status": None, "extract_error": None, "vlm_meta": {}}      # 2
    if prev and prev["fields_all"] and prev["fields_version"] == fv:
        x.update(fields_all=prev["fields_all"], extract_status="done", vlm_meta=prev["vlm_meta"] or {})
    elif v1_reading is not None:
        x.update(fields_all=v1_reading, extract_status="done", vlm_meta={"read": {"model": "v1 reading (dry run)"}})
    else:
        try:
            fa, meta = gemini("read_all", bid, n, vlm.extract_all, v1.png_bytes(up), context.vlm_schema(ctx))
            x.update(fields_all=fa, extract_status="done", vlm_meta={"read": meta})
        except Exception as e:
            x.update(extract_status="failed", extract_error=f"{type(e).__name__}: {e}"[:500])

    cls = {"type_status": "unsure", "doc_type": None, "type_guess": None, "doc_type_conf": None,   # 3 + 4
           "type_votes": {"reason": "not read by the AI OCR"}}
    if x["fields_all"] is not None:
        old = (prev or {}).get("type_votes") or {}
        state = jev_state(x["fields_all"], ctx)
        if old.get("context_version") == ctx_v and old.get("fields_version") == fv_saved and old.get("jev", {}).get("choice"):
            jev = old["jev"]
        else:
            t0 = time.time()
            jev = classify.jev_ask(state, context.jev_question(ctx))
            ledger("jev", "classify", bid, n, bool(jev.get("choice")), {"model": jev.get("model"),
                   "ms": int((time.time() - t0) * 1000)}, jev.get("error") or jev.get("skipped"))
        qr_sor = bool(prep["qr_text"] and classify.SOR_RE.match(prep["qr_text"]))
        status, doc_type, guess, reason = decide(jev, qr_sor, prep["layout_score"])
        votes = {"jev": jev, "state": state, "qr_sor": qr_sor, "layout": prep["layout_score"], "reason": reason,
                 "context_version": ctx_v, "fields_version": fv_saved,
                 "machine": {"status": status, "doc_type": doc_type, "guess": guess, "reason": reason}}
        label = label_of(bid, n)
        if label:                                    # a person's label decides; the machine's answer is kept for grading
            status, doc_type, reason = "labelled", label, f"labelled {label} by a person"
            votes["reason"] = reason
        cls = {"type_status": status, "doc_type": doc_type, "type_guess": guess or doc_type,
               "doc_type_conf": jev.get("confidence"), "type_votes": votes}

    rd, res, zev, sl = {}, None, None, None                                                      # 5, 6, 7
    fields_all = x["fields_all"]
    if cls["type_status"] in ("decided", "labelled"):
        work = enhance.mask_bands(up, *enhance.measure(up)[2:])
        _, rd = enhance.read(work)
        res, zev = check(cls["doc_type"], fields_all, rd, prep["qr_text"], up, ctx)
        if res and v1_reading is None:
            try:
                fields_all, res, zev2, sl = look_again(cls["doc_type"], fields_all, res, rd, prep["qr_text"], up,
                                                       ctx, bid, n)
                zev = zev2 if zev2 is not None else zev
            except Exception as e:
                sl = {"error": f"{type(e).__name__}: {e}"[:300]}
    oc = outcome(cls["type_status"], cls["doc_type"], res)                                       # 8
    fields = project(fields_all, cls["doc_type"]) if fields_all is not None and cls["doc_type"] in DOCS else {}
    keys = keymod.derive(cls["doc_type"], fields, rd.get("classical_text"), prep["qr_text"]) if fields else {}

    with db.connect() as c:
        saved = c.execute("""
            UPDATE staging.page SET status='read', upright_path=%(up)s, thumb_upright_path=%(thumb)s,
              rotation=%(rotation)s, osd_conf=%(osd_conf)s, skew_angle=%(skew_angle)s, black_ratio=%(black_ratio)s,
              dark_band_ratio=%(dark_band_ratio)s, speckle_ratio=%(speckle_ratio)s, qr_text=%(qr_text)s,
              layout_score=%(layout_score)s, prep_version=%(pv)s, quality_flags=%(flags)s,
              ocr_variant=%(ocr_variant)s, variant_scores=%(variant_scores)s, ocr_conf=%(ocr_conf)s,
              confident_chars=%(confident_chars)s, ocr_words=%(ocr_words)s, classical_text=%(classical_text)s,
              fields_all=%(fields_all)s, fields_version=%(fv)s, fields=%(fields)s, keys=%(keys)s,
              extract_status=%(extract_status)s, extract_error=%(extract_error)s, vlm_meta=%(vlm_meta)s,
              model_vlm=%(model_vlm)s,
              type_status=%(type_status)s, doc_type=%(doc_type)s, type_guess=%(type_guess)s,
              doc_type_conf=%(doc_type_conf)s, type_votes=%(type_votes)s, context_version=%(cv)s,
              zoom=%(zoom)s, second_look=%(second_look)s, outcome=%(outcome)s, verify_version=%(vv)s,
              error=NULL, read_at=now()
            WHERE batch_id=%(bid)s AND page_no=%(n)s AND status <> 'read'
              AND EXISTS (SELECT 1 FROM staging.scan_batch WHERE id=%(bid)s AND run=%(run)s)
            RETURNING page_no""",
            {**prep, **{k: rd.get(k) for k in ("ocr_variant", "ocr_conf", "confident_chars", "classical_text")},
             "variant_scores": Json(rd.get("variant_scores")), "ocr_words": Json(rd.get("ocr_words")),
             "flags": prep_flags + rd.get("quality_flags", []), "pv": PREP_VERSION,
             "fields_all": Json(fields_all) if fields_all is not None else None, "fv": fv_saved,
             "fields": Json(fields), "keys": Json(keys), "extract_status": x["extract_status"],
             "extract_error": x["extract_error"], "vlm_meta": Json(x["vlm_meta"]),
             "model_vlm": (x["vlm_meta"].get("read") or {}).get("model"),
             **cls, "type_votes": Json(cls["type_votes"]), "cv": ctx_v,
             "zoom": Json(zev) if zev else None, "second_look": Json(sl) if sl else None, "outcome": oc,
             "vv": verify.VERIFY_VERSION if res else None,
             "up": up_key, "thumb": thumb_key, "bid": bid, "n": n, "run": run}).fetchone()
        if saved:
            verify.store(c, bid, n, fields, res)
    if not saved:
        return None
    return v1.tick(bid, run)


# ---------------------------------------------------------------------------------------------- trials

def once(bid, pages, use_v1_reading=False):
    """Run pages now, without the queue. With use_v1_reading, v1's stored reading stands in for Gemini (dry run)."""
    readings = {}
    if use_v1_reading:
        with psycopg.connect(os.environ["MAIN_DATABASE_URL"], row_factory=dict_row) as m:
            for r in m.execute("""SELECT page_no, doc_type::text AS t, fields FROM staging.page WHERE batch_id=%s
                                  AND page_no = ANY(%s) AND extract_status='done'""", (bid, pages)):
                readings[r["page_no"]] = lift(r["fields"], r["t"])
    run = v1.current_run(bid)
    for n in pages:
        with db.connect() as c:
            p = c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND page_no=%s "
                          "RETURNING image_path, original_path", (bid, n)).fetchone()
        if not p:
            print(f"page {n}: not in this database (clone it first)")
            continue
        if use_v1_reading and n not in readings:
            print(f"page {n}: v1 has no reading for it; skipped in a dry run")
            continue
        t0 = time.time()
        handle({"batch_id": bid, "page_no": n, "run": run, "image_key": p["original_path"] or p["image_path"]},
               v1_reading=readings.get(n) if use_v1_reading else None)
        with db.connect() as c:
            r = c.execute("""SELECT type_status, doc_type::text AS t, type_votes->>'reason' AS why, outcome,
                                    extract_status, extract_error FROM staging.page WHERE batch_id=%s AND page_no=%s""",
                          (bid, n)).fetchone()
            rows = c.execute("SELECT status, count(*) AS k FROM staging.field_check WHERE batch_id=%s AND page_no=%s "
                             "AND field_path LIKE 'header.%%' GROUP BY 1", (bid, n)).fetchall()
        tally = " ".join(f"{x['status']} {x['k']}" for x in rows)
        print(f"page {n}: {r['type_status']} {r['t'] or ''} ({r['why']}) · outcome {r['outcome']} · main fields: "
              f"{tally or '-'} · {time.time() - t0:.0f} s" + (f" · {r['extract_error']}" if r["extract_error"] else ""))


if __name__ == "__main__":
    if sys.argv[1] == "once":
        from worker.clone import pages_arg
        once(sys.argv[2], pages_arg(sys.argv[3]), "--v1-reading" in sys.argv)
