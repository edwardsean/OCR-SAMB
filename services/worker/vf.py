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
  a look-again not run stores second_look = {"waiting": why}.

  On the queue (worker/main.py, several vf-workers): a page whose call the daily limit stopped is parked on
  q.pages.wait and comes back by itself (`after`); a failed call is tried MAX_TRIES times. After every page the
  worker wakes vf-grouper (q.group), which groups the batch, checks the bundles and sends back to q.pages the pages a
  bundle asked to look again (grouper/serve.py). `again` does the same by hand.

  The AI OCR is VF_AI_OCR: "gemini" (common/models/vlm.py) or provider:model through any OpenAI-compatible vision
  model (common/models/openai_vlm.py), e.g. groq:qwen/qwen3.8-27b. A reading is tied to the model that made it.

  python -m worker.vf once <batch> <pages> [--v1-reading] [--no-second-look]   run pages now, without the queue
      --v1-reading      dry run: instead of calling Gemini, use v1's stored reading renamed to the combined list
                        (no boxes, no second look). For trying the flow on days the Gemini quota is used up.
      --no-second-look  read and check only; the pages wait for their look-again
  python -m worker.vf again <batch> [<pages>]   send what waits for the AI OCR back to the page workers (q.pages):
                        look-agains not run yet and failed reads. The reading and Jev's answer are reused. Usually not
                        needed: a bundle's question reaches a worker through vf-grouper, and a call the daily limit
                        stopped comes back from the waiting room (q.pages.wait) by itself.
  python -m worker.vf shadow <batch> <pages>    what today's rules would change (✅ lost or gained, outcomes, keys,
                        new look-again questions), from stored data. Writes nothing, calls no model: measure a rule
                        change before adopting it; adopt with `python -m grouper.group <batch> --recheck`.
"""
import json
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

from common import config, context, db, gates, satellite, settings, trace, transcript, verify, wiki
from common import keys as keymod
from common.fields import DECIDES, DOCS, TYPE_MAP, decides, lift, project
from common.models import openai_vlm, vlm
from worker import boxes as pickboxes, classify, enhance, layout, trace_io, zoom
from worker import main as v1

PREP_VERSION = 1
JEV_DECIDE = 0.85
PREFIX = config.STORAGE_PREFIX
AI_OCR = config.VF_AI_OCR                    # gemini, or provider:model
AI_MAP = config.VF_AI_MAP                    # the text model that maps a transcript (provider:model)
DAILY_CAP = config.VF_AI_OCR_DAILY_CAP
MAP_CAP = config.VF_AI_MAP_DAILY_CAP
CAPS = {AI_OCR: DAILY_CAP, **({AI_MAP: MAP_CAP} if AI_MAP != AI_OCR else {})}   # per model: each has its own quota
READER = config.VF_READER                    # two_step: the mentor's transcribe, then map (read_then_map)


@settings.on_change
def _models_changed():
    """The models saved on the Teknis screen (common/settings.py) replace .env's: a page read after this uses them,
    and its versions name them, so a page another model read is read again."""
    global AI_OCR, AI_MAP, CAPS
    AI_OCR, AI_MAP = config.VF_AI_OCR, config.VF_AI_MAP
    CAPS = {AI_OCR: DAILY_CAP, **({AI_MAP: MAP_CAP} if AI_MAP != AI_OCR else {})}

MAP_TWICE = config.VF_MAP_TWICE              # map each transcript twice and merge (transcript.merge)
STARTING = {"read_all", "transcribe"}      # a page's first call; everything else finishes a page already started
TEXT_PURPOSES = {"map", "map_b"}           # calls that go to the text model (map_b: pass B, with knowledge)
REQUIRED = {code: [f["name"] for f in d["header"] if f["source"] == "6.1"] for code, d in DOCS.items()}
NO_TIME_WAIT = 3600                                                  # seconds, after a daily-limit refusal naming no time
PREP_COLS = ("rotation", "osd_conf", "skew_angle", "black_ratio", "dark_band_ratio", "speckle_ratio", "qr_text",
             "layout_score")


class OutOfBudget(Exception):
    pass


class NotSet(Exception):
    """A model the page needs isn't set on the Teknis screen "Model & kunci API": no call is made, the page waits."""


def not_set():
    """Why no model call can be made because something isn't set on the Teknis screen (a page needs the vision model,
    the text model and Jev), else None."""
    m = settings.missing()
    return f"NotSet: {', '.join(m)} not set: set {'it' if len(m) == 1 else 'them'} on {settings.WHERE}" if m else None


# ---------------------------------------------------------------------------------------------- bookkeeping

def pacific_day():
    """The day the providers' daily quotas count in (Gemini resets at midnight Pacific; VF_QUOTA_TZ for another)."""
    return datetime.now(ZoneInfo(config.VF_QUOTA_TZ)).date()


def ledger(provider, purpose, bid, n, ok, meta=None, error=None):
    meta = meta or {}
    with db.connect(autocommit=True) as c:
        call_id = c.execute("""INSERT INTO staging.model_call (pacific_day, provider, model, purpose, batch_id, page_no,
                                                               ok, ms, tokens, error)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (pacific_day(), provider, meta.get("model"), purpose, bid, n, ok, meta.get("ms"),
                             Json({k: meta.get(k) for k in ("tokens_in", "tokens_out", "tokens_thinking")}),
                             error)).fetchone()["id"]
    trace.save_payload(call_id)                      # its exact request and response (Jejak)


def spec_for(purpose):
    """The model a call goes to: the text model maps, the AI OCR (vision) does everything else."""
    return AI_MAP if purpose in TEXT_PURPOSES else AI_OCR


def cap_of(spec):
    return CAPS.get(spec, DAILY_CAP)


def finish_reserve(spec):
    """Calls kept back from a page's first call, so pages already started can still finish (their look-again, the
    store question): an unfinished page holds its whole bundle."""
    return max(3, cap_of(spec) // 10)


def used_today(c, spec):
    return c.execute("SELECT count(*) AS n FROM staging.model_call WHERE model=%s AND pacific_day=%s",
                     (spec, pacific_day())).fetchone()["n"]


def ai_left(spec=None):
    spec = spec or AI_OCR
    with db.connect() as c:
        return cap_of(spec) - used_today(c, spec)


def reserve(purpose, bid, n, spec=None):
    """Count and claim one call of today's cap for this MODEL, in one transaction under a lock per model: page workers
    running side by side can never spend the same last call, and each model's quota is its own (a Model Studio model
    whose free quota ended never blocks another). The claim is a ledger row ('pending') that the answer completes.
    A page's first call stops finish_reserve() short of the cap. Raises OutOfBudget."""
    spec = spec or spec_for(purpose)
    cap = cap_of(spec)
    with db.connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"ai-budget:{spec}",))
        used = used_today(c, spec)
        if used >= cap or (purpose in STARTING and used >= cap - finish_reserve(spec)):
            raise OutOfBudget(f"vlm-first's daily cap for {spec} ({cap}) is used up"
                              + ("" if used >= cap else f" for new pages ({finish_reserve(spec)} kept to finish pages)"))
        return c.execute("""INSERT INTO staging.model_call (pacific_day, provider, model, purpose, batch_id, page_no,
                                                            ok, error)
                            VALUES (%s, %s, %s, %s, %s, %s, false, 'pending') RETURNING id""",
                         (pacific_day(), spec.partition(":")[0], spec, purpose, bid, n)).fetchone()["id"]


