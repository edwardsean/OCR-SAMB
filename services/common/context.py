"""vlm-first: Jev's context, versioned in staging.context_version.

One registry holds both lists, so they can't drift apart:
  fields   the combined field list (common/fields.py CANON at first). The AI OCR reads every page against it.
  types    per document type: what it is, its titles, what it's not, and which fields it has and how often
           (always / usually / sometimes / never, with a note such as "AEON prints it as RECEIPT NO").
Invariant, checked by validate() on every save: the union of all types' fields = the combined field list.
So a field can only enter the list together with a type that has it, and a type can only name fields on the list.

What a proposal (from the teacher, approved by a person) may change, one change at a time:
  edit_type        a type's description, titles or "not for"
  field_for_type   an existing field becomes part of a type ("some customers' TTGs print a PO number"):
                   the combined list does NOT change
  new_field        a genuinely new field (on no list under any name or meaning): added to the list AND to that type,
                   as a clue field (staging only; a Satellite column is a person's decision and a migration)
Never: the meaning or kind of an existing field, removing fields, the set of types, rules or thresholds.
"""
import copy
import hashlib
import json
import re

from common.fields import CANON, LINE_CANON, TYPE_MAP

HOW_OFTEN = ("always", "usually", "sometimes", "never")
KINDS = ("id", "text", "amount", "qty", "date")
TYPES = ("FP", "TTG", "PO", "SJ", "FPJ", "PEL", "CONTINUATION", "OTHER")
COMMON = ("document_title", "page_marker")          # clue fields every type lists (how often differs)
BUDGET = {"fields": 30, "what": 400, "not_for": 300, "titles": 20, "title": 60, "note": 160, "meaning": 200}
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

HOW_OFTEN_SEED = {   # how often each type prints its fields, as seen on the sample (the teacher refines it)
    "FP": dict.fromkeys(TYPE_MAP["FP"], "always"),
    "TTG": {"document_no": "always", "posting_date": "usually", "po_number": "usually", "vendor_code": "usually",
            "customer_name": "usually", "sor": "sometimes"},
    "PO": {"po_number": "always", "vendor_code": "usually", "vendor_name": "usually", "ppn": "usually",
           "total": "usually", "customer_name": "usually"},
    "FPJ": {**dict.fromkeys(TYPE_MAP["FPJ"], "usually"), "sor": "sometimes"},
}
NOTES_SEED = {
    ("TTG", "sor"): "some customers print SAMB's SOR: Hari Hari as No Ref, Puri Indah as DO#, Indogrosir as S/Fak "
                    "(the last two without the letters SOR)",
    ("TTG", "po_number"): "labels vary: No PO, PO No, Order No, No. Pesanan, a PO column in the rows; "
                          "AEON prints it as RECEIPT NO",
}


# ---------------------------------------------------------------------------------------------------- the seed

def seed(jev_criteria, keywords, jev_types):
    """Version 1: the combined field list + v1's Jev descriptions and title words (worker/classify.py)."""
    key_of = {code: key for key, code in jev_types.items()}
    types = {}
    for code in TYPES:
        crit = jev_criteria[key_of[code]]
        examples = crit.get("examples", [])
        fields = [{"name": n, "how_often": h, "note": NOTES_SEED.get((code, n), "")}
                  for n, h in HOW_OFTEN_SEED.get(code, {}).items()]
        fields += [{"name": "document_title", "how_often": "never" if code == "CONTINUATION" else "usually", "note": ""},
                   {"name": "page_marker", "how_often": "usually" if code == "CONTINUATION" else "sometimes", "note": ""}]
        types[code] = {"jev_key": key_of[code], "what": crit["what"],
                       "titles": list(dict.fromkeys([*keywords.get(code, []), *examples])),
                       "not_for": crit.get("not_for", ""), "fields": fields}
    fields = {n: {k: f[k] for k in ("kind", "meaning", "printed_as", "role")} for n, f in CANON.items()}
    return {"fields": fields, "types": types}


# ---------------------------------------------------------------------------------------------------- checks

