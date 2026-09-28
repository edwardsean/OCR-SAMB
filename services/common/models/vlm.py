"""AI OCR (vision model) adapter. Today: Google Gemini over REST. Swap target: Model Studio / Ark.

Two calls:
  extract(image, doc_type)  fill the field schema for a KNOWN document type (phase 4)
  read(image)               type-agnostic reading for pages classification was unsure about:
                            title, issuer, transcript, visible keys. Used to ask Jev a second time.
Every value comes with source_text: the characters exactly as printed, so phase 5 can check them.
"""
import base64
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from common.models import schemas

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
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
    first = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    rest = [m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash").split(",") if m.strip()]
    return [first] + [m for m in rest if m != first]


def _call(parts, schema, retries=4, timeout=180):
    key = os.environ.get("GEMINI_API_KEY")
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
            r = httpx.post(URL.format(model=model), headers={"x-goog-api-key": key}, json=body, timeout=timeout)
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
