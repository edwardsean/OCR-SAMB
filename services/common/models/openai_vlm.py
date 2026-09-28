"""The AI OCR through any OpenAI-compatible vision model (vlm-first): Groq, OpenRouter, Z.ai, Mistral, Model Studio,
Ollama. Same two calls as vlm.py (extract_all, second_look) and the same result shape, so worker/vf.py doesn't care
which model reads.

  VF_AI_OCR=groq:qwen/qwen3.8-27b        provider:model  (gemini = the Gemini adapter in vlm.py)

JSON mode only guarantees JSON, not its shape, so the field list travels in the prompt and the answer is normalised
here: every field comes back as {value, source_text, box}, with box converted to Gemini's convention
[ymin, xmin, ymax, xmax] on a 0-1000 scale (what worker/zoom.py expects). Anything malformed becomes null.
"""
import base64
import io
import json
import os
import re
import time

import httpx
from PIL import Image

from common.models import vlm

PROVIDERS = {   # name -> (base URL, key variable, max images per request)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", 3),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", 8),
    "zai": ("https://api.z.ai/api/paas/v4", "ZAI_API_KEY", 8),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", 8),
    "dashscope": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", 8),
    "ollama": (os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434") + "/v1", None, 8),
}
MAX_IMAGE_BYTES = 3_500_000


class DailyLimit(Exception):
    """The provider's daily request or token limit is used up: stop asking it today."""


def spec_parts(spec):
    provider, _, model = spec.partition(":")
    if provider not in PROVIDERS or not model:
        raise ValueError(f"VF_AI_OCR must be provider:model with provider in {sorted(PROVIDERS)}, not {spec!r}")
    return provider, model


def _image(png_bytes):
    """(data URL, width, height). Shrinks a page only if it's too big to send."""
    im = Image.open(io.BytesIO(png_bytes))
    w, h = im.size
    if len(png_bytes) > MAX_IMAGE_BYTES:
        im = im.convert("L")
        im.thumbnail((2000, 2000))
        b = io.BytesIO(); im.save(b, "PNG", optimize=True)
        png_bytes = b.getvalue()
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode(), w, h


def _wait_seconds(r):
    for k in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = r.headers.get(k)
        if v:
            m = re.match(r"(?:(\d+)m)?([\d.]+)s?$", v.strip())
            if m:
                return float(m.group(1) or 0) * 60 + float(m.group(2))
    return 10.0


def _post(spec, content, max_tokens=4096):
    provider, model = spec_parts(spec)
    base, key_var, _ = PROVIDERS[provider]
    headers = {}
    if key_var:
        key = os.environ.get(key_var)
        if not key:
            raise RuntimeError(f"no {key_var} in .env")
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}]}
    t0 = time.time()
    for attempt in range(6):
        r = httpx.post(f"{base}/chat/completions", headers=headers, json=body, timeout=240)
        if r.status_code == 429:
            text = r.text.lower()
            if "per day" in text or "(rpd)" in text or "(tpd)" in text:
                nums = re.search(r"limit \d+, used \d+, requested \d+", text)       # Groq's own accounting
                when = re.search(r"try again in ([\dhms.]+)", text)
                raise DailyLimit(f"{spec}: daily limit reached" + (f" ({nums.group(0)})" if nums else "")
                                 + (f", try again in {when.group(1)}" if when else ""))
            time.sleep(min(90.0, _wait_seconds(r) + 1)); continue
        if r.status_code == 403 and "FreeTierOnly" in r.text:     # Model Studio's "Free Quota Only": the quota is gone
            raise DailyLimit(f"{spec}: free quota used up (AllocationQuota.FreeTierOnly); calls stay refused until "
                             "the model's Free Quota Only switch is turned off (then they are billed)")
        if r.status_code >= 500:
            time.sleep(5 * (attempt + 1)); continue
        if r.status_code >= 400:
            raise RuntimeError(f"{spec}: HTTP {r.status_code}: {r.text[:300]}")
        j = r.json()
        u = j.get("usage") or {}
        return j["choices"][0]["message"].get("content") or "", {
            "model": spec, "ms": int((time.time() - t0) * 1000), "tokens_in": u.get("prompt_tokens"),
            "tokens_out": u.get("completion_tokens"), "tokens_thinking": None}
    raise RuntimeError(f"{spec}: unavailable after retries (last HTTP {r.status_code})")


