"""Phase 3 acceptance: every page classified; no wrong type on pages 1–32 (unsure allowed);
no Faktur Penjualan ever given another type; every SOR-QR page decided FP across the whole batch."""
import os
import hashlib
import time

import httpx

UI = os.environ.get("UI_URL", "http://ui:8000")   # vlm-first sets its own UI
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def test_phase3_acceptance():
    deadline = time.time() + 45 * 60
    while time.time() < deadline:
        r = httpx.get(f"{UI}/api/batches/{BID}/phase3", timeout=60).json()
        if r["checks"][0][1]:                      # every page classified
            break
        time.sleep(10)
    failed = [(label, detail) for label, ok, detail in r["checks"] if not ok]
    assert not failed, failed
