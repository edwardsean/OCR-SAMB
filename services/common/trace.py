"""What happened, when, and how long it took (schema/028-trace.sql; the user, 2026-10-08: "we need monitoring and
observability for developers … so that the dev can see what happened at some time, and trace all of it for a single
file or single batch").

  with trace.span("page", batch=bid, page=n):     a piece of work: written 'running' at once, closed with its status
      trace.stage("read")                          and duration. stage() ends the stage before and starts this one,
      …                                            inside the current span, so a long function needs no new nesting.
      trace.stage("classify")
      trace.note(outcome="clear")                  adds to the current span's detail
  trace.event("page.queued", batch=…, page=…)      a moment (status info unless said)

Spans nest by the thread they run in: a span or event started inside another (in the same thread) is its child and
takes its batch, page and order unless it names its own. Tracing never stops the work: a write that fails (the table
is missing, the database is down) is skipped and tracing pauses for a minute. Each thread keeps one connection.

Tests never write here: tests/conftest.py mutes the test process (MUTED) and sends `X-Trace: off` with every call to
the running API, which mutes that request (muted(), api/app.py's middleware). They run on the live database.
"""
import contextvars
import json
import socket
import threading
import time
from contextlib import contextmanager

import psycopg
from psycopg.types.json import Json

from common import config

HOST = socket.gethostname()[:6]
SERVICE = f"python@{HOST}"
PAUSE_S = 60
_current = contextvars.ContextVar("trace_span", default=None)
_muted = contextvars.ContextVar("trace_muted", default=False)
MUTED = False                          # the whole process (the test suite)
_local = threading.local()
_off_until = 0.0


def named(service):
    """Which service this process is (health.serve names every service): written on each row with its container."""
    global SERVICE
    SERVICE = f"{service}@{HOST}"


def _conn():
    c = getattr(_local, "conn", None)
    if c is None or c.closed:
        c = _local.conn = psycopg.connect(config.required("DATABASE_URL"), autocommit=True, connect_timeout=3)
    return c


def _write(sql, args, many=False):
    """One statement on this thread's connection; None (and a minute's pause) when it can't be written."""
    global _off_until
    if MUTED or _muted.get() or time.time() < _off_until:
        return None
    try:
        c = _conn()
        if many:
            with c.cursor() as cur:
                cur.executemany(sql, args)
            return True
        r = c.execute(sql, args)
        return r.fetchone()[0] if r.description else True
    except Exception as e:
        _off_until = time.time() + PAUSE_S
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
        print(f"trace: not written ({type(e).__name__}: {str(e)[:160]}); paused for {PAUSE_S} s", flush=True)
        return None


def _json(d):
    return Json(d or None, dumps=lambda o: json.dumps(o, default=str, ensure_ascii=False)) if d else None


def _clean(d):
    return {k: v for k, v in (d or {}).items() if v is not None}


class Span:
    def __init__(self, kind, batch, page, sor, who, parent, detail):
        self.kind, self.batch, self.page, self.sor, self.who, self.parent = kind, batch, page, sor, who, parent
        self.detail, self.status, self.error = _clean(detail), None, None
        self.t0, self.id, self.stage = time.monotonic(), None, None

    def note(self, **kw):
        self.detail.update(_clean(kw))

    def set(self, status, error=None):
        """Say how it ended (ok · fail · wait · skip); unset, it is ok, or fail when an exception left it."""
        self.status = status
        if error:
            self.error = str(error)[:1000]


