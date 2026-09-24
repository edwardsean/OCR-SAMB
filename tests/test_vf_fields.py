"""vlm-first: the combined field list. Overlapping fields are merged, every per-type field (Satellite column)
maps from exactly one combined field of the same kind, and projecting back is lossless."""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from common.fields import CANON, DOCS, LINE_CANON, LINE_MAP, TYPE_MAP, lift, project


def test_29_per_type_fields_become_18_plus_2_clues():
    per_type = sum(len(d["header"]) for d in DOCS.values())
    assert per_type == 29
    store = [n for n, f in CANON.items() if f["role"] == "store"]
    clue = [n for n, f in CANON.items() if f["role"] == "clue"]
    assert (len(store), len(clue)) == (18, 2)


def test_every_satellite_column_maps_once_with_the_same_kind():
    for code, d in DOCS.items():
        names = [f["name"] for f in d["header"]]
        mapped = list(TYPE_MAP[code].values())
        assert sorted(mapped) == sorted(names), code                 # each per-type field exactly once
        kinds = {f["name"]: f["kind"] for f in d["header"]}
        for canon, name in TYPE_MAP[code].items():
            assert CANON[canon]["role"] == "store"
            assert CANON[canon]["kind"] == kinds[name], (code, canon, name)
    used = {canon for m in TYPE_MAP.values() for canon in m}
    assert used == {n for n, f in CANON.items() if f["role"] == "store"}, "a stored field no type uses"


def test_every_line_column_maps_once_with_the_same_kind():
    for code, d in DOCS.items():
        if not d["lines"]:
            continue
        kinds = {f["name"]: f["kind"] for f in d["lines"]}
        assert sorted(LINE_MAP[code].values()) == sorted(kinds), code
        for canon, name in LINE_MAP[code].items():
            assert LINE_CANON[canon]["kind"] == kinds[name], (code, canon)


@pytest.mark.skipif(not os.environ.get("MAIN_DATABASE_URL"), reason="needs v1's database (vlm-first runtime)")
def test_project_lift_round_trip_on_v1_readings():
    with psycopg.connect(os.environ["MAIN_DATABASE_URL"], row_factory=dict_row) as m:
        rows = m.execute("""SELECT page_no, doc_type::text AS t, fields FROM staging.page
                            WHERE extract_status='done' AND fields <> '{}'::jsonb""").fetchall()
    assert rows
    for r in rows:
        f = {k: v for k, v in r["fields"].items()}
        assert project(lift(f, r["t"]), r["t"]) == f, r["page_no"]