def settle_call(call_id, ok, meta=None, error=None):
    """The claim's answer. The row keeps the model it was reserved for (the ledger is counted per model)."""
    meta = meta or {}
    with db.connect() as c:
        c.execute("UPDATE staging.model_call SET ok=%s, ms=%s, tokens=%s, error=%s WHERE id=%s",
                  (ok, meta.get("ms"), Json({k: meta.get(k) for k in ("tokens_in", "tokens_out", "tokens_thinking")}),
                   error, call_id))
    trace.save_payload(call_id)                      # its exact request and response (Jejak)


def refused_for(spec=None):
    """When this model's last answer was a daily-limit refusal: the seconds of its wait that haven't passed, else 0.
    Asking before then only fails, and each failure counts against the cap: 166 uploaded pages would use it up.
    A refusal that names no time counts as NO_TIME_WAIT."""
    spec = spec or AI_OCR
    with db.connect() as c:
        r = c.execute("""SELECT extract(epoch FROM now() - at) AS ago, ok, error FROM staging.model_call
                          WHERE model=%s AND (ok OR error LIKE 'DailyLimit%%') ORDER BY at DESC LIMIT 1""",
                      (spec,)).fetchone()
    if not r or r["ok"]:
        return 0.0
    wait = seconds_until(r["error"]) or NO_TIME_WAIT    # a refusal with no time (Model Studio's free quota): an hour
    return max(0.0, wait - float(r["ago"]))


