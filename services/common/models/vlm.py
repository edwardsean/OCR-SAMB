"""AI OCR (vision model) adapter. Today: Google Gemini over REST. Swap target: Model Studio / Ark.

Two calls:
  extract(image, doc_type)  fill the field schema for a KNOWN document type (phase 4)
  read(image)               type-agnostic reading for pages classification was unsure about:
                            title, issuer, transcript, visible keys. Used to ask Jev a second time.
Every value comes with source_text: the characters exactly as printed, so phase 5 can check them.
"""
import base64
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from common import config, settings, trace
from common.models import schemas

URL = config.GEMINI_BASE_URL + "/models/{model}:generateContent"
THINKING_BUDGET = 2048
_EXHAUSTED = {}   # model -> date its free-tier DAILY quota ran out; skipped until the next day (no retry waits)
RULES = """You are reading ONE scanned page of Indonesian accounts-receivable paperwork for PT Sarana Abadi Makmur Bersama (SAMB).
Rules, strictly:
- Only report what is PRINTED or handwritten on this page. If a field is absent or you cannot read it with certainty, use null. Never guess, never infer from other fields, never fill from general knowledge.
- source_text: copy the characters exactly as printed, including separators (e.g. "1.126.011,00" or "4,796,546.03", "04-Sep-2026").
- value: the normalised form. Amounts: plain decimal with "." as decimal point and no thousands separators (Indonesian "1.126.011,00" -> "1126011.00"). Dates: YYYY-MM-DD. Identifiers: exactly as printed, without spaces.
- Line items: one entry per printed row, in page order; row_text is the whole printed row as it appears."""


def _models():
    """Pinned model first, then fallbacks for when the free tier says 'high demand' (503)."""
    first = config.GEMINI_MODEL
    return [first] + [m for m in config.GEMINI_FALLBACK_MODELS if m != first]


def _call(parts, schema, retries=4, timeout=180):
    key = settings.key("vision")                    # the vision row's key (Teknis → Model & kunci API)
    if not key:
        raise RuntimeError("no GEMINI_API_KEY")
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema,
                                 "mediaResolution": "MEDIA_RESOLUTION_HIGH",
                                 # measured: uncapped, faint page 8 used ~63,000 thinking tokens and 191 s, and was still wrong
                                 "thinkingConfig": {"thinkingBudget": THINKING_BUDGET}}}
    t0, tried = time.time(), []
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()  # Google resets free quotas at midnight Pacific
    for model in _models():
        if _EXHAUSTED.get(model) == today:
            tried.append(f"{model}:daily-quota"); continue
        for attempt in range(retries):
            trace.ai_request({"model": model, **body})
            r = httpx.post(URL.format(model=model), headers={"x-goog-api-key": key}, json=body, timeout=timeout)
            trace.ai_http(r)
            if r.status_code == 429 and "PerDay" in r.text:  # daily quota gone: stop asking this model today
                _EXHAUSTED[model] = today
                tried.append(f"{model}:daily-quota"); break
            if r.status_code == 429:                        # per-minute rate limit: wait as told, same model
                wait = 10 * 2 ** attempt
                try:
                    for d in r.json()["error"].get("details", []):
                        if "retryDelay" in d:
                            wait = max(wait, float(d["retryDelay"].rstrip("s")) + 1)
                except Exception:
                    pass
                time.sleep(min(wait, 90)); continue
            if r.status_code in (400, 404):                 # model retired or setting unsupported: next model
                tried.append(f"{model}:{r.status_code}"); break
            if r.status_code in (500, 503):                 # overloaded: a couple of short retries, then next model
                if attempt < 1:
                    time.sleep(4); continue
                tried.append(f"{model}:{r.status_code}"); break
            r.raise_for_status()
            j = r.json()
            text = j["candidates"][0]["content"]["parts"][-1]["text"]
            usage = j.get("usageMetadata", {})
            return json.loads(text), {"model": j.get("modelVersion", model), "ms": int((time.time() - t0) * 1000),
                                      "tokens_in": usage.get("promptTokenCount"), "tokens_out": usage.get("candidatesTokenCount"),
                                      "tokens_thinking": usage.get("thoughtsTokenCount"), "fell_back_from": tried or None}
        else:
            tried.append(f"{model}:429")
    raise RuntimeError(f"Gemini unavailable: {tried}")


