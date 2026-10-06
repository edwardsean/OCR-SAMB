"""Settings saved from the UI (2026-10-06; the mentor: the models for the image OCR, the text model and the teachers,
and their API keys, set on a Teknis screen, so the system's environment comes from there).

A row in staging.setting (schema/027) overrides the same name in .env; without one, the .env value stays. common/
config.py still reads .env (the only module that reads the environment); refresh() lays the saved values over it, and
every service calls refresh() as it works (a page ticket, a teacher round, an API request): the table is read again
at most every REFRESH_SECONDS, and modules that keep a copy of a setting (vf.AI_OCR …) are told through on_change.

API keys are kept as typed, like .env keeps them; a screen only ever shows their last 4 characters (masked)."""
import time

from common import config

REFRESH_SECONDS = 10

# what the screen can set: (name, what it is for, kind). kind: "model" = provider:model; "key" = an API key
MODELS = [
    ("VF_AI_OCR", "Image OCR: copies every line of the page image", "vision"),
    ("VF_AI_MAP", "Text model: picks each field's value from that copy", "text"),
    ("WIKI_TEACHER_MODEL", "Teacher: turns corrections into knowledge tips", "text"),
    ("TEACHER_MODEL", "Page-type teacher: improves Jev's descriptions from labels", "vision"),
    ("MATCH_MODEL", "Product matcher: pairs a customer's rows with SAMB's lines", "text"),
]
KEYS = [
    ("DASHSCOPE_API_KEY", "Model Studio (Alibaba)", "dashscope"),
    ("ZAI_API_KEY", "Z.ai (GLM)", "zai"),
    ("GROQ_API_KEY", "Groq", "groq"),
    ("OPENROUTER_API_KEY", "OpenRouter", "openrouter"),
    ("MISTRAL_API_KEY", "Mistral", "mistral"),
    ("GEMINI_API_KEY", "Google Gemini", None),
    ("TYPESAFE_API_KEY", "TypeSafe (Jev, page types)", None),
]
NAMES = {n for n, _, _ in MODELS} | {n for n, _, _ in KEYS}
DEFAULT_PROVIDER = {"WIKI_TEACHER_MODEL": "zai", "TEACHER_MODEL": "zai", "MATCH_MODEL": "zai"}   # a bare model name

BASE = {n: getattr(config, n) for n, _, _ in MODELS} | {n: config.API_KEYS.get(n, "") for n, _, _ in KEYS}
_state = {"checked": 0.0, "applied": {}}
_hooks = []


def on_change(fn):
    """fn() runs after saved settings change what config holds (a module re-reads its copy)."""
    _hooks.append(fn)
    return fn


def saved(c):
    """{name: row} of the settings saved from the UI."""
    return {r["name"]: dict(r) for r in c.execute("SELECT * FROM staging.setting") if r["name"] in NAMES}


def refresh(force=False):
    """Lay the saved settings over .env's (config). Reads the table at most every REFRESH_SECONDS; runs the
    on_change hooks only when what is in effect changes. Before migration 027, or with the database down, what is in
    effect stays. Returns True when something changed."""
    now = time.time()
    if not force and now - _state["checked"] < REFRESH_SECONDS:
        return False
    _state["checked"] = now
    try:
        from common import db
        with db.connect() as c:
            got = {k: r["value"] for k, r in saved(c).items()}
    except Exception:
        return False
    if got == _state["applied"]:
        return False
    for name in NAMES:
        value = got.get(name) or BASE[name]
        if name in config.API_KEYS:
            config.API_KEYS[name] = value
        if hasattr(config, name):
            setattr(config, name, value)
    _state["applied"] = got
    for fn in _hooks:
        fn()
    return True


def spec_of(name, value):
    """A model setting as provider:model (a teacher's bare model name is Z.ai's)."""
    v = (value or "").strip()
    if ":" not in v and name in DEFAULT_PROVIDER and v:
        return f"{DEFAULT_PROVIDER[name]}:{v}"
    return v


