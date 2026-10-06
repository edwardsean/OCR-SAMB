"""The data a new database starts from (2026-10-07; the user: Jev's context "should live in the database, hence we need
a script that my mentor would run to seed the contexts to the database").

  load     put the seed into the database, once: Jev's context (services/seed/jev-context.json) becomes context #1,
           active, when the database has none yet. A database that already has a context is left as it is, so it
           is safe to run again. ./scripts/seed.sh runs it.
  export   write a working database's active context to services/seed/jev-context.json, so new installs start
           from it. Read the notes before committing it: they must not carry a customer's document numbers or
           amounts (a test checks for amounts).

  python -m common.seed load | export"""
import json
import sys

from common import context, db

NOTE = "seeded from services/seed/jev-context.json"


def load(conn=None):
    """Jev's context into the database if it has none. Returns what was done, in words."""
    if conn is None:
        with db.connect() as c:
            return load(c)
    version, _ = context.active(conn)
    if version is not None:
        return f"Jev's context: the database already has #{version} active, left as it is"
    if conn.execute("SELECT 1 FROM staging.context_version LIMIT 1").fetchone():
        return "Jev's context: the database has versions but none active: fix that by hand, nothing loaded"
    content = context.seed_content()
    problems = context.validate(content)
    if problems:
        raise RuntimeError(f"services/seed/jev-context.json is not a valid context: {problems}")
    conn.execute("""INSERT INTO staging.context_version (version, status, content, created_by, note)
                    VALUES (1, 'active', %s, 'seed', %s)""", (json.dumps(content), NOTE))
    return f"Jev's context: loaded as #1 ({len(content['types'])} document types, {len(content['fields'])} fields)"


def export(path=context.SEED_FILE):
    """The active context, written as the seed file. Returns its version."""
    with db.connect() as c:
        version, content = context.active(c)
    if version is None:
        raise RuntimeError("no active context to export")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    return version


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "load":
        print(load())
    elif what == "export":
        v = export()
        print(f"context #{v} written to services/seed/jev-context.json: read its notes before committing "
              "(no customer document numbers or amounts)")
    else:
        sys.exit("usage: python -m common.seed load | export")
