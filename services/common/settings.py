"""Models and their API keys, set from the UI (2026-10-06; the mentor: each model the system uses, and the API key it
calls with, set on a Teknis screen, so the system's environment comes from there).

Each model has its own row: the model (provider:model) and its own API key. A model without a key of its own calls
with its provider's key from .env (DASHSCOPE_API_KEY …), as before. A row in staging.setting (schema/027) overrides
the same name in .env; without one, the .env value stays. common/config.py still reads .env (the only module that
reads the environment); refresh() lays the saved values over it, and every service calls refresh() as it works (a
page ticket, a teacher round, an API request): the table is read again at most every REFRESH_SECONDS, and modules
that keep a copy of a model (vf.AI_OCR …) are told through on_change.

Keys are kept as typed, like .env keeps them; a screen only ever shows their last 4 characters (masked)."""
import time

from common import config

REFRESH_SECONDS = 10

# one row per model: (model setting, its own key setting, what it is, what it does)
MODELS = [
    ("VF_AI_OCR", "VF_AI_OCR_API_KEY", "Image OCR", "copies every line of the page image"),
    ("VF_AI_MAP", "VF_AI_MAP_API_KEY", "Text model", "picks each field's value from that copy"),
    ("JEV_MODEL", "TYPESAFE_API_KEY", "Page-type classifier (Jev)", "decides what kind of document a page is"),
    ("WIKI_TEACHER_MODEL", "WIKI_TEACHER_API_KEY", "Knowledge teacher", "turns corrections into tips (Pengetahuan AI)"),
    ("TEACHER_MODEL", "TEACHER_API_KEY", "Page-type teacher", "improves how Jev tells page types apart (Konteks Jev)"),
    ("MATCH_MODEL", "MATCH_API_KEY", "Product matcher",
     "proposes which SAMB line a customer's row is (run by hand: python -m grouper.matching propose)"),
]
KEY_OF = {m: k for m, k, _, _ in MODELS}
NAMES = set(KEY_OF) | set(KEY_OF.values())
DEFAULT_PROVIDER = {"WIKI_TEACHER_MODEL": "zai", "TEACHER_MODEL": "zai", "MATCH_MODEL": "zai"}   # a bare model name
FIXED = {"JEV_MODEL": "TypeSafe"}                     # a model only one provider serves (no provider:model)


def _base(name):
    if name in config.MODEL_KEYS:
        return config.MODEL_KEYS[name]
    if name in config.API_KEYS:
        return config.API_KEYS[name]
    return getattr(config, name)


BASE = {n: _base(n) for n in NAMES}
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
        if name in config.MODEL_KEYS:
            config.MODEL_KEYS[name] = value
        elif name in config.API_KEYS:
            config.API_KEYS[name] = value
        if hasattr(config, name):
            setattr(config, name, value)
    _state["applied"] = got
    for fn in _hooks:
        fn()
    return True


def spec_of(name, value):
    """A model setting as provider:model (a teacher's or the matcher's bare model name is Z.ai's)."""
    v = (value or "").strip()
    if ":" not in v and name in DEFAULT_PROVIDER and v:
        return f"{DEFAULT_PROVIDER[name]}:{v}"
    return v


def check_model(name, value):
    """Why a model setting can't be saved, or None. provider:model with a known provider; the image OCR may also be
    "gemini"; a teacher's or the matcher's may be a bare Z.ai model name; Jev's is TypeSafe's model name."""
    from common.models.openai_vlm import PROVIDERS
    v = (value or "").strip()
    if not v or name in FIXED or (name == "VF_AI_OCR" and v == "gemini"):
        return None
    provider, _, model = spec_of(name, v).partition(":")
    if provider not in PROVIDERS or not model.strip():
        return f"write it as provider:model, with provider one of {', '.join(sorted(PROVIDERS))}"
    return None