def check_model(name, value):
    """Why a model setting can't be saved, or None. provider:model with a known provider; the image OCR may also be
    "gemini"; a teacher's or the matcher's may be a bare Z.ai model name."""
    from common.models.openai_vlm import PROVIDERS
    v = (value or "").strip()
    if not v:
        return None                                   # empty: back to .env
    if name == "VF_AI_OCR" and v == "gemini":
        return None
    provider, _, model = spec_of(name, v).partition(":")
    if provider not in PROVIDERS or not model.strip():
        return f"write it as provider:model, with provider one of {', '.join(sorted(PROVIDERS))}"
    return None


def key_for(name, value):
    """The API-key setting a model setting needs (None: none, e.g. a local Ollama model)."""
    from common.models.openai_vlm import PROVIDERS
    v = spec_of(name, value)
    if v == "gemini":
        return "GEMINI_API_KEY"
    return (PROVIDERS.get(v.partition(":")[0]) or (None, None))[1]


def masked(value):
    v = (value or "").strip()
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 8 else "•" * len(v))


def view(c):
    """What the screen shows: each model and key with the value in effect, where it comes from (UI or .env), who
    saved it; keys only masked."""
    rows = saved(c)

    def one(name, secret):
        r = rows.get(name)
        value = r["value"] if r else BASE[name]
        return {"name": name, "value": masked(value) if secret else value, "set": bool(value),
                "from": "UI" if r else (".env" if BASE[name] else None),
                "by": r["updated_by"] if r else None, "at": r["updated_at"] if r else None,
                "env": (masked(BASE[name]) if secret else BASE[name]) or None}
    keys = [{**one(n, True), "what": what, "provider": p} for n, what, p in KEYS]
    have = {k["name"]: k["set"] for k in keys}
    models = []
    for n, what, kind in MODELS:
        m = one(n, False)
        need = key_for(n, m["value"]) if m["value"] else None
        models.append({**m, "what": what, "kind": kind, "key": need, "key_set": have.get(need, True) if need else True})
    return {"models": models, "api_keys": keys}


def save(c, name, value, by):
    """Save one setting (empty: remove it, back to .env). Raises ValueError with a reason for people."""
    if name not in NAMES:
        raise ValueError(f"{name} can't be set here")
    if not (by or "").strip():
        raise ValueError("say who you are")
    value = (value or "").strip()
    if name in {n for n, _, _ in MODELS}:
        why = check_model(name, value)
        if why:
            raise ValueError(why)
    if not value:
        c.execute("DELETE FROM staging.setting WHERE name=%s", (name,))
    else:
        c.execute("""INSERT INTO staging.setting (name, value, updated_by) VALUES (%s, %s, %s)
                     ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by,
                       updated_at=now()""", (name, value, by.strip()))
    _state["checked"] = 0.0                           # this process sees it at once; the others within seconds


def check_key(name):
    """Does the provider accept the key in effect? Asks its model list (GET /models: no tokens). (ok, what it says)."""
    import httpx
    from common.models.openai_vlm import PROVIDERS
    refresh(force=True)
    key = config.API_KEYS.get(name, "")
    if not key:
        return False, "no key set"
    provider = next((p for n, _, p in KEYS if n == name), None)
    if name == "GEMINI_API_KEY":
        url, headers = f"{config.GEMINI_BASE_URL}/models?key={key}", {}
    elif provider in PROVIDERS:
        url, headers = f"{PROVIDERS[provider][0]}/models", {"Authorization": f"Bearer {key}"}
    else:
        return None, "this provider can't be checked from here"
    try:
        r = httpx.get(url, headers=headers, timeout=20)
    except Exception as e:
        return False, f"can't reach the provider ({type(e).__name__})"
    if r.status_code in (401, 403):
        return False, "the provider refused this key"
    if r.status_code >= 400:
        return None, f"the provider answered {r.status_code}: can't tell from here"
    n = len((r.json() or {}).get("data") or (r.json() or {}).get("models") or [])
    return True, f"accepted ({n} models available)" if n else "accepted"
