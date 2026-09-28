"""vlm-first page worker (PIPELINE=vlm-first). One ticket = one page:

  1 prepare the image   enhance.prepare: dark bands, upright, straighten, QR. No Tesseract reading.
  2 AI OCR, all fields  Gemini reads the page against the WHOLE combined field list (and where each value is)
  3 Jev                 classifies from that reading, knowing every type's fields (common/context.py)
  4 decide              Jev >= 0.85. An SOR QR means FP; an FP also needs the QR, the FP layout or the printed title
                        FAKTUR PENJUALAN (all from the image, never from the AI). Otherwise unsure: a person's label.
  5 Tesseract reads     only now, after classification (decided or labelled pages)
  6 check               each value: printed in Tesseract's text / = the QR (common/verify.py); then Tesseract re-reads
                        each unconfirmed value's spot, zoomed in (worker/zoom.py); after every witness, the 7a rules
                        (common/gates.py), which only take ✅ away but for FP sums over two independent amounts
  7 look again          the same AI OCR, blind: told which fields Tesseract didn't back, never what Tesseract read.
                        A new answer is kept only if print backs it.
  7b the store (S4)     only when a key the AI alone read matches one SO while SOs one character away ship to other
                        stores: one blind question, which store the goods go to. Its words must name that SO's store
                        and none of the others' (common/satellite.py by_store).
  8 outcome             clear (every §6.1 field backed by print) · waiting_ai (the AI OCR still has to read the page
                        or look again) · needs_person (still not backed after the look-again) · held_unsure (a label)

  Nothing goes to a person before the AI OCR has read the page and looked again. When a call can't run (skipped to
  save tokens, the daily budget is used up, or it failed), the page WAITS for it (outcome waiting_ai): a failed read
  leaves the page unclassified (type_status NULL, never "unsure": the Label screen is for pages Jev couldn't decide);
  a look-again not run stores second_look = {"waiting": why}. `again` runs what is waiting.

  The AI OCR is VF_AI_OCR: "gemini" (common/models/vlm.py) or provider:model through any OpenAI-compatible vision
  model (common/models/openai_vlm.py), e.g. groq:qwen/qwen3.8-27b. A reading is tied to the model that made it.

  python -m worker.vf once <batch> <pages> [--v1-reading] [--no-second-look]   run pages now, without the queue
      --v1-reading      dry run: instead of calling Gemini, use v1's stored reading renamed to the combined list
                        (no boxes, no second look). For trying the flow on days the Gemini quota is used up.
      --no-second-look  read and check only; the pages wait for their look-again
  python -m worker.vf again <batch> [<pages>]   run what waits for the AI OCR: look-agains not run yet and failed
                        reads. The reading and Jev's answer are reused. Stops when the daily budget is used up.
  python -m worker.vf shadow <batch> <pages>    what today's rules would change (✅ lost or gained, outcomes, keys,
                        new look-again questions), from stored data. Writes nothing, calls no model: measure a rule
                        change before adopting it; adopt with `python -m grouper.group <batch> --recheck`.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import cv2
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from common import context, db, gates, satellite, verify
from common import keys as keymod
from common.fields import DECIDES, DOCS, TYPE_MAP, decides, lift, project
from common.models import openai_vlm, vlm
from worker import classify, enhance, layout, zoom
from worker import main as v1

PREP_VERSION = 1
JEV_DECIDE = 0.85
PREFIX = os.environ.get("STORAGE_PREFIX", "")
AI_OCR = os.environ.get("VF_AI_OCR", "gemini")                      # gemini, or provider:model
AI_PROVIDER = "gemini" if AI_OCR == "gemini" else openai_vlm.spec_parts(AI_OCR)[0]
DAILY_CAP = int(os.environ.get("VF_AI_OCR_DAILY_CAP", "40" if AI_OCR == "gemini" else "150"))
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


def ai_left():
    with db.connect() as c:
        used = c.execute("SELECT count(*) AS n FROM staging.model_call WHERE provider=%s AND pacific_day=%s",
                         (AI_PROVIDER, pacific_day())).fetchone()["n"]
    return DAILY_CAP - used


def refused_for():
    """When the provider's last answer was a daily-limit refusal: the seconds of its wait that haven't passed, else 0.
    Asking before then only fails, and each failure counts against the cap: 166 uploaded pages would use it up."""
    with db.connect() as c:
        r = c.execute("""SELECT extract(epoch FROM now() - at) AS ago, ok, error FROM staging.model_call
                          WHERE provider=%s AND (ok OR error LIKE 'DailyLimit%%') ORDER BY at DESC LIMIT 1""",
                      (AI_PROVIDER,)).fetchone()
    wait = r and not r["ok"] and seconds_until(r["error"])
    return max(0.0, wait - float(r["ago"])) if wait else 0.0


def ai_call(purpose, bid, n, call, *args):
    """One AI OCR call, counted in the ledger and stopped at vlm-first's daily cap for this provider. While the
    provider's last refusal still says to wait, the page waits without a call (`again` picks it up)."""
    if ai_left() <= 0:
        raise OutOfBudget(f"vlm-first's daily cap for {AI_PROVIDER} ({DAILY_CAP}) is used up")
    wait = refused_for()
    if wait:
        raise openai_vlm.DailyLimit(f"{AI_OCR}: daily limit reached (its last refusal), "
                                    f"try again in {int(wait // 60)}m{wait % 60:.1f}s")
    try:
        out, meta = call(*args)
    except Exception as e:
        ledger(AI_PROVIDER, purpose, bid, n, False, {"model": AI_OCR}, f"{type(e).__name__}: {e}"[:300])
        raise
    ledger(AI_PROVIDER, purpose, bid, n, True, meta)
    return out, meta


