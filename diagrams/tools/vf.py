# -*- coding: utf-8 -*-
"""vlm-first diagrams (branch vlm-first), as built. Usage: python3 vf.py <out_dir> <viewer_template.html>

  vlm-first.html            overview: v1's pages → clone → vf-worker → ocr_vf, the UI, and the learning loop
  detail-vf-page.html       one page: prepare → AI OCR reads everything → Jev → Tesseract → check → look again
  detail-vf-teacher.html    a label → lesson → GLM → one change → replay → a person approves → active context
"""
import io, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow import Flow, page
import xml.etree.ElementTree as ET

OUT = sys.argv[1]
TPL = io.open(sys.argv[2], encoding="utf-8").read()
UI = "http://localhost:8001"
HINT = ("Click a box marked <b>details ›</b> to open its own diagram · <b>open ↗</b> opens the live screen in the "
        "vlm-first UI (port 8001).")


def write(name, title, f, views, cards, back, back_label):
    svg = f.svg(title)
    ET.fromstring(svg)
    ids = {n[0] for n in f.nodes}
    for e in f.edges:
        assert e[1] in ids and e[2] in ids, e
    for v in views:
        for x in v["focus"]:
            assert x in ids, (name, x)
    io.open(os.path.join(OUT, name), "w", encoding="utf-8").write(
        page(TPL, title, svg, views, cards, back=back, hint=HINT, back_label=back_label))
    print("wrote", name, len(f.nodes), "boxes", len(f.edges), "lines")


# ============================================================ OVERVIEW
f = Flow()
f.node("admin", "external", 0, 0, "Finance admin", "Labels pages, approves", "changes to Jev's context")
f.node("v1", "database", 1, 0, "v1 (unchanged)", "Upload → n8n → page images", "Its DB is read-only here",
       link="ocr-pipeline.html")
f.node("clone", "backend", 2, 0, "Clone", "Page identities + labels", "Nothing v1 computed")
f.node("qvf", "messagebus", 3, 0, "q.pages · vhost vf", "1 ticket per page", "Separate from v1's queue",
       live="http://localhost:15672/#/queues")
f.node("page", "backend", 4, 0, "vf-worker: one page", "AI OCR → Jev → Tesseract", "→ check → look again",
       link="detail-vf-page.html")
f.node("gemini", "cloud", 5, 0, "AI OCR model", "Qwen on Groq (VF_AI_OCR)", "or Gemini: one setting")
f.node("ui", "frontend", 1, 1, "vf UI :8001", "Batch · page · Compare v1", "Acceptance checks", live=UI + "/batches")
f.node("db", "database", 3, 1, "Postgres · ocr_vf", "page · field_check · model_call", "Shared server, own DB")
f.node("registry", "database", 5, 1, "Jev's context", "Combined list = union", "of the types' fields",
       link="detail-vf-teacher.html", live=UI + "/context")
f.node("label", "frontend", 1, 2, "Label screen", "Unsure page → a person", "Resumes the page", live=UI + "/label")
f.node("jev", "cloud", 5, 2, "Jev (TypeSafe)", "Classifies from the reading", "Replays proposals")
f.node("lesson", "security", 2, 3, "Lesson → q.lessons", "Practice, machine missed", "Never an exam label",
       link="detail-vf-teacher.html")
f.node("teacher", "cloud", 3, 3, "GLM-4.6V-Flash (Z.ai)", "Explains the label", "Proposes one change",
       link="detail-vf-teacher.html")
f.node("gate", "security", 4, 3, "Replay gate", "Old vs new context", "No new wrong answer")
f.node("ctx", "frontend", 5, 3, "Context screen", "A person approves", "or rejects", live=UI + "/context")
f.edge("admin", "v1", "PDF").edge("v1", "clone", "read-only").edge("clone", "qvf", "tickets")
f.edge("qvf", "page", "1 page", "emphasis")
f.edge("page", "gemini", "ask", route="hvh", out=-18, into=-18)
f.edge("gemini", "page", "answer", "dashed", route="hvh", out=18, into=18)
f.edge("registry", "page", "field list + context", "emphasis", route="hvh", via=1082, into=30)
f.edge("page", "db", "page row + checks", route="vh", out=-45)
f.edge("page", "jev", "reading → type", route="vh", out=45, into=-18)
f.edge("db", "ui", "reads").edge("admin", "ui", "uses", route="vh").edge("ui", "label", "unsure pages")
f.edge("label", "lesson", "practice, missed", route="vh")
f.edge("lesson", "teacher", "vf-teacher").edge("teacher", "gate", "one change").edge("gate", "ctx", "passed", "emphasis")
f.edge("gate", "jev", "replay", "dashed", route="vh", out=20, into=18)
f.edge("ctx", "registry", "approved", "emphasis", route="right", via=1297)
f.frame("vf UI (port 8001)", ["ui", "label"], tcls="t-frontend")
f.frame("Learning loop: nothing changes until a person approves", ["lesson", "teacher", "gate", "ctx"],
        tcls="t-security")
