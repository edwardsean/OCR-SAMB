"""The models and their API keys, set ONLY on the Teknis screen "Model & kunci API" (staging.setting, schema/027).

Three rows, one endpoint and one API key each (the user, 2026-10-07: "one for the vision model, one for the text
model", then a classification model in place of TypeSafe's Jev):
  vision    every call that sends a page image: the AI OCR's copy of the page, the look-again, the store question, a
            handwriting tip's region, and the page-type teacher (Jev's context, worker/lesson.py);
  text      every call that sends only text: filling in the fields from the copy, applying learned tips (pass B), the
            knowledge teacher (worker/learn.py) and the product matcher (grouper/matching.py);
  classify  each page's type: an instruct (non-thinking) model answers one option number, and the probability of
            each option comes from the endpoint's token log-probabilities (worker/classify.py llm_ask). It replaced
            Jev after matching it on b-c80bbbde4d (15 right, 0 wrong, the same 1 unsure; qwen-flash and
            qwen3-235b-a22b-instruct-2507 alike).
A row is an OpenAI-compatible endpoint (base URL), its API key, and a model from that endpoint's model list: the
screen lists them and suggests one it knows (RECOMMENDED), a person confirms.

Nothing falls back to .env (the user, 2026-10-07: "there should not be any env fallback, it just errors if there is
no env inputted in the env tab"). A row not set is missing(): no call is made, and a page waits in the waiting room
saying what to set (worker/vf.py NotSet), then goes on by itself once it is set.

The rest of the system names a model provider:model (dashscope:qwen3-vl-plus): a reading is versioned by it, the
daily budget counts by it, the call log records it (worker/vf.py). A row on a known provider's address
(config.PROVIDER_URLS) keeps that provider's short name, so pages read before keep their versions; another endpoint
is named by its host.

_apply() lays the rows into config (VF_AI_OCR, VF_AI_MAP, TEACHER_MODEL, WIKI_TEACHER_MODEL, MATCH_MODEL,
CLASSIFY_MODEL); every service calls refresh() as it works (a page ticket, a teacher round, an API request): the table
is read again at most every REFRESH_SECONDS, and modules that keep a copy of a model (vf.AI_OCR …) are told through
on_change. Keys are kept as typed; a screen only ever shows their last 4 characters (masked)."""
import time
from urllib.parse import urlparse

from common import config

REFRESH_SECONDS = 10
WHERE = "Teknis → Model & kunci API"

# kind -> (title, what it does, the jobs that use it)
ROWS = {
    "vision": ("Vision model", "reads the page image", [
        "copies every page (the AI OCR)",
        "looks again at a value nothing printed backs",
        "asks which store the goods go to",
        "reads the region a handwriting or stamp tip points to",
        "page-type teacher: improves the page-type descriptions the classification model reads (Konteks Jev)"]),
    "text": ("Text model", "works on the copy of the page, as text", [
        "fills in the fields from the copy, twice",
        "applies learned tips to a page",
        "knowledge teacher: turns corrections into tips (Pengetahuan AI)",
        "product matcher: proposes which SAMB line a customer's row is"]),
    "classify": ("Classification model", "decides what kind of document a page is", [
        "decides each page's type (FP, PO, TTG …) from its reading: an instruct, non-thinking model that answers one "
        "number; how sure it is comes from the endpoint, not from the model's words",
        "the page-type teacher's test: asks it again about the labelled pages before a better description is kept"]),
}
FIELDS = {k: (f"{k.upper()}_BASE_URL", f"{k.upper()}_API_KEY", f"{k.upper()}_MODEL") for k in ROWS}
NAMES = {n for f in FIELDS.values() for n in f}

