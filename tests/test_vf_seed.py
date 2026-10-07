"""Jev's context lives in the database; a new database gets it from the seed (2026-10-07; the user: "dont hardcode it,
this should live in the database, hence we need a script that my mentor would run to seed the contexts to the
database"). services/seed/jev-context.json, common/seed.py (./scripts/seed.sh), and context.ensure refusing without
it. The database tests run in a transaction rolled back: the live contexts are never touched."""
import json
import os
import re

import pytest

from common import context
from common.fields import CANON

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


def test_the_seed_file_is_a_valid_context_with_every_type_and_field():
    s = context.seed_content()
    assert context.validate(s) == []
    assert set(s["types"]) == set(context.TYPES) and set(s["fields"]) == set(CANON)


def test_the_seed_carries_no_customer_document_numbers_or_amounts():
    """It is committed: a note may name a customer's habit, never a document's figures."""
    notes = " ".join(f.get("note", "") for t in context.seed_content()["types"].values() for f in t["fields"])
    assert not re.search(r"\d{1,3}([.,]\d{3})+([.,]\d{2})?", notes), "an amount in a note"
    assert not re.search(r"\d{8,}", notes), "a document number in a note"


@pytest.fixture
def empty_contexts():
    """A connection whose database has no context (as a new install), rolled back after."""
    import psycopg
    from psycopg.rows import dict_row
    from common import config
    with psycopg.connect(config.required("DATABASE_URL"), row_factory=dict_row) as c:
        c.execute("DELETE FROM staging.lesson")
        c.execute("DELETE FROM staging.context_version")
        try:
            yield c
        finally:
            c.rollback()


@needs_db
def test_without_a_seed_the_system_says_to_run_the_seed_script(empty_contexts):
    with pytest.raises(RuntimeError, match="seed.sh"):
        context.ensure(empty_contexts)


@needs_db
def test_the_seed_loads_once_as_context_1_and_never_over_an_existing_one(empty_contexts):
    from common import seed
    c = empty_contexts
    assert "loaded as #1" in seed.load(c)
    version, content = context.ensure(c)
    assert version == 1 and content == context.seed_content()
    row = c.execute("SELECT created_by, note FROM staging.context_version WHERE version = 1").fetchone()
    assert row["created_by"] == "seed" and "jev-context.json" in row["note"]
    assert "already has #1" in seed.load(c)                      # run again: left as it is
    assert c.execute("SELECT count(*) AS n FROM staging.context_version").fetchone()["n"] == 1


@needs_db
def test_export_writes_the_active_context_as_the_seed(tmp_path):
    from common import db, seed
    out = tmp_path / "jev-context.json"
    v = seed.export(str(out))
    with db.connect() as c:
        version, content = context.active(c)
    assert v == version and json.loads(out.read_text(encoding="utf-8")) == content