write("vlm-first.html", "vlm-first: AI OCR reads everything, Jev classifies from it", f,
  [{"id": "shared", "label": "Starting from v1's pages", "focus": ["admin", "v1", "clone", "qvf"],
    "note": "vlm-first reads the same page images v1 rendered, but copies nothing v1 computed: it prepares, reads, "
            "classifies and checks every page itself."},
   {"id": "one-page", "label": "One page", "focus": ["qvf", "page", "gemini", "jev", "registry", "db"],
    "note": "The AI OCR reads every field on the combined list, Jev classifies from that reading, then Tesseract "
            "checks each value and the AI OCR looks again at what it couldn't back. Only then does a person see it."},
   {"id": "learning", "label": "Learning loop", "focus": ["label", "lesson", "teacher", "gate", "ctx", "registry", "jev"],
    "note": "A person's practice label on a page the machine missed becomes a lesson, and wakes the teacher service "
            "(q.lessons → vf-teacher, one lesson at a time). The teacher proposes one change; "
            "it is replayed old vs new, and only a person makes it active."}],
  [("emerald", "Built and running (branch vlm-first)",
    ["Own database ocr_vf, queue vhost vf, images under vf/: v1 is untouched",
     "The AI OCR is one setting (VF_AI_OCR): Qwen on Groq now, Gemini or others possible",
     "One combined field list: 29 per-type fields → 18 + 2 clues for Jev",
     "Jev's context is versioned; the combined list = the union of the types' fields",
     "Label → lesson → GLM → replay → a person approves"]),
   ("cyan", "Measured: dry run, 19 pages",
    ["v1's stored readings stood in for Gemini (today's quota was used up)",
     "All 7 FPs right; every TTG/PO unsure (v1's readings have no title)",
     "No wrong type, no wrong ✅, 38 of 38 one-digit changes caught"]),
   ("violet", "Rules kept from v1",
    ["A confident wrong answer never passes",
     "The answer key is only used in the UI and tests",
     "The teacher sees practice labels only, never exam labels",
     "The look-again is blind: never Tesseract's reading",
     "A value goes to a person only after the AI OCR has looked again"]),
   ("rose", "Waiting for",
    ["Groq's daily tokens (they refill ~8K an hour): 6 reads and 17 look-agains still waiting",
     "A free Z.ai key (ZAI_API_KEY) for the teacher",
     "More labels from Finance: only 3 practice labels today"])],
  back="ocr-pipeline.html", back_label="← v1 overview")

# ============================================================ ONE PAGE
f = Flow()
f.node("tix", "messagebus", 0, 0, "Ticket", "vhost vf", "Old run → dropped", link="vlm-first.html")
f.node("prep", "backend", 1, 0, "Prepare the image", "Upright · straighten · QR", "No Tesseract reading")
f.node("read", "backend", 2, 0, "AI OCR: all fields", "20 fields + where each is", "Reused on re-run")
f.node("jev", "backend", 3, 0, "Jev classifies", "Only the AI's reading", "Context from the registry")
f.node("dec", "security", 4, 0, "Decide", "≥ 0.85; FP: QR or layout", "Both from the image")
f.node("tess", "backend", 5, 0, "Tesseract reads", "After the type is known", "Never earlier")
f.node("chk", "security", 6, 0, "Check each value", "Printed · QR · then the 7a rules", "Never 'close enough'")
f.node("gem", "cloud", 2, 1, "AI OCR model", "Qwen on Groq, or Gemini", "One setting: VF_AI_OCR")
f.node("jevapi", "cloud", 3, 1, "Jev (TypeSafe)", "Choice over 8 types", "Text only, no image")
f.node("held", "messagebus", 4, 1, "Unsure: held", "No Tesseract reading", "Waits for a label")
f.node("zoom", "security", 6, 1, "Zoomed Tesseract look", "No reading may disagree", "At the value's spot")
f.node("label", "frontend", 4, 2, "A person labels it", "Label screen; resumes", "At Tesseract", link="detail-vf-teacher.html",
       live=UI + "/label")
