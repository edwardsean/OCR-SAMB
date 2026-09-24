# -*- coding: utf-8 -*-
import io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow import Flow, page
import xml.etree.ElementTree as ET

OUT = sys.argv[1]
TPL = io.open(sys.argv[2], encoding='utf-8').read()
UI = "http://localhost:8000"
BACK = "ocr-pipeline.html"
HINT = "Click a box marked <b>details ›</b> to open its own diagram · <b>open ↗</b> opens the live screen in the inspection UI · <b>← Overview</b> goes back."

def write(name, title, f, views, cards):
    svg = f.svg(title)
    ET.fromstring(svg)
    ids = {n[0] for n in f.nodes}
    for e in f.edges: assert e[1] in ids and e[2] in ids, e
    for v in views:
        for x in v["focus"]: assert x in ids, (name, x)
    io.open(os.path.join(OUT, name), "w", encoding="utf-8").write(page(TPL, title, svg, views, cards, back=BACK, hint=HINT))
    print("wrote", name, len(f.nodes), "boxes", len(f.edges), "lines")

# ============================================================ CLASSIFY
f = Flow()
f.node("in", "backend", 0, 1, "Page from step 2", "Upright image + OCR words", "QR result + quality flags", link="detail-enhance.html")
f.node("kw", "backend", 1, 0, "Title words", "Short lines, not labels", "Knows customers' names")
f.node("lay", "backend", 1, 1, "FP layout", "Table lines vs SAMB's FP", "Votes FP at ≥ 0.70")
f.node("qr", "backend", 1, 2, "SOR QR code", "Only an FP carries one", "From step 2")
f.node("jev", "backend", 1, 3, "Ask Jev", "Title zone + page text", "One Choice, 8 types")
f.node("jevapi", "cloud", 1, 4, "Jev (TypeSafe)", "Text only, no image", "Answer + confidence")
f.node("r1", "security", 2, 0, "Rule 1", "SOR QR → FP", "Jev names another type → unsure")
f.node("r2", "security", 2, 1, "Rule 2 · FP", "Jev FP + a 2nd witness", "QR, FP layout or FP title")
f.node("r3", "security", 2, 2, "Rule 3", "Title and Jev agree", "Jev ≥ 0.5")
f.node("r4", "security", 2, 3, "Rule 4", "Jev ≥ 0.85", "No title says otherwise")
f.node("r5", "security", 2, 4, "Rule 5", "Title alone", "Only if Jev is down")
f.node("dec", "database", 3, 1, "Decided", "doc_type saved on the page", "Safe for grouping (phase 6)")
f.node("uns", "messagebus", 2, 5, "Unsure", "Best guess kept, never linked", "Page is held, not guessed")
f.node("lq", "frontend", 3, 5, "Label screen", "Next unsure page", "Machine's guess hidden", live=UI + "/label")
f.node("adm", "external", 4, 5, "Finance admin", "Picks type (+ customer, note)", "Skip if not sure")
f.node("pile", "security", 5, 5, "Pile drawn once", "80% practice · 20% exam", "Relabel never moves it")
f.node("tl", "database", 5, 4, "staging.type_label", "The correct answer", "Practice / exam", live=UI + "/labels")
f.edge("in", "kw", route="hvh", via=210).edge("in", "lay").edge("in", "qr", route="hvh", via=210).edge("in", "jev", route="hvh", via=210)
f.edge("jev", "jevapi", "page text", route="vhv", out=-35, into=-35).edge("jevapi", "jev", "answer", "dashed", route="vhv", out=35, into=35)
for v in ("kw", "lay", "qr", "jev"):
    f.edge(v, "r1", "all votes" if v == "kw" else "", route="hvh", via=445)
