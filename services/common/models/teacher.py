"""The teacher: a second vision model, from a different company than the AI OCR (Z.ai GLM-4.6V-Flash, free).

It looks at a page a person labelled, together with the label, what the AI OCR read, what Jev answered and Jev's
whole context, explains the label, and proposes ONE change to Jev's context (worker/lesson.py). It is never shown
an exam-pile label; lesson.py refuses those before calling it.

Z.ai: POST https://api.z.ai/api/paas/v4/chat/completions, Bearer ZAI_API_KEY. Images up to 5 MB and 6000 px, sent
as a base64 data URL. Vision models have no JSON mode, so the answer is parsed strictly (and asked once more if it
isn't JSON). Rate limits aren't published: one call at a time, backing off on 429 and 5xx.

A model is a Z.ai model name, or provider:model for another OpenAI-compatible provider (2026-10-06: the teachers'
models are set on the Teknis screen "Model & kunci API", common/settings.py).
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
    """(chat URL, API key setting, model name) for a model setting."""
    from common.models.openai_vlm import PROVIDERS
    provider = provider_of(spec)
    name = spec.partition(":")[2] if ":" in spec else spec
    if provider not in PROVIDERS:
        raise RuntimeError(f"{spec!r}: unknown provider (one of {', '.join(sorted(PROVIDERS))})")
    base, key_var, _ = PROVIDERS[provider]
    return base + "/chat/completions", key_var, name


def _post(messages, model=None):
    """model: another model for a text-only task (the knowledge teacher, the product matching)."""
    url, key_var, name = _where(model or MODEL)
    key = config.API_KEYS.get(key_var) if key_var else ""
    if key_var and not key:
        raise RuntimeError(f"no {key_var}: set it on Teknis → Model & kunci API, or in .env")
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
