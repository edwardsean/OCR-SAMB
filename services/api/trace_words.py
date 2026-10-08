"""The trace in plain words, for a developer who doesn't know the workflow yet (the user, 2026-10-08: "what is
ai.transcribe, etc? make it human readable and simple … the first step (upload), then split pages, etc").

  STEPS          the workflow's steps in order, each with what it does
  NAMES          every trace kind and AI call: (step, title, what it does)
  title(row)     "AI copies the page's text"
  describe(row)  one sentence from the row's detail: why it was queued, what it decided, the model and tokens…

Kinds are the code's names (common/trace.py writes them, staging.model_call's purpose for AI calls); they stay on
screen small, for searching the code and the logs."""
import json

STEPS = [
    ("upload", "1. Upload", "A person starts a batch and uploads PDF files. Each file is stored and recorded."),
    ("split", "2. Split into pages", "Each PDF is rendered into one image per page (the intake service)."),
    ("read", "3. Read each page", "Each page waits in a queue for a free page worker (3 run at once), which: prepares "
     "the image; has the vision model copy its text and the text model map that onto the combined field list; decides "
     "the document type; applies learned tips (text model, only if there are any); maps the fields onto that type's "
     "own fields (code); has Tesseract read the print; checks every value; asks the AI again about what nothing backed. A page can be read more than once (an order asks for a look again, a "
     "person changes its type, a retry)."),
    ("group", "4. Group into orders", "Pages become documents and documents join their order (SOR) by the numbers "
     "printed on them; then the order's documents are checked against each other and Satellite (the grouper)."),
    ("people", "5. What people did", "Types chosen, values corrected, numbers confirmed, differences accepted, orders "
     "approved, retries."),
    ("teach", "6. What the teachers learned", "From people's corrections: the classifier's descriptions and the "
     "knowledge tips, each replayed on stored pages and used only when it helps."),
    ("send", "7. Sent to Satellite", "Finished orders written to Satellite with one PDF per order."),
    ("jobs", "Scheduled jobs", "The scheduler's periodic work (not tied to one batch)."),
]