def validate(content):
    """Problems with a context; an empty list means it may be saved and used."""
    p = []
    fields, types = content.get("fields", {}), content.get("types", {})
    if set(types) != set(TYPES):
        p.append(f"the set of types must stay {', '.join(TYPES)}")
    used = set()
    for code, t in types.items():
        names = [f["name"] for f in t.get("fields", [])]
        if len(names) != len(set(names)):
            p.append(f"{code} lists a field twice")
        for f in t.get("fields", []):
            used.add(f["name"])
            if f["name"] not in fields:
                p.append(f"{code} names {f['name']!r}, which is not on the combined field list")
            if f.get("how_often") not in HOW_OFTEN:
                p.append(f"{code}.{f['name']}: how_often must be one of {HOW_OFTEN}")
            if len(f.get("note") or "") > BUDGET["note"]:
                p.append(f"{code}.{f['name']}: note longer than {BUDGET['note']}")
        for c in COMMON:
            if c not in names:
                p.append(f"{code} must list the common field {c!r}")
        if len(t.get("what") or "") > BUDGET["what"] or not t.get("what"):
            p.append(f"{code}: description empty or longer than {BUDGET['what']}")
        if len(t.get("not_for") or "") > BUDGET["not_for"]:
            p.append(f"{code}: 'not for' longer than {BUDGET['not_for']}")
        titles = t.get("titles", [])
        if len(titles) > BUDGET["titles"] or any(len(x) > BUDGET["title"] for x in titles):
            p.append(f"{code}: too many or too long titles")
    orphans = set(fields) - used
    if orphans:
        p.append(f"fields no type has (the combined list must equal the union of the types' fields): {sorted(orphans)}")
    if len(fields) > BUDGET["fields"]:
        p.append(f"more than {BUDGET['fields']} header fields")
    for name, f in fields.items():
        if not NAME.match(name):
            p.append(f"bad field name {name!r}")
        if f.get("kind") not in KINDS:
            p.append(f"{name}: kind must be one of {KINDS}")
        if not f.get("meaning") or len(f["meaning"]) > BUDGET["meaning"]:
            p.append(f"{name}: meaning empty or longer than {BUDGET['meaning']}")
    for name, f in CANON.items():                     # the stored fields are fixed: Satellite has columns for them
        g = fields.get(name)
        if not g:
            p.append(f"the stored field {name!r} was removed")
        elif (g["kind"], g["role"]) != (f["kind"], f["role"]):
            p.append(f"{name}: kind/role changed")
    return p


def union_equals_list(content):
    return {f["name"] for t in content["types"].values() for f in t["fields"]} == set(content["fields"])


def apply_change(content, change):
    """(new content, problems). One change; see the module docstring for what is allowed."""
    new = copy.deepcopy(content)
    kind, code = change.get("kind"), change.get("type")
    if code not in new["types"]:
        return content, [f"unknown type {code!r}"]
    t = new["types"][code]
    if kind == "edit_type":
        for k in ("what", "not_for"):
            if change.get(k):
                t[k] = change[k].strip()
        t["titles"] = list(dict.fromkeys(t["titles"] + [x.strip() for x in change.get("titles_add", []) if x.strip()]))
    elif kind == "field_for_type":
        name = change.get("field")
        if name not in new["fields"]:
            return content, [f"{name!r} is not on the combined list; propose it as a new field instead"]
        if name in COMMON:
            return content, [f"{name} is on every type already: adding it to one type tells Jev nothing"]
        entry = {"name": name, "how_often": change.get("how_often", "sometimes"), "note": change.get("note", "")}
        at = next((i for i, f in enumerate(t["fields"]) if f["name"] == name), None)
        if at is None:
            t["fields"].append(entry)
        else:                                         # in place, so repeating an entry is seen as no change
            t["fields"][at] = entry
    elif kind == "new_field":
        f = change.get("field") or {}
        name = f.get("name", "")
        if name in new["fields"]:
            return content, [f"{name!r} is already on the list: that is 'new for this type', not a new field"]
        if not f.get("example", {}).get("source_text"):
            return content, ["a new field needs an example as printed on the page"]
        new["fields"][name] = {"kind": f.get("kind"), "meaning": (f.get("meaning") or "").strip(),
                               "printed_as": f.get("printed_as", []), "role": "clue"}
        t["fields"].append({"name": name, "how_often": change.get("how_often", "sometimes"),
                            "note": change.get("note", "")})
    else:
        return content, [f"unknown change {kind!r}"]
    if new == content:
        return content, ["this changes nothing: the context already says it"]
    problems = validate(new)
    for name, f in content["fields"].items():         # meanings and kinds of existing fields never change
        if new["fields"].get(name, {}).get("meaning") != f["meaning"] or new["fields"][name]["kind"] != f["kind"]:
            problems.append(f"{name}: an existing field's meaning or kind changed")
    return (new, problems) if problems else (new, [])


