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


def _call(parts, schema, retries=4):
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
            r = httpx.post(URL.format(model=model), headers={"x-goog-api-key": key}, json=body, timeout=180)
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