def read_all(png, schema):
    return vlm.extract_all(png, schema) if AI_OCR == "gemini" else openai_vlm.extract_all(png, schema, AI_OCR)


def look(png, asks, crops):
    return vlm.second_look(png, asks, crops) if AI_OCR == "gemini" else openai_vlm.second_look(png, asks, crops, AI_OCR)


def store_q(png):
    return vlm.store(png) if AI_OCR == "gemini" else openai_vlm.store(png, AI_OCR)


# ---------------------------------------------------------------------------------------------- the steps

def prepare(ticket, prev, bid, n):
    """Step 1. Reused when this page was already prepared by the same code."""
    if prev and prev["prep_version"] == PREP_VERSION and (prev["upright_path"] or "").startswith(PREFIX or "\0"):
        return v1.load(prev["upright_path"]), {k: (float(prev[k]) if isinstance(prev[k], Decimal) else prev[k])
                                                for k in PREP_COLS}, prev["upright_path"], \
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


def normalise_amounts(fields_all, ctx):
    """Amounts get their value from the printed text by code (verify.amount), never from the model: Qwen stored 111.586
    (one hundred eleven) for a printed 111.586 on page 4. The model's own value is kept as ai_value. Idempotent."""
    for name, f in (fields_all or {}).items():
        if name == "lines" or not isinstance(f, dict) or ctx["fields"].get(name, {}).get("kind") != "amount":
            continue
        a = verify.amount(f.get("source_text"))
        if a is not None and f.get("value") != f"{a:.2f}":
            f.setdefault("ai_value", f.get("value"))
            f["value"] = f"{a:.2f}"
    return fields_all


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


def fp_title(up):
    """An FP's third image witness: its printed title FAKTUR PENJUALAN, found by Tesseract in the top 35% of the page.
    Measured on 47 read pages (2026-09-28): 14 of 15 FPs, 0 of 32 other pages. Pages 1 and 10 of 7000363700-03 have a
    QR code too faint to decode and a layout score under 0.70."""
    top = up[: int(up.shape[0] * 0.35)]
    _, rd = enhance.read(enhance.mask_bands(top, *enhance.measure(top)[2:]))
    t = re.sub(r"[^A-Z]", "", (rd.get("classical_text") or "").upper())
    return "FAKTURPENJUALAN" in t or ("FAKTUR" in t and "PENJUAL" in t)


def needs_title(jev, qr_sor, layout_score):
    """Look for the title only when it can decide: Jev says FP, sure enough, and neither QR nor layout backs it."""
    return (jev.get("choice") == "FP" and (jev.get("confidence") or 0) >= JEV_DECIDE and not qr_sor
            and (layout_score or 0) < classify.LAYOUT_FP)


def decide(jev, qr_sor, layout_score, title=False):
    """(type_status, doc_type, guess, reason). Jev decides at >= 0.85; the image vetoes a wrong FP: an FP also needs
    the SOR QR code, the FP layout or the printed title (Jev said FP 0.88 on SAMB's handwritten SALES ORDER form)."""
    jc, conf = jev.get("choice"), jev.get("confidence") or 0.0
    if not jc:
        return "unsure", None, None, "Jev unavailable"
    fp_witness = qr_sor or (layout_score or 0) >= classify.LAYOUT_FP or bool(title)
    if conf < JEV_DECIDE:
        return "unsure", None, jc, f"Jev not sure enough ({jc} {conf:.2f} < {JEV_DECIDE})"
    if qr_sor and jc != "FP":
        return "unsure", None, jc, f"the SOR QR code says FP, Jev says {jc}"
    if jc == "FP" and not fp_witness:
        return "unsure", None, "FP", "an FP needs the QR code, the FP layout or the printed title as well"
    return "decided", jc, jc, f"Jev {jc} {conf:.2f}" + (" + QR" if qr_sor else "" if jc != "FP" else " + FP layout"
                                                        if (layout_score or 0) >= classify.LAYOUT_FP else " + printed title")