def _image_part(png_bytes):
    return {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png_bytes).decode()}}


def extract(png_bytes, doc_type):
    schema = schemas.SCHEMAS[doc_type]
    prompt = RULES + f"\n\nThis page is a {schemas.NAMES[doc_type]}. Fill the schema."
    return _call([{"text": prompt}, _image_part(png_bytes)], schema)


def read(png_bytes):
    prompt = RULES + "\n\nDo NOT decide what kind of document this is. Report what the page shows: its title (if printed), " \
                     "who issued it (the letterhead company), transcript, and every identifier you can see."
    return _call([{"text": prompt}, _image_part(png_bytes)], schemas.READ)


# ---------------------------------------------------------------------------------------------- read, then map
# The mentor's two steps (2026-09-29): the AI OCR copies EVERYTHING on the page with no field list (handwriting,
# stamps and marks included: a sketch on an invoice may be the only sign of a rejection), then a text model maps that
# copy onto the field list and collects notes. A prompt's text is part of its version: change one, bump its _V
# (tests/test_vf_transcript.py fails otherwise), so stored transcripts and mappings are never silently reused.
TRANSCRIBE_V = 1
TRANSCRIBE = """You are copying ONE scanned page of Indonesian business paperwork exactly as it appears.
Do not interpret it, do not summarise it, do not fill in any form. Copy EVERYTHING on the page:
- every printed line: letterhead, titles, labels with their values, remarks, footers;
- every table: its header row, then each row, cell by cell in column order (an empty cell is "");
- handwriting (numbers, words), stamps with their text, dates written by hand;
- marks and sketches, described in square brackets, with what they touch: [tick], [cross over the row],
  [circle around "2"], [strike-through over "10"], [arrow from "5" to "3"], [signature].
Rules, strictly:
- Copy characters exactly as printed, with their separators ("1.126.011,00", "04-Sep-2026", "4505832724").
- A number cut off at the page's edge stays cut ("1.078.330,"): never complete it.
- A character you cannot read is [?]. Never guess, never correct, never add what is not on the page.
- Reading order: top to bottom, left to right. One printed line = one block; one table row = one block.
Answer with ONE JSON object:
{"blocks": [{"id": "b1", "kind": "printed" | "table_header" | "table_row" | "handwriting" | "stamp" | "mark",
             "text": "the block's text", "cells": ["...", "..."] (table_header and table_row only),
             "box": {"x0": 0, "y0": 0, "x1": 0, "y1": 0} (0-1000 of the page's width and height),
             "about": "b12" (handwriting, stamp, mark: the block it is written on or next to, if any)}]}"""

MAP_V = 3
MAP = """Below is the complete transcript of ONE scanned page of Indonesian accounts-receivable paperwork for
PT Sarana Abadi Makmur Bersama (SAMB): one block per line, as [id] kind (x0-x1, y0-y1 on 0-1000) text.
It could be SAMB's sales invoice (Faktur Penjualan), a customer's goods receipt, a purchase order, a delivery note,
a tax invoice, a payment document or a later page of one. You are NOT asked what kind it is.
Task 1 - fields: for each field in the list that is on this page, give the block it is in and ONLY the value's own
characters exactly as they appear in that block, never its label or the rest of the line
(block "NO.PO : AH9Q69089 DIV : T" -> "text": "AH9Q69089"): {"block": "b7", "text": "...", "value": "normalised"}.
Use null for a field that is not on this page. One printed value goes to one field. Use only the transcript: never
infer, never compute, never fill from general knowledge. document_title is the document's own name as printed
(e.g. PURCHASE ORDER, RECEIVING NOTE, FAKTUR PENJUALAN, BUKTI PENERIMAAN BARANG), never a company's name or its
letterhead. value: amounts as plain decimals with "." as the decimal
point and no thousands separators ("1.126.011,00" -> "1126011.00"); dates YYYY-MM-DD; identifiers exactly as
printed without spaces.
Task 2 - lines: one entry per item row of a table: {"row": "<the row's block id>", <line columns>: the cell's text
exactly as in the row, or null}.
Task 3 - notes: everything handwritten, stamped or marked, and printed remarks a person should know about (a return,
a rejection, a quantity crossed out or corrected): {"kind": "handwriting" | "stamp" | "mark" | "remark" |
"signature", "text": "what it says or shows", "blocks": ["b14"], "about": "what it seems to concern, e.g. row b14 qty"}.
A signature is only {"kind": "signature", "blocks": [...]}.
Answer with ONE JSON object: {"fields": {<field>: {...} or null}, "lines": [...], "notes": [...]}."""

