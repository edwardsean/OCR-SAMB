"""Re-checking a page (after a person's confirmation, a rule change, or new Satellite data) must stay cheap: no model
call, no whole-page Tesseract reading, and a page re-checked twice reuses its zoomed readings. It is also stable:
re-checking gives the same verdicts again."""
import os

import pytest

from common import db

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
BID = "b-4bab9b736d"


def _state(n):
    with db.connect() as c:
        p = c.execute("SELECT outcome, keys, fields FROM staging.page WHERE batch_id=%s AND page_no=%s",
                      (BID, n)).fetchone()
        checks = c.execute("SELECT field_path, status, confirmed_by, adjudicated_value FROM staging.field_check "
                           "WHERE batch_id=%s AND page_no=%s ORDER BY field_path", (BID, n)).fetchall()
    return p, checks


def test_recheck_calls_no_model_and_reuses_its_zoom(monkeypatch):
    from worker import enhance, vf, zoom

    def forbidden(*a, **k):
        raise AssertionError("re-checking must not call a model or read the whole page again")
    monkeypatch.setattr(vf, "ai_call", forbidden)
    monkeypatch.setattr(enhance, "read", forbidden)
    with db.connect() as c:
        pages = [r["page_no"] for r in c.execute(
            "SELECT page_no FROM staging.page WHERE batch_id=%s AND classical_text IS NOT NULL ORDER BY 1", (BID,))]
    if not pages:
        pytest.skip("no read pages")
    n = 10 if 10 in pages else pages[0]
    vf.recheck(BID, n)                                   # fills the zoom cache if it wasn't
    before = _state(n)
    calls = []
    real = zoom.reread
    monkeypatch.setattr(zoom, "reread", lambda *a, **k: calls.append(a) or real(*a, **k))
    vf.recheck(BID, n)
    assert not calls, f"{len(calls)} zoomed Tesseract readings on the second re-check"
    assert _state(n) == before                           # the same verdicts again