def ai_call(purpose, bid, n, call, *args):
    """One model call, counted in the ledger and stopped at vlm-first's daily cap for its model (reserved before the
    call: several page workers share the cap). While the model's last refusal still says to wait, the page waits
    without a call: its worker parks it on q.pages.wait (park)."""
    spec = spec_for(purpose)
    unset = not_set()
    if unset:
        raise NotSet(unset.removeprefix("NotSet: "))
    wait = refused_for(spec)
    if wait:
        raise openai_vlm.DailyLimit(f"{spec}: daily limit reached (its last refusal), "
                                    f"try again in {int(wait // 60)}m{wait % 60:.1f}s")
    call_id = reserve(purpose, bid, n, spec)
    try:
        out, meta = call(*args)
    except Exception as e:
        settle_call(call_id, False, None, f"{type(e).__name__}: {e}"[:300])
        raise
    settle_call(call_id, True, meta)
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
    """(type_status, doc_type, guess, reason). The classification model (`jev`: Jev until 2026-10-07) decides at
    >= 0.85; the image vetoes a wrong FP: an FP also needs the SOR QR code, the FP layout or the printed title (Jev said
    FP 0.88 on SAMB's handwritten SALES ORDER form; qwen-flash 0.97 on a description of it)."""
    jc, conf = jev.get("choice"), jev.get("confidence") or 0.0
    if not jc:
        return "unsure", None, None, "the classification model didn't answer"
    fp_witness = qr_sor or (layout_score or 0) >= classify.LAYOUT_FP or bool(title)
    if conf < JEV_DECIDE:
        return "unsure", None, jc, f"the classification model isn't sure enough ({jc} {conf:.2f} < {JEV_DECIDE})"
    if qr_sor and jc != "FP":
        return "unsure", None, jc, f"the SOR QR code says FP, the classification model says {jc}"
    if jc == "FP" and not fp_witness:
        return "unsure", None, "FP", "an FP needs the QR code, the FP layout or the printed title as well"
    return "decided", jc, jc, f"classified {jc} {conf:.2f}" + (" + QR" if qr_sor else "" if jc != "FP" else " + FP layout"
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
        _, ctx = context.ensure(c)
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
    """One page, by one worker at a time: a second ticket for the same page (a label, a bundle's question and a retry
    can each send one) waits for the first, then finds the page read and does nothing. Grouping is the caller's:
    the queue worker wakes vf-grouper, `once` regroups itself."""
    bid, n = ticket["batch_id"], ticket["page_no"]
    run = ticket.get("run", 1)
    if run != v1.current_run(bid):
        trace.event("page", "skip", batch=bid, page=n, why="a ticket from an earlier run")
        return "stale"
    with db.connect(autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(hashtext(%s), %s)", (f"page:{bid}", n))
        try:
            p = lock.execute("SELECT status FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
            if p and p["status"] == "read":
                trace.event("page", "skip", batch=bid, page=n, why="read meanwhile by another ticket")
                return "done"                         # another ticket's worker finished it meanwhile
            trace.cut_off(bid, n)                     # spans a stopped worker left 'running'
            with trace.span("page", batch=bid, page=n, run=run, tries=ticket.get("tries"),
                            back_from_waiting=bool(ticket.get("parked")) or None,
                            **{"in": {"message taken off the queue (q.pages)": ticket}}):
                return _handle(ticket, bid, n, run, v1_reading, second_look)
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (f"page:{bid}", n))


def _handle(ticket, bid, n, run, v1_reading, second_look):
    with db.connect() as c:
        prev = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        ctx_v, ctx = context.ensure(c)
    fv = context.fields_version(ctx) + "@" + AI_OCR           # a reading belongs to the list AND the model that made it
    if READER == "two_step":                                   # … and, read then mapped, to the transcript and mapper
        fv = two_step_versions(ctx)[2]
    fv_saved = fv if v1_reading is None else "dry-run"   # a dry-run reading is never reused as if Gemini made it

    trace.stage("prepare")
    up, prep, up_key, thumb_key, prep_flags = prepare(ticket, prev, bid, n)                        # 1
    trace.stage_note(**trace_io.prepare(ticket, prep, prep_flags, up_key))

    title = (prev or {}).get("fp_title")             # the printed-title witness, looked for at most once per page
    x = {"fields_all": None, "extract_status": None, "extract_error": None, "vlm_meta": {}}      # 2
    earlier = None                                   # this reading's earlier look-again, if the reading is reused
    two = {"notes": (prev or {}).get("notes"), "mapping": (prev or {}).get("mapping"),
           "blocks": (prev or {}).get("transcript"), "tv": (prev or {}).get("transcript_version")}
    trace.stage("read")
    unset = None if v1_reading is not None else not_set()
    if unset:                                        # a model isn't set on the Teknis screen: no call, the page waits
        x.update(extract_status="failed", extract_error=unset)
    elif prev and prev["fields_all"] and prev["fields_version"] == fv:
        x.update(fields_all=prev["fields_all"], extract_status="done", vlm_meta=prev["vlm_meta"] or {})
        earlier = prev["second_look"]
        trace.stage_note(reused="the page's own reading")
    elif v1_reading is not None:
        x.update(fields_all=v1_reading, extract_status="done", vlm_meta={"read": {"model": "v1 reading (dry run)"}})
    elif READER == "two_step":
        try:
            fa, meta, blocks, notes, mapping = read_then_map(bid, n, up, ctx, prev)
            x.update(fields_all=fa, extract_status="done", vlm_meta={"read": meta})
            two.update(notes=notes, mapping=mapping, blocks=blocks, tv=two_step_versions(ctx)[0])
            earlier = carry_over((prev or {}).get("second_look"), fa)
        except Exception as e:
            x.update(extract_status="failed", extract_error=f"{type(e).__name__}: {e}"[:500])
    else:
        try:
            fa, meta = ai_call("read_all", bid, n, read_all, v1.png_bytes(up), context.vlm_schema(ctx))
            x.update(fields_all=fa, extract_status="done", vlm_meta={"read": meta})
        except Exception as e:
            x.update(extract_status="failed", extract_error=f"{type(e).__name__}: {e}"[:500])

    if x["fields_all"] is not None:
        trace.stage_note(**trace_io.read(two["blocks"], two["mapping"], x["fields_all"], two["notes"],
                                         len(ctx.get("fields") or {})))
    elif x["extract_error"]:
        trace.stage_note(**{"out": {"error": x["extract_error"]}})
    trace.stage("classify")
    if x["fields_all"] is not None:
        normalise_amounts(x["fields_all"], ctx)
    cls = {"type_status": None, "doc_type": None, "type_guess": None, "doc_type_conf": None,       # 3 + 4
           "type_votes": {"reason": "not read by the AI OCR yet"}}         # nothing to classify: waits, not unsure
    if x["fields_all"] is not None:
        old = (prev or {}).get("type_votes") or {}
        state = jev_state(wiki.pass_a(x["fields_all"], two["mapping"]), ctx)   # Jev: pass A only, never knowledge
        if old.get("context_version") == ctx_v and old.get("fields_version") == fv_saved and old.get("jev", {}).get("choice"):
            jev = old["jev"]
        else:
            t0 = time.time()
            jev = classify.jev_ask(state, context.jev_question(ctx))
            provider, meta = classify.ledger_meta(jev, int((time.time() - t0) * 1000))
            ledger(provider, "classify", bid, n, bool(jev.get("choice")), meta, jev.get("error") or jev.get("skipped"))
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
        trace.stage_note(**trace_io.classify(state, jev, votes["machine"], label, qr_sor, prep["layout_score"], title))

    kwait = None                                     # 4b: pass B, the text model again with what people taught
    if (READER == "two_step" and x["fields_all"] is not None and cls["type_status"] in ("decided", "labelled")
            and cls["doc_type"] in DOCS):
        from worker import learn
        trace.stage("knowledge")
        try:
            fa_k, map_k, _ = learn.step(bid, n, cls["doc_type"], x["fields_all"], two["mapping"], prep["qr_text"],
                                        ctx, up)
            trace.stage_note(**trace_io.knowledge((map_k or {}).get("pass_b"), x["fields_all"], fa_k))
            earlier = wiki.forget(earlier, wiki.changed(x["fields_all"], fa_k, sorted((set(fa_k) | set(x["fields_all"])) - {"lines"})))
            x["fields_all"], two["mapping"] = fa_k, map_k
        except Exception as e:                       # pass A's reading stands meanwhile; the page waits for the call
            kwait = f"the call failed: pass B: {type(e).__name__}: {e}"[:300]
            x["fields_all"], two["mapping"] = wiki.undo(x["fields_all"], two["mapping"])

    rd, res, zev, sl, looked, pick = {}, None, None, None, True, None                            # 5, 6, 7
    ship_to = (prev or {}).get("ship_to")            # the store question's answer (S4), kept across re-runs
    fields_all = x["fields_all"]
    if fields_all is not None and cls["doc_type"] in DOCS:
        trace.stage("project")                       # code, not AI: the combined list → the type's own fields
    fields = project(fields_all, cls["doc_type"]) if fields_all is not None and cls["doc_type"] in DOCS else {}
    if fields:
        trace.stage_note(**trace_io.project(cls["doc_type"], fields))
    if cls["type_status"] in ("decided", "labelled"):
        dt = cls["doc_type"]
        trace.stage("tesseract")
        work = enhance.mask_bands(up, *enhance.measure(up)[2:])
        _, rd = enhance.read(work)
        trace.stage_note(**trace_io.tesseract(rd))
        if READER == "two_step" and two["mapping"] and fields_all:   # boxes on Tesseract's own words, now it has read
            transcript.snap_boxes(fields_all, two["mapping"], rd.get("ocr_words"), up.shape)
        if two["blocks"] and two["tv"]:              # what a person can click on the page viewer (worker/boxes.py)
            pick = pick_boxes(up, two["blocks"], two["tv"], rd.get("ocr_words"))
        with db.connect() as c:
            sos, confirmed, day = satellite.load(c), satellite.confirmations(c, bid, n), scan_day_of(c, bid)
        trace.stage("check")
        fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                         second=earlier, scan_day=day, ship_to=ship_to)   # never ask again what a person or Satellite settles
        trace.stage_note(**trace_io.check(res))
        skip = ("dry run: no AI OCR to ask" if v1_reading is not None else
                None if second_look else "skipped in this run to save tokens (--no-second-look)")
        trace.stage("look_again")
        out, res2, zev2, sl, looked = look_again_step(dt, fields_all, res, rd, prep["qr_text"], up, ctx, bid, n,
                                                      skip, earlier)
        trace.stage_note(**trace_io.look(sl))
        if res2 is not res:                          # it looked again: the verdicts once more, with its answers
            fields_all = out
            fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                             second=sl, zoom_cache=zev2, scan_day=day, ship_to=ship_to)
        if looked and store_pending(dt, res, ship_to):   # 7b (S4): a key the store printed on the page can decide
            trace.stage("store")
            ship_to, wait = store_step(bid, n, up, skip)
            trace.stage_note(**{"out": {"store printed on the page": ship_to, "waiting": wait}})
            if ship_to is not None:
                fields, res, zev = page_verdicts(dt, fields_all, rd, prep["qr_text"], up, ctx, sos, confirmed,
                                                 second=sl, zoom_cache=zev, scan_day=day, ship_to=ship_to)
            else:
                sl, looked = {**(sl or {}), "waiting": wait}, False
    if kwait:
        sl, looked = {**(sl or {}), "waiting": kwait}, False
    trace.stage("save")
    oc = outcome(cls["type_status"], cls["doc_type"], res, looked)                               # 8
    trace_outcome(oc, cls, x, sl)
    keys = keymod.derive(cls["doc_type"], fields, rd.get("classical_text"), prep["qr_text"],
                         (res or {}).get("header") if res else None) if fields else {}
    trace.stage_note(**trace_io.save(oc, keys, cls))

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
              ship_to=%(ship_to)s, fp_title=%(fp_title)s, notes=%(notes)s, mapping=%(mapping)s,
              pick=COALESCE(%(pick)s::jsonb, pick), error=NULL, read_at=now()
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
             "notes": Json(two["notes"]) if two["notes"] is not None else None,
             "mapping": Json(two["mapping"]) if two["mapping"] is not None else None,
             "pick": Json(pick) if pick else None, "vv": verify.VERIFY_VERSION if res else None,
             "up": up_key, "thumb": thumb_key, "bid": bid, "n": n, "run": run}).fetchone()
        if saved:
            verify.store(c, bid, n, fields, res)
    if not saved:
        return None
    return v1.tick(bid, run)


