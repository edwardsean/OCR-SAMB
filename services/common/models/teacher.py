"""The teacher: the vision model, asked to teach Jev (the user, 2026-10-07: the teachers use the vision and text models).

It looks at a page a person labelled, together with the label, what the AI OCR read, what Jev answered and Jev's
whole context, explains the label, and proposes ONE change to Jev's context (worker/lesson.py). It is never shown
an exam-pile label; lesson.py refuses those before calling it.

Any OpenAI-compatible endpoint: POST <endpoint>/chat/completions with the row's key, the image as a base64 data URL.
The answer is parsed strictly (and asked once more if it isn't JSON); one call at a time, backing off on 429 and 5xx.
ask_text is the same for a text-only task (the knowledge teacher, the product matcher: the text model).

A model is provider:model; its endpoint and key are the vision or text row on the Teknis screen "Model & kunci API"
(common/settings.py endpoint). Until 2026-10-07 the teachers were Z.ai's free GLM models, from a different company
than the AI OCR; a bare model name is still read as Z.ai's.
"""
import base64
import json
import re
import time

import httpx

from common import config, settings

MODEL = config.TEACHER_MODEL


@settings.on_change
def _model_changed():
    global MODEL
    MODEL = config.TEACHER_MODEL


def provider_of(spec):
    """'zai' for a bare model name, else the provider of provider:model."""
    p, sep, _ = (spec or "").partition(":")
    return p if sep else "zai"


def _where(spec):
    """(chat URL, API key, model name) for a model (common/settings.py endpoint: its row's, else its provider's)."""
    base, key, name = settings.endpoint(spec)
    return base + "/chat/completions", key, name


def _post(messages, model=None, role="TEACHER_MODEL"):
    """model: another model for a text-only task (the knowledge teacher, the product matching: the text model);
    role: which job asks, for the error message."""
    url, key, name = _where(model or MODEL)
    if not key and settings.needs_key(url):
        raise RuntimeError(f"no API key for {role} ({model or MODEL}): set it on Teknis → Model & kunci API")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = time.time()
    for attempt in range(5):
        r = httpx.post(url, headers=headers,
                       json={"model": name, "messages": messages, "temperature": 0.2}, timeout=240)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(60, 5 * 2 ** attempt)); continue
        r.raise_for_status()
        j = r.json()
        return j["choices"][0]["message"]["content"], {"model": j.get("model", name),
                                                       "ms": int((time.time() - t0) * 1000), **(j.get("usage") or {})}
    raise RuntimeError(f"{provider_of(model or MODEL)} unavailable (HTTP {r.status_code}) after retries")


def ask_text(prompt, model, role="WIKI_TEACHER_MODEL"):
    """(parsed JSON answer, meta) from a text model."""
    messages = [{"role": "user", "content": prompt}]
    text, meta = _post(messages, model, role)
    try:
        return parse(text), meta
    except ValueError:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Answer again with ONLY the JSON object, nothing else."}]
        text, meta = _post(messages, model, role)
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