f.edge("r1", "r2", "no").edge("r2", "r3", "no").edge("r3", "r4", "no").edge("r4", "r5", "no").edge("r5", "uns", "no rule fits")
for i, r in enumerate(("r1", "r2", "r3", "r4", "r5")):
    f.edge(r, "dec", "yes" if r == "r1" else "", "emphasis", route="hvh", via=660)
f.edge("uns", "lq", "held").edge("lq", "adm").edge("adm", "pile", "answer").edge("pile", "tl", "saved")
f.frame("Four independent votes", ["kw", "lay", "qr", "jev"], tcls="t-backend")
f.frame("Decision rules, tried in order", ["r1", "r2", "r3", "r4", "r5"], tcls="t-security")
f.frame("When it can't decide: a person answers", ["lq", "adm", "pile", "tl"], tcls="t-frontend")
write("detail-classify.html", "Classify — what kind of document is this page?", f,
  [{"id":"votes","label":"The four votes","focus":["in","kw","lay","qr","jev","jevapi"],
    "note":"Each vote looks at the page a different way. Jev only ever sees text."},
   {"id":"rules","label":"Decision rules","focus":["r1","r2","r3","r4","r5","dec","uns"],
    "note":"Tried top to bottom. Jev leads; an FP needs a second witness. If none fits, the page is unsure."},
   {"id":"unsure","label":"When it's unsure","focus":["uns","lq","adm","pile","tl"],
    "note":"The page is held, never guessed. A person answers on the Label screen; the answer lands in the practice or exam pile."}],
  [("emerald","Why 'unsure' instead of a guess",
    ["Unseen customers (Indomaret POs have no title): Jev's weak guess was FP, which is wrong",
     "A wrong type would start a false bundle; unsure only costs a person a minute"]),
   ("cyan","What Jev sees",
    ["title_zone: top 35% of Tesseract's text · page_text: up to 3,000 chars · footer",
     "Not the image, QR, layout, customer or neighbouring pages; those are separate votes"]),
   ("amber","Why an FP needs two witnesses",
    ["An FP starts a bundle, so a wrong FP creates a false SOR group",
     "Jev said FP at 0.88 on SAMB's own handwritten SALES ORDER form (page 68); with no FP title, layout or QR it stays unsure"]),
   ("violet","Measured before adopting (all 288 pages, stored votes)",
    ["Unsure 73 → 54; still 0 wrong on the answer-key pages 1–32",
     "Jev alone (no title words) would have saved only 3 and made 7 of pages 1–32 unsure"]),
   ("rose","Not built yet",
    ["Using the labels: better Jev descriptions from the practice pile only, tested on all labels, you approve, exam pile confirms",
     "Grouping (phase 6) is the first step that will use a decided type"])])