def fields_version(content):
    """Which combined list a reading was made against: a reading is reused while this is unchanged."""
    return hashlib.sha1(json.dumps(content["fields"], sort_keys=True).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------------------------------- generated

def vlm_schema(content):
    """The AI OCR's instructions (Gemini responseSchema): every field on the combined list, plus line items."""
    def one(name, f):
        hint = f["meaning"] + (f" Printed as e.g.: {', '.join(f['printed_as'])}." if f["printed_as"] else "")
        return {"type": "OBJECT", "nullable": True, "description": hint,
                "properties": {"value": {"type": "STRING", "nullable": True},
                               "source_text": {"type": "STRING", "nullable": True},
                               "box": {"type": "ARRAY", "nullable": True, "items": {"type": "INTEGER"},
                                       "description": "where the printed value is: [ymin, xmin, ymax, xmax], 0-1000"}},
                "required": ["value", "source_text"]}
    props = {n: one(n, f) for n, f in content["fields"].items()}
    row = {c: {"type": "STRING", "nullable": True, "description": f["meaning"]} for c, f in LINE_CANON.items()}
    row["row_text"] = {"type": "STRING", "description": "the whole printed row"}
    props["lines"] = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": row, "required": list(row)}}
    return {"type": "OBJECT", "properties": props, "required": list(props)}


def jev_question(content):
    """Jev's Choice question, built from the types: what each is, its titles, and the fields it has."""
    fields = content["fields"]

    def listing(t, how):
        return [f"{f['name']}: {fields[f['name']]['meaning']}" + (f" ({f['note']})" if f["note"] else "")
                for f in t["fields"] if f["how_often"] == how]
    criteria = {}
    for code, t in content["types"].items():
        c = {"what": t["what"]}
        if t["titles"]:
            c["titles"] = t["titles"]
        for how, key in (("always", "always_has"), ("usually", "usually_has"), ("sometimes", "sometimes_has"),
                         ("never", "never_has")):
            items = listing(t, how)
            if items:
                c[key] = items
        if t.get("not_for"):
            c["not_for"] = t["not_for"]
        criteria[t["jev_key"]] = c
    return {"doc_type": {
        "type": "choice",
        "instructions": (
            "An AI OCR model read ONE scanned page of Indonesian accounts-receivable paperwork for the distributor "
            "PT Sarana Abadi Makmur Bersama (SAMB). `found` lists the values it found on the page, as printed; "
            "`not_found` lists fields it looked for but did not find; `document_title` and `page_marker` are as "
            "printed; `line_rows` counts table rows. SAMB's name appears on every kind of document (as the sender, "
            "the supplier or the addressee), so it says nothing about the type. Which kind of document is this page?"),
        "criteria": criteria}}


# ---------------------------------------------------------------------------------------------------- storage

def active(conn):
    r = conn.execute("SELECT version, content FROM staging.context_version WHERE status='active'").fetchone()
    return (r["version"], r["content"]) if r else (None, None)


def ensure(conn, jev_criteria, keywords, jev_types):
    """The active context; seeds version 1 if there is none. Refuses an invalid one."""
    version, content = active(conn)
    if version is None:
        content = seed(jev_criteria, keywords, jev_types)
        problems = validate(content)
        if problems:
            raise RuntimeError(f"seed context is invalid: {problems}")
        conn.execute("""INSERT INTO staging.context_version (version, status, content, created_by, note)
                        VALUES (1, 'active', %s, 'seed', 'combined field list + v1 Jev descriptions')
                        ON CONFLICT (version) DO NOTHING""", (json.dumps(content),))
        version, content = active(conn)
    problems = validate(content)
    if problems:
        raise RuntimeError(f"active context #{version} is invalid: {problems}")
    return version, content


def propose(conn, content, parent, created_by, note):
    problems = validate(content)
    if problems:
        raise ValueError(problems)
    v = conn.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM staging.context_version").fetchone()["v"]
    conn.execute("""INSERT INTO staging.context_version (version, parent, status, content, created_by, note)
                    VALUES (%s, %s, 'proposed', %s, %s, %s)""", (v, parent, json.dumps(content), created_by, note))
    return v


def activate(conn, version, approved_by):
    """A person approved it: it becomes the one active context; the old one is retired. A proposal built on an older
    context is refused: its content is that older context plus one change, so activating it would silently undo
    whatever was approved since, and its replay was measured against the wrong context."""
    r = conn.execute("SELECT status, parent, content FROM staging.context_version WHERE version=%s",
                     (version,)).fetchone()
    if not r or r["status"] != "proposed":
        raise ValueError(f"context #{version} is not a proposal")
    now = conn.execute("SELECT version FROM staging.context_version WHERE status='active'").fetchone()
    if now and r["parent"] != now["version"]:
        raise ValueError(f"context #{version} was built on #{r['parent']}, but #{now['version']} is active now. "
                         "Reject it and ask the teacher again (python -m worker.lesson run).")
    problems = validate(r["content"])
    if problems:
        raise ValueError(problems)
    conn.execute("UPDATE staging.context_version SET status='retired' WHERE status='active'")
    conn.execute("""UPDATE staging.context_version SET status='active', approved_by=%s, approved_at=now()
                    WHERE version=%s""", (approved_by, version))


def reject(conn, version, by):
    conn.execute("UPDATE staging.context_version SET status='rejected', approved_by=%s, approved_at=now() "
                 "WHERE version=%s AND status='proposed'", (by, version))
