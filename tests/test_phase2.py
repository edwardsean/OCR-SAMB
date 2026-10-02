"""Phase 2 acceptance: every page enhanced and read by Tesseract; golden checks on pages 1–32; bell rung at N of N."""
import os

import pytest
import hashlib
import time

import httpx

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") == "vlm-first", reason="v1 acceptance; vlm-first has its own")

UI = os.environ.get("API_URL", "http://localhost:8000")
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def test_phase2_acceptance():
    deadline = time.time() + 45 * 60
    while time.time() < deadline:
        b = httpx.get(f"{UI}/api/batches/{BID}").json()["batch"]
        if b["page_done"] == b["page_total"]:
            break
        time.sleep(10)
    r = httpx.get(f"{UI}/api/batches/{BID}/phase2", timeout=30).json()
    failed = [(label, detail) for label, ok, detail in r["checks"] if not ok]
    assert not failed, failed
    assert httpx.get(f"{UI}/api/batches/{BID}").json()["depths"]["group"] >= 1, "bell not rung on q.group"
