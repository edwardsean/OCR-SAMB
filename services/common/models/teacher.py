"""The teacher: a second vision model, from a different company than the AI OCR (Z.ai GLM-4.6V-Flash, free).

It looks at a page a person labelled, together with the label, what the AI OCR read, what Jev answered and Jev's
whole context, explains the label, and proposes ONE change to Jev's context (worker/lesson.py). It is never shown
an exam-pile label; lesson.py refuses those before calling it.

Z.ai: POST https://api.z.ai/api/paas/v4/chat/completions, Bearer ZAI_API_KEY. Images up to 5 MB and 6000 px, sent
as a base64 data URL. Vision models have no JSON mode, so the answer is parsed strictly (and asked once more if it
isn't JSON). Rate limits aren't published: one call at a time, backing off on 429 and 5xx.
"""
import base64
import json
import re
import time

import httpx

from common import config

URL = config.PROVIDER_URLS["zai"] + "/chat/completions"
MODEL = config.TEACHER_MODEL


def _post(messages, model=None):
    """model: another Z.ai model for a text-only task (the product matching, grouper/matching.py)."""
    key = config.ZAI_API_KEY
    if not key:
        raise RuntimeError("no ZAI_API_KEY: add a free Z.ai key to .env to run the teacher")
    model = model or MODEL
    t0 = time.time()
    for attempt in range(5):
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"},
                       json={"model": model, "messages": messages, "temperature": 0.2}, timeout=240)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(60, 5 * 2 ** attempt)); continue
        r.raise_for_status()
        j = r.json()
        return j["choices"][0]["message"]["content"], {"model": j.get("model", model),
                                                       "ms": int((time.time() - t0) * 1000), **(j.get("usage") or {})}
    raise RuntimeError(f"Z.ai unavailable (HTTP {r.status_code}) after retries")


def ask_text(prompt, model):
    """(parsed JSON answer, meta) from a text model."""
    messages = [{"role": "user", "content": prompt}]
    text, meta = _post(messages, model)
    try:
        return parse(text), meta
    except ValueError:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Answer again with ONLY the JSON object, nothing else."}]
        text, meta = _post(messages, model)
        return parse(text), meta


def parse(text):
    """The JSON object in the answer: fenced or bare. Raises ValueError if there is none."""
    t = text if isinstance(text, str) else json.dumps(text)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    body = m.group(1) if m else t[t.find("{"): t.rfind("}") + 1]
    if not body:
        raise ValueError("no JSON object in the answer")
    return json.loads(body)


def ask(png_bytes, prompt):
    """(parsed JSON answer, meta)."""
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png_bytes).decode()}}
    messages = [{"role": "user", "content": [image, {"type": "text", "text": prompt}]}]
    text, meta = _post(messages)
    try:
        return parse(text), meta
    except ValueError:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Answer again with ONLY the JSON object, nothing else."}]
        text, meta2 = _post(messages)
        return parse(text), {**meta, "retried": True, "ms": meta["ms"] + meta2["ms"]}
