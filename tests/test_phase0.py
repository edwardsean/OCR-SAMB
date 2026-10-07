"""Phase 0 acceptance: every service healthy, every expected table in satellite + staging (19 base + type_label, and the
vlm-first migrations)."""
import os
import httpx

UI = os.environ.get("API_URL", "http://localhost:8000")
VF = os.environ.get("PIPELINE") == "vlm-first"


def test_all_services_healthy():
    s = httpx.get(f"{UI}/api/status", timeout=30).json()
    down = {r["name"]: r["detail"] for r in s["services"] if not r["ok"]}
    assert len(s["services"]) == (9 if VF else 8)          # vf: 3 shared servers + intake, worker, grouper, teacher, scheduler, api
    assert not down, down


def test_schema_loaded():
    s = httpx.get(f"{UI}/api/status", timeout=30).json()
    assert len(s["tables"]) == (35 if VF else 20), s["tables"]   # vf: + notice (018), reading_trial (019), extract_example (020), knowledge (021), job_run (025), upload (026), setting (027)
    for t in ("satellite.sor", "satellite.sor_document", "satellite.doc_ttg",
              "staging.scan_batch", "staging.page", "staging.bundle"):
        assert t in s["tables"]