def pick_boxes(up, blocks, tv, words):
    """The page viewer's clickable boxes (worker/boxes.py), for this transcript. They only help a person: a failure
    here never fails the page (the viewer then pairs the copy with Tesseract's own reading)."""
    try:
        return {"v": pickboxes.version(tv), **pickboxes.make(up, blocks, words)}
    except Exception as e:
        print(f"boxes for the page viewer failed: {type(e).__name__}: {e}", flush=True)
        return None


def regroup(bid):
    """Phase 6 after a page, for runs outside the queue (`once`): grouping is cheap and re-runnable, so a page joins its
    bundle as soon as its keys are resolved. A grouping failure never fails the page. (On the queue, vf-grouper
    does this: grouper/serve.py.)"""
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
        with psycopg.connect(config.required("MAIN_DATABASE_URL"), row_factory=dict_row) as m:
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
        regroup(bid)
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
    """Pages waiting for the AI OCR: readings that failed first (nothing else can run on them), then look-agains.
    Never a page of an order already sent to Satellite: Satellite keeps its values, and reading it again would leave
    two truths (the user, 2026-10-08; the same rule as a person's retry, api/stuck.py)."""
    with db.connect() as c:
        return [r["page_no"] for r in c.execute("""
            SELECT p.page_no FROM staging.page p
             WHERE p.batch_id=%s AND (p.second_look ? 'waiting' OR p.extract_status='failed')
               AND (%s::int[] IS NULL OR p.page_no = ANY(%s::int[]))
               AND NOT EXISTS (SELECT 1 FROM staging.document d JOIN staging.bundle_document bd ON bd.document_id = d.id
                                 JOIN staging.bundle b ON b.id = bd.bundle_id
                                WHERE d.batch_id = p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                                  AND b.status = 'published')
             ORDER BY p.extract_status = 'failed' DESC, p.page_no""", (bid, pages, pages))]


