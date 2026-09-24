"""vlm-first: lessons for the teacher, proposals for Jev's context, and the replay that gates them.

A lesson = a person's PRACTICE-pile label on a page the machine was unsure or wrong about. The teacher
(common/models/teacher.py) sees the page image, the label, what the AI OCR read, Jev's answer and Jev's whole
context, and proposes ONE change (common/context.py apply_change). The change becomes a proposed context version,
and is replayed before anyone can approve it:
  Jev is asked again with the old context and with the new one, side by side, on every practice-labelled page
  (truth = the label) and on anchor pages (decided with the QR code or the FP layout; truth = that decision).
  Pass = no page that was right or unsure becomes wrong, no anchor changes, and unsure does not go up.
Then a person approves or rejects it on the Context screen. After approval the exam pile is scored, for the report.
Exam-pile labels are never sent to the teacher: refused here, before any call.

  python -m worker.lesson backfill <batch>   lessons for labels that already exist (e.g. cloned from v1)
  python -m worker.lesson run [limit]        ask the teacher about waiting lessons (needs ZAI_API_KEY)
  python -m worker.lesson exam <version>     score an approved context on the exam pile
"""
import json
import sys
import time

from psycopg.types.json import Json

from common import context, db, verify
from common.models import teacher, vlm
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


def prompt(ctx, label, note, state, jev):
    types = {code: {k: t[k] for k in ("what", "titles", "not_for", "fields")} for code, t in ctx["types"].items()}
    fields = {n: f["meaning"] for n, f in ctx["fields"].items()}
    return f"""You are helping improve a document classifier for PT Sarana Abadi Makmur Bersama (SAMB), an Indonesian
distributor. The classifier (Jev) never sees the image: it reads only what an AI OCR model extracted from the page
(below) and picks a document type using the CONTEXT below: each type's description, titles, and the fields it has
and how often.

A person looked at this page and said it is: {label}.{(' Their note: ' + note) if note else ''}
Jev said: {jev.get('choice')} with confidence {jev.get('confidence')}; probabilities {json.dumps(jev.get('probabilities'))}.

Look at the image, then answer with ONE JSON object:
{{"evidence": "what is VISIBLE on this page that shows it is a {label} (quote printed text)",
  "why_missed": "why Jev, reading only the AI OCR's fields, was unsure or chose differently",
  "change": ONE of
    {{"kind": "edit_type", "type": "{label}", "what": "new description (optional, max 400 chars)", "titles_add": ["title as printed"], "not_for": "optional"}}
    {{"kind": "field_for_type", "type": "{label}", "field": "<a name from FIELD LIST>", "how_often": "always|usually|sometimes", "note": "max 160 chars, e.g. which customers print it and how it is labelled"}}
    {{"kind": "new_field", "type": "{label}", "field": {{"name": "snake_case", "kind": "id|text|amount|qty|date", "meaning": "what it is", "printed_as": ["label as printed"], "example": {{"value": "...", "source_text": "exactly as printed on THIS page"}}}}, "how_often": "sometimes", "note": "..."}}
    {{"kind": "none"}} if no change would help
}}
Rules: a field that already exists in FIELD LIST under any name or meaning is "field_for_type", never "new_field"
(e.g. No. Pesanan is po_number, DO# is sor). A new field must be printed on THIS page. Never change what existing
fields mean. Keep text short. One change only.

CONTEXT: {json.dumps(types, ensure_ascii=False)}
FIELD LIST: {json.dumps(fields, ensure_ascii=False)}
WHAT THE AI OCR READ (all Jev saw): {json.dumps(state, ensure_ascii=False)}
"""


def jev_decides(fields_all, ctx, qr_text, layout_score, bid, n, purpose):
    state = vf.jev_state(fields_all, ctx)
    t0 = time.time()
    jev = classify.jev_ask(state, context.jev_question(ctx))
    vf.ledger("jev", purpose, bid, n, bool(jev.get("choice")), {"model": jev.get("model"),
              "ms": int((time.time() - t0) * 1000)}, jev.get("error"))
    qr_sor = bool(qr_text and classify.SOR_RE.match(qr_text))
    status, doc_type, _, _ = vf.decide(jev, qr_sor, layout_score)
    return doc_type if status == "decided" else None


