"""The trace (common/trace.py, schema/028-trace.sql): spans written 'running' at once and closed with their status
and duration, stages inside a span, events that take their span's page, and tracing that never stops the work."""
import os
import time

import pytest

from common import trace

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
BID = "test-trace"


@pytest.fixture
def rows():
    from common import db

    def clean():
        trace.flush()
        with db.connect() as c:
            c.execute("DELETE FROM staging.trace WHERE batch_id=%s", (BID,))
    clean()

    def get():
        assert trace.flush()                                            # the writer thread has written it all
        with db.connect() as c:
            return c.execute("SELECT * FROM staging.trace WHERE batch_id=%s ORDER BY id", (BID,)).fetchall()
    yield get
    clean()


@needs_db
def test_a_span_its_stages_and_an_event_inside_it(rows):
    with trace.span("page", batch=BID, page=7, run=2) as sp:
        assert [r["status"] for r in rows()] == ["running"]           # visible while it runs
        trace.stage("read")
        time.sleep(0.02)
        trace.stage("classify")
        trace.stage_note(model="qwen-flash")
        trace.event("page.parked", "wait", why="DailyLimit")          # takes the span's page
        sp.note(outcome="clear")
    r = {x["kind"]: x for x in rows()}
    page = r["page"]
    assert page["status"] == "ok" and page["ms"] >= 20 and page["detail"] == {"run": 2, "outcome": "clear"}
    assert r["page.read"]["parent"] == page["id"] and r["page.read"]["ms"] >= 20 and r["page.read"]["page_no"] == 7
    assert r["page.classify"]["detail"] == {"model": "qwen-flash"} and r["page.classify"]["status"] == "ok"
    assert (r["page.parked"]["status"], r["page.parked"]["page_no"], r["page.parked"]["parent"]) == ("wait", 7, page["id"])
    assert r["page"]["service"] == trace.SERVICE


@needs_db
def test_a_failure_closes_the_span_and_its_stage_as_failed(rows):
    with pytest.raises(ValueError):
        with trace.span("page", batch=BID, page=1):
            trace.stage("read")
            raise ValueError("the AI's answer is no JSON")
    r = {x["kind"]: x for x in rows()}
    assert r["page"]["status"] == "fail" and r["page"]["error"] == "ValueError: the AI's answer is no JSON"
    assert r["page.read"]["status"] == "fail"
    with trace.span("page", batch=BID, page=2):
        trace.set_status("wait", "DailyLimit: 45 min left")          # stopped by a limit: goes on by itself
    assert rows()[-1]["status"] == "wait" and rows()[-1]["error"].startswith("DailyLimit")


@needs_db
def test_a_span_a_stopped_worker_left_running_is_closed_before_the_page_starts_again(rows):
    trace._open("page", BID, 3)                                         # never closed: the worker stopped
    trace.cut_off(BID, 3)
    r = rows()[0]
    assert r["status"] == "fail" and r["error"].startswith("cut off") and r["ended_at"]


@needs_db
def test_tracing_never_stops_the_work(monkeypatch):
    """A write that fails (here a table that doesn't exist, as before migration 028) is dropped, the work goes on, and
    tracing pauses for a while."""
    trace.flush()
    monkeypatch.setattr(trace, "_off_until", 0.0)
    trace._write("INSERT INTO staging.no_such_table VALUES (1)", ())
    with trace.span("page", batch=BID, page=1) as sp:                  # nothing raised here
        trace.stage("read")
        sp.note(outcome="clear")
    trace.flush()
    assert trace._off_until > time.time()                               # paused: what came after was dropped


@needs_db
def test_the_work_never_waits_for_the_trace(rows):
    """Written by one thread per process, in order, with the time it happened (not the time it was written)."""
    t0 = time.perf_counter()
    for n in range(200):
        with trace.span("page", batch=BID, page=n):
            trace.stage("read")
    assert time.perf_counter() - t0 < 0.5          # 600 rows queued: no database round trip on the way
    got = rows()
    assert len(got) == 400 and all(r["status"] == "ok" for r in got)
    first = next(r for r in got if r["kind"] == "page" and r["page_no"] == 0)
    child = next(r for r in got if r["kind"] == "page.read" and r["page_no"] == 0)
    assert child["parent"] == first["id"] and child["at"] >= first["at"]