def provider_key(name, value):
    """The provider's key setting in .env a model falls back to (None: none, e.g. Jev or a local Ollama model)."""
    from common.models.openai_vlm import PROVIDERS
    if name in FIXED:
        return None
    v = spec_of(name, value)
    if v == "gemini":
        return "GEMINI_API_KEY"
    return (PROVIDERS.get(v.partition(":")[0]) or (None, None))[1]


def _own(name):
    k = KEY_OF[name]
    return config.MODEL_KEYS.get(k) if k in config.MODEL_KEYS else config.API_KEYS.get(k, "")


def key(name, spec=None):
    """The API key the model `name` calls with: its own, else its provider's (.env). "" when there is none."""
    own = _own(name)
    if own:
        return own
    var = provider_key(name, spec if spec is not None else getattr(config, name))
    return config.API_KEYS.get(var, "") if var else ""


def masked(value):
    v = (value or "").strip()
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 8 else "•" * len(v))


def view(c):
    """One row per model, for the screen: the model in effect and where it comes from (UI or .env); its own key
    (masked) and where it comes from, else the provider key it falls back to."""
    rows = saved(c)
    out = []
    for name, key_name, what, does in MODELS:
        m, k = rows.get(name), rows.get(key_name)
        value = m["value"] if m else BASE[name]
        own = k["value"] if k else BASE[key_name]
        shared = provider_key(name, value) if value else None
        shared_val = config.API_KEYS.get(shared, "") if shared else ""
        out.append({
            "name": name, "what": what, "does": does, "value": value, "fixed": FIXED.get(name),
            "from": "UI" if m else (".env" if BASE[name] else None), "by": m and m["updated_by"],
            "at": m and m["updated_at"], "env": BASE[name] or None,
            "key_name": key_name, "key": masked(own),
            "key_from": "UI" if k else (".env" if BASE[key_name] else None),
            "key_by": k and k["updated_by"], "key_at": k and k["updated_at"],
            "shared": shared, "shared_key": masked(shared_val),
            "needs_key": name in FIXED or bool(shared),
            "has_key": bool(own or shared_val),
        })
    return out


def save(c, name, value, by):
    """Save one setting (empty: remove it, back to .env). Raises ValueError with a reason for people."""
    if name not in NAMES:
        raise ValueError(f"{name} can't be set here")
    if not (by or "").strip():
        raise ValueError("say who you are")
    value = (value or "").strip()
    if name in KEY_OF:
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
    """Does the model's provider accept the key it calls with, and does that account have the model? Asks the
    provider's model list (GET /models: no tokens). (True / False / None when it can't tell, what it says)."""
    import httpx
    from common.models.openai_vlm import PROVIDERS
    refresh(force=True)
    if name in FIXED:
        return None, f"{FIXED[name]} can't be checked from here"
    spec = spec_of(name, getattr(config, name))
    k = key(name, spec)
    if not k:
        return False, "no API key for this model"
    if spec == "gemini":
        url, headers, model = f"{config.GEMINI_BASE_URL}/models?key={k}", {}, config.GEMINI_MODEL
    elif spec.partition(":")[0] in PROVIDERS:
        provider, _, model = spec.partition(":")
        url, headers = f"{PROVIDERS[provider][0]}/models", {"Authorization": f"Bearer {k}"}
    else:
        return None, "this model's provider can't be checked from here"
    try:
        r = httpx.get(url, headers=headers, timeout=20)
    except Exception as e:
        return False, f"can't reach the provider ({type(e).__name__})"
    if r.status_code in (401, 403):
        return False, "the provider refused this key"
    if r.status_code >= 400:
        return None, f"the provider answered {r.status_code}: can't tell from here"
    body = r.json() or {}
    ids = {str(m.get("id") or m.get("name") or "").split("/")[-1]
           for m in (body.get("data") or body.get("models") or []) if isinstance(m, dict)}
    if ids and model.split("/")[-1] not in ids:
        return False, f"the key works, but {model} isn't in this account's model list"
    return True, f"the key works and {model} is available" if ids else "the key works"