AI = "AI call: "
NAMES = {
    # 1-2 upload and split
    "person.new_upload": ("upload", "Batch created", "A person started an upload batch."),
    "file.received": ("upload", "File uploaded", "The PDF was stored and recorded; it waits to be split into pages."),
    "file.split": ("split", "File split into pages", "Every page of the PDF was rendered as an image."),
    # 3 read each page
    "page.queued": ("read", "Page sent to the reading queue", "It waits there for a free page worker."),
    "page.parked": ("read", "Page put in the waiting room", "An AI limit, or a model not set, stopped it; it comes "
                    "back to the queue by itself."),
    "page": ("read", "A page worker read the page", "One attempt, from taking the page off the queue to saving it."),
    "page.prepare": ("read", "Prepare the image", "Turn it upright, straighten it, mask dark bands, read the QR code."),
    "page.read": ("read", "AI reads the page", "The vision model copies the page's text (it isn't told the type), then "
                  "the text model maps that text onto the combined field list of every type, twice at the same time: "
                  "a field the two answers disagree on is left empty. Both are kept, so a re-read costs nothing."),
    "page.classify": ("read", "Decide the document type", "The classification model sees only the fields found (never "
                      "the image) and picks Faktur, PO, Tanda Terima…; a Faktur also needs a second witness (QR code, "
                      "layout or printed title)."),
    "page.knowledge": ("read", "Apply learned tips", "Text model again, only when people's corrections taught tips for "
                       "this customer and type; only the fields the tips name are taken from it."),
    "page.project": ("read", "Map onto the type's fields (code)", "Code, not AI: the combined field list becomes this "
                     "document type's own fields (e.g. po_number is a PO's purchase_order_no)."),
    "page.tesseract": ("read", "Tesseract reads the print", "The local OCR program reads the page: the independent "
                       "witness every value is checked against. It starts as soon as the image is prepared, beside "
                       "the AI's reading."),
    "page.wait_tesseract": ("read", "Wait for Tesseract to finish", "The AI was quicker than Tesseract this time."),
    "page.boxes": ("read", "Place the clickable boxes", "Where each value sits on the paper, for the page viewer; and "
                   "Satellite's orders loaded for the checks (once every ten minutes per worker)."),
    "page.check": ("read", "Check every value", "Each value against the print, the QR code and Satellite's record."),
    "page.look_again": ("read", "AI looks again", "The vision model is asked again, blind, about values nothing backed."),
    "page.store": ("read", "AI finds the store", "Asked only when the store printed on the page decides its order."),
    "page.save": ("read", "Save the result", "The page's fields, verdicts and outcome are stored."),
    "page.crashed": ("read", "The worker crashed on this page", "An error stopped it; it is tried once more, then "
                     "given up (a person can retry it)."),
    "page.gave_up": ("read", "Gave up after repeated failed AI calls", "The page waits for the sweep or a person's retry."),
    # 4 group
    "group": ("group", "Group pages into orders", "Documents join their order and the order's checks run."),
    "order.status": ("group", "An order's status changed", "Its checks gave a new result."),
    # 5 people
    "person.save_label": ("people", "A person set a page's type", "On Jenis halaman, or Ubah jenis on the page."),
    "person.fix_page": ("people", "A person corrected a value on a page", "Clicked on the paper in the page viewer."),
    "person.confirm": ("people", "A person confirmed a value", "On an order's Review."),
    "person.confirm_key": ("people", "A person confirmed a linking number", "So the document joins its order."),
    "person.pair": ("people", "A person paired a product row", "A customer's row with SAMB's order line."),
    "person.accept": ("people", "A person accepted a difference", "With a reason, on an order's Review."),
    "person.calibrate": ("people", "A person answered a customer's one-time question", ""),
    "person.approve": ("people", "A person approved an order", ""),
    "person.publish": ("people", "A person pressed Kirim", "Sends the finished orders to Satellite."),
    "person.retry_page": ("people", "A person retried a page", ""),
    "person.retry_scan": ("people", "A person retried a file", ""),
    "person.retry_upload": ("people", "A person retried everything stuck", ""),
    "person.settings": ("people", "A person changed a model setting", "Teknis → Model & kunci API (the key is never "
                        "written here)."),
    "person.context_revert": ("people", "A person took the classifier's context back", ""),
    # 6 teachers
    "lesson.type": ("teach", "The teacher studied a page typed differently", "Why the classifier missed it, and one "
                    "change to its descriptions, replayed on every labelled page."),
    "context.active": ("teach", "The classifier's descriptions changed", "A change that passed its replay."),
    "lesson.tip": ("teach", "The teacher wrote a tip from a correction", ""),
    "tip.test": ("teach", "A tip was tested on stored pages", "Used only when it gets more right and nothing wrong."),
    "tip.active": ("teach", "A tip was switched on", ""),
    "tip.apply": ("teach", "A tip was applied to stored pages", ""),
    # 7 send
    "publish": ("send", "Order sent to Satellite", "Its rows and its PDF."),
    "unpublish": ("send", "Order taken back from Satellite", "A developer's undo."),
    # jobs
    "job.intake": ("jobs", "Retry files not split yet", ""),
    "job.notify": ("jobs", "Note orders that newly need a person", ""),
    "job.sweep": ("jobs", "Send pages still waiting for the AI back to the queue", ""),
    "job.lint": ("jobs", "Take out tips a later correction contradicts", ""),
    "job.trace": ("jobs", "Tidy the trace", "Delete what is older than TRACE_KEEP_DAYS."),
    # AI calls (staging.model_call's purpose)
    "ai.transcribe": ("read", AI + "copy the page's text", "Vision model: the page image → its text, block by block."),
    "ai.map": ("read", AI + "map the text onto fields", "Text model: which text is the PO number, the total, …"),
    "ai.map_b": ("read", AI + "map again with learned tips", "Text model, with the tips for this customer."),
    "ai.read_all": ("read", AI + "read every field from the image", "Vision model (the older one-step reading)."),
    "ai.classify": ("read", AI + "decide the document type", "Classification model: one token, its probabilities."),
    "ai.second_look": ("read", AI + "look again at unsure values", "Vision model, shown only the field names and crops."),
    "ai.ship_to": ("read", AI + "find the store the goods go to", "Vision model."),
    "ai.visual": ("read", AI + "read a marked region", "Vision model, for a tip about handwriting or a stamp."),
    "ai.recheck": ("read", AI + "decide the type again", "Classification model, on a stored reading."),
    "ai.trial_read": ("read", AI + "trial reading", "A developer's comparison run."),
    "ai.teach": ("teach", AI + "the teacher studies a page", "Vision model: why the classifier missed it."),
    "ai.replay": ("teach", AI + "re-ask the classifier to test a change", "Classification model, about a labelled "
                  "page: this page is one of the pages a change is tested on."),
    "ai.exam": ("teach", AI + "score a change on the exam pages", "Classification model."),
    "ai.teach_wiki": ("teach", AI + "the teacher writes a tip", "Text model."),
    "ai.match": ("group", AI + "propose product pairs", "Text model."),
}

OUTCOME = {"clear": "every value backed", "needs_person": "needs a person", "waiting_ai": "waits for the AI",
           "held_unsure": "type unsure: a person chooses"}
