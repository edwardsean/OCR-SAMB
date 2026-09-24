# Diagram tools

The diagrams in `diagrams/` are generated, not hand-edited. Edit these scripts and regenerate.

| File | What it does |
|---|---|
| `archify.py` | Writes an Archify-compatible `<svg>` (boxes, lines, frames, labels) |
| `flow.py` | Grid layout + line routing for detail diagrams; adds the `details ›` / `open ↗` chips and the click-to-open script |
| `overview.py` | Builds `ocr-pipeline.html`, the **as-built** overview (only what exists and runs) |
| `details.py` | Builds `detail-intake.html`, `detail-enhance.html`, `detail-classify.html`, `detail-worker.html`, `detail-extract.html` (AI OCR + check) |
| `viewer_template.html` | A clean copy of the Archify viewer; each page is this file with its own SVG, views and cards spliced in |
| `nav_test.mjs` | Browser test: a click opens the detail diagram, ← Overview goes back, `open ↗` opens the live screen, every link resolves |

```bash
cd diagrams/tools
python3 overview.py .. viewer_template.html
python3 details.py  .. viewer_template.html
PUPPETEER=$(find ~/.npm/_npx -path '*node_modules/puppeteer/lib/puppeteer/puppeteer.js' | head -1) node nav_test.mjs
```

Rule: the overview shows only what is built. When a phase is built, add its boxes here; the target design lives in `docs/` and the plan.
