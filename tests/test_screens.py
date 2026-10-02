"""Every read the web app makes answers (HTTP 200, JSON), and every Teknis screen renders, including a page in every
state the pipeline can leave it in: extracted, extraction failed, unsure, faint, rotated. Catches errors in the view
functions and the Teknis templates that the logic tests can't see."""
import os
import hashlib

import httpx
import pytest

from common import db

API = os.environ.get("API_URL", "http://localhost:8000")
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def sample_pages():
    """One page per distinct state, picked from the database so new states are covered automatically."""
    with db.connect() as c:
        rows = c.execute("""
            SELECT min(page_no) AS page_no FROM staging.page WHERE batch_id=%s
            GROUP BY extract_status, type_status, doc_type, ('faint' = ANY(quality_flags)), rotation <> 0
            ORDER BY 1""", (BID,)).fetchall()
    return [r["page_no"] for r in rows]


READS = ["/api/v1/session", "/api/v1/words", "/api/v1/home", "/api/v1/scans", f"/api/v1/scans/{BID}",
         "/api/v1/labels", f"/api/v1/labels?batch={BID}", f"/api/v1/orders?batch={BID}", f"/api/v1/bundles?batch={BID}",
         "/api/v1/published"]
TEKNIS = ["/status", "/labels", "/fields", f"/teknis/scan/{BID}", "/api/status", f"/api/batches/{BID}",
          f"/api/batches/{BID}/phase2", f"/api/batches/{BID}/phase3", f"/api/batches/{BID}/phase4"]


@pytest.mark.parametrize("path", READS)
def test_the_web_apps_reads_answer(path):
    r = httpx.get(API + path, timeout=60)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json"), (path, r.text[:200])


@pytest.mark.parametrize("path", TEKNIS)
def test_teknis_screen_renders(path):
    r = httpx.get(API + path, timeout=60)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_every_page_state_answers():
    bad = []
    for n in sample_pages():
        for path in (f"/api/v1/scans/{BID}/pages/{n}", f"/teknis/halaman/{BID}/{n}"):
            r = httpx.get(API + path, timeout=60)
            if r.status_code != 200:
                bad.append((path, r.status_code))
    assert not bad, bad


def test_the_apis_own_address_sends_a_browser_to_the_web_app():
    r = httpx.get(API + "/", timeout=30, follow_redirects=False)
    web = os.environ.get("WEB_URL")
    assert (r.status_code == 307 and r.headers["location"] == web) if web else r.json()["api"] == "/api/v1"
