"""The table models (common/tables.py), generated from the database itself, so they are always what the database is
(the user, 2026-10-13: "add the models for all tables, and make the code use them so it is neater").

  python -m common.tablegen write        rewrite common/tables.py from the live database (after a migration)
  python -m common.tablegen check        does common/tables.py say what the database is? (tests/test_tables.py)
  python -m common.tablegen jsonschema   rewrite docs/satellite-schema/*.schema.json: what Satellite's data must look like

One TypedDict per table of the pipeline's schemas (staging, satellite): a row as `SELECT *` gives it (psycopg's
dict_row), so the code keeps reading rows as dictionaries and an editor knows every key and its type. Each column's
comment in the database (COMMENT ON COLUMN) becomes a comment above it.
"""
import json
import os
import re
import sys
import textwrap

SCHEMAS = ("satellite", "staging")
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "tables.py")
DOCS = os.path.join(HERE, "..", "..", "docs", "satellite-schema")     # docs/satellite/ is the real export: never in git
PY = {"text": "str", "bpchar": "str", "varchar": "str", "int2": "int", "int4": "int", "int8": "int",
      "numeric": "Decimal", "float4": "float", "float8": "float", "bool": "bool", "date": "date",
      "timestamptz": "datetime", "timestamp": "datetime", "jsonb": "Any", "json": "Any", "bytea": "bytes",
      "uuid": "str"}
JSON = {"str": {"type": "string"}, "int": {"type": "integer"}, "Decimal": {"type": "number"},
        "float": {"type": "number"}, "bool": {"type": "boolean"}, "date": {"type": "string", "format": "date"},
        "datetime": {"type": "string", "format": "date-time"}, "Any": {}}


def catalog(c):
    """{"schema.table": {"columns": [(name, type, nullable, default, comment)], "key": [...]}}, and the enums."""
    enums = {}
    for r in c.execute("""SELECT t.typname AS name, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
                            FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY t.typname ORDER BY 1"""):
        enums[r["name"]] = list(r["labels"])
    out = {}
    for r in c.execute("""
            SELECT c.table_schema AS s, c.table_name AS t, c.column_name AS col, c.is_nullable = 'YES' AS nullable,
                   c.udt_name AS udt, c.column_default AS dflt,
                   col_description(format('%%I.%%I', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS note
              FROM information_schema.columns c
              JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
             WHERE c.table_schema = ANY(%s) AND t.table_type = 'BASE TABLE'
             ORDER BY 1, 2, c.ordinal_position""", (list(SCHEMAS),)):
        out.setdefault(f"{r['s']}.{r['t']}", {"columns": [], "key": []})["columns"].append(
            (r["col"], r["udt"], r["nullable"], r["dflt"], r["note"]))
    for r in c.execute("""
            SELECT k.table_schema AS s, k.table_name AS t, k.column_name AS col
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage k
                ON k.constraint_schema = tc.constraint_schema AND k.constraint_name = tc.constraint_name
             WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = ANY(%s)
             ORDER BY 1, 2, k.ordinal_position""", (list(SCHEMAS),)):
        out[f"{r['s']}.{r['t']}"]["key"].append(r["col"])
    return out, enums


def class_name(table):
    return "".join(p.capitalize() for p in table.split(".", 1)[1].split("_"))


def enum_name(udt):
    return "".join(p.capitalize() for p in udt.lstrip("_").split("_"))


def py_type(udt, nullable, enums):
    if udt.startswith("_"):
        inner = enum_name(udt) if udt[1:] in enums else PY.get(udt[1:], "Any")
        t = f"list[{inner}]"
    else:
        t = enum_name(udt) if udt in enums else PY.get(udt, "Any")
    return f"{t} | None" if nullable and t != "Any" else t


def render(cat, enums):
    """The text of common/tables.py."""
    names = {t: class_name(t) for t in cat}
    assert len(set(names.values())) == len(names), "two tables would share a class name"
    used = {u.lstrip("_") for t in cat.values() for _, u, *_ in t["columns"] if u.lstrip("_") in enums}
    out = ['"""Every table of the pipeline\'s database (PIPELINE_DB, schemas satellite and staging): one TypedDict per table,',
           "a row as `SELECT *` gives it (psycopg's dict_row). A query that selects fewer columns gives fewer keys.",
           "",
           "GENERATED from the database by `python -m common.tablegen write`. Do not edit by hand: change the schema",
           "(schema/0NN-*.sql), apply it, and generate again. tests/test_tables.py fails while this file and the database",
           "differ. A column's comment in the database (COMMENT ON COLUMN) is the comment above it.",
           "",
           '  TABLES["satellite.sor"] is Sor, NAME[Sor] is "satellite.sor", KEYS["satellite.sor"] its primary key,',
           "  columns(Sor) its columns in order, select(Sor) them as a SELECT list.",
           '"""',
           "from datetime import date, datetime",
           "from decimal import Decimal",
           "from typing import Any, Literal, TypedDict, TypeVar",
           ""]
    for e in sorted(used):
        out.append(f"{enum_name(e)} = Literal[{', '.join(repr(x) for x in enums[e])}]")
    for schema in SCHEMAS:
        out += ["", "", f"# {'-' * 50} {schema}"]
        for t in sorted(x for x in cat if x.startswith(schema + ".")):
            out += ["", "", f"class {names[t]}(TypedDict, total=False):", f'    """{t}"""']
            for col, udt, nullable, dflt, note in cat[t]["columns"]:
                if note:
                    out += textwrap.wrap(" ".join(note.split()), 112, initial_indent="    # ", subsequent_indent="    # ")
                out.append(f"    {col}: {py_type(udt, nullable, enums)}")
    out += ["", "", "TABLES: dict[str, type] = {"]
    out += [f"    {t!r}: {names[t]}," for t in sorted(cat)]
    out += ["}", "NAME: dict[type, str] = {cls: name for name, cls in TABLES.items()}", "KEYS: dict[str, tuple[str, ...]] = {"]
    out += [f"    {t!r}: ({', '.join(repr(k) for k in cat[t]['key'])}{',' if len(cat[t]['key']) == 1 else ''})," for t in sorted(cat)]
    out += ["}", "", "",
            "def columns(table: type) -> tuple[str, ...]:",
            '    """A table\'s columns, in the database\'s order."""',
            "    return tuple(table.__annotations__)",
            "", "",
            "def select(table: type, without: tuple[str, ...] = (), prefix: str = \"\") -> str:",
            '    """Its columns as a SELECT list (`prefix` "s." for a table aliased s), leaving out `without`."""',
            "    return \", \".join(prefix + c for c in columns(table) if c not in without)",
            "", "",
            "T = TypeVar(\"T\")",
            "", "",
            "def row(c, table: type[T], **where: Any) -> T | None:",
            '    """One row of `table`, as SELECT * gives it, by column values (usually its key), or None when there is',
            '    none: row(c, Page, batch_id=bid, page_no=n)."""',
            "    if not where or not set(where) <= set(columns(table)):",
            "        raise ValueError(f\"{NAME[table]} has no column {sorted(set(where) - set(columns(table)))}\")",
            "    sql = f\"SELECT * FROM {NAME[table]} WHERE \" + \" AND \".join(f\"{k} = %s\" for k in where)",
            "    return c.execute(sql, tuple(where.values())).fetchone()",
            ""]
    return "\n".join(out)