# ============================================================ INTAKE
f = Flow()
f.node("up", "frontend", 0, 0, "Upload screen", "Pick the PDF", "Stands in for SCP drop", live=UI + "/upload")
f.node("seen", "security", 1, 0, "Seen before?", "SHA-256 of the file", "Same file = same batch")
f.node("minio", "database", 2, 0, "Store PDF", "MinIO scans/<day>/<batch>/", "Temp repository")
f.node("hook", "backend", 3, 0, "Call n8n webhook", "n8n replies 'started'", "Screen doesn't hang")
f.node("split", "backend", 4, 0, "n8n: Split", "POST /internal/intake/split", "Node 2 of 3")
f.node("count", "database", 5, 0, "Scoreboard row", "scan_batch: page_total = N", "status 'splitting'")
f.node("refuse", "external", 1, 1, "Refused (409)", "Links to the existing batch", "Nothing processed twice")
f.node("n8ndown", "security", 3, 1, "n8n not reachable", "Error shown on screen", "PDF already in MinIO")
f.node("render", "backend", 5, 1, "Render pages", "300 dpi, 6 at a time", "pdftoppm, black & white")
f.node("pages", "database", 5, 2, "Each page", "PNG + thumbnail → MinIO", "Page row 'rendered'")
f.node("fail", "security", 5, 3, "A page fails", "Batch 'failed' + error", "Shown on Batches screen")
f.node("done", "database", 4, 2, "Batch 'split'", "All pages rendered", "")
f.node("n8nt", "backend", 3, 2, "n8n: One ticket", "POST /internal/intake/enqueue", "Node 3 of 3")
f.node("flip", "security", 2, 2, "split → queued", "Only once", "A retry can't double-queue")
f.node("tix", "messagebus", 1, 2, "N tickets", "On q.pages", "{batch, run, page, image}", link="detail-worker.html")
f.node("rerun", "frontend", 2, 3, "Re-run button", "run + 1, scoreboard 0", "Old tickets become stale", live=UI + "/batches")
f.edge("up", "seen", "PDF").edge("seen", "minio", "new").edge("minio", "hook").edge("hook", "split", "webhook").edge("split", "count")
f.edge("seen", "refuse", "yes, seen", "dashed").edge("hook", "n8ndown", "fails", "dashed")
f.edge("count", "render").edge("render", "pages").edge("pages", "done").edge("done", "n8nt").edge("n8nt", "flip").edge("flip", "tix", "publish")
f.edge("pages", "fail", "error", "dashed").edge("rerun", "flip", "back to split")
f.frame("Done by the Upload screen (UI)", ["up", "seen", "minio", "hook", "refuse", "n8ndown"], tcls="t-frontend")
write("detail-intake.html", "Intake — from an uploaded PDF to one ticket per page", f,
  [{"id":"happy","label":"Normal upload","focus":["up","seen","minio","hook","split","count","render","pages","done","n8nt","flip","tix"],
    "note":"About 70 seconds for the 288-page sample."},
   {"id":"guards","label":"What can go wrong","focus":["seen","refuse","hook","n8ndown","pages","fail","flip","rerun"],
    "note":"Duplicates are refused, a dead n8n is reported, a failed page fails the batch visibly, and a retry can never queue pages twice."}],
  [("emerald","Why the SHA-256 check",
    ["The same PDF uploaded twice would double every page and every bundle",
     "Batch id = 'b-' + first 10 hex of the SHA-256, so it's the same id every time"]),
   ("cyan","Why n8n replies at once",
    ["Rendering 288 pages takes about a minute; the Upload screen would hang",
     "The batch page fills in live instead"]),
   ("violet","Why 'split → queued' happens only once",
    ["If n8n retries its last step, a second publish would put every page on the queue twice",
     "Only the call that changes the status actually publishes"]),
   ("rose","Not built yet",["SCP / Tailscale drop from the scanner; the Upload screen stands in"])])

