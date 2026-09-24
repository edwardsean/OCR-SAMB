"""Phase 0 acceptance: all 9 services healthy, every expected table in satellite + staging (19 base + type_label)."""
import os
import httpx

UI = os.environ.get("UI_URL", "http://ui:8000")   # vlm-first sets its own UI
VF = os.environ.get("PIPELINE") == "vlm-first"


def test_all_services_healthy():
    s = httpx.get(f"{UI}/api/status", timeout=30).json()
    down = {r["name"]: r["detail"] for r in s["services"] if not r["ok"]}
    assert len(s["services"]) == (5 if VF else 9)
    assert not down, down


def test_schema_loaded():
    s = httpx.get(f"{UI}/api/status", timeout=30).json()
    assert len(s["tables"]) == (23 if VF else 20), s["tables"]
    for t in ("satellite.sor", "satellite.sor_document", "satellite.doc_ttg",
              "staging.scan_batch", "staging.page", "staging.bundle"):
        assert t in s["tables"]