# models the system has been measured with, best first: the screen pre-selects the first one an endpoint lists
RECOMMENDED = {
    "vision": ["qwen3-vl-plus", "qwen3-vl-flash", "qwen-vl-max", "glm-4.6v", "glm-4.6v-flash", "gemini-3.8-flash",
               "qwen/qwen3.8-27b"],
    "text": ["qwen3-235b-a22b-instruct-2507", "qwen-plus", "qwen-flash", "glm-4.7", "glm-4.7-flash",
             "gemini-3.8-flash", "qwen/qwen3.8-27b"],
    "classify": ["qwen-flash", "qwen-turbo", "qwen-plus", "qwen3-235b-a22b-instruct-2507"],   # instruct only
}
LOCAL = ("localhost", "127.0.0.1", "host.docker.internal", "ollama")      # endpoints that may need no key

EFFECTIVE = {}                                      # kind -> the row in effect (_resolve)
_state = {"checked": 0.0, "effective": None, "saved": {}}
_hooks = []


def on_change(fn):
    """fn() runs after saved settings change what config holds (a module re-reads its copy)."""
    _hooks.append(fn)
    return fn


def norm(url):
    """An endpoint as stored: no spaces, no trailing slash, no /chat/completions (people paste either)."""
    u = (url or "").strip().rstrip("/")
    return u.removesuffix("/chat/completions").rstrip("/")


def provider_at(url):
    """The known provider serving this address, or None."""
    u = norm(url)
    return next((p for p, base in config.PROVIDER_URLS.items() if u and u == norm(base)), None)


def ident(url, model):
    """The model as the rest of the system names it: provider:model, the host for an endpoint no provider is known
    at ("" when the row has no endpoint or no model)."""
    if not model or not norm(url):
        return ""
    p = provider_at(url) or (urlparse(norm(url)).netloc or norm(url)).replace(":", "-")
    return f"{p}:{model}"


def needs_key(url):
    host = (urlparse(norm(url)).hostname or "").lower()
    return bool(host) and host not in LOCAL and "." in host


def _resolve(kind, got):
    """The row in effect, from the saved settings `got` only: {kind, url, key, model, ident}."""
    url_n, key_n, model_n = FIELDS[kind]
    url, key, model = norm(got.get(url_n)), (got.get(key_n) or "").strip(), (got.get(model_n) or "").strip()
    return {"kind": kind, "url": url, "key": key, "model": model, "ident": ident(url, model)}


def _apply(got):
    """Lay the saved settings (`got`: name -> value) into config. True when what is in effect changed."""
    rows = {k: _resolve(k, got) for k in ROWS}
    vision, text = rows["vision"]["ident"], rows["text"]["ident"]
    config.VF_AI_OCR = config.TEACHER_MODEL = vision         # every call that sends a page image
    config.VF_AI_MAP = config.WIKI_TEACHER_MODEL = config.MATCH_MODEL = text   # every call that sends only text
    config.CLASSIFY_MODEL = rows["classify"]["ident"]        # each page's type
    EFFECTIVE.clear()
    EFFECTIVE.update(rows)
    _state["saved"] = dict(got)
    now = sorted((k, r["url"], r["key"], r["model"]) for k, r in rows.items())
    changed = now != _state["effective"]
    _state["effective"] = now
    return changed


def saved(c):
    """{name: row} of the settings saved from the UI."""
    return {r["name"]: dict(r) for r in c.execute("SELECT * FROM staging.setting") if r["name"] in NAMES}


def refresh(force=False):
    """Read the saved settings into config. Reads the table at most every REFRESH_SECONDS; runs the on_change hooks
    only when what is in effect changes. Before migration 027, or with the database down, what is in effect stays.
    Returns True when something changed."""
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
    return _lay(got)


def _lay(got):
    """_apply, then tell the modules that keep a copy of a model (on_change) when what is in effect changed."""
    if not _apply(got):
        return False
    for fn in _hooks:
        fn()
    return True


_apply({})                                          # nothing set until the table is read
refresh(force=True)                                 # read it now when the database answers: modules copy config next


def missing():
    """What isn't set, for people ([] when everything is): no call is made without it."""
    return [f"the {ROWS[k][0].lower()}" for k, r in EFFECTIVE.items()
            if not r["ident"] or (not r["key"] and needs_key(r["url"]))]