def _json(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    body = m.group(1) if m else t[t.find("{"): t.rfind("}") + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:     # qwen3-vl-plus garbles a box now and then ('{"x0": 760,   " "`y0": 83, …}'):
        return json.loads(re.sub(r'"box"\s*:\s*\{[^{}]*\}', '"box": null', body))   # keep the answer, drop the box


def _box(b, w, h):
    """Their box → Gemini's [ymin, xmin, ymax, xmax] on 0-1000. Pixel boxes are scaled; nonsense becomes None."""
    try:
        if isinstance(b, dict):
            x0, y0, x1, y1 = (float(b[k]) for k in ("x0", "y0", "x1", "y1"))
        elif isinstance(b, (list, tuple)) and len(b) == 4:
            x0, y0, x1, y1 = (float(v) for v in b)
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if max(x0, x1) > 1000 or max(y0, y1) > 1000:                  # pixels, not 0-1000
        if max(x0, x1) > w * 1.05 or max(y0, y1) > h * 1.05:
            return None
        x0, x1, y0, y1 = x0 / w * 1000, x1 / w * 1000, y0 / h * 1000, y1 / h * 1000
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    return [int(y0), int(x0), int(y1), int(x1)]


def _text(v):
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return s or None


def _field(v, w, h):
    if isinstance(v, dict):
        out = {"value": _text(v.get("value")), "source_text": _text(v.get("source_text")),
               "box": _box(v.get("box"), w, h)}
        if "unsure" in v:
            out["unsure"] = bool(v.get("unsure"))
        return out if out["value"] is not None or out.get("unsure") else None
    s = _text(v)
    return {"value": s, "source_text": s, "box": None} if s else None


def _field_list(schema):
    props = schema["properties"]
    head = [f"- {n}: {p.get('description', '')}" for n, p in props.items() if n != "lines"]
    cols = props.get("lines", {}).get("items", {}).get("properties", {})
    return "\n".join(head), ", ".join(f"{c} ({p.get('description', '')})" for c, p in cols.items())


def extract_all(png_bytes, schema, spec):
    """Read the page against every field of the combined list. Returns (fields_all, meta), shaped like vlm.extract_all."""
    url, w, h = _image(png_bytes)
    heads, cols = _field_list(schema)
    prompt = (vlm.RULES + vlm.READ_ALL.replace("give box = [ymin, xmin, ymax, xmax] on a 0-1000 scale",
                                              "give box as {\"x0\", \"y0\", \"x1\", \"y1\"} on a 0-1000 scale of the "
                                              "page's width and height") +
              f"\n\nFIELDS (key: meaning):\n{heads}\n\nLINE ITEM COLUMNS: {cols}\n\n"
              "Answer with ONE JSON object: every field key above mapped to either null or "
              '{"value": "...", "source_text": "exactly as printed", "box": {"x0": 0, "y0": 0, "x1": 0, "y1": 0}}, '
              'plus "lines": a list of objects with the line item columns and "row_text" (the whole printed row). '
              "Use null for every field that is not printed on this page.")
    content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]
    text, meta = _post(spec, content, max_tokens=4096)
    raw = _json(text)
    out = {n: _field(raw.get(n), w, h) for n in schema["properties"] if n != "lines"}
    colnames = list(schema["properties"].get("lines", {}).get("items", {}).get("properties", {}))
    out["lines"] = [{c: _text(r.get(c)) for c in colnames} for r in raw.get("lines") or [] if isinstance(r, dict)]
    return out, meta


def second_look(png_bytes, asks, crops, spec):
    """Blind second look: field names, meanings and crops only (never the other reader's text or the first answer)."""
    _, _, max_images = PROVIDERS[spec_parts(spec)[0]]
    url, w, h = _image(png_bytes)
    crops = crops[:max_images - 1]
    prompt = (vlm.RULES + vlm.SECOND_LOOK + "\n".join(f"- {name}: {meaning}" for name, meaning in asks) +
              "\n\nThe first image is the page; the others are zoomed crops, in this order: " +
              (", ".join(name for name, _ in crops) or "none") + ".\nAnswer with ONE JSON object mapping each field "
              'above to {"value": "...", "source_text": "exactly as printed", "box": {"x0": 0, "y0": 0, "x1": 0, '
              '"y1": 0} (0-1000 of the page), "unsure": false}.')   # valid JSON: qwen3-vl-plus copies the example
    content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]
    for _, png in crops:
        content.append({"type": "image_url", "image_url": {"url": _image(png)[0]}})
    # answers are ~300 tokens; Groq seems to charge the whole allowance to the daily budget (the limit came after
    # 136K reported tokens of 200K on 2026-09-24), so ask for no more than needed
    text, meta = _post(spec, content, max_tokens=1024)
    raw = _json(text)
    return {name: _field(raw.get(name), w, h) for name, _ in asks}, meta


def store(png_bytes, spec):
    """S4, blind: the store the page's goods go to. The page only, never Satellite's store names. ({value,
    source_text, unsure}, meta)."""
    url, _, _ = _image(png_bytes)
    prompt = vlm.RULES + vlm.STORE + ('\nAnswer with ONE JSON object: {"value": "...", "source_text": "exactly as '
                                      'printed", "unsure": true or false}.')
    text, meta = _post(spec, [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}],
                       max_tokens=256)
    raw = _json(text)
    return {"value": _text(raw.get("value")), "source_text": _text(raw.get("source_text")),
            "unsure": bool(raw.get("unsure"))}, meta