f.node("again", "backend", 6, 2, "Look again (AI OCR)", "Blind: names + crops", "Kept only if print backs it")
f.node("out", "database", 6, 3, "Outcome", "clear · person · held", "or waits for the AI OCR")
f.edge("tix", "prep").edge("prep", "read", "image").edge("read", "jev", "the reading")
f.edge("jev", "dec", "type").edge("dec", "tess", "decided", "emphasis").edge("tess", "chk", "text + words")
f.edge("read", "gem", "ask", route="vhv", out=-25, into=-25).edge("gem", "read", "values", "dashed", route="vhv", out=25, into=25)
f.edge("jev", "jevapi", "ask", route="vhv", out=-25, into=-25).edge("jevapi", "jev", "answer", "dashed", route="vhv", out=25, into=25)
f.edge("dec", "held", "unsure", "dashed").edge("held", "label", "held")
f.edge("label", "tess", "labelled: resume", "emphasis", route="hv")
f.edge("chk", "zoom", "not backed").edge("zoom", "again", "still ⚠, or empty").edge("again", "out", "checked again")
f.edge("chk", "out", "all backed", "emphasis", route="right", via=1512)
f.frame("AI reads, Jev decides: Tesseract hasn't read anything yet", ["prep", "read", "jev", "gem", "jevapi"],
        tcls="t-backend")
f.frame("Tesseract checks, after classification", ["tess", "chk", "zoom"], tcls="t-security")
f.frame("When Jev is unsure", ["held", "label"], tcls="t-messagebus")
write("detail-vf-page.html", "vlm-first: one page, in the order you designed", f,
  [{"id": "read", "label": "AI reads, Jev decides", "focus": ["prep", "read", "gem", "jev", "jevapi", "dec"],
    "note": "The AI OCR reads every field on the combined list (and where each value is). Jev sees only that "
            "reading."},
   {"id": "unsure", "label": "When Jev is unsure", "focus": ["dec", "held", "label", "tess"],
    "note": "The page waits, with no Tesseract reading. A person's label decides its type and the page resumes at "
            "Tesseract."},
   {"id": "check", "label": "Check and look again", "focus": ["tess", "chk", "zoom", "again", "out"],
    "note": "A value is ✅ only when print backs it, and the 7a rules don't take it away. Otherwise the AI OCR "
            "looks again, blind, and its new answer is checked the same way. A person sees the value only after "
            "that; if the look-again can't run now (skipped, daily limit, a failed call), the page waits for it."}],
  [("emerald", "What counts as ✅",
    ["Printed in Tesseract's reading of the page (one line, never inside a longer number)",
     "The FP's SOR equals its QR code",
     "Or Tesseract finds it zoomed in at its spot, and no reading of that spot disagrees",
     "An FP amount by DPP + PPN = Total only when the other two are backed by print, Satellite or a person, and "
     "the sum is exact (7a)",
     "Amounts: code reads the number from the printed text (111.586 = 111586); one cut off at the scan's edge "
     "(1.014.424,5) never gets ✅"]),
   ("rose", "7a: rules that only take ✅ away (common/gates.py)",
    ["An FP amount counts only printed in full (1.126.011,00): the scan's edge cuts some (p10 \"106.86\")",
     "Three whole FP amounts that don't add up keep no ✅ that print alone gave (p8)",
     "A PO total includes PPN: total − PPN must give that PPN back (Hero's TOTAL NET PURCHASE doesn't, p18)",
     "A table quantity or unit never gets ✅ from its row alone: the row also prints the other columns (p12's 2)",
     "No date after the day the stack was scanned"]),
   ("amber", "Two readers can share a mistake",
    ["Page 22 prints S10232; Gemini read 510232 and so did Tesseract zoomed in",
     "Tesseract's whole-page reading there said $10232: two readings disagree, so no ✅",
     "Zoomed reads misread too (p1, p9): they never cancel a whole-page ✅"]),
   ("cyan", "The look-again is blind",
    ["The AI OCR gets the field names, their meanings and zoomed crops",
     "Never Tesseract's reading, never its own first answer",
     "Same answer twice, or a changed one, stays ⚠ unless print backs it",
     "Never skipped: if it can't run now, the page waits for it (python -m worker.vf again)"]),
   ("violet", "Why Tesseract waits",
    ["Your design: Tesseract only checks, after the type is known",
     "Only a quick orientation check (90° or 270°) runs earlier, during preparation"])],
  back="vlm-first.html", back_label="← vlm-first overview")