def row(kind):
    """The row in effect for "vision", "text" or "classify" (its url, key, model, ident)."""
    return EFFECTIVE.get(kind) or {"kind": kind, "url": "", "key": "", "model": "", "ident": ""}


def endpoint(spec):
    """(base URL, API key, model) to call for a model as the system names it: the vision, text or classification
    row (the first whose model it is)."""
    for row in EFFECTIVE.values():
        if row["ident"] and row["ident"] == spec:
            return row["url"], row["key"], row["model"]
    raise RuntimeError(f"no model {spec!r}: set the vision and text models on {WHERE}" if spec else
                       f"the model isn't set: set the vision and text models on {WHERE}")


def key(name):
    """The API key a row calls with ("vision", "text", "classify"). "" when there is none."""
    return EFFECTIVE[name]["key"] if name in EFFECTIVE else ""


def masked(value):
    v = (value or "").strip()
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 8 else "•" * len(v))


# ---------------------------------------------------------------------------------------------------- the screen

def view(c):
    """The screen: the three rows, each with who saved it; keys only masked."""
    rows = saved(c)
    got = {k: r["value"] for k, r in rows.items()}
    out = []
    for kind, (title, does, jobs) in ROWS.items():
        r = _resolve(kind, got)
        url_n, key_n, model_n = FIELDS[kind]
        who = rows.get(model_n) or rows.get(url_n)
        out.append({"kind": kind, "title": title, "does": does, "jobs": jobs, "url": r["url"], "model": r["model"],
                    "ident": r["ident"], "key": masked(r["key"]), "has_key": bool(r["key"]),
                    "needs_key": needs_key(r["url"]), "set": bool(r["ident"]) and (bool(r["key"]) or not needs_key(r["url"])),
                    "by": who and who["updated_by"], "at": who and who["updated_at"],
                    "key_by": rows.get(key_n) and rows[key_n]["updated_by"]})
    return {"rows": out}


def _put(c, name, value, by):
    c.execute("""INSERT INTO staging.setting (name, value, updated_by) VALUES (%s, %s, %s)
                 ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by,
                   updated_at=now()""", (name, value, by))


def save_row(c, kind, url, model, api_key, by):
    """Save a row: its endpoint, its model and (when typed) its key. An empty key keeps the saved one, but only on
    the same endpoint: a new endpoint needs its own (a local one may have none). Raises ValueError for people."""
    if kind not in ROWS:
        raise ValueError(f"{kind} isn't a model row")
    if not (by or "").strip():
        raise ValueError("say who you are")
    url, model, api_key = norm(url), (model or "").strip(), (api_key or "").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("the endpoint must start with https:// (or http:// for a model on this network)")
    if not model:
        raise ValueError("choose a model from the endpoint's list")
    current = _resolve(kind, {k: r["value"] for k, r in saved(c).items()})
    if not api_key and needs_key(url) and (url != current["url"] or not current["key"]):
        raise ValueError("a new endpoint needs its API key")
    url_n, key_n, model_n = FIELDS[kind]
    _put(c, url_n, url, by.strip())
    _put(c, model_n, model, by.strip())
    if api_key:
        _put(c, key_n, api_key, by.strip())
    elif url != current["url"]:                              # a keyless local endpoint: no older key goes along
        c.execute("DELETE FROM staging.setting WHERE name=%s", (key_n,))
    _state["checked"] = 0.0                                  # this process sees it at once; the others within seconds


def list_models(url, api_key):
    """The endpoint's model list (GET /models: no tokens). (ids, None), or ([], why)."""
    import httpx
    u = norm(url)
    if not u.startswith(("https://", "http://")):
        return [], "the endpoint must start with https:// (or http:// for a model on this network)"
    try:
        r = httpx.get(f"{u}/models", headers={"Authorization": f"Bearer {api_key}"} if api_key else {}, timeout=20)
    except Exception as e:
        return [], f"can't reach the endpoint ({type(e).__name__})"
    if r.status_code in (401, 403):
        return [], "the endpoint refused this key" if api_key else "the endpoint needs an API key"
    if r.status_code >= 400:
        return [], f"the endpoint answered {r.status_code} to GET /models: is this its base URL (ending /v1 or similar)?"
    try:
        body = r.json() or {}
    except ValueError:
        return [], "the endpoint's model list isn't JSON: is this its base URL?"
    items = (body.get("data") or body.get("models") or []) if isinstance(body, dict) else body
    ids = {str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in items if isinstance(m, dict)}
    return sorted(i for i in ids if i), None