# Pass B, the knowledge pass (the user, 2026-10-09: "asking it only for those fields would be much cheaper"): only the
# fields its tips name, a table's rows only when a tip names a column, never notes (pass A's stand). Made from MAP, so
# the two can't drift apart: its introduction and field task, and its line task when one is asked.
MAP_NAMED_V = 1
_MAP_HEAD, _MAP_REST = MAP.split("Task 2 - lines", 1)
MAP_NAMED = _MAP_HEAD + "Only the fields listed below are asked for.\n"
MAP_NAMED_LINES = "Task 2 - lines" + _MAP_REST.split("Task 3 - notes", 1)[0]


# ---------------------------------------------------------------------------------------------- vlm-first
READ_ALL = """
This page could be any of SAMB's paperwork: SAMB's own sales invoice (Faktur Penjualan), a customer's goods receipt
(Tanda Terima, Goods Receive Note, Receiving Slip…), a customer's purchase order, a delivery note, a tax invoice
(Faktur Pajak), a payment document, or a later page of a multi-page document. You are NOT asked what kind it is.
Fill every field of the schema that is printed on THIS page. Most fields will be null on any one page. One printed
value goes to one field. For each value, also give box = [ymin, xmin, ymax, xmax] on a 0-1000 scale: where the value
itself is printed."""

SECOND_LOOK = """
You read this page before. Another reader does not agree with your reading of the fields listed below, or you found
nothing for them. Look again at the page and at the zoomed crops (each crop is labelled with its field). Read each
value character by character, exactly as printed. If a value is not printed, give null. If you cannot read it with
certainty, set unsure = true. Do not guess, and do not fill a value from other fields.
Fields to look at again:
"""

STORE = """
Where do the goods on this page go? Give the store, branch or warehouse they are delivered to or received at, as
printed: a Ship To / Deliver To / Delivery Address / Dikirim ke / Cabang / Store / Site / Lokasi / Gudang, or the
receiving store's name in the header. Not SAMB (the supplier), and not the customer's head office when a store or
branch is printed as well. If no store or branch is printed, give null. If you cannot read it with certainty, set
unsure = true.
"""


def extract_all(png_bytes, schema):
    """vlm-first: read the page against the WHOLE combined field list (common/context.vlm_schema)."""
    return _call([{"text": RULES + READ_ALL}, _image_part(png_bytes)], schema, timeout=240)


def second_look(png_bytes, asks, crops):
    """vlm-first, blind: asks = [(field name, meaning)]; crops = [(field name, png bytes)] of where it said the value is.
    The request never contains the other reader's text or this model's first answer."""
    one = {"type": "OBJECT", "nullable": True,
           "properties": {"value": {"type": "STRING", "nullable": True},
                          "source_text": {"type": "STRING", "nullable": True},
                          "box": {"type": "ARRAY", "nullable": True, "items": {"type": "INTEGER"}},
                          "unsure": {"type": "BOOLEAN"}},
           "required": ["value", "source_text", "unsure"]}
    schema = {"type": "OBJECT", "properties": {name: one for name, _ in asks}, "required": [n for n, _ in asks]}
    text = RULES + SECOND_LOOK + "\n".join(f"- {name}: {meaning}" for name, meaning in asks)
    parts = [{"text": text}, _image_part(png_bytes)]
    for name, png in crops:
        parts += [{"text": f"Zoomed crop for {name}:"}, _image_part(png)]
    return _call(parts, schema, timeout=240)


def store(png_bytes):
    """S4, blind: the store the page's goods go to. The page only, never Satellite's store names. ({value,
    source_text, unsure}, meta)."""
    schema = {"type": "OBJECT", "properties": {"value": {"type": "STRING", "nullable": True},
                                               "source_text": {"type": "STRING", "nullable": True},
                                               "unsure": {"type": "BOOLEAN"}},
              "required": ["value", "source_text", "unsure"]}
    return _call([{"text": RULES + STORE}, _image_part(png_bytes)], schema, timeout=240)