WHY = {"new": "new upload", "tried again": "tried again"}
LESSON = {"proposed": "a change passed and is in use", "failed": "no change passed the replay",
          "no_change": "nothing to change", "retry": "the AI couldn't be reached: tried later",
          "learned": "the tip is in use", "needs_pages": "no other stored page to test it on yet",
          "already_right": "already read right", "wait": "waits for an earlier tip"}


def name(kind):
    return NAMES.get(kind) or (("people", "A person: " + kind[7:].replace("_", " "), "") if kind.startswith("person.")
                               else ("jobs", "Scheduled: " + kind[4:], "") if kind.startswith("job.")
                               else ("read", AI + kind[3:].replace("_", " "), "") if kind.startswith("ai.")
                               else ("read", kind, ""))


def title(row_or_kind):
    k = row_or_kind if isinstance(row_or_kind, str) else row_or_kind["kind"]
    return name(k)[1]


def explain(row_or_kind):
    k = row_or_kind if isinstance(row_or_kind, str) else row_or_kind["kind"]
    return name(k)[2]


def step(kind):
    return name(kind)[0]


def _n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


def describe(r):
    """One plain sentence from the row's detail (the raw detail is shown beside it, folded)."""
    k, d = r["kind"], r.get("detail") or {}
    if k.startswith("ai."):
        parts = [d.get("model") or "?"]
        if d.get("tokens_in") is not None or d.get("tokens_out") is not None:
            parts.append(f"{_n(d.get('tokens_in') or 0)} tokens in, {_n(d.get('tokens_out') or 0)} out")
        if d.get("usd") is not None:
            parts.append(f"${d['usd']:.4f}")
        return " · ".join(parts)
    if k == "page.queued":
        return "Why: " + WHY.get(d.get("why"), d.get("why") or "?")
    if k == "page.parked":
        return (d.get("why") or "") + (f" · back in {d['back_in_minutes']} min" if d.get("back_in_minutes") else "")
    if k == "page":
        if r.get("status") == "skip":
            return "Nothing to do: " + (d.get("why") or "")
        bits = []
        if d.get("doc_type"):
            bits.append(f"type {d['doc_type']}" + (" (chosen by a person)" if d.get("type_status") == "labelled" else ""))
        if d.get("outcome"):
            bits.append(OUTCOME.get(d["outcome"], d["outcome"]))
        if d.get("waiting"):
            bits.append(f"waits: {d['waiting']}")
        if d.get("tries"):
            bits.append(f"try {d['tries'] + 1}")
        if d.get("back_from_waiting"):
            bits.append("back from the waiting room")
        return "; ".join(bits)
    if k == "page.read" and d.get("reused"):
        return "Reused the page's earlier AI reading: no AI call."
    if k.startswith("page.") and (d.get("out") or {}):    # a stage: its headline output, the rest under "Input and output"
        return stage_line(k, d["out"])
    if k in ("file.received", "file.split"):
        return " · ".join(x for x in (d.get("file"), (f"{d['pages']} page" + ("s" if d["pages"] != 1 else ""))
                                      if d.get("pages") else None,
                                      f"{d['kb']:,} KB" if d.get("kb") else None) if x)
    if k == "group":
        bits = []
        if d.get("orders"):
            bits.append(("order " if len(d["orders"]) == 1 else f"{len(d['orders'])} orders: ") + ", ".join(d["orders"]))
        if d.get("waiting"):
            bits.append(f"{d['waiting']} document{'s' if d['waiting'] != 1 else ''} still waiting for its order")
        if d.get("look_again_sent"):
            bits.append("sent pages " + ", ".join(map(str, d["look_again_sent"])) + " back to the AI to look again")
        if d.get("knowledge_redone"):
            bits.append("tips redone for the order's customer on pages " + ", ".join(map(str, d["knowledge_redone"])))
        return "; ".join(bits)
    if k == "order.status":
        why = d.get("why") or []
        return f"{d.get('was')} → {d.get('now')}" + (": " + "; ".join(why) if why else "")
    if k in ("lesson.type", "lesson.tip"):
        bits = [LESSON.get(d.get("result"), d.get("result") or "")]
        if d.get("label"):
            bits.insert(0, f"a person said {d['label']}")
        if d.get("field"):
            bits.insert(0, d["field"])
        if d.get("why_missed"):
            bits.append(f"why missed: {d['why_missed']}")
        if d.get("not_kept"):
            bits.append(f"not kept: {d['not_kept']}")
        return "; ".join(b for b in bits if b)
    if k == "tip.test":
        return ("passed" if d.get("passed") else "not passed") + (f": {d['why']}" if d.get("why") else "")
    if k == "context.active":
        return f"context #{d.get('was')} → #{d.get('version')}: {d.get('change') or ''}" + (
            f" (right {d['right']})" if d.get("right") else "")
    if k.startswith("job."):
        return job(k, d.get("result"))
    if k.startswith("person."):
        return " · ".join(f"{a.replace('_', ' ')}: {v}" for a, v in d.items() if v not in (None, "") and a not in IO)[:240]
    if k == "publish":
        return f"{d.get('documents', '?')} documents, {d.get('pages', '?')} pages, {d.get('kb', '?')} KB PDF"
    return " · ".join(f"{a}: {v}" for a, v in d.items() if v not in (None, "") and a not in IO)[:240]