def label_of(bid, n):
    with db.connect() as c:
        r = c.execute("SELECT label::text AS label FROM staging.type_label WHERE batch_id=%s AND page_no=%s",
                      (bid, n)).fetchone()
    return r and r["label"]


def check(doc_type, fields_all, rd, qr_text, up, ctx, zoom_cache=None):
    """Step 6: verify.run on the type's fields, then a zoomed Tesseract look at each value's spot that the
    whole-page reading didn't back. Returns (verdicts, zoom evidence). zoom_cache: the page's stored zoom evidence;
    a field whose spot and printed text are unchanged reuses its zoomed readings (no Tesseract call)."""
    fields = project(fields_all, doc_type)
    res = verify.run(doc_type, fields, rd["classical_text"], qr_text, sums=False)   # sums: gates, after every witness
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
            ev[name] = {"how": None, "ok": False, "why": "no spot found", "source": f.get("source_text")}
            continue
        seen = (zoom_cache or {}).get(name) or {}
        texts = (seen["texts"] if seen.get("rect") == list(rect) and seen.get("source") == f.get("source_text")
                 and seen.get("texts") is not None else zoom.reread(up, rect))
        near = zoom.band(rd["ocr_words"], rect)
        ok, why = zoom.confirm(f.get("source_text"), texts, near)
        ev[name] = {"rect": list(rect), "how": how, "texts": texts, "band": near, "ok": ok, "why": why,
                    "source": f.get("source_text")}
        res["header"][name] = {"verdict": "ok", "by": "zoom"} if ok else {**v, "why": f"{v['why']}; zoomed in: {why}"}
    return res, ev


def noted(doc_type, res, second):
    """Say what the look-again did, on the verdicts print gave: a kept answer is ✅ by second_look; a field it couldn't
    settle carries its reason (read the same twice, changed its reading, unsure)."""
    for c, r in ((second or {}).get("results") or {}).items():
        v = (res or {}).get("header", {}).get(TYPE_MAP[doc_type].get(c))
        if not v:
            continue
        if r.get("kept_second") and v["verdict"] == "ok":
            v["by"] = "second_look"
        elif v["verdict"] != "ok" and r["why"] not in (v.get("why") or ""):
            v["why"] = f"{v.get('why', 'empty')}; {r['why']}"
    return res


def page_verdicts(doc_type, fields_all, rd, qr_text, up, ctx, sos, confirmed, second=None, zoom_cache=None,
                  scan_day=None, ship_to=None):
    """Every verdict of one page, the one way: print (Tesseract's page, the QR code, the zoomed spot) → what the
    look-again did → a person and Satellite (and the page's store, S4) → the 7a rules (common/gates.py) → an FP's
    amounts and item lines from its SO record (7b). Returns (fields, verdicts, zoom evidence)."""
    res, zev = check(doc_type, fields_all, rd, qr_text, up, ctx, zoom_cache)
    res = noted(doc_type, res, second)
    fields, res = settled(doc_type, fields_all, res, sos, confirmed, ship_to, qr_text)
    res = gates.apply(doc_type, fields, res, scan_day)
    fields, res = satellite.settle_record(doc_type, fields, res, sos, satellite.items_for)   # 7b: the FP's record
    return fields, res, zev


def scan_day_of(c, bid):
    """The day the stack was scanned: no date on its pages can be later."""
    r = c.execute("SELECT coalesce(scanned_day, received_at::date) AS d FROM staging.scan_batch WHERE id=%s",
                  (bid,)).fetchone()
    return r and r["d"]


def crop_png(up, box):
    rect = zoom.by_ai_box(box, up.shape)
    if not rect:
        return None
    x0, y0, x1, y1 = rect
    h, w = up.shape[:2]
    c = up[max(0, y0 - 40):min(h, y1 + 40), max(0, x0 - 60):min(w, x1 + 60)]
    return v1.png_bytes(cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)) if c.size else None