# ============================================================ ENHANCE
f = Flow()
f.node("reuse", "security", 0, 0, "Read before?", "Same code version?", "enhance_version")
f.node("load", "backend", 1, 0, "Load page", "300 dpi original", "From MinIO")
f.node("meas", "backend", 2, 0, "Measure", "Black %, solid black rows", "≥ 6% rows → dark_band")
f.node("mask", "backend", 3, 0, "Blank dark bands", "For reading only", "Stops garbage text")
f.node("side", "security", 4, 0, "Sideways?", "Text lines run down the page", "Ruling lines removed first")
f.node("turn", "backend", 5, 0, "Try 90° and 270°", "Keep the better read", "Quick Tesseract read")
f.node("skip", "database", 0, 1, "Reuse stored result", "Skip every step", "Re-run takes seconds")
f.node("flip", "security", 4, 1, "Upside down?", "OSD says 180° (≥ 1.5)", "…and it reads better")
f.node("desk", "backend", 5, 1, "Straighten", "Try −4° … +4°", "Flag skewed if ≥ 1°")
f.node("read", "backend", 5, 2, "Read with Tesseract", "'close' cleanup, 2× size", "Indonesian + English")
f.node("weak", "security", 4, 2, "Weak read?", "Confidence < 55", "on real words only")
f.node("smooth", "backend", 4, 3, "Also try 'smooth'", "Keep more confident chars", "Only on weak reads")
f.node("qr", "backend", 3, 2, "Decode QR", "Top quarter, 8 attempts", "SOR on every FP")
f.node("flags", "security", 2, 2, "Set flags", "rotated · skewed · dark_band", "faint · poor_quality")
f.node("save", "database", 1, 2, "Save images", "upright · clean · thumb", "MinIO")
f.node("row", "database", 0, 2, "Page row", "text, words, QR, flags", "Then classify", link="detail-classify.html")
f.edge("reuse", "load", "no").edge("reuse", "skip", "yes", "emphasis").edge("load", "meas").edge("meas", "mask").edge("mask", "side")
f.edge("side", "turn", "yes").edge("side", "flip", "no").edge("turn", "desk").edge("flip", "desk")
f.edge("desk", "read").edge("read", "weak").edge("weak", "smooth", "yes").edge("weak", "qr", "no")
f.edge("smooth", "qr", route="hv").edge("qr", "flags").edge("flags", "save").edge("save", "row")
f.edge("skip", "row", "", "emphasis")
f.frame("Make it upright and straight", ["side", "turn", "flip", "desk"], tcls="t-backend")
f.frame("Read it", ["read", "weak", "smooth", "qr"], tcls="t-backend")
write("detail-enhance.html", "Enhance + read — one page, no AI", f,
  [{"id":"upright","label":"Upright + straight","focus":["meas","mask","side","turn","flip","desk"],
    "note":"About a third of the sample's pages are landscape documents scanned sideways onto portrait A4."},
   {"id":"read","label":"Reading","focus":["read","weak","smooth","qr","flags"],
    "note":"Every page is read with 'close' (reconnects dotted strokes) at 2×; 'smooth' is tried only when that read is weak."},
   {"id":"reuse","label":"Re-runs","focus":["reuse","skip","row"],
    "note":"If the page was already read by the same code version, everything here is skipped."}],
  [("emerald","How sideways is decided",
    ["Table borders are wiped first, then letters are smudged sideways and downwards: whichever makes long bars is the direction the text runs",
     "Measured on all 288 pages: upright ≤ 1.2, sideways ≥ 6. Tesseract's own check couldn't tell them apart; the old test got 12 pages wrong"]),
   ("cyan","Why 'close' at 2×",
    ["The scans are black-and-white only: faint print breaks into dots",
     "'close' reconnects the dots; 2× helped every page measured. The stored image stays 1×"]),
   ("amber","What it can't fix",
    ["Faint pages (6, 8): the grey was thrown away by the scanner",
     "Best fix is outside the code: scan in greyscale 300 dpi"]),
   ("violet","Measured on the sample",["≈ 10 s per page, 6 workers: ≈ 10 min for 288 pages",
                                       "32 turned upright · 21 dark bands · 25 faint · 82 QR = SOR"])])

