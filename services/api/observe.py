"""The developers' view of what happened (the user, 2026-10-08: "observability for all things in our workflow, so that
the dev can see what happened at some time, and trace all of it for a single file or single batch"). It reads the
trace (common/trace.py, staging.trace) and the AI calls (staging.model_call) together:

  explore(c, …)          Teknis → Jejak: everything in a time window, newest first, filtered by the workflow's step,
                         how it ended, which service, or a search (a batch number, file, scan id, SOR, person)
  of_upload(c, id)       Jejak for one batch, told in the workflow's order (story): upload, split, each page's own
                         log (attempts, their stages, the AI calls inside each stage), grouping and orders, people,
                         the teachers, sending; with a summary and a timeline
  of_page(c, b, n)       one page's log
Plain words for every kind: api/trace_words.py.
  metrics(c, days)       Teknis → Metrik: how long pages, files and stages take (median, 90th percentile), the AI
                         calls (latency, failures, tokens, cost), failures by kind, the teachers, people's actions

Pure helpers (pct, cost, summarise) are tested without a database."""
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
# USD per million tokens (in, out): Alibaba Model Studio, International, list prices noted 2026-10-07. A model not
# listed has no cost shown, never a guess.
PRICES = {"qwen3-vl-plus": (0.20, 1.60), "qwen3-vl-flash": (0.05, 0.40), "qwen-plus": (0.40, 1.20),
          "qwen3-235b-a22b-instruct-2507": (0.23, 0.92), "qwen-flash": (0.05, 0.40), "qwen-turbo": (0.05, 0.20)}
# AI calls written after they ended (vf.ledger); the others are claimed before the call (vf.reserve), so `at` is
# their start
AFTER = {"classify", "teach", "replay", "teach_wiki", "exam", "match", "propose", "recheck"}
STAGES = ("prepare", "read", "classify", "knowledge", "project", "tesseract", "check", "look_again", "store", "save")



def step_kinds(step):
    """Pure: (trace kinds, AI purposes) of one workflow step (api/trace_words.py)."""
    from api import trace_words as tw
    kinds = [k for k, v in tw.NAMES.items() if v[0] == step]
    return [k for k in kinds if not k.startswith("ai.")], [k[3:] for k in kinds if k.startswith("ai.")]


def model_name(m):
    return (m or "").split(":", 1)[-1]


def cost(model, tokens):
    """Pure: USD for one call, or None when the model's price isn't known."""
    p = PRICES.get(model_name(model))
    if not p or not tokens or (tokens.get("tokens_in") is None and tokens.get("tokens_out") is None):
        return None                                      # the call's answer didn't say how many tokens
    return ((tokens.get("tokens_in") or 0) * p[0] + (tokens.get("tokens_out") or 0) * p[1]) / 1e6


def pct(values, q):
    """Pure: the q-th percentile (0–100) of the values, by nearest rank; None when there are none."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(round(q / 100 * len(v) + 0.5)) - 1))
    return v[k]


def dur(ms):
    """Pure: '850 ms', '42 dtk', '3 mnt 5 dtk', '2 jam 4 mnt'."""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{int(ms)} ms"
    s = int(round(ms / 1000))
    if s < 60:
        return f"{s} dtk"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} mnt {s} dtk" if s else f"{m} mnt"
    h, m = divmod(m, 60)
    return f"{h} jam {m} mnt" if m else f"{h} jam"


def summary(detail, limit=160):
    """Pure: a row's detail as one short line ('outcome=clear · doc_type=FP')."""
    if not detail:
        return ""
    parts = []
    for k, v in detail.items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, dict):
            v = ", ".join(f"{a}={b}" for a, b in v.items() if b not in (None, ""))
        parts.append(f"{k}={v}")
    out = " · ".join(parts)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def _ai_row(m):
    """A model_call row as a trace row: kind ai.<purpose>, its start, its status."""
    start = m["at"] - timedelta(milliseconds=m["ms"] or 0) if m["purpose"] in AFTER else m["at"]
    status = ("running" if m["error"] == "pending" else "ok" if m["ok"] else
              "wait" if any(k in (m["error"] or "") for k in ("DailyLimit", "OutOfBudget", "NotSet")) else "fail")
    tokens = m.get("tokens") or {}
    return {"src": "ai", "id": m["id"], "at": start, "ended_at": None if status == "running" else
            start + timedelta(milliseconds=m["ms"] or 0), "ms": m["ms"], "status": status, "kind": f"ai.{m['purpose']}",
            "service": m["provider"], "batch_id": m["batch_id"], "page_no": m["page_no"], "sor_no": None, "who": None,
            "parent": None, "error": None if m["error"] == "pending" else m["error"],
            "detail": {"model": model_name(m["model"]), "tokens_in": tokens.get("tokens_in"),
                       "tokens_out": tokens.get("tokens_out"),
                       "usd": round(cost(m["model"], tokens), 5) if cost(m["model"], tokens) is not None else None}}