def second_look_asks(doc_type, res, ctx, asked=(), requested=()):
    """What the page's look-again asks (verification redesign, S1): only a value the page decides itself that nothing
    has settled: its keys while none of them is settled, the FP's own values. Never a value print holds against
    Satellite (a person decides that), an amount cut at the scan's edge (its digits aren't printed), a value its
    bundle judges against Satellite (a PO's total, a receipt's date: the bundle asks, S2), or a value kept as read.
    Unconfirmed, or required (§6.1) and empty; minus what the look-again already asked for this reading. `requested`:
    what the bundle's checks asked for (S2, item 11: a PO's or receipt's value only the AI read, beyond Satellite's)."""
    canon_of = {v: k for k, v in TYPE_MAP[doc_type].items()}
    return [(canon_of[name], ctx["fields"][canon_of[name]]["meaning"])
            for name in to_ask(doc_type, res["header"], asked, requested)]


def to_ask(doc_type, header, asked=(), requested=()):
    """second_look_asks() on the verdicts alone: the names of the fields to ask (header order, then the bundle's)."""
    canon_of = {v: k for k, v in TYPE_MAP[doc_type].items()}
    keys = DECIDES.get(doc_type, {}).get("keys", ())
    wanted = decides(doc_type, "page", "support") | (set() if any(_ok(header.get(k)) for k in keys) else set(keys))
    own = [name for name, v in header.items()
           if name in wanted and not v.get("conflict") and not v.get("cut") and canon_of[name] not in asked
           and (v["verdict"] == "check" or (v["verdict"] == "empty" and name in REQUIRED[doc_type]))]
    return own + [TYPE_MAP[doc_type][c] for c in requested
                  if c in TYPE_MAP[doc_type] and c not in asked and TYPE_MAP[doc_type][c] not in own]


def _ok(v):
    return (v or {}).get("verdict") == "ok"


def page_settled(doc_type, header):
    """Has the page settled what it decides itself? A key it links by, the FP's own amounts, and nothing it decides
    that print holds against Satellite. A PO's or a receipt's amounts and dates are its bundle's to judge, and values
    kept as read never hold a page."""
    keys = DECIDES.get(doc_type, {}).get("keys", ())
    return (not keys or any(_ok(header.get(k)) for k in keys)) and \
        all(_ok(header.get(f)) for f in decides(doc_type, "page")) and \
        not any((header.get(f) or {}).get("conflict") for f in decides(doc_type, "support"))


def asked_of(second):
    """Fields the look-again already asked for this reading."""
    s = second or {}
    return set(s.get("asked") or []) | set((s.get("results") or {}).keys())


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


def look_again(doc_type, fields_all, res, rd, qr_text, up, ctx, bid, n, asked=(), requested=()):
    """Step 7. Returns (fields_all, verdicts, zoom evidence, second-look evidence)."""
    asks = second_look_asks(doc_type, res, ctx, asked, requested)
    if not asks:
        return fields_all, res, None, None
    crops = [(c, png) for c, _ in asks for png in [crop_png(up, (fields_all.get(c) or {}).get("box"))] if png]
    answers, meta = ai_call("second_look", bid, n, look, v1.png_bytes(up), asks, crops)
    trial = dict(fields_all)
    for c, _ in asks:
        a = (answers or {}).get(c)
        if a and not a.get("unsure") and a.get("value") not in (None, ""):
            trial[c] = {k: a.get(k) for k in ("value", "source_text", "box")}
    normalise_amounts(trial, ctx)
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


def settled(doc_type, fields_all, res, sos, confirmed, ship_to=None, qr_text=None):
    """Step 6b: (fields, verdicts) after a person's confirmations and Satellite's SO record (common/satellite.py),
    the witnesses beside print. The fields are the type's projection; a changed value keeps the AI's as ai_value."""
    fields = project(fields_all, doc_type) if fields_all is not None and doc_type in DOCS else {}
    if not res:
        return fields, res
    f, h = satellite.settle(doc_type, fields, res["header"], sos, confirmed, satellite.items_for, ship_to, qr_text)
    f, ls = satellite.person_lines(doc_type, f, res["lines"], confirmed)   # line cells a person confirmed (7d)
    return f, {**res, "header": h, "lines": ls}


STORE_WAIT = "the store question (S4): the AI OCR hasn't said which store the page's goods go to"


def store_pending(doc_type, res, ship_to):
    """S4: does the page wait for the store question? When none of its keys is settled, one of them only the AI read
    matches exactly one SO while others are one character away, and their stores differ (the verdict carries
    `store`), and the question hasn't been asked for this page."""
    if ship_to is not None or not res:
        return False
    keys = DECIDES.get(doc_type, {}).get("keys", ())
    header = res["header"]
    return not any(_ok(header.get(k)) for k in keys) and any((header.get(k) or {}).get("store") for k in keys)


