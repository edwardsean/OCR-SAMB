# -*- coding: utf-8 -*-
"""As-built overview: only what exists and runs today. Usage: python3 overview.py <out_dir> <viewer_template.html>"""
import io, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xml.etree.ElementTree as ET
import archify
from archify import build
from flow import add_link_chips, page

archify.NH = 80
OUT, TPL = sys.argv[1], io.open(sys.argv[2], encoding="utf-8").read()
UI = "http://localhost:8000"
TITLE = "SAMB OCR — what is built today (phases 0–5)"

NODES = [
 ("jev",      "cloud",      1160,  30, 150, "Jev (TypeSafe)",     "Choice over 8 types",        "Page text only, no image", "Hosted"),
 ("admin",    "external",     20, 200, 150, "Finance admin",      "Uploads scans, labels pages","A person",                 "Outside"),
 ("upload",   "frontend",    210, 200, 150, "Upload screen",      "Stands in for SCP drop",     "Same file twice → refused","Docker · UI"),
 ("label",    "frontend",    210, 380, 150, "Label screen",       "Unsure pages → a person",    "Practice 80% · exam 20%",  "Docker · UI"),
 ("views",    "frontend",    210, 560, 150, "Batch + page views", "Acceptance checks",          "Only place answer key is used","Docker · UI"),
 ("n8n",      "backend",     400, 200, 150, "n8n intake",         "Webhook → split → enqueue",  "Replies at once",          "Docker"),
 ("split",    "backend",     590, 200, 150, "Split + enqueue",    "300 dpi pages, 6 at a time", "common/intake.py",         "Docker"),
 ("qpages",   "messagebus",  780, 200, 150, "q.pages",            "1 ticket per page",          "Ticket carries run no.",   "Docker · RabbitMQ"),
 ("dlq",      "messagebus",  780, 380, 150, "q.pages.dlq",        "Failed twice",               "0 on the sample",          "Docker · RabbitMQ"),
 ("enhance",  "backend",     970, 200, 150, "Enhance + read",     "Upright · Tesseract · QR",   "Phase 2 · reused on re-run","Docker · Worker ×6"),
 ("classify", "backend",    1160, 200, 150, "Classify",           "Keywords · layout · Jev",    "Decided or unsure",        "Docker · Worker ×6"),
 ("aiocr",    "backend",    1160, 380, 150, "AI OCR + check",     "Gemini reads the fields",    "Code checks every value",  "Docker · Worker ×6"),
 ("gemini",   "cloud",      1370, 380, 150, "Gemini (Google)",    "Reads the page image",       "Free: 20 calls/model/day", "Hosted"),
 ("fanin",    "backend",     970, 380, 150, "Scoreboard",         "Recount, run-aware",         "Exactly one bell",         "Docker · Worker ×6"),
 ("qgroup",   "messagebus", 1160, 560, 150, "q.group",            "Bell at N of N",             "No listener yet (phase 6)","Docker · RabbitMQ"),
 ("minio",    "database",    590, 380, 150, "MinIO",              "PDF + 4 images per page",    "Temp repository",          "Docker"),
 ("pg",       "database",    780, 560, 150, "Postgres · staging", "scan_batch · page · labels", "satellite.* still empty",  "Docker"),
]
EDGES = [
 ("e_up",   "admin",   "upload",  "PDF",                  "emphasis", "170,240;210,240", "M 170 240 L 210 240", None),
 ("e_lab",  "admin",   "label",   "answers",              "default",  "95,280;95,420;210,420", "M 95 280 L 95 412 Q 95 420 103 420 L 210 420", (95,350)),
 ("e_hook", "upload",  "n8n",     "",                     "emphasis", "360,240;400,240", "M 360 240 L 400 240", None),
 ("e_pdf",  "upload",  "minio",   "PDF",                  "default",  "360,262;380,262;380,412;590,412",
   "M 360 262 L 372 262 Q 380 262 380 270 L 380 404 Q 380 412 388 412 L 590 412", (480,412)),
 ("e_split","n8n",     "split",   "POST",                 "emphasis", "550,240;590,240", "M 550 240 L 590 240", (570,300)),
 ("e_imgs", "split",   "minio",   "page images",          "default",  "665,280;665,380", "M 665 280 L 665 380", (665,330)),
 ("e_rows", "split",   "pg",      "batch + page rows",    "default",  "740,262;760,262;760,592;780,592",
   "M 740 262 L 752 262 Q 760 262 760 270 L 760 584 Q 760 592 768 592 L 780 592", (760,520)),
 ("e_tix",  "split",   "qpages",  "",                     "emphasis", "740,240;780,240", "M 740 240 L 780 240", None),
 ("e_take", "qpages",  "enhance", "1 page each",          "default",  "930,240;970,240", "M 930 240 L 970 240", (950,300)),
 ("e_dlq",  "qpages",  "dlq",     "failed twice",         "dashed",   "855,280;855,380", "M 855 280 L 855 380", (855,330)),
 ("e_next", "enhance", "classify","image + text",         "default",  "1120,240;1160,240", "M 1120 240 L 1160 240", (1140,300)),
 ("e_img2", "enhance", "minio",   "upright + clean",      "default",  "970,262;950,262;950,480;665,480;665,460",
   "M 970 262 L 958 262 Q 950 262 950 270 L 950 472 Q 950 480 942 480 L 673 480 Q 665 480 665 472 L 665 460", (830,480)),
 ("e_ask",  "classify","jev",     "page text",            "default",  "1210,200;1210,110", "M 1210 200 L 1210 110", (1210,160)),
 ("e_ans",  "jev",     "classify","type + probabilities", "dashed",   "1275,110;1275,200", "M 1275 110 L 1275 200", (1275,145)),
 ("e_x",    "classify","aiocr",   "decided type",         "default",  "1235,280;1235,380", "M 1235 280 L 1235 380", (1235,330)),
 ("e_vlm",  "aiocr",   "gemini",  "image",                "default",  "1310,405;1370,405", "M 1310 405 L 1370 405", (1340,397)),
 ("e_vals", "gemini",  "aiocr",   "values",               "dashed",   "1370,440;1310,440", "M 1370 440 L 1310 440", (1340,452)),
 ("e_done", "aiocr",   "fanin",   "",                     "default",  "1160,420;1120,420", "M 1160 420 L 1120 420", None),
 ("e_row",  "fanin",   "pg",      "page row",             "default",  "990,460;990,610;930,610",
   "M 990 460 L 990 602 Q 990 610 982 610 L 930 610", (990,535)),
 ("e_bell", "fanin",   "qgroup",  "bell, once",           "emphasis", "1060,460;1060,600;1160,600",
   "M 1060 460 L 1060 592 Q 1060 600 1068 600 L 1160 600", (1112,600)),
 ("e_lbl",  "label",   "pg",      "type_label",           "default",  "360,440;548,440;548,580;780,580",
   "M 360 440 L 540 440 Q 548 440 548 448 L 548 572 Q 548 580 556 580 L 780 580", (650,580)),
 ("e_read", "pg",      "views",   "reads",                "default",  "780,622;360,622", "M 780 622 L 360 622", (450,622)),
]
FRAMES = [
 ("region",         0, "Hosted — TypeSafe",         1140,  10,  190, 120, "c-region",         "t-cloud",    12),
 ("security-group", 1, "Docker compose (your Mac)",  190, 164, 1150, 510, "c-security-group", "t-security",  8),
 ("region",         2, "Inspection UI",              198, 182,  174, 470, "c-subgroup",       "t-frontend", 12),
 ("region",         3, "Page worker ×6",             958, 182,  364, 290, "c-subgroup",       "t-backend",  12),
 ("region",         4, "Hosted — Google",           1350, 360,  190, 120, "c-region",         "t-cloud",    12),
]
LINKS = {
 "upload":  {"detail": "detail-intake.html",   "live": UI + "/upload"},
 "n8n":     {"detail": "detail-intake.html",   "live": "http://localhost:5678"},
 "split":   {"detail": "detail-intake.html"},
 "enhance": {"detail": "detail-enhance.html"},
 "classify":{"detail": "detail-classify.html"},
 "label":   {"detail": "detail-classify.html", "live": UI + "/label"},
 "jev":     {"detail": "detail-classify.html"},
 "qpages":  {"detail": "detail-worker.html",   "live": "http://localhost:15672/#/queues"},
 "dlq":     {"detail": "detail-worker.html"},
 "fanin":   {"detail": "detail-worker.html"},
 "aiocr":   {"detail": "detail-extract.html"},
 "gemini":  {"detail": "detail-extract.html"},
 "qgroup":  {"detail": "detail-worker.html"},
 "pg":      {"detail": "../docs/img/staging.svg"},
 "views":   {"live": UI + "/batches"},
 "minio":   {"live": "http://localhost:9001"},
}
VIEWS = [
 {"id":"intake","label":"Upload → tickets","focus":["admin","upload","n8n","split","minio","pg","qpages"],
  "note":"One PDF becomes page images, page rows and one ticket per page. Same file twice is refused."},
 {"id":"one-page","label":"One page's journey","focus":["qpages","enhance","classify","jev","aiocr","gemini","fanin","pg","minio"],
  "note":"A worker takes one ticket: turn upright, read with Tesseract, decode the QR, classify, read the fields, check them, save, tick the scoreboard."},
 {"id":"fields","label":"Fields + checks","focus":["classify","aiocr","gemini","fanin","pg"],
  "note":"Gemini reads the fields of each decided page. Plain code then checks every value against Tesseract's text, the QR and the FP's sums; anything unconfirmed waits for a person."},
 {"id":"unsure","label":"Unsure → a person","focus":["classify","pg","label","admin"],
  "note":"Pages the classifier won't guess on go to the Label screen. Answers are split into practice and a locked exam pile."},
 {"id":"n-of-n","label":"N of N","focus":["fanin","qgroup","pg"],
  "note":"The scoreboard is recounted from the pages; the worker that finishes the last page rings the bell once. Nothing listens yet."},
]
CARDS = [
 ("emerald","Built and running",
  ["Upload → MinIO → n8n → 288 page images and tickets (≈ 70 s)",
   "Worker ×6: upright, deskew, dark-band mask, Tesseract, QR (≈ 10 min for 288 pages)",
   "Classify every page: title keywords + FP layout + Jev + QR",
   "AI OCR (Gemini) on pages 1–31, then a code-only check of every value",
   "Label screen for unsure pages: practice 80% / exam 20%"]),
 ("cyan","Measured on the sample",
  ["82 QR codes decoded, every one an SOR",
   "Pages 1–32: no wrong type, faint FPs 6 and 8 found",
   "Whole batch: 99 FP · 85 TTG · 52 PO · 52 unsure",
   "Pages 1–31 (19 read): 92 main values ✅, 26 to a person, no wrong value ✅"]),
 ("violet","Rules in force",
  ["Unsure beats a guess: a wrong answer never passes",
   "Answer key only reachable by the UI and tests, never pipeline code",
   "Proposer may read practice labels only, never the exam pile"]),
 ("rose","Not built yet",
  ["Grouping into SORs (6), review + cross-checks (7), publish to Satellite (8)",
   "AI OCR on all 288 pages: needs a paid key or another provider",
   "SCP/Tailscale intake; the Upload screen stands in",
   "Grouper, publisher and Ollama containers run idle"]),
]
HINT = "Click a box marked <b>details ›</b> to open its own diagram · <b>open ↗</b> opens the live screen or console."

svg = add_link_chips(build(TITLE, (1560, 700), NODES, EDGES, FRAMES), LINKS)
ET.fromstring(svg)
ids = {n[0] for n in NODES}
for e in EDGES: assert e[1] in ids and e[2] in ids, e[0]
io.open(os.path.join(OUT, "ocr-pipeline.html"), "w", encoding="utf-8").write(page(TPL, TITLE, svg, VIEWS, CARDS, back=None, hint=HINT))
print("wrote ocr-pipeline.html", len(NODES), "boxes", len(EDGES), "lines")