def window(since=None, until=None, around=None, hours=6):
    """(since, until) from the screen's filters: around a moment ±15 minutes, a range, or the last `hours`."""
    now = datetime.now(timezone.utc)
    if around:
        return around - timedelta(minutes=15), around + timedelta(minutes=15)
    until = until or now
    return since or until - timedelta(hours=hours), until


def explore(c, q=None, since=None, until=None, step=None, status=None, service=None, limit=300):
    """Everything in the window, newest first: trace rows and AI calls, filtered by the workflow's step ('ai': the AI
    calls only)."""
    kinds, purposes = step_kinds(step) if step and step != "ai" else (None, None)
    like = f"%{q.strip()}%" if q and q.strip() else None
    batches = [r["id"] for r in c.execute(
        """SELECT s.id FROM staging.scan_batch s LEFT JOIN staging.upload u ON u.id = s.upload_id
            WHERE s.id ILIKE %(l)s OR s.file_name ILIKE %(l)s OR u.code ILIKE %(l)s""", {"l": like})] if like else []
    rows = []
    if step != "ai":
        rows += [dict(r, src="trace") for r in c.execute(
            """SELECT id, at, ended_at, ms, status, kind, service, batch_id, page_no, sor_no, who, parent, detail, error
                 FROM staging.trace
                WHERE at BETWEEN %(since)s AND %(until)s
                  AND (%(like)s::text IS NULL OR batch_id = ANY(%(b)s) OR sor_no ILIKE %(like)s OR who ILIKE %(like)s)
                  AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s)
                       OR (%(step)s = 'people' AND kind LIKE 'person.%%') OR (%(step)s = 'jobs' AND kind LIKE 'job.%%'))
                  AND (%(status)s::text IS NULL OR status = %(status)s)
                  AND (%(svc)s::text IS NULL OR service ILIKE %(svc)s || '%%')
                ORDER BY at DESC LIMIT %(limit)s""",
            {"since": since, "until": until, "like": like, "b": batches, "kinds": kinds, "step": step, "status": status,
             "svc": service, "limit": limit * 3})]
    if (step is None or step == "ai" or purposes) and not service:
        ai = [_ai_row(dict(m)) for m in c.execute(
            """SELECT * FROM staging.model_call
                WHERE at BETWEEN %(since)s AND %(until)s AND (%(like)s::text IS NULL OR batch_id = ANY(%(b)s))
                  AND (%(p)s::text[] IS NULL OR purpose = ANY(%(p)s))
                ORDER BY at DESC LIMIT %(limit)s""",
            {"since": since - timedelta(minutes=10), "until": until, "like": like, "b": batches, "limit": limit,
             "p": purposes})]
        rows += [r for r in ai if status is None or r["status"] == status]
    rows.sort(key=lambda r: r["at"], reverse=True)
    if step != "jobs":                    # a scheduled job that found nothing to do is noise: shown under its own step
        from api import trace_words as tw
        rows = [r for r in rows if not tw.idle_job(r)]
    return rows[:limit]


def _queue_waits(rows):
    """Pure: {page span id: ms it waited in the queue}: from the last time the page was sent (page.queued, or back
    from the waiting room: page.parked) to the start of that attempt."""
    out, last = {}, {}
    for r in sorted(rows, key=lambda r: r["at"]):
        k = (r["batch_id"], r["page_no"])
        if r["kind"] in ("page.queued", "page.parked"):
            last[k] = r["at"]
        elif r["kind"] == "page" and r["status"] != "skip" and k in last:
            out[r["id"]] = max(0, int((r["at"] - last.pop(k)).total_seconds() * 1000))
    return out