def recommend(kind, ids):
    """The model to pre-select: the first of RECOMMENDED[kind] the endpoint lists (also as org/model), else None."""
    by_tail = {i.split("/")[-1].lower(): i for i in ids}
    for want in RECOMMENDED.get(kind, []):
        if want in ids:
            return want
        if want.split("/")[-1].lower() in by_tail:
            return by_tail[want.split("/")[-1].lower()]
    return None


def _probe_png():
    """A small image with a number printed on it, to see that a model really reads images."""
    import io
    from PIL import Image, ImageDraw
    im = Image.new("L", (60, 16), 255)
    ImageDraw.Draw(im).text((4, 2), "SAMB 4821", fill=0)
    im = im.resize((360, 96), Image.NEAREST)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_row(kind):
    """Does the row work? Its model list (the key and the model), then one tiny call: the vision model reads a number
    from an image, the text model adds two numbers. (True / False, what it says)."""
    import base64
    import httpx
    refresh(force=True)
    row = EFFECTIVE.get(kind)
    if not row or not row["ident"]:
        return False, "not set: enter its endpoint and choose a model"
    if not row["key"] and needs_key(row["url"]):
        return False, "no API key"
    ids, why = list_models(row["url"], row["key"])
    if why:
        return False, why
    tails = {i.split("/")[-1] for i in ids}
    if ids and row["model"] not in ids and row["model"].split("/")[-1] not in tails:
        return False, f"the key works, but {row['model']} isn't in this endpoint's model list"
    extra = {}
    if kind == "vision":
        url = "data:image/png;base64," + base64.b64encode(_probe_png()).decode()
        content = [{"type": "text", "text": "Which number is printed in this image? Answer with the number only."},
                   {"type": "image_url", "image_url": {"url": url}}]
        want = "4821"
    elif kind == "classify":                 # one option number, with the endpoint's probability of each option
        content = ("Which kind of document is a page titled GOODS RECEIVE NOTE, listing the quantities received?\n"
                   "1. a sales invoice\n2. a goods receipt\n3. a purchase order\nAnswer with the number only.")
        want, extra = "2", {"max_tokens": 1, "logprobs": True, "top_logprobs": 5}
    else:
        content, want = "What is 17 + 25? Answer with the number only.", "42"
    headers = {"Authorization": f"Bearer {row['key']}"} if row["key"] else {}
    t0 = time.time()
    try:
        r = httpx.post(f"{row['url']}/chat/completions", headers=headers, timeout=120,
                       json={"model": row["model"], "max_tokens": 200, "temperature": 0,
                             "messages": [{"role": "user", "content": content}], **extra})
    except Exception as e:
        return False, f"the call failed ({type(e).__name__})"
    if r.status_code >= 400:
        return False, f"the call failed: HTTP {r.status_code} {r.text[:160]}"
    choice = (r.json().get("choices") or [{}])[0]
    answer = (choice.get("message") or {}).get("content") or ""
    if want not in str(answer):
        return False, (f"{row['model']} answered {str(answer)[:60]!r}: "
                       + {"vision": "it doesn't seem to read images", "classify": "not the expected 2"}.get(kind,
                                                                                                    "not the expected 42"))
    if kind == "vision":
        return True, f"{row['model']} works and reads images"
    if kind == "classify":
        if not ((choice.get("logprobs") or {}).get("content")):
            return False, (f"{row['model']} answers, but this endpoint gives no token probabilities (logprobs): "
                           "the classification needs them to know when it isn't sure")
        return True, f"{row['model']} works, gives probabilities, and answered in {time.time() - t0:.1f} s"
    return True, f"{row['model']} works"