def seconds_until(text):
    """'try again in 1h2m3.5s' → seconds, or None."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", text or "")
    return m and int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))


# ---------------------------------------------------------------------------------------------- read, then map
# The mentor's two steps (2026-09-29; common/transcript.py): the AI OCR copies the whole page, a text model maps the
# copy onto the field list and collects notes. Stage 1a: a TRIAL only (staging.reading_trial), measured beside the
# page's own reading before anything adopts it.

def transcribe_png(png):
    if AI_OCR == "gemini":
        raise NotImplementedError("read-then-map needs an OpenAI-compatible model: VF_AI_OCR=provider:model")
    return openai_vlm.transcribe(png, AI_OCR)


def map_blocks(blocks, schema, hints=None):
    heads, cols = openai_vlm._field_list(schema)
    return openai_vlm.map_text(transcript.map_prompt(blocks, heads, cols, hints), AI_MAP)


def mapped(raw, blocks, schema, words, img, ctx):
    """The text model's answer → (fields_all, mapping, notes), grounded, boxes snapped to print (or tightened to the
    value's ink when Tesseract didn't read it), amounts normalised."""
    shape = img.shape
    props = schema["properties"]
    names = [k for k in props if k != "lines"]
    cols = [c for c in props.get("lines", {}).get("items", {}).get("properties", {}) if c != "row_text"]
    kinds = {n: ((ctx.get("fields") or {}).get(n) or {}).get("kind") for n in names}
    fa, mapping, notes = transcript.to_fields_all(raw, blocks, names, cols, kinds)
    transcript.snap_boxes(fa, mapping, words, shape)
    h, w = shape[:2]
    for name, where in mapping.get("fields", {}).items():       # still a whole block or cell: its ink, no borders
        f = fa.get(name)
        if f and f.get("box") and where.get("box_by") == "block":
            r = zoom.ink_rect(img, zoom.by_ai_box(f["box"], shape))
            f["box"] = [int(r[1] * 1000 / h), int(r[0] * 1000 / w), int(r[3] * 1000 / h), int(r[2] * 1000 / w)]
            where["box_by"] = "ink"
    normalise_amounts(fa, ctx)
    return fa, mapping, notes


def flips(a, b):
    """Where two mappings of one transcript disagree: the text model's own noise (a gate's gain must beat it)."""
    out = [k for k in set(a) | set(b) if k != "lines"
           and verify.flat((a.get(k) or {}).get("value")) != verify.flat((b.get(k) or {}).get("value"))]
    ra, rb = a.get("lines") or [], b.get("lines") or []
    cells = sum(1 for x, y in zip(ra, rb) for c in set(x) | set(y)
                if c != "row_text" and verify.flat(x.get(c)) != verify.flat(y.get(c))) + abs(len(ra) - len(rb))
    return {"fields": sorted(out), "cells": cells}


def first_reading(p):
    """The page's own reading as the AI OCR first gave it: kept look-again answers put back to their first answer
    (the trial has no look-again yet, so it is compared with the first reading, not the finished one)."""
    fa = json.loads(json.dumps(p["fields_all"] or {}))
    for c, r in ((p.get("second_look") or {}).get("results") or {}).items():
        if r.get("kept_second") and c in fa:
            fa[c] = r.get("first")
    return fa


def trial(bid, pages, variant=None, twice=True):
    """Read then map these pages into staging.reading_trial, beside their own reading; the page itself is untouched.
    A transcript already made for a page (any variant, same transcript version) is reused, so comparing two text
    models costs one image read. twice: map a second time to measure the text model's flip rate."""
    variant = variant or f"map@{AI_MAP}"
    with db.connect() as c:
        _, ctx = context.ensure(c)
    schema = context.vlm_schema(ctx)
    tv = transcript.transcript_version(AI_OCR, PREP_VERSION)
    mv = transcript.map_version(context.fields_version(ctx), tv, AI_MAP)
    done = []
    for n in pages:
        with db.connect() as c:
            p = c.execute("SELECT upright_path, ocr_words FROM staging.page WHERE batch_id=%s AND page_no=%s",
                          (bid, n)).fetchone()
            old = c.execute("""SELECT transcript, meta FROM staging.reading_trial WHERE batch_id=%s AND page_no=%s
                                 AND versions->>'transcript' = %s AND transcript IS NOT NULL LIMIT 1""",
                            (bid, n, tv)).fetchone()
            mine = c.execute("""SELECT mapping->'raw' AS raw, versions->>'map' AS map, meta->'map' AS meta
                                  FROM staging.reading_trial WHERE batch_id=%s AND page_no=%s AND variant=%s
                                   AND mapping ? 'raw'""", (bid, n, variant)).fetchone()
        old_raw = dict(mine) if mine else None
        if not p or not p["upright_path"]:
            print(f"page {n}: not prepared yet"); continue
        up, meta, err = v1.load(p["upright_path"]), {}, None
        blocks = fa = mapping = notes = None
        try:
            if old:
                blocks, meta["transcribe"] = old["transcript"], {"reused": True}
            else:
                raw_blocks, meta["transcribe"] = ai_call("transcribe", bid, n, transcribe_png, v1.png_bytes(up))
                blocks = transcript.normalise_blocks(raw_blocks)
            if old_raw and old_raw.get("map") == mv:        # this mapping already made: the first run, no call
                raw, meta["map"] = old_raw["raw"], old_raw.get("meta") or {"reused": True}
            else:
                raw, meta["map"] = ai_call("map", bid, n, map_blocks, blocks, schema)
            first = mapped(raw, blocks, schema, p["ocr_words"], up, ctx)
            fa, mapping, notes = first
            if twice:                                       # a second run, merged: agreements and single finds kept,
                raw2, meta["map2"] = ai_call("map", bid, n, map_blocks, blocks, schema)   # disagreements left empty
                second = mapped(raw2, blocks, schema, p["ocr_words"], up, ctx)
                fa, mapping, notes = transcript.merge(first, second)
                mapping["flips"] = flips(first[0], second[0])
                mapping["raw2"] = raw2
            mapping["raw"] = raw                  # the text model's own answers: a grounding fix re-runs with no call
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:500]
        with db.connect() as c:
            c.execute("""INSERT INTO staging.reading_trial (batch_id, page_no, variant, transcript, fields_all, notes,
                                                            mapping, versions, meta, error)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (batch_id, page_no, variant) DO UPDATE SET transcript=EXCLUDED.transcript,
                           fields_all=EXCLUDED.fields_all, notes=EXCLUDED.notes, mapping=EXCLUDED.mapping,
                           versions=EXCLUDED.versions, meta=EXCLUDED.meta, error=EXCLUDED.error, created_at=now()""",
                      (bid, n, variant, Json(blocks) if blocks is not None else None,
                       Json(fa) if fa is not None else None, Json(notes) if notes is not None else None,
                       Json(mapping) if mapping is not None else None, Json({"transcript": tv, "map": mv}),
                       Json(meta), err))
        filled = sum(1 for k, f in (fa or {}).items() if k != "lines" and f)
        print(f"page {n}: " + (f"error {err}" if err else
              f"{len(blocks)} blocks, {filled} fields, {len((fa or {}).get('lines') or [])} rows, {len(notes)} notes, "
              f"{len(mapping['dropped'])} dropped, flips {mapping.get('flips')}"), flush=True)
        done.append(n)
    return done


def reground(bid, variant=None):
    """The trial's stored text-model answers (mapping.raw) grounded again with today's code: a grounding fix measured
    with no model call. Pages whose raw answer wasn't kept are left as they are."""
    with db.connect() as c:
        _, ctx = context.ensure(c)
        rows = c.execute("""SELECT t.page_no, t.variant, t.transcript, t.mapping, p.upright_path, p.ocr_words
                              FROM staging.reading_trial t JOIN staging.page p USING (batch_id, page_no)
                             WHERE t.batch_id=%s AND (%s::text IS NULL OR t.variant=%s)
                               AND t.mapping ? 'raw'""", (bid, variant, variant)).fetchall()
    schema = context.vlm_schema(ctx)
    for r in rows:
        raw, raw2 = r["mapping"]["raw"], r["mapping"].get("raw2")
        img = v1.load(r["upright_path"])
        first = mapped(raw, r["transcript"], schema, r["ocr_words"], img, ctx)
        fa, mapping, notes = first
        if raw2:
            second = mapped(raw2, r["transcript"], schema, r["ocr_words"], img, ctx)
            fa, mapping, notes = transcript.merge(first, second)
            mapping["flips"], mapping["raw2"] = flips(first[0], second[0]), raw2
        mapping["raw"] = raw
        with db.connect() as c:
            c.execute("UPDATE staging.reading_trial SET fields_all=%s, mapping=%s, notes=%s WHERE batch_id=%s "
                      "AND page_no=%s AND variant=%s", (Json(fa), Json(mapping), Json(notes), bid, r["page_no"],
                                                       r["variant"]))
    return len(rows)