def summarise(files, rows, ai):
    """Pure: one batch's trace. files: scan_batch rows; rows: its trace rows; ai: its AI calls as _ai_row.
    Returns {pages: […], files: […], batch: {…}, lanes: timeline rows, events: what followed}."""
    t_files = {f["id"]: f for f in files}
    waits = _queue_waits(rows)
    pages = defaultdict(lambda: {"attempts": 0, "proc_ms": 0, "queue_ms": 0, "stages": Counter(), "ai_n": 0,
                                 "ai_ms": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0, "usd_known": True,
                                 "fails": [], "waits": 0, "segments": [], "outcome": None, "status": None,
                                 "first": None, "last": None, "first_done": None})
    spans = {r["id"]: r for r in rows if r["kind"] == "page"}
    for r in rows:
        if r["page_no"] is None or not r["kind"].startswith("page"):
            continue
        p = pages[(r["batch_id"], r["page_no"])]
        p["first"] = min(filter(None, (p["first"], r["at"])))
        end = r.get("ended_at") or r["at"]
        p["last"] = max(filter(None, (p["last"], end)))
        if r["kind"] == "page" and r["status"] != "skip":
            p["attempts"] += 1
            p["proc_ms"] += r["ms"] or 0
            p["queue_ms"] += waits.get(r["id"], 0)
            p["status"] = r["status"]
            p["outcome"] = (r.get("detail") or {}).get("outcome") or p["outcome"]
            if r["status"] in ("ok", "wait") and r.get("ended_at") and not p["first_done"]:
                p["first_done"] = r["ended_at"]                  # read for the first time (later runs are re-reads)
            if r["status"] == "fail":
                p["fails"].append(r.get("error"))
            if r["status"] == "wait":
                p["waits"] += 1
            if r["id"] in waits:
                p["segments"].append({"kind": "queue", "at": r["at"] - timedelta(milliseconds=waits[r["id"]]),
                                      "ms": waits[r["id"]], "status": "info"})
        elif r["kind"].startswith("page.") and r["kind"][5:] in STAGES:
            p["stages"][r["kind"][5:]] += r["ms"] or 0
            p["segments"].append({"kind": r["kind"][5:], "at": r["at"], "ms": r["ms"], "status": r["status"],
                                  "parent": r["parent"] in spans})
        elif r["kind"] in ("page.crashed", "page.gave_up"):
            p["fails"].append(r.get("error"))
    for a in ai:
        if a["page_no"] is None:
            continue
        p = pages[(a["batch_id"], a["page_no"])]
        p["ai_n"] += 1
        p["ai_ms"] += a["ms"] or 0
        d = a["detail"]
        p["tokens_in"] += d.get("tokens_in") or 0
        p["tokens_out"] += d.get("tokens_out") or 0
        if d.get("usd") is None:
            p["usd_known"] = p["usd_known"] and not (d.get("tokens_in") or d.get("tokens_out"))
        else:
            p["usd"] += d["usd"]
    out_pages = [{"batch_id": b, "page_no": n, "file_name": (t_files.get(b) or {}).get("file_name"), **v,
                  "stages": dict(v["stages"])} for (b, n), v in sorted(pages.items(), key=lambda kv: (
                      (t_files.get(kv[0][0]) or {}).get("file_name") or "", kv[0][1]))]
    # the batch's time is what the trace saw: from the file arriving (or, read before the trace existed, the first
    # time a page was sent) to the last thing that happened; AI calls from before then aren't on the timeline
    times = [r["at"] for r in rows]
    t0 = min(times) if times else None
    t1 = max([r.get("ended_at") or r["at"] for r in rows], default=None)
    starts = [r["at"] for r in rows if r["kind"] in ("file.received", "page.queued")]
    start = min(starts) if starts else t0
    firsts = [p["first_done"] for p in out_pages if p["attempts"]]
    read_at = max(firsts) if firsts and all(firsts) else None
    proc = [p["proc_ms"] for p in out_pages if p["attempts"]]
    batch = {"t0": t0, "t1": t1, "start": start, "pages": len(out_pages), "read_at": read_at,
             "wall_ms": int((read_at - start).total_seconds() * 1000) if read_at and start else None,
             "p50_ms": pct(proc, 50), "p90_ms": pct(proc, 90), "queue_p50_ms": pct([p["queue_ms"] for p in out_pages
                                                                                   if p["attempts"]], 50),
             "attempts": sum(p["attempts"] for p in out_pages), "ai_n": len(ai),
             "tokens_in": sum(p["tokens_in"] for p in out_pages), "tokens_out": sum(p["tokens_out"] for p in out_pages),
             "usd": round(sum((a["detail"].get("usd") or 0) for a in ai), 4),
             "fails": sum(1 for r in rows if r["status"] == "fail") + sum(1 for a in ai if a["status"] == "fail"),
             "waits": sum(1 for r in rows if r["kind"] == "page.parked")}
    split = {r["batch_id"]: r for r in rows if r["kind"] == "file.split"}
    out_files = []
    for f in files:                       # a file's time: from its arrival (or first page sent) to its last page read once
        mine = [p for p in out_pages if p["batch_id"] == f["id"]]
        began = [r["at"] for r in rows if r["batch_id"] == f["id"] and r["kind"] in ("file.received", "page.queued")]
        firsts = [p["first_done"] for p in mine if p["attempts"]]
        done_at = max(firsts) if firsts and all(firsts) else None
        out_files.append({**f, "split_ms": (split.get(f["id"]) or {}).get("ms"), "done_at": done_at,
                          "pages_done": sum(1 for p in mine if p["status"] in ("ok", "wait")),
                          "wall_ms": int((done_at - min(began)).total_seconds() * 1000) if done_at and began else None})
    follow = [r for r in rows if not r["kind"].startswith("page") and r["kind"] not in ("file.received",)]
    return {"batch": batch, "files": out_files, "pages": out_pages, "events": follow}