def store_step(bid, n, up, skip=None):
    """S4: ask the AI OCR, blind (the page only; never Satellite's store names), which store the goods go to.
    (answer, None), or (None, why it waits)."""
    if skip:
        return None, f"{STORE_WAIT} ({skip})"
    try:
        a, meta = ai_call("ship_to", bid, n, store_q, v1.png_bytes(up))
    except Exception as e:
        return None, f"{STORE_WAIT}: the call failed: {type(e).__name__}: {e}"[:300]
    return {**(a or {}), "model": (meta or {}).get("model"), "at": datetime.now().isoformat(timespec="seconds")}, None


def recompute(bid, n):
    """Steps 6 and 8 again from what is stored (Tesseract's reading, the AI's reading with the look-again's kept
    answers), with today's rules, Satellite records and people's confirmations. No model call, no new whole-page
    Tesseract reading, nothing written. None for a page that can't be checked (not read, or waiting for a label)."""
    with db.connect() as c:
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        if (not p or p["type_status"] not in ("decided", "labelled") or p["fields_all"] is None
                or p["classical_text"] is None):
            return None
        _, ctx = context.ensure(c, classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS,
                                classify.JEV_TYPES)
        sos, confirmed, day = satellite.load(c), satellite.confirmations(c, bid, n), scan_day_of(c, bid)
    dt = p["doc_type"]
    rd = {"classical_text": p["classical_text"], "ocr_words": p["ocr_words"] or []}
    fields, res, zev = page_verdicts(dt, p["fields_all"], rd, p["qr_text"], v1.load(p["upright_path"]), ctx, sos,
                                     confirmed, second=p["second_look"], zoom_cache=p["zoom"], scan_day=day,
                                     ship_to=p.get("ship_to"))
    asks = second_look_asks(dt, res, ctx, asked_of(p["second_look"]),
                            (p["second_look"] or {}).get("bundle_asks") or ()) if res else []
    sl = waiting_for(p["second_look"], asks)
    store = store_pending(dt, res, p.get("ship_to"))
    if store and not asks:                           # the look-again is done; the store question waits (S4)
        sl = {**(sl or {}), "waiting": (p["second_look"] or {}).get("waiting") or STORE_WAIT}
    return {"page": p, "fields": fields, "res": res, "zoom": zev, "second_look": sl, "asks": [c for c, _ in asks],
            "store": store,
            "outcome": outcome(p["type_status"], dt, res, not (sl or {}).get("waiting")),
            "keys": keymod.derive(dt, fields, rd["classical_text"], p["qr_text"], res["header"] if res else None)
            if fields else {}}


def recheck(bid, n):
    """recompute(), stored. Seconds. Used after a person confirms a value, when Satellite's records change, and to
    adopt a rule change. Returns the outcome."""
    r = recompute(bid, n)
    if not r:
        return None
    with db.connect() as c:
        c.execute("""UPDATE staging.page SET fields=%s, keys=%s, outcome=%s, zoom=%s, second_look=%s
                     WHERE batch_id=%s AND page_no=%s""",
                  (Json(r["fields"]), Json(r["keys"]), r["outcome"], Json(r["zoom"]) if r["zoom"] else None,
                   Json(r["second_look"]) if r["second_look"] else None, bid, n))
        verify.store(c, bid, n, r["fields"], r["res"])
    return r["outcome"]