def json_schema(cat, enums, table, described):
    """A JSON Schema for one table's rows: types from the database, required = NOT NULL without a default,
    descriptions from `described` ({column: text})."""
    props, required = {}, []
    for col, udt, nullable, dflt, note in cat[table]["columns"]:
        base = udt.lstrip("_")
        one = {"enum": enums[base]} if base in enums else dict(JSON[PY.get(base, "Any")])
        if udt.startswith("_"):
            one = {"type": "array", "items": one}
        if nullable and "type" in one:
            one["type"] = [one["type"], "null"]
        if described.get(col) or note:
            one = {"description": described.get(col) or " ".join(note.split()), **one}
        props[col] = one
        if not nullable and dflt is None:
            required.append(col)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": table, "type": "object",
            "properties": props, "required": required, "additionalProperties": False}


def _connect():
    from common import db
    return db.connect()


def _order_in_file():
    """{table: its columns in the order common/tables.py lists them}, or {} before there is one."""
    if not os.path.exists(TARGET):
        return {}
    out, table = {}, None
    for line in open(TARGET):
        m = re.match(r'    """((?:satellite|staging)\.\w+)"""$', line.rstrip("\n"))
        if m:
            table = out.setdefault(m.group(1), [])
        elif not line.startswith("    "):
            table = None
        elif table is not None and (m := re.match(r"    (\w+): ", line)):
            table.append(m.group(1))
    return out


def ordered(cat):
    """The catalog with each table's columns in the order tables.py already has them, new ones last. A column's place
    in the database depends on how its migration ran (030 adds satuan at the end of a table with rows, and rebuilds
    an empty one with it beside uom), so the same schema gives the same file on every machine."""
    known = _order_in_file()
    for table, info in cat.items():
        order = known.get(table) or []
        info["columns"].sort(key=lambda col: order.index(col[0]) if col[0] in order else len(order))
    return cat


def write():
    with _connect() as c:
        cat, enums = catalog(c)
    text = render(ordered(cat), enums)           # before opening: opening for writing empties the file it reads
    with open(TARGET, "w") as f:
        f.write(text)
    print(f"wrote {TARGET}: {len(cat)} tables")


def check():
    """[] when common/tables.py is what the database is (its columns in any order); else what differs, in words."""
    with _connect() as c:
        cat, enums = catalog(c)
    want = render(ordered(cat), enums)
    have = open(TARGET).read() if os.path.exists(TARGET) else ""
    if want == have:
        return []
    got = {m.group(1) for m in re.finditer(r"^class (\w+)\(", have, re.M)}
    need = {class_name(t) for t in cat}
    out = [f"{n}: in the database, not in tables.py" for n in sorted(need - got)]
    out += [f"{n}: in tables.py, not in the database" for n in sorted(got - need)]
    return out or ["tables.py differs from the database (a column, a type or a comment): run python -m common.tablegen write"]


def jsonschema():
    from common import satellite_feed
    with _connect() as c:
        cat, enums = catalog(c)
    cat = ordered(cat)                           # the same order as tables.py on every machine
    os.makedirs(DOCS, exist_ok=True)
    for table, fields in (("satellite.sor", satellite_feed.SOR), ("satellite.sor_item", satellite_feed.SOR_ITEM)):
        described = {col: f"{meaning} (Satellite's export: {src})" if src else meaning
                     for col, (src, meaning) in fields.items()}
        path = os.path.join(DOCS, table.split(".")[1] + ".schema.json")
        with open(path, "w") as f:
            json.dump(json_schema(cat, enums, table, described), f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"wrote {os.path.normpath(path)}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "write":
        write()
    elif what == "check":
        diff = check()
        print("\n".join(diff) or "tables.py is what the database is")
        sys.exit(1 if diff else 0)
    elif what == "jsonschema":
        jsonschema()
    else:
        print("usage: python -m common.tablegen write | check | jsonschema")