def two_step_versions(ctx):
    """(transcript version, the mapping's version as the trial records it, the page's fields_version)."""
    tv = transcript.transcript_version(AI_OCR, PREP_VERSION)
    mv = transcript.map_version(context.fields_version(ctx), tv, AI_MAP)
    return tv, mv, mv + ("#x2" if MAP_TWICE else "")


def read_then_map(bid, n, up, ctx, prev):
    """Step 2, the mentor's way: the AI OCR's copy of the page (reused from the page or a trial when the same image,
    model and prompt made it; else transcribed and saved at once), then the text model's mapping (reused from a trial
    with the same version; else mapped, twice and merged). Returns (fields_all, meta, transcript, notes, mapping).
    Tesseract hasn't read the page yet: boxes are tightened to the value's ink now, to its words after step 5."""
    tv, mv, _ = two_step_versions(ctx)
    schema, meta = context.vlm_schema(ctx), {}
    with db.connect() as c:
        tr = c.execute("""SELECT transcript, mapping FROM staging.reading_trial WHERE batch_id=%s AND page_no=%s
                            AND versions->>'transcript' = %s AND transcript IS NOT NULL
                          ORDER BY (versions->>'map' = %s) DESC LIMIT 1""", (bid, n, tv, mv)).fetchone()
        same_map = c.execute("""SELECT 1 FROM staging.reading_trial WHERE batch_id=%s AND page_no=%s
                                  AND versions->>'map' = %s AND mapping ? 'raw'""", (bid, n, mv)).fetchone()
    if prev and prev.get("transcript") and prev.get("transcript_version") == tv:
        blocks, meta["transcribe"] = prev["transcript"], {"reused": "page"}
    elif tr:
        blocks, meta["transcribe"] = tr["transcript"], {"reused": "trial"}
    else:
        raw_blocks, meta["transcribe"] = ai_call("transcribe", bid, n, transcribe_png, v1.png_bytes(up))
        blocks = transcript.normalise_blocks(raw_blocks)
    with db.connect() as c:                          # saved at once: a paid transcription survives a failed mapping
        c.execute("""UPDATE staging.page SET transcript=%s, transcript_version=%s, transcript_status='done'
                     WHERE batch_id=%s AND page_no=%s""", (Json(blocks), tv, bid, n))
    old = (tr["mapping"] if tr and same_map else None) or {}
    if not old and ((prev or {}).get("mapping") or {}).get("version") == mv:
        old = prev["mapping"]                        # the page's own mapping, same version: no call
    raw = old.get("raw")
    if raw is None:
        raw, meta["map"] = ai_call("map", bid, n, map_blocks, blocks, schema)
    first = mapped(raw, blocks, schema, None, up, ctx)
    fa, mapping, notes = first
    if MAP_TWICE:
        raw2 = old.get("raw2")
        if raw2 is None:
            raw2, meta["map2"] = ai_call("map", bid, n, map_blocks, blocks, schema)
        second = mapped(raw2, blocks, schema, None, up, ctx)
        fa, mapping, notes = transcript.merge(first, second)
        mapping["raw2"] = raw2
    mapping["raw"], mapping["version"] = raw, mv
    return fa, meta, blocks, notes, mapping


def read_fields(bid, n, ctx):
    """A page's reading with a given context (the teacher's new-field trial): one step, the AI OCR reads the image
    again; two steps, the stored transcript is mapped again (a text call, no image)."""
    with db.connect() as c:
        prev = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
    up = v1.load(prev["upright_path"])
    if READER != "two_step":
        return ai_call("read_all", bid, n, read_all, v1.png_bytes(up), context.vlm_schema(ctx))[0]
    return read_then_map(bid, n, up, ctx, prev)[0]


def carry_over(second, fields_all):
    """A look-again made for an earlier reading, kept where the new reading gives the same first answer (so it is
    never paid for twice); its kept answer is put back in. Answers to other first answers are dropped: the new
    reading's value is new, and the look-again rule decides afresh whether it is asked."""
    if not second:
        return None
    res = {}
    for c, r in (second.get("results") or {}).items():
        now = fields_all.get(c)
        if now and verify.flat((r.get("first") or {}).get("source_text")) == verify.flat(now.get("source_text")):
            res[c] = r
            if r.get("kept_second") and r.get("second"):
                fields_all[c] = {**now, "value": r["second"].get("value"), "source_text": r["second"].get("source_text")}
    keep = {k: second[k] for k in ("bundle_asks",) if second.get(k)}
    return {"asked": sorted(res), "results": res, **keep} if res or keep else None


def evaluate_reading(bid, n, fields_all):
    """The page's checks on a given reading (the trial's, or its own first one): what recompute() would say with no
    look-again, no store answer, nothing stored. For the trial report."""
    with db.connect() as c:
        p = c.execute("SELECT * FROM staging.page WHERE batch_id=%s AND page_no=%s", (bid, n)).fetchone()
        if (not p or p["type_status"] not in ("decided", "labelled") or p["classical_text"] is None
                or fields_all is None):
            return None
        _, ctx = context.ensure(c)
        sos, confirmed, day = satellite.load(c), satellite.confirmations(c, bid, n), scan_day_of(c, bid)
    dt = p["doc_type"]
    rd = {"classical_text": p["classical_text"], "ocr_words": p["ocr_words"] or []}
    fields, res, _ = page_verdicts(dt, fields_all, rd, p["qr_text"], v1.load(p["upright_path"]), ctx, sos,
                                   confirmed, scan_day=day, ship_to=p.get("ship_to"))
    asks = second_look_asks(dt, res, ctx) if res else []
    return {"doc_type": dt, "fields": fields, "res": res, "asks": [c for c, _ in asks],
            "outcome": outcome(p["type_status"], dt, res, not asks),
            "keys": keymod.derive(dt, fields, rd["classical_text"], p["qr_text"], res["header"] if res else None)
            if fields else {}}