def shadow(bid, pages):
    """What today's rules would change on these pages, from stored data, writing nothing: every ✅ lost or gained
    with its new reason, outcome and key changes, and the look-again questions a re-check would add (with their
    token cost at the measured average). Measure a rule change with it before adopting it."""
    with db.connect() as c:
        per_call = c.execute("""SELECT avg((tokens->>'tokens_in')::int + (tokens->>'tokens_out')::int) AS t
                                  FROM staging.model_call WHERE purpose='second_look' AND ok""").fetchone()["t"]
    lost, gained, moved, asked = [], [], [], {}
    for n in pages:
        r = recompute(bid, n)
        if not r:
            continue
        with db.connect() as c:
            old = {x["field_path"]: x for x in c.execute(
                "SELECT field_path, status, confirmed_by, coalesce(adjudicated_value, vlm_value) AS value "
                "FROM staging.field_check WHERE batch_id=%s AND page_no=%s", (bid, n))}
        new = {x["field_path"]: x for x in verify.rows(r["fields"], r["res"])} if r["res"] else {}
        p, notes = r["page"], []
        for path in sorted(set(old) | set(new)):
            a, b = old.get(path) or {}, new.get(path) or {}
            if a.get("status") == "ok" and b.get("status") != "ok":
                lost.append((n, path))
                notes.append(f"  − {path} {a.get('value')!r} ✅ {a.get('confirmed_by')} → {b.get('reason') or 'gone'}")
            elif b.get("status") == "ok" and a.get("status") != "ok":
                gained.append((n, path))
                notes.append(f"  + {path} {b.get('adjudicated_value') or b.get('vlm_value')!r} ✅ {b.get('confirmed_by')}"
                             + (f" (the AI read {b.get('vlm_value')!r})" if b.get("adjudicated_value") else ""))
        if r["outcome"] != p["outcome"]:
            moved.append((n, p["outcome"], r["outcome"]))
        if r["asks"] or r["store"]:
            asked[n] = r["asks"] + (["ship_to"] if r["store"] else [])
        miss = ((r["res"] or {}).get("so_lines") or {}).get("missing")
        if notes or r["outcome"] != p["outcome"] or r["keys"] != (p["keys"] or {}) or miss:
            print(f"p{n} {p['doc_type']} · outcome {p['outcome']} → {r['outcome']}"
                  + (" · KEYS CHANGE" if r["keys"] != (p["keys"] or {}) else "")
                  + (f" · would ask the AI again: {', '.join(r['asks'])}" if r["asks"] else "")
                  + (f" · SO lines no row matched: {miss}" if miss else ""), flush=True)
            print("\n".join(notes), flush=True)
    tokens = f" (≈{len(asked) * per_call / 1000:.0f}K tokens at the measured {per_call / 1000:.1f}K a call)" \
        if asked and per_call else ""
    print(f"\n✅ lost {len(lost)} (header {sum('header.' in p for _, p in lost)}, lines "
          f"{sum('lines[' in p for _, p in lost)}) · ✅ gained {len(gained)} · outcomes changed: "
          + (", ".join(f"p{n} {a}→{b}" for n, a, b in moved) or "none")
          + f"\nnew look-again calls: {len(asked)}{tokens}: "
          + (" · ".join(f"p{n} {', '.join(v)}" for n, v in asked.items()) or "none"), flush=True)
    return {"lost": lost, "gained": gained, "moved": moved, "asked": asked}


def waiting_for(second, new):
    """The page's second-look evidence given the questions still unasked (`new`): it waits while any is left (keeping
    the first reason it waited for), and stops waiting once there is none."""
    s = dict(second or {})
    if new and not s.get("waiting"):
        s["waiting"] = "new questions for the look-again: " + ", ".join(c for c, _ in new)
    elif not new:
        s.pop("waiting", None)
    return s or None


def look_again_step(doc_type, fields_all, res, rd, qr_text, up, ctx, bid, n, skip=None, second=None):
    """Step 7 and its bookkeeping. Returns (fields_all, verdicts, zoom evidence or None, second-look evidence, looked).
    skip = why the look-again can't run now (a dry run, --no-second-look). Skipped or failed, the page waits for it.
    second = this reading's earlier look-again: what it already asked is never asked again, and its results are kept."""
    asked, requested = asked_of(second), (second or {}).get("bundle_asks") or ()
    if not res or not second_look_asks(doc_type, res, ctx, asked, requested):
        return fields_all, res, None, waiting_for(second, []), True
    if fields_all is None:
        return fields_all, res, None, {**(second or {}), "waiting": "the AI OCR hasn't read this page yet"}, False
    if skip:
        return fields_all, res, None, {**(second or {}), "waiting": skip}, False
    try:
        out, res2, zev, sl = look_again(doc_type, fields_all, res, rd, qr_text, up, ctx, bid, n, asked, requested)
    except Exception as e:
        return fields_all, res, None, {**(second or {}), "waiting": f"the call failed: {type(e).__name__}: {e}"[:300]}, False
    merged = {**(second or {}), **sl, "asked": sorted(asked | set(sl["asked"])),
              "results": {**((second or {}).get("results") or {}), **sl["results"]}}
    merged.pop("waiting", None)
    return out, res2, zev, merged, True


def outcome(type_status, doc_type, res, looked=True):
    """type_status None: the AI OCR hasn't read the page yet. looked=False: the look-again hasn't run yet. Either way
    the page waits for the AI OCR instead of going to a person. Otherwise the page is clear once it has settled what
    it decides itself (page_settled)."""
    if type_status is None:
        return "waiting_ai"
    if type_status not in ("decided", "labelled"):
        return "held_unsure"
    if doc_type == "OTHER":
        return "needs_person"
    if not res:                                      # CONTINUATION, SJ, PEL: no field list to check
        return "clear"
    if page_settled(doc_type, res["header"]):
        return "clear"
    return "needs_person" if looked else "waiting_ai"


# ---------------------------------------------------------------------------------------------- one page

