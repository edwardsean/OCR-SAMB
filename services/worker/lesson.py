"""vlm-first: lessons for the teacher, proposals for Jev's context, and the replay that gates them.

A lesson = a person's PRACTICE-pile label on a page the machine was unsure or wrong about. The teacher
(common/models/teacher.py) sees the page image, the label, what the AI OCR read, Jev's answer and Jev's whole
context, and proposes ONE change (common/context.py apply_change). The change is replayed before it can become a
proposal:
  Jev is asked again with the old context and with the new one, side by side, on every practice-labelled page
  (truth = the label) and on anchor pages (decided with the QR code or the FP layout; truth = that decision).
  Pass = more practice pages right, none becomes wrong, no anchor changes, unsure does not go up.
Only a change that passes becomes a proposed context (context #N); a person approves or rejects it on the Context
screen. After approval the exam pile is scored, for the report. Exam-pile labels are never sent to the teacher:
refused here, before any call.

Runs by itself: the vf-teacher service consumes q.lessons (a label made a lesson; a context was approved or
rejected) and runs the waiting lessons one at a time, in order.

  python -m worker.lesson backfill <batch>   lessons for labels that already exist (e.g. cloned from v1)
  python -m worker.lesson run [limit]        the same as a wake-up, by hand: one change at a time (stops at the first
                                             proposal until a person approves or rejects it); lessons a newer context
                                             already gets right are closed
  python -m worker.lesson serve              the vf-teacher service
  python -m worker.lesson exam <version>     score an approved context on the exam pile
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Json

from common import context, db, verify
from common.models import teacher
from worker import classify, vf
from worker import main as v1


def _machine(votes):
    m = (votes or {}).get("machine") or {}
    return m.get("doc_type") if m.get("status") == "decided" else None


def backfill(bid):
    """Lessons for existing practice labels on pages vlm-first has read and got unsure or wrong."""
    with db.connect() as c:
        rows = c.execute("""SELECT p.page_no, l.label::text AS label, p.type_votes FROM staging.page p
                            JOIN staging.type_label l USING (batch_id, page_no)
                            WHERE p.batch_id=%s AND l.pile='practice' AND p.fields_all IS NOT NULL""", (bid,)).fetchall()
        made = 0
        for r in rows:
            if _machine(r["type_votes"]) != r["label"]:
                made += c.execute("""INSERT INTO staging.lesson (batch_id, page_no, label) VALUES (%s, %s, %s)
                                     ON CONFLICT (batch_id, page_no) DO NOTHING""", (bid, r["page_no"], r["label"])).rowcount
    print(f"{made} new lessons from {len(rows)} practice labels")


def pulls(ctx, label, state, jev):
    """Facts for the teacher, from the context and the AI OCR's reading only: for each other type Jev gave weight to,
    the fields found on this page that the other type lists but the labelled type doesn't."""
    found = set((state.get("found") or {}))
    mine = {f["name"] for f in ctx["types"][label]["fields"]}
    out = {}
    for code, p in sorted((jev.get("probabilities") or {}).items(), key=lambda kv: -kv[1]):
        if code == label or p < 0.05 or code not in ctx["types"]:
            continue
        theirs = {f["name"]: f["how_often"] for f in ctx["types"][code]["fields"]}
        extra = sorted(n for n in found - mine if n in theirs)
        if extra:
            out[code] = {"weight": p, "fields": {n: theirs[n] for n in extra}}
    return out


PLACEHOLDER = re.compile(r"^<.*>$|^optional$|^title as printed$|max \d+ chars", re.I)


