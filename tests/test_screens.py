"""Every screen renders (HTTP 200), including page views in every state the pipeline can leave a page in:
extracted, extraction failed, unsure, faint, rotated. Catches template errors that API tests can't see."""
import hashlib

import httpx
import pytest

from common import db

UI = "http://ui:8000"
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def sample_pages():
    """One page per distinct state, picked from the database so new states are covered automatically."""
    with db.connect() as c:
        rows = c.execute("""
            SELECT min(page_no) AS page_no FROM staging.page WHERE batch_id=%s
            GROUP BY extract_status, type_status, doc_type, ('faint' = ANY(quality_flags)), rotation <> 0
            ORDER BY 1""", (BID,)).fetchall()
    return [r["page_no"] for r in rows]


SCREENS = ["/", "/upload", "/batches", f"/batches/{BID}", f"/batches/{BID}?flag=faint", f"/batches/{BID}?flag=type:unsure",
           f"/partials/batch/{BID}/stats", f"/partials/batch/{BID}/grid", "/label", "/labels", "/fields", "/api/status",
           f"/api/batches/{BID}", f"/api/batches/{BID}/phase2", f"/api/batches/{BID}/phase3", f"/api/batches/{BID}/phase4"]


@pytest.mark.parametrize("path", SCREENS)
def test_screen_renders(path):
    r = httpx.get(UI + path, timeout=60)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_every_page_state_renders():
    bad = []
    for n in sample_pages():
        r = httpx.get(f"{UI}/batches/{BID}/pages/{n}", timeout=60)
        if r.status_code != 200:
            bad.append((n, r.status_code))
    assert not bad, bad
