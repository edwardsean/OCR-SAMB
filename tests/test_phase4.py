"""Phase 4 acceptance on pages 1–31: extraction ran (or recorded why not); Boots PO page 4 read right;
every WRONG value is flagged unconfirmed. Accuracy is measured and shown, not gated — phase 5 is the gate."""
import os

import pytest
import hashlib
import time

import httpx

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") == "vlm-first", reason="v1 acceptance; vlm-first has its own")

UI = os.environ.get("UI_URL", "http://ui:8000")   # vlm-first sets its own UI
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def test_phase4_acceptance():
    deadline = time.time() + 40 * 60
    while time.time() < deadline:
        r = httpx.get(f"{UI}/api/batches/{BID}/phase4", timeout=60).json()
        if r["checks"] and r["checks"][0][1]:
            break
        time.sleep(15)
    failed = [(label, detail) for label, ok, detail in r["checks"] if not ok]
    assert not failed, failed