IO = ("in", "out")                         # a stage's input and output (worker/trace_io.py): shown on their own


def job(kind, res):
    """A scheduled job's result in words; '' when it found nothing to do."""
    if not isinstance(res, dict):
        return "" if res in (None, "", [], {}) else str(res)[:200]
    if kind == "job.notify":
        return f"notice: {res.get('text')}" if res.get("new") else ""
    if kind == "job.intake":
        return f"sent {len(res['sent'])} file(s) back to be split" if res.get("sent") else ""
    if kind == "job.sweep":
        if res.get("skipped"):
            return f"skipped: {res['skipped']}"
        sent = res.get("sent") or {}
        bits = [f"sent {sum(len(v) for v in sent.values())} page(s) back to the AI"] if sent else []
        if res.get("regrouped"):
            bits.append(f"regrouped {len(res['regrouped'])} file(s)")
        return "; ".join(bits)
    if kind == "job.trace":
        bits = ([f"deleted {res['deleted']:,} old rows"] if res.get("deleted") else []) + (
            [f"deleted {res['payloads_deleted']:,} old AI payloads"] if res.get("payloads_deleted") else []) + (
            [f"closed {res['cut_off']} cut-off spans"] if res.get("cut_off") else [])
        return "; ".join(bits)
    if kind == "job.lint":
        bits = [f"{len(v)} {k}" for k, v in res.items() if isinstance(v, list) and v]
        return "; ".join(bits)
    return json.dumps(res, ensure_ascii=False, default=str)[:200] if res else ""


def idle_job(r):
    """A scheduled job that ran fine and found nothing to do."""
    return r["kind"].startswith("job.") and r["status"] == "ok" and not job(r["kind"], (r.get("detail") or {}).get("result"))


def stage_line(kind, out):
    """A stage's output in a few words, on its line."""
    o = out or {}
    if kind == "page.read":
        m = o.get("mapping onto the field list (text model)")
        n = len(m) if isinstance(m, dict) else 0
        first = (o.get("AI OCR copy (vision model)") or "").split("\n", 1)[0]
        return f"{first}; {n} field{'s' if n != 1 else ''} found" + (
            f"; left empty (the two mappings disagreed): {o['left empty: the two mappings disagreed']}"
            if o.get("left empty: the two mappings disagreed") else "")
    if kind == "page.classify":
        return o.get("decision") or ""
    if kind == "page.project":
        f = next((v for k, v in o.items() if k.endswith("'s fields")), None)
        return f"{len(f)} of the type's fields filled" if isinstance(f, dict) else str(f or "")
    if kind == "page.tesseract":
        return " · ".join(x for x in (f"{o['words']:,} words" if o.get("words") else None,
                                      f"confidence {o['confidence']}" if o.get("confidence") is not None else None,
                                      f"variant {o['variant used']}" if o.get("variant used") else None) if x)
    if kind == "page.check":
        v = o.get("values") or {}
        ok = sum(1 for x in v.values() if str(x).startswith("✓"))
        return f"{ok} of {len(v)} values backed" if v else ""
    if kind == "page.look_again":
        asked = o.get("asked")
        return f"asked: {asked}" if isinstance(asked, str) else f"{len(o)} answer{'s' if len(o) != 1 else ''}"
    if kind == "page.knowledge":
        c = o.get("changed") or o.get("tips")
        return c if isinstance(c, str) else f"changed {', '.join(c)}"
    if kind == "page.prepare":
        return " · ".join(x for x in (f"turned {o['turned (degrees)']}°" if o.get("turned (degrees)") else None,
                                      f"QR {o['QR code']}" if o.get("QR code") else None, o.get("quality")) if x)
    if kind == "page.save":
        return f"outcome: {o.get('outcome')}" + (f"; linking numbers: {', '.join(f'{k}={v}' for k, v in o['linking numbers'].items())}"
                                                 if o.get("linking numbers") else "")
    return ""


def raw(r):
    """Everything the row holds, for the folded detail."""
    keep = {k: r.get(k) for k in ("id", "kind", "status", "service", "batch_id", "page_no", "sor_no", "who", "parent",
                                  "ms", "at", "ended_at") if r.get(k) is not None}
    return json.dumps({**keep, "detail": r.get("detail"), "error": r.get("error")}, indent=2, ensure_ascii=False,
                      default=str)