def handle(ticket, v1_reading=None, second_look=True):
    bid, n = ticket["batch_id"], ticket["page_no"]
    run = ticket.get("run", 1)
    if run != v1.current_run(bid):
        return "stale"
    with db.connect() as c:
        prev = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        ctx_v, ctx = context.ensure(c, classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS,
                                    classify.JEV_TYPES)
    fv = context.fields_version(ctx) + "@" + AI_OCR           # a reading belongs to the list AND the model that made it
    fv_saved = fv if v1_reading is None else "dry-run"   # a dry-run reading is never reused as if Gemini made it

    up, prep, up_key, thumb_key, prep_flags = prepare(ticket, prev, bid, n)                        # 1

    title = (prev or {}).get("fp_title")             # the printed-title witness, looked for at most once per page
    x = {"fields_all": None, "extract_status": None, "extract_error": None, "vlm_meta": {}}      # 2
    earlier = None                                   # this reading's earlier look-again, if the reading is reused
    if prev and prev["fields_all"] and prev["fields_version"] == fv:
        x.update(fields_all=prev["fields_all"], extract_status="done", vlm_meta=prev["vlm_meta"] or {})
        earlier = prev["second_look"]
    elif v1_reading is not None:
        x.update(fields_all=v1_reading, extract_status="done", vlm_meta={"read": {"model": "v1 reading (dry run)"}})
    else:
        try:
            fa, meta = ai_call("read_all", bid, n, read_all, v1.png_bytes(up), context.vlm_schema(ctx))
            x.update(fields_all=fa, extract_status="done", vlm_meta={"read": meta})
        except Exception as e:
            x.update(extract_status="failed", extract_error=f"{type(e).__name__}: {e}"[:500])

    if x["fields_all"] is not None:
        normalise_amounts(x["fields_all"], ctx)
    cls = {"type_status": None, "doc_type": None, "type_guess": None, "doc_type_conf": None,       # 3 + 4
           "type_votes": {"reason": "not read by the AI OCR yet"}}         # nothing to classify: waits, not unsure
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
        if title is None and needs_title(jev, qr_sor, prep["layout_score"]):
            title = fp_title(up)
        status, doc_type, guess, reason = decide(jev, qr_sor, prep["layout_score"], title)
        votes = {"jev": jev, "state": state, "qr_sor": qr_sor, "layout": prep["layout_score"], "title": title,
                 "reason": reason,
                 "context_version": ctx_v, "fields_version": fv_saved,
                 "machine": {"status": status, "doc_type": doc_type, "guess": guess, "reason": reason}}
        label = label_of(bid, n)
        if label:                                    # a person's label decides; the machine's answer is kept for grading
            status, doc_type, reason = "labelled", label, f"labelled {label} by a person"
            votes["reason"] = reason
        cls = {"type_status": status, "doc_type": doc_type, "type_guess": guess or doc_type,
               "doc_type_conf": jev.get("confidence"), "type_votes": votes}

    rd, res, zev, sl, looked = {}, None, None, None, True                                        # 5, 6, 7
    ship_to = (prev or {}).get("ship_to")            # the store question's answer (S4), kept across re-runs
    fields_all = x["fields_all"]
    fields = project(fields_all, cls["doc_type"]) if fields_all is not None and cls["doc_type"] in DOCS else {}
    if cls["type_status"] in ("decided", "labelled"):
        dt = cls["doc_type"]
        work = enhance.mask_bands(up, *enhance.measure(up)[2:])
        _, rd = enhance.read(work)
        with db.connect() as c:
            sos, confirmed, day = satellite.load(c), satellite.confirmations(c, bid, n), scan_day_of(c, bid)
        fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                         second=earlier, scan_day=day, ship_to=ship_to)   # never ask again what a person or Satellite settles
        skip = ("dry run: no AI OCR to ask" if v1_reading is not None else
                None if second_look else "skipped in this run to save tokens (--no-second-look)")
        out, res2, zev2, sl, looked = look_again_step(dt, fields_all, res, rd, prep["qr_text"], up, ctx, bid, n,
                                                      skip, earlier)
        if res2 is not res:                          # it looked again: the verdicts once more, with its answers
            fields_all = out
            fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                             second=sl, zoom_cache=zev2, scan_day=day, ship_to=ship_to)
        if looked and store_pending(dt, res, ship_to):   # 7b (S4): a key the store printed on the page can decide
            ship_to, wait = store_step(bid, n, up, skip)
            if ship_to is not None:
                fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                                 second=sl, zoom_cache=zev, scan_day=day, ship_to=ship_to)
            else:
                sl, looked = {**(sl or {}), "waiting": wait}, False
    oc = outcome(cls["type_status"], cls["doc_type"], res, looked)                               # 8
    keys = keymod.derive(cls["doc_type"], fields, rd.get("classical_text"), prep["qr_text"],
                         (res or {}).get("header") if res else None) if fields else {}

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
              ship_to=%(ship_to)s, fp_title=%(fp_title)s,
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
             "ship_to": Json(ship_to) if ship_to is not None else None, "fp_title": title,
             "vv": verify.VERIFY_VERSION if res else None,
             "up": up_key, "thumb": thumb_key, "bid": bid, "n": n, "run": run}).fetchone()
        if saved:
            verify.store(c, bid, n, fields, res)
    if not saved:
        return None
    regroup(bid)
    return v1.tick(bid, run)