def trial_detail(trial_row, page_row):
    """What each step produced, for the trial screen (pure, from stored data):
    blocks  the AI OCR's copy of the page, in order, each with the fields/rows that ended up taken from it;
    mapped  the text model's own answer per field (the block it named, what it copied, its value) and what grounding
            did with it: kept, moved to the block that really prints it, or dropped (and why);
    rows    its table rows, each cell with the value it gave and whether grounding kept it."""
    blocks = trial_row.get("transcript") or []
    mapping = trial_row.get("mapping") or {}
    raw = mapping.get("raw") or {}
    fa = trial_row.get("fields_all") or {}
    moved = set(mapping.get("moved") or [])
    dropped = {d.get("field"): d for d in mapping.get("dropped") or [] if d.get("field")}
    cell_dropped = {(d.get("row"), d.get("column")) for d in mapping.get("dropped") or [] if d.get("column")}
    used = {}
    for name, w in (mapping.get("fields") or {}).items():
        used.setdefault(w.get("block"), []).append(name)
    for i, w in enumerate(mapping.get("rows") or [], 1):
        used.setdefault(w.get("block"), []).append(f"row {i}")
    answer = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    mapped = []
    for name, m in sorted(answer.items()):
        if m in (None, "", {}):
            continue
        m = m if isinstance(m, dict) else {"value": m}
        f = fa.get(name)
        if f:
            final = (mapping.get("fields") or {}).get(name, {}).get("block")
            result = f"moved to {final}" if name in moved else "kept"
        else:
            result = "dropped: " + (dropped.get(name) or {}).get("why", "not on the page")
        mapped.append({"field": name, "block": m.get("block"), "text": m.get("text"), "value": m.get("value"),
                       "result": result, "source": (f or {}).get("source_text"), "ok": bool(f)})
    by_id = {b.get("id"): b for b in blocks}
    rows = []
    for r in raw.get("lines") or []:
        if not isinstance(r, dict):
            continue
        b = by_id.get(r.get("row"))
        cells = [(c, v, (r.get("row"), c) not in cell_dropped) for c, v in r.items() if c != "row" and v not in (None, "")]
        rows.append({"row": r.get("row"), "printed": transcript.block_text(b) if b else None, "cells": cells})
    return {"image": page_row.get("upright_path"),
            "blocks": [{"id": b.get("id"), "kind": b.get("kind"), "box": b.get("box"), "about": b.get("about"),
                        "text": " | ".join(str(c) for c in b["cells"]) if b.get("cells") else (b.get("text") or ""),
                        "used": used.get(b.get("id"), [])} for b in blocks],
            "mapped": mapped, "rows": rows, "conflicts": mapping.get("conflicts") or [],
            "one_run": mapping.get("one_run") or [], "twice": bool(mapping.get("raw2"))}


def compare_trial(bid, n, trial_row, page_row):
    """One page: the page's own first reading beside the trial's, both through today's checks (evaluate_reading).
    What the trial report shows: outcome, keys, the values print backs, every value that differs, the rows'
    quantities, notes, what the text model's answer lost to grounding, flips, tokens."""
    old = evaluate_reading(bid, n, first_reading(page_row))
    new = evaluate_reading(bid, n, trial_row["fields_all"]) if trial_row.get("fields_all") else None
    out = {"page": n, "type": page_row.get("doc_type"), "type_status": page_row.get("type_status"),
           "error": trial_row.get("error"), "notes": trial_row.get("notes") or [],
           "dropped": (trial_row.get("mapping") or {}).get("dropped") or [],
           "flips": (trial_row.get("mapping") or {}).get("flips"), "meta": trial_row.get("meta") or {},
           "blocks": len(trial_row.get("transcript") or []), "detail": trial_detail(trial_row, page_row)}
    if not old or not new:
        return {**out, "checkable": False}

    def ok(r):
        return sorted(k for k, v in ((r["res"] or {}).get("header") or {}).items() if (v or {}).get("verdict") == "ok")

    def key(r):
        return {k: (v.get("value"), v.get("confirmed_by")) for k, v in (r["keys"] or {}).items()}
    of, nf = old["fields"] or {}, new["fields"] or {}
    diffs = [(k, (of.get(k) or {}).get("value"), (nf.get(k) or {}).get("value")) for k in sorted(set(of) | set(nf))
             if k != "lines" and verify.flat((of.get(k) or {}).get("value")) != verify.flat((nf.get(k) or {}).get("value"))]
    qty = next((c for c in ("qty", "qty_crt") if any(c in r for r in (of.get("lines") or []) + (nf.get("lines") or []))), "qty")
    rows = lambda f: [{"text": (r.get("row_text") or "")[:90], "qty": r.get(qty)} for r in f.get("lines") or []]
    return {**out, "checkable": True, "outcome": (old["outcome"], new["outcome"]), "links": (key(old), key(new)),
            "ok": (ok(old), ok(new)), "diffs": diffs, "rows": (rows(of), rows(nf))}


# ---------------------------------------------------------------------------------------------- the queue's side

MAX_TRIES = 3                  # a call that failed (not a limit) is tried this many times from the waiting room
LIMITS = ("DailyLimit", "OutOfBudget", "NotSet")       # a page stopped by one waits in q.pages.wait, never failing


def blocked():
    """Why no AI OCR call can be made right now (today's cap is used up, or the provider's last refusal still
    stands, or a model isn't set on the Teknis screen), else None. Checked before any work on a parked page: waiting
    it out costs one query, not a page run."""
    unset = not_set()
    if unset:
        return unset
    for spec in CAPS:                    # a page needs every model: the AI OCR, and the text model that maps
        if ai_left(spec) <= 0:
            return f"OutOfBudget: vlm-first's daily cap for {spec} ({cap_of(spec)}) is used up"
        wait = refused_for(spec)
        if wait:
            return f"DailyLimit: {int(wait // 60)} min left of {spec}'s refusal"
    return None


def retry_of(bid, n):
    """After a page's run: ('limit', why) when it waits because the daily limit or cap stopped a call, ('failed', why)
    when a call failed otherwise, else None. A page waiting for a bundle's question, a person or nothing is None."""
    with db.connect() as c:
        r = c.execute("""SELECT extract_status, extract_error, second_look->>'waiting' AS waits FROM staging.page
                          WHERE batch_id=%s AND page_no=%s""", (bid, n)).fetchone()
    return r and retry_kind(r["extract_status"], r["extract_error"], r["waits"])


