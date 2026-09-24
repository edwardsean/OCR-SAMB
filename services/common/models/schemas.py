"""AI OCR instructions (Gemini responseSchema), GENERATED from common/fields.py — edit the field list there, not here.
Header fields come back as {value, source_text}; line items as plain values plus row_text (the whole printed row)."""
from common.fields import DOCS


def f(desc):
    return {"type": "OBJECT", "nullable": True, "description": desc,
            "properties": {"value": {"type": "STRING", "nullable": True}, "source_text": {"type": "STRING", "nullable": True}},
            "required": ["value", "source_text"]}


def obj(props, required=None):
    return {"type": "OBJECT", "properties": props, "required": required or list(props)}


def lines(cols):
    row = {c["name"]: {"type": "STRING", "nullable": True, "description": c["desc"]} for c in cols}
    row["row_text"] = {"type": "STRING", "description": "the whole printed row"}
    return {"type": "ARRAY", "items": obj(row)}


NAMES = {code: f"{d['name']} ({d['about']})" for code, d in DOCS.items()}
SCHEMAS = {}
for code, d in DOCS.items():
    props = {x["name"]: f(x["desc"]) for x in d["header"]}
    if d["lines"]:
        props["lines"] = lines(d["lines"])
    SCHEMAS[code] = obj(props)

READ = obj({
    "title": f("the document title as printed, if any"),
    "issuer": f("company whose letterhead / name heads the page (who issued it)"),
    "page_marker": f("page marker if printed, e.g. Page 12 of 35"),
    "transcript": {"type": "STRING", "description": "all printed text in reading order, max ~3000 characters"},
    "sor_numbers": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "every SOR... number printed"},
    "po_numbers": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "every purchase-order number printed"},
})
