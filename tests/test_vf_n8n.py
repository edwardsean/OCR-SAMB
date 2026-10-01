"""vlm-first on n8n (Stage 2, the user 2026-09-29: "we should be using n8n for the workflow"): the intake, the sweep
and the needs-you notice are n8n workflows calling vf-ui; the work itself stays on RabbitMQ and the workers."""
import json
import os
from pathlib import Path

import pytest

from common import db, notice

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
FLOWS = Path("/app/n8n")


@pytest.mark.parametrize("name,calls", [
    ("vf-intake", ["/internal/intake/split", "/internal/intake/enqueue"]),
    ("vf-sweep", ["/internal/vf/sweep"]),
    ("vf-notify", ["/internal/vf/notify"]),
    ("vf-lint", ["/internal/vf/lint"]),
])
def test_each_workflow_calls_vlm_first_never_v1(name, calls):
    w = json.loads((FLOWS / f"{name}.workflow.json").read_text())
    assert len(w["id"]) == 16                            # n8n's id length; the setup script publishes by it
    nodes = {n["name"]: n for n in w["nodes"]}
    urls = [n["parameters"]["url"] for n in w["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    assert urls == [f"http://vf-ui:8000{c}" for c in calls]   # vf-ui, never http://ui:8000 (v1's)
    for src, out in w["connections"].items():            # a chain: every link names a node that exists
        assert src in nodes and all(l["node"] in nodes for branch in out["main"] for l in branch)
    assert len(w["connections"]) == len(w["nodes"]) - 1


def test_the_intake_webhook_is_its_own():
    w = json.loads((FLOWS / "vf-intake.workflow.json").read_text())
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "vf-intake"     # v1's is "intake": one n8n serves both


def test_a_bundle_is_new_once_per_fingerprint():
    a = {"batch": "b", "sor": "SOR1", "fingerprint": "f1", "customer": "AEON EASTVARA TANGERANG", "reasons": 3}
    b = {**a, "sor": "SOR2", "fingerprint": None, "customer": None}
    assert notice.new_items([a, b], set()) == [a, b]
    assert notice.new_items([a, b], {("SOR1", "f1"), ("SOR2", "")}) == []
    assert notice.new_items([{**a, "fingerprint": "f2"}], {("SOR1", "f1")})[0]["fingerprint"] == "f2"   # changed since
    assert notice.text_of([a, b]) == "2 bundles need you: AEON EASTVARA TANGERANG (SOR1), Unknown customer (SOR2)"
    assert notice.text_of([a]).startswith("1 bundle needs you:")


def test_a_notice_is_recorded_once():
    """On the real bundles, inside a transaction that is rolled back: the second call finds nothing new."""
    c = db.connect()
    try:
        c.execute("DELETE FROM staging.notice")          # rolled back below
        first = notice.record(c)
        with db.connect() as other:
            needing = other.execute("SELECT count(*) AS n FROM staging.bundle WHERE status='needs_review' "
                                    "AND sor_no IS NOT NULL").fetchone()["n"]
        assert (first is not None) == (needing > 0)
        assert notice.record(c) is None
        if first:
            assert len(notice.unseen(c)) == needing
            notice.mark_seen(c)
            assert notice.unseen(c) == []
    finally:
        c.rollback()
        c.close()


def test_the_sweep_sends_nothing_while_the_ai_is_refused(monkeypatch):
    from common import queue
    from worker import vf
    sent = []
    monkeypatch.setattr(queue, "send", lambda q, m: sent.append(q))
    monkeypatch.setattr(vf, "blocked", lambda: "DailyLimit: 40 min left")
    assert vf.sweep() == {"skipped": "DailyLimit: 40 min left"} and sent == []


def test_fetching_review_never_marks_a_notice_seen():
    """Only a browser showing /review marks notices seen (its POST /notices/seen after load): the screen tests once
    marked n8n's first notice seen before anyone had looked."""
    import httpx
    ui = os.environ.get("UI_URL", "http://ui:8000")
    with db.connect() as c:
        nid = c.execute("INSERT INTO staging.notice (items, text) VALUES ('[]', 'test: never seen by a fetch') "
                        "RETURNING id").fetchone()["id"]
    try:
        assert httpx.get(f"{ui}/review", timeout=60).status_code == 200
        with db.connect() as c:
            assert c.execute("SELECT seen_at FROM staging.notice WHERE id=%s", (nid,)).fetchone()["seen_at"] is None
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.notice WHERE id=%s", (nid,))