def retry_kind(extract_status, extract_error, waits):
    """Pure: ('limit' | 'failed', why) for a page whose reading failed or whose look-again call failed, else None."""
    why = extract_error if extract_status == "failed" else waits if waits and "the call failed" in waits else None
    if not why:
        return None
    return ("limit" if any(w in why for w in LIMITS) else "failed"), why


def trace_outcome(oc, cls, x, sl):
    """The page's span says how it ended: wait (a limit or a model not set stopped a call: it goes on by itself),
    fail (a call failed), or ok (read; a look-again a bundle asked for may still wait, by plan)."""
    err = x["extract_error"] if x["extract_status"] == "failed" else None
    why = err or (sl or {}).get("waiting")
    trace.note(outcome=oc, doc_type=cls["doc_type"], type_status=cls["type_status"], waiting=(sl or {}).get("waiting"))
    if why and any(k in why for k in LIMITS):
        trace.set_status("wait", why)
    elif err or (why and "the call failed" in why):
        trace.set_status("fail", why)


def park(ticket, why):
    """Put a page in the waiting room (q.pages.wait): it comes back to q.pages by itself after queue.WAIT_MS. The page
    stays 'queued' while it has a ticket, so nothing else sends it (vf-grouper sends only pages that are 'read')."""
    from common import queue
    with db.connect() as c:
        c.execute("UPDATE staging.page SET status='queued' WHERE batch_id=%s AND page_no=%s",
                  (ticket["batch_id"], ticket["page_no"]))
    queue.send(queue.Q_WAIT, [{**ticket, "parked": why[:200]}])
    trace.event("page.parked", "wait", batch=ticket["batch_id"], page=ticket["page_no"], why=why[:300],
                back_in_minutes=round(queue.WAIT_MS / 60000, 1) if getattr(queue, "WAIT_MS", None) else None)


def after(ticket):
    """The worker's step after a page ran: a call the limit stopped waits and is tried again (never counted); a failed
    call is tried MAX_TRIES times, then the page stays waiting for `again` or the sweep. Returns what was done."""
    r = retry_of(ticket["batch_id"], ticket["page_no"])
    if not r:
        return None
    kind, why = r
    tries = ticket.get("tries", 0) + (kind == "failed")
    if tries >= MAX_TRIES:
        trace.event("page.gave_up", "fail", batch=ticket["batch_id"], page=ticket["page_no"], tries=tries,
                    error=why[:300])
        return f"gave up after {tries} failed calls: {why[:120]}"
    park({**ticket, "tries": tries}, why)
    return f"parked ({kind}): {why[:120]}"


def enqueue(bid, pages, waits=None, tries=0, why=None):
    """Send pages to q.pages: each set 'queued' first (only those that are 'read': a page with a ticket keeps its one),
    and tickets published after the commit. waits = only pages whose look-again waits for this reason (its start).
    Returns the pages sent."""
    from common import queue
    with db.connect() as c:
        rows = c.execute("""UPDATE staging.page p SET status='queued' FROM staging.scan_batch b
                             WHERE b.id = p.batch_id AND p.batch_id=%s AND p.page_no = ANY(%s) AND p.status='read'
                               AND (%s::text IS NULL OR starts_with(p.second_look->>'waiting', %s::text))
                         RETURNING p.page_no, coalesce(p.original_path, p.image_path) AS key, b.run""",
                         (bid, list(pages), waits, waits)).fetchall()
    queue.send(queue.Q_PAGES, [{"batch_id": bid, "page_no": r["page_no"], "run": r["run"], "image_key": r["key"],
                                **({"tries": tries} if tries else {})}
                               for r in sorted(rows, key=lambda r: r["page_no"])])
    trace.events("page.queued", [{"batch": bid, "page": r["page_no"]} for r in rows], why=why or waits)
    return sorted(r["page_no"] for r in rows)


def sweep():
    """The scheduler's safety net ("sweep", every SWEEP_EVERY_MINUTES): every page still waiting for the AI OCR
    with no ticket goes back on q.pages, and each batch with a bundle still waiting (grouping) regroups. A page whose
    calls failed gets ONE more try per sweep (tries = MAX_TRIES - 1), so a page the AI can't answer costs at most one
    call per sweep. Nothing is sent while the AI is refused (its parked pages come back by themselves)."""
    from common import queue
    why = blocked()
    if why:
        return {"skipped": why}
    with db.connect() as c:
        batches = [r["id"] for r in c.execute("SELECT id FROM staging.scan_batch ORDER BY received_at")]
        grouping = [r["batch_id"] for r in c.execute("""
            SELECT DISTINCT d.batch_id FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
              JOIN staging.document d ON d.id = bd.document_id WHERE b.status = 'grouping'""")]
    sent = {}
    for bid in batches:
        todo = waiting(bid)
        pages = enqueue(bid, todo, tries=MAX_TRIES - 1, why="the sweep: still waiting for the AI") if todo else []
        if pages:
            sent[bid] = pages
    for bid in grouping:
        queue.wake_grouper(bid, "the sweep: a bundle still waits")
    return {"sent": sent, "regrouped": grouping}


def again(bid, pages=None):
    """What waits for the AI OCR goes back on the queue, for the page workers (they wait out a daily limit by
    themselves). Returns the pages sent."""
    todo = waiting(bid, pages)
    sent = enqueue(bid, todo, why="tried again") if todo else []
    print(f"waiting for the AI OCR: {todo or 'nothing'} · sent to {len(sent)} page worker tickets: {sent}", flush=True)
    return sent


if __name__ == "__main__":
    from worker.clone import pages_arg
    if sys.argv[1] == "once":
        once(sys.argv[2], pages_arg(sys.argv[3]), "--v1-reading" in sys.argv, "--no-second-look" not in sys.argv)
    elif sys.argv[1] == "again":
        again(sys.argv[2], pages_arg(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif sys.argv[1] == "shadow":
        shadow(sys.argv[2], pages_arg(sys.argv[3]))
    elif sys.argv[1] == "reground":          # reground <batch>: the stored answers, grounded again (no call)
        print(reground(sys.argv[2]), "pages grounded again")
    elif sys.argv[1] == "trial":             # trial <batch> <pages> [--variant name] [--once]
        rest = sys.argv[4:]
        trial(sys.argv[2], pages_arg(sys.argv[3]),
              rest[rest.index("--variant") + 1] if "--variant" in rest else None, "--once" not in rest)