# ============================================================ WORKER LIFE
f = Flow()
f.node("tix", "messagebus", 0, 0, "Ticket on q.pages", "{batch, run, page, image}", "From intake", link="detail-intake.html")
f.node("take", "backend", 1, 0, "A worker takes it", "6 workers, 1 page each", "prefetch = 1")
f.node("stale", "security", 2, 0, "Current run?", "ticket.run = batch.run", "Re-run makes old ones stale")
f.node("proc", "backend", 3, 0, "Steps 2 → 5", "Enhance · classify · AI OCR", "· check each value", link="detail-enhance.html")
f.node("crash", "security", 4, 0, "Crashed?", "Any error", "")
f.node("save", "security", 5, 0, "Save the page", "Only if not read yet…", "…and run still current")
f.node("count", "database", 6, 0, "Scoreboard", "Recount read pages", "Never goes down")
f.node("retry", "messagebus", 3, 1, "Back on q.pages", "Try once more", "")
f.node("first", "security", 4, 1, "First failure?", "", "")
f.node("notsaved", "external", 5, 1, "Not saved", "Someone else read it,", "or an older run")
f.node("won", "security", 6, 1, "Last page?", "reading → read", "Only one worker wins")
f.node("drop", "external", 2, 2, "Drop it", "Acknowledge, do nothing", "")
f.node("dlq", "messagebus", 4, 2, "q.pages.dlq", "Page 'dead_letter'", "Error text kept")
f.node("ack", "backend", 5, 2, "Acknowledge", "Ticket removed", "Take the next one")
f.node("bell", "messagebus", 6, 2, "Bell on q.group", "{batch, run}", "Exactly once per run")
f.node("none", "external", 6, 3, "Nobody listens yet", "Grouping worker", "Phase 6")
f.edge("tix", "take").edge("take", "stale").edge("stale", "proc", "yes").edge("proc", "crash").edge("crash", "save", "no")
f.edge("save", "count", "saved", "emphasis").edge("save", "notsaved", "not saved")
f.edge("stale", "drop", "no", "dashed", lp=(545, 183))
f.edge("crash", "first", "yes", "dashed").edge("first", "retry", "yes").edge("first", "dlq", "no, 2nd time", "dashed")
f.edge("retry", "take", "again", "dashed", route="hv", lp=(440, 253))
f.edge("count", "won").edge("won", "bell", "yes", "emphasis").edge("won", "ack", "no", route="vhv", via=365, into=-20)
f.edge("notsaved", "ack", "", route="v", ).edge("bell", "ack").edge("bell", "none")
write("detail-worker.html", "One ticket's life — retries, stale runs, scoreboard, the bell", f,
  [{"id":"happy","label":"Normal page","focus":["tix","take","stale","proc","crash","save","count","won","ack"],
    "note":"Most tickets go straight through: process, save, recount, acknowledge."},
   {"id":"fail","label":"When a page fails","focus":["crash","first","retry","take","dlq"],
    "note":"One retry. A second failure goes to the dead-letter queue with its error, never silently lost."},
   {"id":"bell","label":"The N-of-N bell","focus":["save","count","won","bell","none"],
    "note":"Only the worker that turns the batch from reading to read rings the bell, so it rings exactly once."}],
  [("emerald","Why the run number",
    ["A re-run while old tickets are still queued would process pages twice",
     "Old tickets fail the 'current run?' check and are dropped in milliseconds"]),
   ("cyan","Why recount, not +1",
    ["A +1 counter once reached 288 while page 286 was unread and rang the bell early",
     "Now: count the pages actually read, and never let the number go down"]),
   ("violet","Why exactly one bell",
    ["The bell is a status change reading → read that only one worker can make",
     "Tested: 40 pages finishing at the same instant, 20 times, one bell every time"]),
   ("rose","Not built yet",["The grouping worker that answers the bell (phase 6)"])])

