"""The AI OCR waits out a daily-limit refusal: while the provider's last answer says to come back later, a page waits
without a call. Otherwise an upload's pages would each fail in turn, and every failure counts against vlm-first's
daily cap (it once had 113 calls left, and the uploaded file has 166 pages)."""
import os

import pytest

from common import db
from common.models import openai_vlm

pytestmark = pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")
PROVIDER = "test-breaker"
SPEC = PROVIDER + ":vision"                        # the AI OCR under test
MAP_SPEC = PROVIDER + ":text"                      # a text model with its own quota
REFUSED = ("DailyLimit: groq:x: daily limit reached (limit 200000, used 199986, requested 6892), "
           "try again in 49m31.296s.")


@pytest.fixture
def vf(monkeypatch):
    from worker import vf
    monkeypatch.setattr(vf, "AI_OCR", SPEC)
    monkeypatch.setattr(vf, "AI_MAP", MAP_SPEC)
    monkeypatch.setattr(vf, "CAPS", {SPEC: 150, MAP_SPEC: 300})
    yield vf
    with db.connect() as c:
        c.execute("DELETE FROM staging.model_call WHERE provider=%s", (PROVIDER,))


def answer(ago_s, ok, error=None, spec=SPEC):
    from worker import vf                    # the day the cap counts by (Pacific), not the database's UTC date
    with db.connect() as c:
        c.execute("""INSERT INTO staging.model_call (at, pacific_day, provider, model, purpose, ok, error)
                     VALUES (now() - make_interval(secs => %s), %s, %s, %s, 'read_all', %s, %s)""",
                  (ago_s, vf.pacific_day(), PROVIDER, spec, ok, error))


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



def test_one_models_refusal_never_blocks_another(vf):
    """Each Model Studio model has its own free quota: the AI OCR's quota ending must not stop the text model."""
    answer(60, False, "DailyLimit: dashscope:qwen3-vl-plus: free quota used up (AllocationQuota.FreeTierOnly)")
    assert vf.refused_for(SPEC) > 0 and vf.refused_for(MAP_SPEC) == 0
    assert vf.ai_call("map", "b-test", 1, lambda: ({"ok": 1}, {}))[0] == {"ok": 1}   # the text model still answers
    with pytest.raises(vf.openai_vlm.DailyLimit):
        vf.ai_call("read_all", "b-test", 1, lambda: ({}, {}))
    assert vf.blocked().startswith("DailyLimit")                                     # a page needs both models


def test_a_pages_first_call_leaves_calls_to_finish_pages(vf, monkeypatch):
    """New pages stop short of the cap, so pages already started can still ask their look-again."""
    monkeypatch.setattr(vf, "CAPS", {SPEC: 10, MAP_SPEC: 300})
    for _ in range(7):
        answer(10, True)
    with pytest.raises(vf.OutOfBudget, match="for new pages"):
        vf.reserve("read_all", "b-test", 1)
    assert vf.reserve("second_look", "b-test", 1)                                    # finishing still has room
