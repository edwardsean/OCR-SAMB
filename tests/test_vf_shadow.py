"""Verification redesign, S0 (the user, 2026-09-26): a bundle rule change can be measured before it's adopted
(crosscheck.inputs / evaluate / shadow write nothing), the FP is graded on the order side (as ordered), and a misread
beyond what a bundle check allows never passes it."""
import os

import pytest

from common import db

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
BID = "b-4bab9b736d"


@pytest.fixture(scope="module")
def xs():
    from grouper import crosscheck
    with db.connect() as c:
        out = crosscheck.inputs(c, BID)
    if not out:
        pytest.skip("no complete bundles in the sample batch")
    return out


def snapshot():
    with db.connect() as c:
        return [tuple(r.values()) for r in c.execute(
            "SELECT id, status::text, checks::text, fingerprint, checked_at FROM staging.bundle ORDER BY id")]


def test_shadow_writes_nothing():
    import _data
    _data.scan(BID)
    from grouper import crosscheck
    before = snapshot()
    out = crosscheck.shadow(BID, show=lambda *_: None)
    assert out and snapshot() == before


def test_evaluate_is_repeatable(xs):
    from grouper import crosscheck
    for x in xs:
        assert crosscheck.evaluate(x) == crosscheck.evaluate(x), x["sor"]


def test_stored_checks_are_todays(xs):
    """Right after `grouper.group <batch> --recheck`, what is stored is what today's rules say. After a rule change
    this fails until it's adopted: measure it first with `python -m grouper.crosscheck shadow <batch>`."""
    from grouper import crosscheck
    for x in xs:
        r = crosscheck.evaluate(x)
        was = x["stored"].get("checks") or {}
        assert {k: (c["status"], c["why"]) for k, c in r["checks"].items()} == \
            {k: (c["status"], c["why"]) for k, c in was.items()}, x["sor"]
        assert r["status"] == x["status"], x["sor"]


def test_fp_graded_as_ordered():
    """The FP prints the SO as ordered; the invoice (sor.dpp/ppn/total) is what was received, lower after a tolakan."""
    from api import app
    assert {k: app.FP_SATELLITE[k] for k in ("dpp", "ppn", "total")} == \
        {"dpp": "order_dpp", "ppn": "order_ppn", "total": "order_total"}


def test_bundle_bends(monkeypatch):
    """Every value a passing bundle check used, bent at each digit: beyond the allowance it must stop passing. And the
    test itself catches a check that passes whatever it's given."""
    import _data
    _data.scan(BID)
    from grouper import crosscheck
    from api import app
    changed, missed = app._vf_bend_bundles(BID)
    assert changed and not missed, missed
    real = crosscheck.check_bundle

    def loose(*a, **k):
        out = real(*a, **k)
        for name, c in out.items():
            if c["status"] == "fail":
                out[name] = {**c, "status": "pass"}
        return out
    monkeypatch.setattr(crosscheck, "check_bundle", loose)
    assert app._vf_bend_bundles(BID)[1]


def test_beyond():
    from api import app
    amount = {"kind": "amount", "ref": 1078330.01, "allow": 5.0}
    assert not app._beyond(amount, "1078329.00") and app._beyond(amount, "1078339.00")
    date = {"kind": "date", "lo": "2026-09-07", "hi": "2026-09-23"}
    assert not app._beyond(date, "2026-09-10") and app._beyond(date, "2026-10-09") and app._beyond(date, "2026-09-06")
    assert [v for v, _ in app._date_bends("2026-09-09")] == ["3026-09-09", "2126-09-09", "2036-09-09", "2027-09-09",
                                                            "2026-09-19"]    # month 19 and day 00 aren't dates