def placeholder_problems(change):
    """A small model may copy the answer template's placeholders instead of filling them in."""
    def strings(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from strings(v)
        elif isinstance(x, list):
            for v in x:
                yield from strings(v)
    bad = [s for s in strings(change) if PLACEHOLDER.search(s.strip())]
    return [f"the answer still has template text in it: {bad[0]!r}"] if bad else []


def titles_problems(change, page):
    """A title added to a type must be printed on this page (Tesseract finds it)."""
    text = verify.windows(page["classical_text"] or "")
    return [f"the title {t!r} is not printed on this page (Tesseract)" for t in change.get("titles_add") or []
            if t.strip() and not verify.found(t, text)]


def prompt(ctx, label, note, state, jev, earlier=None):
    types = {code: {k: t[k] for k in ("what", "titles", "not_for", "fields")} for code, t in ctx["types"].items()}
    fields = {n: f["meaning"] for n, f in ctx["fields"].items()}
    again = (f"""
EARLIER TRY on this page: you proposed {json.dumps(earlier[0], ensure_ascii=False)}. It was not kept: {earlier[1]}.
Propose a DIFFERENT change.
""" if earlier else "")
    facts = "".join(f"\n- {code} (Jev gave it {x['weight']}) lists {', '.join(f'{n} ({h})' for n, h in x['fields'].items())},"
                    f" which were found on this page; {label} lists none of them."
                    for code, x in pulls(ctx, label, state, jev).items())
    facts = (f"\nFACTS, worked out from the CONTEXT and the reading (not guesses):{facts}\n" if facts else "")
    return f"""You are helping improve a document classifier for PT Sarana Abadi Makmur Bersama (SAMB), an Indonesian
distributor. The classifier (Jev) never sees the image: it reads only what an AI OCR model extracted from the page
(below) and picks a document type using the CONTEXT below: each type's description, titles, and the fields it has
and how often.

A person looked at this page and said it is: {label}.{(' Their note: ' + note) if note else ''}
Jev said: {jev.get('choice')} with confidence {jev.get('confidence')}; probabilities {json.dumps(jev.get('probabilities'))}.

How Jev weighs the CONTEXT: it compares the fields found on the page with each type's field list. A field found on
this page that {label}'s list does not have, but another type Jev considered has as "always", pulls Jev toward that
other type (for example an SOR plus PPN and Total look like SAMB's invoice, FP). A useful change adds such a field to
{label} ("field_for_type", with a note naming the customer that prints it), or says it in {label}'s description
("edit_type" "what": one sentence naming the customer and those fields). document_title and page_marker are on every
type already: never propose them. The change is kept only if, replayed on the labelled pages, more of them come out
right and none come out wrong.
{facts}{again}
Look at the image, then answer with ONE JSON object. Replace every <...> with real content; never copy a <...> or
leave an optional key you don't use (omit it instead):
{{"evidence": "<what is VISIBLE on this page that shows it is a {label}, quoting printed text>",
  "why_missed": "<why Jev, reading only the AI OCR's fields, was unsure or chose differently>",
  "change": ONE of
    {{"kind": "field_for_type", "type": "{label}", "field": "<a name from FIELD LIST>", "how_often": "<always|usually|sometimes>", "note": "<up to 160 chars: which customer prints it, and its printed label>"}}
    {{"kind": "edit_type", "type": "{label}", "what": "<the type's new description, up to 400 chars>", "titles_add": ["<a title printed on this page>"], "not_for": "<what it is easily mistaken for>"}}
    {{"kind": "new_field", "type": "{label}", "field": {{"name": "<snake_case>", "kind": "<id|text|amount|qty|date>", "meaning": "<what it is>", "printed_as": ["<its label as printed>"], "example": {{"value": "<value>", "source_text": "<exactly as printed on THIS page>"}}}}, "how_often": "sometimes", "note": "<short>"}}
    {{"kind": "none"}} if no change would help
}}
Rules: a field already in FIELD LIST under any name or meaning is "field_for_type"; a name NOT in FIELD LIST can only
be "new_field" (e.g. No. Pesanan is po_number, DO# is sor). A new field or title must be printed on THIS page. Never
change what existing fields mean. Keep text short. One change only.

CONTEXT: {json.dumps(types, ensure_ascii=False)}
FIELD LIST: {json.dumps(fields, ensure_ascii=False)}
WHAT THE AI OCR READ (all Jev saw): {json.dumps(state, ensure_ascii=False)}
"""


def _decide(fields_all, ctx, qr_text, layout_score, bid, n, purpose, title=None):
    """(type or None, whether Jev answered). Jev failing is not Jev being unsure: a replay can't use it."""
    state = vf.jev_state(fields_all, ctx)
    t0 = time.time()
    jev = classify.jev_ask(state, context.jev_question(ctx))
    vf.ledger("jev", purpose, bid, n, bool(jev.get("choice")), {"model": jev.get("model"),
              "ms": int((time.time() - t0) * 1000)}, jev.get("error"))
    qr_sor = bool(qr_text and classify.SOR_RE.match(qr_text))
    status, doc_type, _, _ = vf.decide(jev, qr_sor, layout_score, title)
    return (doc_type if status == "decided" else None), bool(jev.get("choice"))


def jev_decides(fields_all, ctx, qr_text, layout_score, bid, n, purpose, title=None):
    return _decide(fields_all, ctx, qr_text, layout_score, bid, n, purpose, title)[0]


JEV_AT_ONCE = 6                                       # replay calls are independent: ask Jev about several at once


def replay(old, new, trial=None):
    """Jev with the old and the new context, side by side, on practice labels and anchors. trial = {page: reading}
    for pages re-read with a new field."""
    with db.connect() as c:
        practice = c.execute("""SELECT p.batch_id, p.page_no, p.fields_all, p.qr_text, p.layout_score, p.fp_title,
                                       l.label::text AS truth
                                FROM staging.page p JOIN staging.type_label l USING (batch_id, page_no)
                                WHERE l.pile='practice' AND p.fields_all IS NOT NULL ORDER BY p.page_no""").fetchall()
        anchors = c.execute("""SELECT batch_id, page_no, fields_all, qr_text, layout_score, fp_title, type_votes FROM staging.page
                               WHERE fields_all IS NOT NULL AND type_votes->'machine'->>'status' = 'decided'
                                 AND type_votes->'machine'->>'doc_type' = 'FP'
                                 AND ((type_votes->>'qr_sor')::boolean OR layout_score >= %s OR fp_title) ORDER BY page_no""",
                            (classify.LAYOUT_FP,)).fetchall()
    return verdict(score(practice, anchors, old, new, trial))


def score(practice, anchors, old, new, trial=None, decide=None):
    """The replay's counts, and each practice page's answer before → after. Pure but for `decide` (Jev)."""
    decide = decide or _decide
    jobs = ([(r["fields_all"], old, r) for r in practice] +
            [((trial or {}).get(r["page_no"], r["fields_all"]), new, r) for r in practice] +
            [(r["fields_all"], new, r) for r in anchors])
    with ThreadPoolExecutor(JEV_AT_ONCE) as ex:
        answers = list(ex.map(lambda j: decide(j[0], j[1], j[2]["qr_text"], j[2]["layout_score"], j[2]["batch_id"],
                                               j[2]["page_no"], "replay", title=j[2].get("fp_title")), jobs))
    k = len(practice)
    g = {"practice_pages": k, "right_before": 0, "right_after": 0, "unsure_before": 0, "unsure_after": 0,
         "wrong_before": 0, "wrong_after": 0, "new_wrong": [], "anchor_pages": len(anchors), "anchor_flips": [],
         "jev_errors": sorted({j[2]["page_no"] for j, (_, ok) in zip(jobs, answers) if not ok}), "pages": {}}

    def grade(t, truth):
        return "unsure" if t is None else ("right" if t == truth else "wrong")
    for r, (tb, _), (ta, _) in zip(practice, answers[:k], answers[k:2 * k]):
        b, a = grade(tb, r["truth"]), grade(ta, r["truth"])
        g[f"{b}_before"] += 1
        g[f"{a}_after"] += 1
        if a == "wrong" and b != "wrong":
            g["new_wrong"].append(r["page_no"])
        g["pages"][str(r["page_no"])] = {"label": r["truth"], "before": tb, "after": ta}
    for r, (ta, _) in zip(anchors, answers[2 * k:]):
        if ta != "FP":
            g["anchor_flips"].append(r["page_no"])
    return g


def verdict(g):
    """The gate, from the replay's counts. The rule: a change is kept only if it makes fewer pages unsure with no new
    wrong answer, so it must also HELP (more practice pages right), not merely do no harm. A replay where Jev failed
    to answer proves nothing (a failed "before" would look like an improvement)."""
    g["improved"] = g["right_after"] > g["right_before"] and g["unsure_after"] <= g["unsure_before"]
    g["passed"] = not g["new_wrong"] and not g["anchor_flips"] and not g.get("jev_errors") and g["improved"]
    return g


def gate_summary(g):
    if g.get("jev_errors"):
        return f"Jev didn't answer on pages {g['jev_errors']}: the replay proves nothing"
    if g["new_wrong"] or g["anchor_flips"]:
        return f"the replay made pages wrong: {g['new_wrong'] + g['anchor_flips']}"
    return (f"the replay changed nothing that matters: right {g['right_before']} → {g['right_after']}, "
            f"unsure {g['unsure_before']} → {g['unsure_after']} of {g['practice_pages']} labelled pages")


def new_field_problems(change, page):
    """A genuinely new field must be printed on this page (Tesseract finds its example) and must not just repeat
    another field's value (Puri Indah's DO# is the SOR without its letters)."""
    ex = ((change.get("field") or {}).get("example") or {})
    src = ex.get("source_text") or ""
    p = []
    if not verify.found(src, verify.windows(page["classical_text"] or "")):
        p.append(f"its example {src!r} is not in Tesseract's reading of the page")
    others = {n: f.get("value") for n, f in (page["fields_all"] or {}).items()
              if n != "lines" and isinstance(f, dict) and f.get("value")}
    same = [n for n, v in others.items() if verify.flat(v) and verify.flat(v) in (verify.flat(src), verify.flat(ex.get("value")))]
    if same:
        p.append(f"its example repeats {same[0]}: that is 'new for this type', not a new field")
    return p


def approved_after(c, version, when):
    """Was this context approved by a person after `when` (the seed never was)?"""
    r = c.execute("SELECT approved_at FROM staging.context_version WHERE version=%s", (version,)).fetchone()
    return bool(r and r["approved_at"] and r["approved_at"] > when)


def run_one(lesson):
    bid, n = lesson["batch_id"], lesson["page_no"]
    with db.connect() as c:
        page = c.execute("""SELECT p.*, l.pile, l.note, l.label::text AS label FROM staging.page p
                            JOIN staging.type_label l USING (batch_id, page_no)
                            WHERE p.batch_id=%s AND p.page_no=%s""", (bid, n)).fetchone()
        ctx_v, ctx = context.ensure(c, classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS,
                                    classify.JEV_TYPES)
        newer = approved_after(c, ctx_v, lesson["created_at"])

    def done(status, **kw):
        with db.connect() as c:
            c.execute("""UPDATE staging.lesson SET status=%s, answer=%s, proposal=%s, error=%s, teacher_model=%s,
                         asked_at=now() WHERE id=%s""", (status, Json(kw.get("answer")), kw.get("proposal"),
                                                         kw.get("error"), teacher.MODEL, lesson["id"]))
        return status
    if not page or page["pile"] != "practice":
        return done("failed", error="not a practice-pile label: the teacher never sees exam labels")
    if not page["fields_all"]:
        return done("failed", error="the AI OCR hasn't read this page yet")
    if newer:
        # A change was approved since the miss: if it already fixed this page, there's nothing left to teach. Only
        # then: re-asking the same context could pass a borderline page by chance and hide the weakness.
        votes = page["type_votes"] or {}
        now = _machine(votes) if votes.get("context_version") == ctx_v else jev_decides(
            page["fields_all"], ctx, page["qr_text"], page["layout_score"], bid, n, "recheck", page.get("fp_title"))
        if now == page["label"]:
            return done("no_change", answer={"note": f"context #{ctx_v}, approved after this lesson, already "
                                                      "decides it right"})
    state, jev = vf.jev_state(page["fields_all"], ctx), (page["type_votes"] or {}).get("jev") or {}
    try:
        png = v1.png_bytes(v1.load(page["upright_path"]))
    except Exception as e:
        return done("failed", error=f"page image: {type(e).__name__}: {e}"[:300])
    tries, earlier = [], None

    def later(err):
        """A model couldn't be reached (no key, rate limit, outage): the lesson waits and is retried, up to 3 times."""
        calls = ((lesson.get("answer") or {}).get("failed_calls") or 0) + 1
        if calls >= MAX_FAILED_CALLS:
            return done("failed", answer={"tries": tries, "failed_calls": calls}, error=err)
        with db.connect() as c:
            c.execute("UPDATE staging.lesson SET answer=%s, error=%s WHERE id=%s",
                      (Json({"failed_calls": calls}), f"will retry: {err}", lesson["id"]))
        return "retry"
    for _ in range(2):                                # a second try is told why the first wasn't kept
        try:
            answer, meta = teacher.ask(png, prompt(ctx, page["label"], page["note"], state, jev, earlier))
            vf.ledger("zai", "teach", bid, n, True, meta)
        except Exception as e:
            vf.ledger("zai", "teach", bid, n, False, error=f"{type(e).__name__}: {e}"[:300])
            return later(f"teacher: {type(e).__name__}: {e}"[:300])
        change = answer.get("change") or {"kind": "none"}
        if change.get("kind") == "none":
            return done("no_change", answer={**answer, "tries": tries})
        change["type"] = page["label"]                # the teacher may only change the labelled type
        problems = placeholder_problems(change)
        if not problems:
            new, problems = context.apply_change(ctx, change)
        if not problems and change.get("kind") == "new_field":
            problems += new_field_problems(change, page)
        if not problems and change.get("kind") == "edit_type":
            problems += titles_problems(change, page)
        gate = None
        if not problems:
            trial = None
            if change.get("kind") == "new_field":    # the AI OCR must read the new field before the replay means much
                try:
                    reading = vf.read_fields(bid, n, new)   # one step: the image again; two: the transcript
                except Exception as e:
                    return later(f"AI OCR: {type(e).__name__}: {e}"[:300])
                trial = {n: reading}
            gate = replay(ctx, new, trial)
            if gate["jev_errors"]:                   # Jev failing isn't the teacher's fault: try again later
                return later(gate_summary(gate))
            if gate["passed"]:
                with db.connect() as c:
                    version = context.propose(c, new, ctx_v, f"teacher:{teacher.MODEL}",
                                              f"{change['kind']} for {page['label']} from page {n}")
                    c.execute("UPDATE staging.context_version SET gate=%s WHERE version=%s", (Json(gate), version))
                return done("proposed", answer={**answer, "tries": tries}, proposal=version)
        why = "; ".join(problems) if problems else gate_summary(gate)
        tries.append({"change": change, "evidence": answer.get("evidence"), "why_missed": answer.get("why_missed"),
                      "not_kept": why, "gate": gate})
        earlier = (change, why)
    return done("failed", answer={"tries": tries}, error=f"no change was kept after {len(tries)} tries: {why}"[:500])


MAX_FAILED_CALLS = 3


def ask_in_turn(lessons, ask):
    """One change at a time: stop at the first proposal. It was replayed against the active context, and the next
    lesson must be asked against whatever a person approves (or keeps). Also stop when a model can't be reached.
    A generator, so each lesson's result shows as soon as it is known."""
    for l in lessons:
        status = ask(l)
        yield l, status
        if status in ("proposed", "retry"):
            return


def run(limit=50, quiet=False):
    with db.connect() as c:
        pending = c.execute("SELECT version FROM staging.context_version WHERE status='proposed'").fetchall()
        # only pages vlm-first prepared: the teacher needs the upright image (the tests' throwaway pages have none)
        lessons = c.execute("""SELECT l.* FROM staging.lesson l JOIN staging.page p USING (batch_id, page_no)
                               WHERE l.status='waiting' AND p.upright_path IS NOT NULL
                               ORDER BY l.created_at LIMIT %s""", (limit,)).fetchall()
    if pending:
        if not quiet:
            print(f"proposal context #{pending[0]['version']} waits on the Context screen: approve or reject it first")
        return
    for l, status in ask_in_turn(lessons, run_one):
        print(f"page {l['page_no']} ({l['label']}): {status}", flush=True)
        if status == "proposed":
            print("one change at a time: approve or reject it on the Context screen", flush=True)
        if status == "retry":
            print("a model couldn't be reached: the lesson waits and is retried later", flush=True)
    if not lessons and not quiet:
        print("no waiting lessons")


def serve():
    """vf-teacher: one consumer on q.lessons. A message is only a wake-up (staging.lesson is the state): a label made
    a lesson, or a person approved or rejected a context. Half an hour without messages wakes it too, to retry lessons
    a model couldn't be reached for. One consumer, so lessons are taught one at a time, in order."""
    from common import health, queue
    health.serve("vf-teacher", role="vlm-first teacher: lesson → GLM → replay → proposal, one at a time")
    while True:
        try:
            conn = queue.connect()
            ch = conn.channel()
            ch.queue_declare(queue=queue.Q_LESSONS, durable=True)
            ch.basic_qos(prefetch_count=1)
            print("vf-teacher waiting on", queue.Q_LESSONS, flush=True)
            run(quiet=True)                           # whatever waited while this service was down
            teach_wiki()
            for method, _, body in ch.consume(queue.Q_LESSONS, inactivity_timeout=1800):
                if method:
                    ch.basic_ack(method.delivery_tag)   # a wake-up; the work itself is in staging.lesson
                    print("woken:", json.loads(body or b"{}").get("reason"), flush=True)
                run(quiet=True)
                teach_wiki()
        except Exception as e:
            print("vf-teacher reconnecting:", e, flush=True)
            time.sleep(5)


def teach_wiki():
    """Stage 3 (read-then-map): the extraction lessons, one at a time, after Jev's (worker/learn.py teach). A
    failure never stops the teacher: the lesson waits."""
    try:
        from worker import learn
        learn.teach(show=lambda *a: print(*a, flush=True))
    except Exception as e:
        print("the wiki's lessons failed:", type(e).__name__, e, flush=True)


def score_exam(version):
    """After a person approved a context: how does it do on the exam pile it was never built from? (report only)"""
    with db.connect() as c:
        ctx = c.execute("SELECT content FROM staging.context_version WHERE version=%s", (version,)).fetchone()["content"]
        exam = c.execute("""SELECT p.batch_id, p.page_no, p.fields_all, p.qr_text, p.layout_score, p.fp_title,
                                   l.label::text AS truth
                            FROM staging.page p JOIN staging.type_label l USING (batch_id, page_no)
                            WHERE l.pile='exam' AND p.fields_all IS NOT NULL""").fetchall()
    out = {"pages": len(exam), "right": 0, "unsure": 0, "wrong": 0}
    for r in exam:
        t = jev_decides(r["fields_all"], ctx, r["qr_text"], r["layout_score"], r["batch_id"], r["page_no"], "exam",
                        r["fp_title"])
        out["unsure" if t is None else ("right" if t == r["truth"] else "wrong")] += 1
    with db.connect() as c:
        c.execute("UPDATE staging.context_version SET gate = coalesce(gate, '{}'::jsonb) || jsonb_build_object('exam', %s::jsonb) "
                  "WHERE version=%s", (json.dumps(out), version))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "backfill":
        backfill(sys.argv[2])
    elif cmd == "run":
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    elif cmd == "serve":
        serve()
    elif cmd == "exam":
        print(score_exam(int(sys.argv[2])))