def lanes(s):
    """Pure: the timeline's rows, each segment placed as a share (0–100) of the batch's time."""
    t0, t1 = s["batch"]["t0"], s["batch"]["t1"]
    if not t0 or not t1:
        return []
    span = max((t1 - t0).total_seconds(), 1)

    def at(x, ms):
        left = (x - t0).total_seconds() / span * 100
        width = max((ms or 0) / 1000 / span * 100, 0.4)
        return round(left, 3), round(min(width, 100 - left), 3)
    out = []
    for p in s["pages"]:
        segs = []
        for g in p["segments"]:
            left, width = at(g["at"], g["ms"])
            segs.append({**g, "left": left, "width": width})
        out.append({"label": f"{p['file_name']} · hal. {p['page_no']}", "batch_id": p["batch_id"],
                    "page_no": p["page_no"], "segments": segs})
    marks = []
    for e in s["events"]:
        left, width = at(e["at"], e.get("ms"))
        marks.append({**e, "left": left, "width": width})
    return {"pages": out, "marks": marks, "t0": t0, "t1": t1, "span_s": span}


def page_log(rows, ai):
    """Pure: one page's log, oldest first: {log, loose}. Each attempt (a 'page' span) carries its stages, and each stage
    the AI calls that ran inside it; the queue wait before an attempt is said on it. `loose`: AI calls outside every
    attempt (made before the trace existed, or a teacher testing a change on this page), shown folded."""
    waits = _queue_waits(rows)
    attempts = [dict(r, stages=[], calls=[], waited=waits.get(r["id"])) for r in rows if r["kind"] == "page"]
    by_id = {a["id"]: a for a in attempts}
    for r in rows:
        if r.get("parent") in by_id and r["kind"].startswith("page."):
            by_id[r["parent"]]["stages"].append(dict(r, calls=[]))
    loose = []
    for x in ai:
        end = lambda s: s.get("ended_at") or s["at"]                                      # noqa: E731
        a = next((a for a in attempts if a["at"] <= x["at"] <= end(a)), None)
        if not a:
            loose.append(x)
            continue
        st = next((s for s in a["stages"] if s["at"] <= x["at"] <= end(s)), None)
        (st["calls"] if st else a["calls"]).append(x)
    events = [r for r in rows if r["kind"] != "page" and r.get("parent") not in by_id]
    return {"log": sorted(attempts + events, key=lambda r: r["at"]), "loose": loose}