# ============================================================ AI OCR + CHECK (phases 4 and 5)
f = Flow()
f.node("in", "backend", 0, 0, "Page from step 3", "Type decided or unsure", "+ Tesseract text + QR", link="detail-classify.html")
f.node("ask", "backend", 1, 0, "Ask Gemini", "Page image + this type's", "field list (fields.py)")
f.node("answered", "security", 2, 0, "Answered?", "20 calls/model/day, 3 models", "Busy → next model")
f.node("agree", "security", 3, 0, "Value = as printed?", "1.126.011,00 → 1126011.00", "Each value, one by one")
f.node("qr", "security", 4, 0, "FP's SOR vs QR", "Only the FP has a QR", "Same → ✅, differs → ⚠")
f.node("text", "security", 5, 0, "In Tesseract's text?", "Same line, letters + digits", "Never inside a longer number")
f.node("ok", "database", 6, 0, "✅ Confirmed", "Saved with how:", "printed · QR · adds up")
f.node("uns", "messagebus", 0, 1, "Unsure page", "Best guess kept", "Not extracted yet")
f.node("jev2", "backend", 1, 1, "Ask Jev again", "With Gemini's transcript", "Same rules as step 3")
f.node("failed", "external", 2, 1, "Recorded 'failed'", "Page still counts as read", "Re-run 1–31 after 14:00 WIB")
f.node("adds", "security", 5, 1, "FP amounts add up?", "DPP + PPN = Total", "PPN = 11% of DPP")
f.node("readall", "backend", 0, 2, "Gemini reads it", "Title · issuer · transcript", "Doesn't pick a type")
f.node("held", "external", 2, 2, "Still unsure", "Skipped: a person labels it", "Label screen", live=UI + "/label")
f.node("person", "messagebus", 4, 2, "⚠ A person checks it", "With the reason", "Review screen (phase 7)")
f.edge("in", "ask", "decided").edge("in", "uns", "unsure").edge("uns", "readall")
f.edge("readall", "jev2", route="hv", into=-30).edge("jev2", "ask", "now decided", "emphasis")
f.edge("jev2", "held", "still unsure", route="vh", out=30)
f.edge("ask", "answered").edge("answered", "agree", "yes").edge("answered", "failed", "no", "dashed")
f.edge("agree", "qr", "yes").edge("agree", "person", "no", route="vhv", via=325, into=-30)
f.edge("qr", "text", "no QR").edge("qr", "ok", "same", "emphasis", route="vhv", via=35).edge("qr", "person", "differs")
f.edge("text", "ok", "yes", "emphasis").edge("text", "adds", "no")
f.edge("adds", "ok", "yes", "emphasis", route="hv").edge("adds", "person", "no", route="vh")
f.frame("Unsure pages: a second try", ["uns", "jev2", "readall"], tcls="t-messagebus")
f.frame("Phase 5 · check each value — plain code, no AI", ["agree", "qr", "text", "adds", "ok", "person"], tcls="t-security")
write("detail-extract.html", "AI OCR reads the fields, then code checks every value", f,
  [{"id":"read","label":"Reading","focus":["in","ask","answered","failed","agree"],
    "note":"Gemini gets the page image and the field list for its type, and returns every value with the text exactly as printed."},
   {"id":"unsure","label":"Unsure pages","focus":["in","uns","readall","jev2","ask","held"],
    "note":"Gemini reads the page without choosing a type; Jev decides again from that transcript. Still unsure → a person."},
   {"id":"check","label":"The check","focus":["agree","qr","text","adds","ok","person"],
    "note":"Each value is ✅ only if something printed backs it: Tesseract's text, the QR, or FP amounts that add up. Otherwise a person looks."}],
  [("emerald","What counts as ✅",
    ["Printed in Tesseract's text, on one line (or wrapped onto the next); never part of a longer number",
     "The FP's SOR equals its QR code",
     "The FP's DPP + PPN = Total and PPN = 11% of DPP (independent numbers agreeing)"]),
   ("amber","Never 'close enough'",
    ["Page 22: printed S10232, Tesseract read $10232, the AI 510232 → a person",
     "Page 4: Tesseract read 11.126.006, the AI 1.126.006 → a person, even though the AI was right"]),
   ("cyan","Measured on pages 1–31 (19 read so far)",
    ["Main fields: 92 ✅ · 26 to a person · no wrong value got ✅",
     "Page 8's misread SOR (faint print) goes to a person",
     "Line items: Tesseract can't read most table rows → mostly ⚠; phase 7 compares FP / TTG / PO"]),
   ("violet","Limits and re-runs",
    ["Gemini free tier: 20 calls per model per day; resets 14:00 WIB",
     "Re-runs reuse stored values (no new call); the check reruns in seconds: worker.reverify"]),
   ("rose","Not built yet",["The Review screen where a person fixes ⚠ values (phase 7)"])])