def replay(old, new, trial=None):
    """Jev with the old and the new context, side by side, on practice labels and anchors. trial = {page: reading}
    for pages re-read with a new field."""
    with db.connect() as c:
        practice = c.execute("""SELECT p.batch_id, p.page_no, p.fields_all, p.qr_text, p.layout_score, l.label::text AS truth
                                FROM staging.page p JOIN staging.type_label l USING (batch_id, page_no)
                                WHERE l.pile='practice' AND p.fields_all IS NOT NULL""").fetchall()
        anchors = c.execute("""SELECT batch_id, page_no, fields_all, qr_text, layout_score, type_votes FROM staging.page
                               WHERE fields_all IS NOT NULL AND type_votes->'machine'->>'status' = 'decided'
                                 AND type_votes->'machine'->>'doc_type' = 'FP'
                                 AND ((type_votes->>'qr_sor')::boolean OR layout_score >= %s)""",
                            (classify.LAYOUT_FP,)).fetchall()
    g = {"practice_pages": len(practice), "right_before": 0, "right_after": 0, "unsure_before": 0, "unsure_after": 0,
         "wrong_before": 0, "wrong_after": 0, "new_wrong": [], "anchor_pages": len(anchors), "anchor_flips": []}

    def grade(t, truth):
        return "unsure" if t is None else ("right" if t == truth else "wrong")
    for r in practice:
        b = grade(jev_decides(r["fields_all"], old, r["qr_text"], r["layout_score"], r["batch_id"], r["page_no"],
                              "replay"), r["truth"])
        reading = (trial or {}).get(r["page_no"], r["fields_all"])
        a = grade(jev_decides(reading, new, r["qr_text"], r["layout_score"], r["batch_id"], r["page_no"], "replay"),
                  r["truth"])
        g[f"{b}_before"] += 1
        g[f"{a}_after"] += 1
        if a == "wrong" and b != "wrong":
            g["new_wrong"].append(r["page_no"])
    for r in anchors:
        a = jev_decides(r["fields_all"], new, r["qr_text"], r["layout_score"], r["batch_id"], r["page_no"], "replay")
        if a != "FP":
            g["anchor_flips"].append(r["page_no"])
    g["passed"] = not g["new_wrong"] and not g["anchor_flips"] and g["unsure_after"] <= g["unsure_before"]
    return g


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


def run_one(lesson):
    bid, n = lesson["batch_id"], lesson["page_no"]
    with db.connect() as c:
        page = c.execute("""SELECT p.*, l.pile, l.note, l.label::text AS label FROM staging.page p
                            JOIN staging.type_label l USING (batch_id, page_no)
                            WHERE p.batch_id=%s AND p.page_no=%s""", (bid, n)).fetchone()
        ctx_v, ctx = context.ensure(c, classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS,
                                    classify.JEV_TYPES)

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
    state, jev = vf.jev_state(page["fields_all"], ctx), (page["type_votes"] or {}).get("jev") or {}
    try:
        answer, meta = teacher.ask(v1.png_bytes(v1.load(page["upright_path"])),
                                   prompt(ctx, page["label"], page["note"], state, jev))
        vf.ledger("zai", "teach", bid, n, True, meta)
    except Exception as e:
        vf.ledger("zai", "teach", bid, n, False, error=f"{type(e).__name__}: {e}"[:300])
        return done("failed", error=f"{type(e).__name__}: {e}"[:300])
    change = answer.get("change") or {"kind": "none"}
    if change.get("kind") == "none":
        return done("no_change", answer=answer)
    change["type"] = page["label"]                    # the teacher may only change the labelled type
    new, problems = context.apply_change(ctx, change)
    if change.get("kind") == "new_field":
        problems += new_field_problems(change, page)
    if problems:
        return done("failed", answer=answer, error="; ".join(problems)[:500])
    trial = None
    if change.get("kind") == "new_field":            # the AI OCR must read the new field before the replay means much
        reading, _ = vf.gemini("read_all", bid, n, vlm.extract_all, v1.png_bytes(v1.load(page["upright_path"])),
                               context.vlm_schema(new))
        trial = {n: reading}
    gate = replay(ctx, new, trial)
    with db.connect() as c:
        version = context.propose(c, new, ctx_v, f"teacher:{teacher.MODEL}",
                                  f"{change['kind']} for {page['label']} from page {n}")
        c.execute("UPDATE staging.context_version SET gate=%s WHERE version=%s", (Json(gate), version))
    return done("proposed", answer=answer, proposal=version)


def run(limit=5):
    with db.connect() as c:
        lessons = c.execute("SELECT * FROM staging.lesson WHERE status='waiting' ORDER BY created_at LIMIT %s",
                            (limit,)).fetchall()
    for l in lessons:
        print(f"page {l['page_no']} ({l['label']}): {run_one(l)}")
    if not lessons:
        print("no waiting lessons")


def score_exam(version):
    """After a person approved a context: how does it do on the exam pile it was never built from? (report only)"""
    with db.connect() as c:
        ctx = c.execute("SELECT content FROM staging.context_version WHERE version=%s", (version,)).fetchone()["content"]
        exam = c.execute("""SELECT p.batch_id, p.page_no, p.fields_all, p.qr_text, p.layout_score, l.label::text AS truth
                            FROM staging.page p JOIN staging.type_label l USING (batch_id, page_no)
                            WHERE l.pile='exam' AND p.fields_all IS NOT NULL""").fetchall()
    out = {"pages": len(exam), "right": 0, "unsure": 0, "wrong": 0}
    for r in exam:
        t = jev_decides(r["fields_all"], ctx, r["qr_text"], r["layout_score"], r["batch_id"], r["page_no"], "exam")
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
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
    elif cmd == "exam":
        print(score_exam(int(sys.argv[2])))