def story(files, rows, ai, uploads=()):
    """Pure: the batch in the workflow's order (api/trace_words.py STEPS): {step: rows}, with 'read' as one log per
    page ({batch_id, page_no, file_name, log, summary}). uploads: the batch's own 'person.new_upload' rows."""
    from api import trace_words as tw
    names = {f["id"]: f["file_name"] for f in files}
    out = {k: [] for k, _, _ in tw.STEPS}
    out["upload"] = list(uploads)
    pages = defaultdict(lambda: {"rows": [], "ai": []})
    for r in rows:
        st = tw.step(r["kind"])
        if r["kind"].startswith("page") and r.get("page_no") is not None:
            pages[(r["batch_id"], r["page_no"])]["rows"].append(r)
        else:
            out[st if st in out else "jobs"].append(r)
    for x in ai:
        if x.get("page_no") is not None:
            pages[(x["batch_id"], x["page_no"])]["ai"].append(x)
        else:
            out[tw.step(x["kind"])].append(x)
    s = summarise(files, rows, ai)
    by_page = {(p["batch_id"], p["page_no"]): p for p in s["pages"]}
    out["read"] = [{"batch_id": b, "page_no": n, "file_name": names.get(b, b), **page_log(v["rows"], v["ai"]),
                    "summary": by_page.get((b, n), {})}
                   for (b, n), v in sorted(pages.items(), key=lambda kv: (names.get(kv[0][0], ""), kv[0][1]))]
    for k in out:
        if k != "read":
            out[k].sort(key=lambda r: r["at"])
    return out


def of_upload(c, upload_id):
    files = [dict(r) for r in c.execute("""SELECT id, file_name, page_total, status::text AS status, received_at, error
                                             FROM staging.scan_batch WHERE upload_id=%s ORDER BY file_name""",
                                        (upload_id,))]
    ids = [f["id"] for f in files]
    sors = [r["sor_no"] for r in c.execute(
        """SELECT DISTINCT b.sor_no FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
             JOIN staging.document d ON d.id = bd.document_id WHERE d.batch_id = ANY(%s)""", (ids,))]
    rows = [dict(r) for r in c.execute(
        """SELECT id, at, ended_at, ms, status, kind, service, batch_id, page_no, sor_no, who, parent, detail, error
             FROM staging.trace WHERE batch_id = ANY(%s) OR sor_no = ANY(%s) ORDER BY at""", (ids, sors))]
    ai = [_ai_row(dict(m)) for m in c.execute("SELECT * FROM staging.model_call WHERE batch_id = ANY(%s) ORDER BY at",
                                              (ids,))]
    ups = [dict(r) for r in c.execute(
        """SELECT id, at, ended_at, ms, status, kind, service, batch_id, page_no, sor_no, who, parent, detail, error
             FROM staging.trace WHERE kind = 'person.new_upload' AND detail->>'upload' = %s""", (str(upload_id),))]
    s = summarise(files, rows, ai)
    first = min((r["at"] for r in rows), default=None)
    pre = any(f["received_at"] and (not first or f["received_at"] < first - timedelta(minutes=1)) for f in files)
    return {**s, "lanes": lanes(s), "sors": sors, "story": story(files, rows, ai, ups), "pre_trace": pre}


def of_page(c, batch, page):
    """One page's log (page_log): its attempts with their stages and AI calls, and its events."""
    rows = [dict(r) for r in c.execute(
        """SELECT id, at, ended_at, ms, status, kind, service, batch_id, page_no, sor_no, who, parent, detail, error
             FROM staging.trace WHERE batch_id=%s AND page_no=%s ORDER BY at, id""", (batch, page))]
    ai = [_ai_row(dict(m)) for m in c.execute(
        "SELECT * FROM staging.model_call WHERE batch_id=%s AND page_no=%s ORDER BY at", (batch, page))]
    return page_log(rows, ai)


