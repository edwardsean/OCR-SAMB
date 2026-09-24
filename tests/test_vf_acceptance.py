"""vlm-first acceptance on pages 1–31 (the gates in /api/batches/{id}/vf):
every page processed; no wrong machine type; every SOR-QR page the machine decided is FP; no wrong value gets ✅;
every ✅ value changed by one digit fails the whole chain; every context valid with one active; no exam lesson.
"Don't know" (unsure, a person checks it) always passes. Fails until pages 1–31 have been read by the AI OCR."""
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("UI_URL", "http://ui:8000")
BID = "b-" + hashlib.sha256(open("/data/sample.pdf", "rb").read()).hexdigest()[:10]


def test_vf_acceptance():
    r = httpx.get(f"{UI}/api/batches/{BID}/vf", timeout=300).json()
    failed = [(label, detail) for label, ok, detail in r["checks"] if not ok]
    assert not failed, failed