# ============================================================ TEACHER
f = Flow()
f.node("lab", "frontend", 0, 0, "A person labels", "Label screen", "Type + a note", live=UI + "/label")
f.node("pile", "security", 1, 0, "Pile, drawn once", "80% practice · 20% exam", "v1's pile kept if it has one")
f.node("lesson", "messagebus", 2, 0, "Lesson → q.lessons", "Wakes vf-teacher", "One lesson at a time")
f.node("teach", "cloud", 3, 0, "GLM-4.6V-Flash", "Image + label + reading", "+ Jev's whole context")
f.node("change", "security", 4, 0, "One change?", "Only allowed kinds", "Checked by code")
f.node("gate", "security", 5, 0, "Replay", "Old vs new context", "Practice labels + anchors")
f.node("screen", "frontend", 6, 0, "Context screen", "Diff + replay numbers", "Approve or reject",
       live=UI + "/context")
f.node("exam", "external", 1, 1, "Exam pile", "Never shown to GLM", "Scored after approval")
f.node("refused", "external", 4, 1, "Refused", "Not allowed · changes nothing", "Or not printed")
f.node("stay", "external", 5, 1, "Didn't help", "One more try, told why", "Then the lesson fails")
f.node("active", "database", 6, 1, "Active context", "Combined list = union", "of the types' fields")
f.node("examsc", "security", 5, 2, "Exam scored", "After approval", "Report only")
f.node("aiocr", "backend", 6, 2, "AI OCR's field list", "Reads new fields", "on the next page", link="detail-vf-page.html")
f.node("jevq", "backend", 7, 2, "Jev's criteria", "Generated per type", "on the next page", link="detail-vf-page.html")
f.edge("lab", "pile").edge("pile", "lesson", "practice").edge("pile", "exam", "exam", "dashed")
f.edge("lesson", "teach", "vf-teacher").edge("teach", "change", "evidence + reason")
f.edge("change", "gate", "valid").edge("change", "refused", "not allowed", "dashed")
f.edge("gate", "screen", "passed", "emphasis").edge("gate", "stay", "didn't pass", "dashed")
f.edge("screen", "active", "approved", "emphasis").edge("active", "aiocr")
f.edge("active", "jevq", route="hv", out=20).edge("active", "examsc", "then", route="vh")
f.frame("Only practice labels reach the teacher", ["pile", "lesson", "exam"], tcls="t-security")
f.frame("Nothing changes until a person approves", ["gate", "screen", "stay", "active"], tcls="t-frontend")
write("detail-vf-teacher.html", "vlm-first: how a label teaches Jev", f,
  [{"id": "lesson", "label": "Label → lesson", "focus": ["lab", "pile", "lesson", "exam"],
    "note": "Only a practice-pile label on a page the machine was unsure or wrong about becomes a lesson. Exam labels "
            "are refused in code before any call."},
   {"id": "teach", "label": "The teacher", "focus": ["lesson", "teach", "change", "refused"],
    "note": "GLM sees the page, the label, what the AI OCR read, Jev's answer and Jev's whole context, and proposes "
            "one change. If it is refused or doesn't help, GLM gets one more try and is told why."},
   {"id": "gate", "label": "Replay and approval", "focus": ["gate", "screen", "stay", "active", "examsc", "aiocr", "jevq"],
    "note": "Jev is asked with the old and the new context side by side. Only a change that helps becomes a "
            "proposal, and only a person makes it active; then the AI OCR and Jev use it on the next page. One "
            "proposal at a time: the next lesson is asked against whatever the person approved."}],
  [("emerald", "One registry, so the lists can't drift",
    ["The AI OCR's field list and Jev's descriptions come from the same context version",
     "Combined list = the union of every type's fields, checked on every save",
     "The worker refuses to start on an invalid context"]),
   ("cyan", "Two kinds of new field",
    ["New for this type: already on the list (some TTGs print po_number): only that type's list changes",
     "Genuinely new: joins the list and the type; its example must be printed, must not repeat another field, "
     "and a person confirms"]),
   ("amber", "The replay",
    ["Practice labels: truth is the label. Anchors: FPs decided by the QR code or the FP layout",
     "Pass: more labelled pages right, none becomes wrong, no anchor changes, unsure doesn't go up",
     "\"No worse\" isn't enough: GLM's first try on page 2 changed nothing and was refused"]),
   ("violet", "The teacher may never change",
    ["Rules and thresholds", "What an existing field means", "The stored fields or the set of document types"]),
   ("rose", "Approving safely",
    ["A proposal built on an older context is refused: approving it would undo what was approved since",
     "A lesson a newer context already gets right is closed without asking GLM"])],
  back="vlm-first.html", back_label="← vlm-first overview")
