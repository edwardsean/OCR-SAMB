"""Phase 6 acceptance on the sample (vlm-first): graded in the UI with the answer key. Held is always allowed;
a page in a wrong bundle, or a link by page order, never is."""
import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
UI = os.environ.get("UI_URL", "http://ui:8000")
BID = "b-4bab9b736d"


@pytest.fixture(scope="module")
def p6():
    r = httpx.get(f"{UI}/api/batches/{BID}/phase6", timeout=60)
    if r.status_code == 404:
        pytest.skip("sample batch not cloned into vlm-first")
    return {label: (ok, detail) for label, ok, detail in r.json()["checks"]}


@pytest.mark.parametrize("gate", ["No page in a wrong bundle (answer key)",
                                  "Nothing linked by page order (a continuation belongs to the page before it)",
                                  "At most one FP per bundle",
                                  "Each bundle's pages are in its SOR's folder"])
def test_gate(p6, gate):
    ok, detail = p6[gate]
    assert ok, detail