def _open(kind, batch=None, page=None, sor=None, who=None, detail=None, parent=None):
    par = _current.get() if parent is None else None
    s = Span(kind, batch if batch is not None else par and par.batch, page if page is not None else par and par.page,
             sor if sor is not None else par and par.sor, who, parent or (par.id if par else None), detail)
    s.id = _write("""INSERT INTO staging.trace (status, kind, service, batch_id, page_no, sor_no, parent, who, detail)
                     VALUES ('running', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                  (kind, SERVICE, s.batch, s.page, s.sor, s.parent, who, _json(s.detail)))
    return s


def _close(s, status=None, error=None):
    if s.stage:
        _close(s.stage, "fail" if (status or s.status) == "fail" else None)
        s.stage = None
    status = status or s.status or "ok"
    error = error or s.error
    if s.id:
        _write("""UPDATE staging.trace SET status=%s, ended_at=clock_timestamp(), ms=%s, detail=%s, error=%s
                  WHERE id=%s""", (status, int((time.monotonic() - s.t0) * 1000), _json(s.detail), error, s.id))


@contextmanager
def span(kind, batch=None, page=None, sor=None, who=None, **detail):
    s = _open(kind, batch, page, sor, who, detail)
    token = _current.set(s)
    try:
        yield s
    except BaseException as e:
        if s.status is None:
            s.set("fail", f"{type(e).__name__}: {e}")
        raise
    finally:
        _current.reset(token)
        _close(s)


def current():
    return _current.get()


@contextmanager
def muted():
    """Nothing in this context is written (a test's call to the running API)."""
    token = _muted.set(True)
    try:
        yield
    finally:
        _muted.reset(token)


def stage(name, **detail):
    """The current span's next stage (its kind + '.' + name): the stage before it ends here."""
    s = _current.get()
    if not s:
        return
    if s.stage:
        _close(s.stage)
    s.stage = _open(f"{s.kind}.{name}", s.batch, s.page, s.sor, detail=detail, parent=s.id)


def stage_note(**kw):
    """Adds to the current stage's detail (or the span's, between stages)."""
    s = _current.get()
    if s:
        (s.stage or s).note(**kw)


def note(**kw):
    s = _current.get()
    if s:
        s.note(**kw)


def set_status(status, error=None):
    s = _current.get()
    if s:
        s.set(status, error)


def event(kind, status="info", batch=None, page=None, sor=None, who=None, ms=None, error=None, **detail):
    par = _current.get()
    _write("""INSERT INTO staging.trace (status, kind, service, batch_id, page_no, sor_no, parent, who, ms, detail, error,
                                         ended_at)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())""",
           (status, kind, SERVICE, batch if batch is not None else par and par.batch,
            page if page is not None else par and par.page, sor if sor is not None else par and par.sor,
            par.id if par else None, who, ms, _json(_clean(detail)), str(error)[:1000] if error else None))


def events(kind, rows, status="info", **common):
    """Many events of one kind at once: rows = [{batch, page, sor, …detail}]."""
    if not rows:
        return
    out = []
    for r in rows:
        r = {**common, **r}
        b, p, s, who = r.pop("batch", None), r.pop("page", None), r.pop("sor", None), r.pop("who", None)
        out.append((status, kind, SERVICE, b, p, s, who, _json(_clean(r))))
    _write("""INSERT INTO staging.trace (status, kind, service, batch_id, page_no, sor_no, who, detail, ended_at)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())""", out, many=True)


# ---------------------------------------------------------------------------------------------- AI payloads

_payload = contextvars.ContextVar("trace_payload", default=None)
TEXT_CAP = 120_000                     # characters of one text in a payload (a transcript is ~30,000)


def _shrink(x):
    """A payload as kept: an image's base64 (a data: URL, Gemini's inline data) replaced by its size; a very long text
    cut. Never the API key: it travels in a header, never in the body."""
    if isinstance(x, dict):
        if isinstance(x.get("url"), str) and x["url"].startswith("data:"):
            return {**x, "url": f"<image: {len(x['url']) * 3 // 4 // 1024:,} KB, not kept: the page image is in storage>"}
        if "data" in x and "mime_type" in x and isinstance(x["data"], str):
            return {**x, "data": f"<{x['mime_type']}: {len(x['data']) * 3 // 4 // 1024:,} KB, not kept>"}
        return {k: _shrink(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_shrink(v) for v in x]
    if isinstance(x, str) and len(x) > TEXT_CAP:
        return x[:TEXT_CAP] + f"… ({len(x) - TEXT_CAP:,} more characters)"
    return x


def ai_request(body):
    """An AI call is about to be sent: its body (an adapter calls this before each HTTP request)."""
    _payload.set({"request": _shrink(body), "response": None, "http": None})


def ai_response(response, http=None):
    """What came back for the request just sent (the parsed JSON, or the text of an error)."""
    p = _payload.get() or {"request": None}
    _payload.set({**p, "response": _shrink(response), "http": http})


def ai_http(r):
    """An HTTP response from a model endpoint: its JSON (or text) and status, as the request's answer."""
    try:
        body = r.json()
    except Exception:
        body = r.text
    ai_response(body, r.status_code)


def save_payload(call_id):
    """The payload of this thread's last AI call, kept with its ledger row (vf.settle_call, vf.ledger)."""
    p = _payload.get()
    _payload.set(None)
    if not p or not call_id:
        return
    _write("""INSERT INTO staging.ai_payload (call_id, http, request, response) VALUES (%s, %s, %s, %s)
              ON CONFLICT (call_id) DO UPDATE SET http=EXCLUDED.http, request=EXCLUDED.request,
                                                  response=EXCLUDED.response""",
           (call_id, p.get("http"), _json(p.get("request")), _json(p.get("response"))))


def cut_off(batch, page):
    """A page's spans still 'running' were cut off (its worker stopped mid-page): closed as failed before the page
    starts again, so the screens never show them as running."""
    _write("""UPDATE staging.trace SET status='fail', ended_at=clock_timestamp(),
                     error='cut off: the worker stopped before it finished'
              WHERE batch_id=%s AND page_no=%s AND status='running'""", (batch, page))