def regroup(bid):
    """Phase 6 after every page: grouping is cheap and re-runnable, so a page joins its bundle as soon as its keys are
    resolved. A grouping failure never fails the page."""
    try:
        from grouper import group
        group.run(bid)
    except Exception as e:
        print(f"grouping {bid} failed: {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------------------------------------- trials

def once(bid, pages, use_v1_reading=False, second_look=True):
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
               v1_reading=readings.get(n) if use_v1_reading else None, second_look=second_look)
        with db.connect() as c:
            r = c.execute("""SELECT type_status, doc_type::text AS t, type_votes->>'reason' AS why, outcome,
                                    extract_status, extract_error, second_look->>'waiting' AS waits
                               FROM staging.page WHERE batch_id=%s AND page_no=%s""", (bid, n)).fetchone()
            rows = c.execute("SELECT status, count(*) AS k FROM staging.field_check WHERE batch_id=%s AND page_no=%s "
                             "AND field_path LIKE 'header.%%' GROUP BY 1", (bid, n)).fetchall()
        tally = " ".join(f"{x['status']} {x['k']}" for x in rows)
        print(f"page {n}: {r['type_status'] or 'not read'} {r['t'] or ''} ({r['why']}) · outcome {r['outcome']} · main fields: "
              f"{tally or '-'} · {time.time() - t0:.0f} s" + (f" · {r['extract_error']}" if r["extract_error"] else "")
              + (f" · look-again waits: {r['waits']}" if r["waits"] else ""))


def waiting(bid, pages=None):
    """Pages waiting for the AI OCR: readings that failed first (nothing else can run on them), then look-agains."""
    with db.connect() as c:
        return [r["page_no"] for r in c.execute("""
            SELECT page_no FROM staging.page WHERE batch_id=%s AND (second_look ? 'waiting' OR extract_status='failed')
               AND (%s::int[] IS NULL OR page_no = ANY(%s::int[]))
             ORDER BY extract_status = 'failed' DESC, page_no""", (bid, pages, pages))]


def seconds_until(text):
    """'try again in 1h2m3.5s' → seconds, or None."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", text or "")
    return m and int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))


def again(bid, pages=None, patience=3600):
    """Run what waits for the AI OCR, one page at a time. At the provider's daily limit, wait for it to refill when it
    says it will within `patience` seconds (Groq refills its daily tokens continuously), else stop."""
    todo = waiting(bid, pages)
    print(f"waiting for the AI OCR: {todo or 'nothing'}", flush=True)
    while todo:
        n = todo.pop(0)
        once(bid, [n])
        if not waiting(bid, [n]):
            continue
        with db.connect() as c:
            r = c.execute("SELECT second_look, extract_error FROM staging.page WHERE batch_id=%s AND page_no=%s",
                          (bid, n)).fetchone()
        why = f"{(r['second_look'] or {}).get('waiting') or ''} {r['extract_error'] or ''}"
        if "OutOfBudget" in why:
            print("stopped: vlm-first's own daily cap is used up", flush=True)
            return
        if "DailyLimit" in why:
            wait = seconds_until(why)
            if wait is None or wait > patience:
                print(f"stopped: the provider's daily limit ({why.strip()[:160]})", flush=True)
                return
            print(f"page {n}: the daily limit refills in {wait / 60:.0f} min; waiting", flush=True)
            time.sleep(wait + 30)
            todo.insert(0, n)                        # the same page again, first


if __name__ == "__main__":
    from worker.clone import pages_arg
    if sys.argv[1] == "once":
        once(sys.argv[2], pages_arg(sys.argv[3]), "--v1-reading" in sys.argv, "--no-second-look" not in sys.argv)
    elif sys.argv[1] == "again":
        again(sys.argv[2], pages_arg(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif sys.argv[1] == "shadow":
        shadow(sys.argv[2], pages_arg(sys.argv[3]))
