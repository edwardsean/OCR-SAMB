"""Composite resolution on the real data (phase 7b): every FP of pages 1–31, its SOR set aside, against Satellite's
whole export (~38K SOs). It may say nothing; it must never name another SO, not even when the total it read is
changed at any one digit. Graded with the answer key (tests only)."""
import json
import os

import pytest

from common import db, satellite
from common.fields import project

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
BID = "b-4bab9b736d"


@pytest.fixture(scope="module")
def world():
    golden = json.load(open("/app/testdata/golden_p1-32.json"))
    truth = {n: b["sor"] for b in golden["bundles"] for n in b["pages"]}
    with db.connect() as c:
        sos = satellite.load(c)
        pages = c.execute("""SELECT page_no, fields_all FROM staging.page WHERE batch_id=%s AND doc_type='FP'
                               AND page_no BETWEEN 1 AND 31 AND fields_all IS NOT NULL ORDER BY 1""", (BID,)).fetchall()
    if not pages or not sos:
        pytest.skip("no FP readings or no Satellite records")
    return truth, sos, [(p["page_no"], project(p["fields_all"], "FP")) for p in pages]


def test_never_another_so(world):
    """Page 8 stays unresolved, rightly: its 754,022.80 is also Hari Hari Ciledug's (same two items), and the page's
    store, customer code and CPO were all misread. A person confirmed it (/bundles)."""
    truth, sos, pages = world
    named = {}
    for n, f in pages:
        so, why = satellite.resolve_fp(f, sos, satellite.items_for)
        assert so is None or so["sor_no"] == truth[n], f"p{n} → {so['sor_no']} ({why}); truth {truth[n]}"
        named[n] = so and so["sor_no"]
    assert named.get(8) is None
    # measured 2026-09-25: pages 1, 3, 6, 17 and 29 resolve even with no SOR read (page 3's total fits 20 Boots SOs;
    # only one has its customer code and name); the cut totals of 10, 13, 20, 23 and 26 fit none
    assert sum(1 for s in named.values() if s) >= 5


def test_a_bent_total_never_names_another_so(world):
    truth, sos, pages = world
    for n, f in pages:
        src = str((f.get("total") or {}).get("source_text") or "")
        for i, ch in enumerate(src):
            if ch.isdigit():
                bent = {**f, "total": {"value": None, "source_text": src[:i] + str((int(ch) + 1) % 10) + src[i + 1:]}}
                so, why = satellite.resolve_fp(bent, sos, satellite.items_for)
                assert so is None or so["sor_no"] == truth[n], f"p{n} {bent['total']['source_text']!r} → {so['sor_no']}"