def metrics(c, days=1):
    """Teknis → Metrik over the last `days`."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [dict(r) for r in c.execute(
        """SELECT id, kind, status, ms, service, batch_id, page_no, at, detail, who, error FROM staging.trace
            WHERE at >= %s""",
        (since,))]
    ai = [_ai_row(dict(m)) for m in c.execute("SELECT * FROM staging.model_call WHERE at >= %s", (since,))]
    pages = [r for r in rows if r["kind"] == "page" and r["status"] not in ("skip", "running")]
    by_kind = defaultdict(list)
    for r in rows:
        if r["ms"] is not None:
            by_kind[r["kind"]].append(r["ms"])
    stage = [{"stage": st, "n": len(by_kind.get(f"page.{st}", [])), "p50": pct(by_kind.get(f"page.{st}", []), 50),
              "p90": pct(by_kind.get(f"page.{st}", []), 90), "total": sum(by_kind.get(f"page.{st}", []))}
             for st in STAGES]
    q_ms = list(_queue_waits(rows).values())
    files = [r["ms"] for r in rows if r["kind"] == "file.split" and r["ms"] is not None]
    calls = defaultdict(lambda: {"n": 0, "fail": 0, "wait": 0, "ms": [], "tokens_in": 0, "tokens_out": 0, "usd": 0.0,
                                 "priced": True})
    for a in ai:
        k = (a["kind"][3:], a["detail"]["model"])
        x = calls[k]
        x["n"] += 1
        x["fail"] += a["status"] == "fail"
        x["wait"] += a["status"] == "wait"
        if a["ms"]:
            x["ms"].append(a["ms"])
        x["tokens_in"] += a["detail"].get("tokens_in") or 0
        x["tokens_out"] += a["detail"].get("tokens_out") or 0
        if a["detail"].get("usd") is None:
            x["priced"] = False
        else:
            x["usd"] += a["detail"]["usd"]
    ai_rows = sorted(({"purpose": k[0], "model": k[1], **{**v, "p50": pct(v["ms"], 50), "p90": pct(v["ms"], 90)}}
                      for k, v in calls.items()), key=lambda x: -x["n"])
    errors = Counter()
    for r in rows:
        if r["status"] == "fail":
            errors[(r["kind"], _cause(r))] += 1
    for a in ai:
        if a["status"] == "fail":
            errors[(a["kind"], _cause(a))] += 1
    lessons = Counter((r["kind"], (r.get("detail") or {}).get("result") or r["status"]) for r in rows
                      if r["kind"] in ("lesson.type", "lesson.tip"))
    people = Counter((r["kind"][7:], r["status"]) for r in rows if r["kind"].startswith("person."))
    hours = Counter(r["at"].astimezone(WIB).strftime("%d %b %H:00") for r in pages if r["status"] == "ok")
    return {"days": days, "since": since,
            "pages": {"n": len(pages), "ok": sum(r["status"] == "ok" for r in pages),
                      "fail": sum(r["status"] == "fail" for r in pages), "wait": sum(r["status"] == "wait" for r in pages),
                      "p50": pct([r["ms"] for r in pages], 50), "p90": pct([r["ms"] for r in pages], 90),
                      "queue_p50": pct(q_ms, 50), "queue_p90": pct(q_ms, 90),
                      "workers": len({r["service"] for r in pages})},
            "files": {"n": len(files), "split_p50": pct(files, 50), "split_p90": pct(files, 90)},
            "stages": stage, "ai": ai_rows, "ai_usd": round(sum(x["usd"] for x in ai_rows), 4),
            "ai_tokens": (sum(x["tokens_in"] for x in ai_rows), sum(x["tokens_out"] for x in ai_rows)),
            "errors": errors.most_common(15), "lessons": sorted(lessons.items()), "people": sorted(people.items()),
            "hours": sorted(hours.items(), key=lambda kv: datetime.strptime(kv[0], "%d %b %H:00"))[-48:],
            "groups": {"n": len(by_kind.get("group", [])), "p50": pct(by_kind.get("group", []), 50)},
            "orders": Counter((r.get("detail") or {}).get("now") for r in rows if r["kind"] == "order.status"),
            "running": [r for r in rows if r["status"] == "running"]}


def _cause(r):
    """A failure in a few words, for grouping: the exception's name or the first words of the error."""
    e = (r.get("error") or "").strip()
    if not e:
        return "?"
    head = e.split(":", 1)[0]
    return head if len(head) <= 40 else e[:40]
