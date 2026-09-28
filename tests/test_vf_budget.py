"""The AI OCR waits out a daily-limit refusal: while the provider's last answer says to come back later, a page waits
without a call. Otherwise an upload's pages would each fail in turn, and every failure counts against vlm-first's
daily cap (it once had 113 calls left, and the uploaded file has 166 pages)."""
import os

import pytest

from common import db
from common.models import openai_vlm

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
PROVIDER = "test-breaker"
REFUSED = ("DailyLimit: groq:x: daily limit reached (limit 200000, used 199986, requested 6892), "
           "try again in 49m31.296s.")


@pytest.fixture
def vf(monkeypatch):
    from worker import vf
    monkeypatch.setattr(vf, "AI_PROVIDER", PROVIDER)
    yield vf
    with db.connect() as c:
        c.execute("DELETE FROM staging.model_call WHERE provider=%s", (PROVIDER,))


def answer(ago_s, ok, error=None):
    with db.connect() as c:
        c.execute("""INSERT INTO staging.model_call (at, pacific_day, provider, model, purpose, ok, error)
                     VALUES (now() - make_interval(secs => %s), current_date, %s, 'x', 'read_all', %s, %s)""",
                  (ago_s, PROVIDER, ok, error))


def calls(vf):
    with db.connect() as c:
        return c.execute("SELECT count(*) AS n FROM staging.model_call WHERE provider=%s", (PROVIDER,)).fetchone()["n"]


def test_waits_without_a_call(vf):
    answer(60, False, REFUSED)
    made = []
    with pytest.raises(openai_vlm.DailyLimit) as e:
        vf.ai_call("read_all", "b-test", 1, lambda: made.append(1) or ({}, {}))
    assert not made and calls(vf) == 1               # no call, and nothing new counted against the cap
    left = vf.seconds_until(str(e.value))            # what's left of the wait, in the words `again` waits on
    assert 48 * 60 < left < 48 * 60 + 40


def test_asks_once_the_wait_has_passed(vf):
    answer(3600, False, REFUSED)
    assert vf.ai_call("read_all", "b-test", 1, lambda: ({"ok": 1}, {}))[0] == {"ok": 1}
    assert calls(vf) == 2


def test_a_later_answer_ends_the_wait(vf):
    answer(120, False, REFUSED)
    answer(60, True)                                 # the provider answered since (e.g. a smaller request fit)
    assert vf.refused_for() == 0


def test_other_failures_never_make_it_wait(vf):
    answer(60, False, "RuntimeError: groq:x: HTTP 400: bad image")
    assert vf.refused_for() == 0
