"""Reading many pages at once (the mentor, 2026-10-08: "delivery time … nanonets in 3 hours can process thousands"):
a worker reads WORKER_CONCURRENCY pages at once, a page's two mappings are asked at the same time, Tesseract reads the
page beside the AI, and the trace never makes the work wait (tests/test_trace.py)."""
import contextvars
import os
import threading
import time

import pytest

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


@needs_db
def test_the_two_mappings_are_asked_at_the_same_time(monkeypatch):
    from worker import vf
    calls, lock = [], threading.Lock()

    def ai_call(purpose, bid, n, fn, *args):
        with lock:
            calls.append((purpose, time.perf_counter()))
        time.sleep(0.3)                                     # a text model's answer
        return {"fields": {}}, {"ms": 300}
    monkeypatch.setattr(vf, "ai_call", ai_call)
    monkeypatch.setattr(vf, "MAP_TWICE", True)
    monkeypatch.setattr(vf, "mapped", lambda raw, *a: ({}, {"raw": raw}, []))
    monkeypatch.setattr(vf.transcript, "merge", lambda first, second: first)
    ctx = {"fields": {}, "types": {}}
    monkeypatch.setattr(vf, "two_step_versions", lambda c: ("tv", "mv", "mv#x2"))
    monkeypatch.setattr(vf.context, "vlm_schema", lambda c: {})
    prev = {"transcript": [{"id": "b1", "text": "PO"}], "transcript_version": "tv", "mapping": None}
    t0 = time.perf_counter()
    fa, meta, blocks, notes, mapping = vf.read_then_map("test-throughput", 1, None, ctx, prev)
    took = time.perf_counter() - t0
    assert [p for p, _ in calls] == ["map", "map"] and abs(calls[0][1] - calls[1][1]) < 0.1
    assert took < 0.55, took                                # side by side: ~0.3 s, not 0.6 s
    assert meta["map"] == meta["map2"] == {"ms": 300} and mapping["raw2"] == {"fields": {}}


def test_tesseract_beside_the_ai_stays_under_the_pages_try():
    """Run in the page's own context (contextvars.copy_context), its trace row is a stage of that try."""
    from common import trace
    seen = {}

    def background():
        with trace.span("page.tesseract") as s:
            seen["tess"] = s
    with trace.span("page", batch="test-throughput", page=1) as page:
        f = threading.Thread(target=contextvars.copy_context().run, args=(background,))
        f.start(); f.join()
    assert seen["tess"].parent == page.id and (seen["tess"].batch, seen["tess"].page) == ("test-throughput", 1)


def test_a_page_threads_acks_run_on_the_connections_thread():
    from worker import main
    done = []

    class Conn:
        def add_callback_threadsafe(self, fn):
            done.append(("queued", fn))                     # pika runs it on its own thread, in order

    class Ch:
        def basic_ack(self, tag):
            done.append(("ack", tag))

        def basic_nack(self, tag, requeue=True):
            done.append(("nack", tag))
    safe = main._OnItsThread(Conn(), Ch())
    safe.basic_ack(7)
    assert done[0][0] == "queued" and len(done) == 1        # nothing ran on the page's thread
    done[0][1]()
    assert done[-1] == ("ack", 7)

    class Closed:
        def add_callback_threadsafe(self, fn):
            raise RuntimeError("connection closed")
    main._OnItsThread(Closed(), Ch()).basic_nack(8)         # dropped, never raised: RabbitMQ gives the page again


def test_the_caps_let_a_days_work_through():
    from common import config
    assert config.VF_AI_OCR_DAILY_CAP >= 10_000 and config.VF_AI_MAP_DAILY_CAP >= 20_000   # ~6,800 pages a day
    assert config.WORKER_CONCURRENCY >= 1


def test_recording_a_stage_never_fails_the_page():
    """The load test (2026-10-08): a model answered fields as bare strings, and recording the stage crashed 7 pages."""
    from worker import trace_io as io
    mapping = {"raw": {"fields": {"po_number": "70031000039035", "total": {"value": "1.00"}}},
               "raw2": {"fields": {"po_number": "70031000039035", "total": "2.00"}}}
    r = io.read([], mapping, {"po_number": {"value": "70031000039035"}}, [], 20)
    assert r["out"]["left empty: the two mappings disagreed"] == "total"
    assert io.read([], {"raw": "not even a dict"}, None, None, 20)["out"]["mapping onto the field list (text model)"] \
        == "nothing found"
    broken = io.check({"header": "not a dict"})                       # any other surprise: a note, never an error
    assert "not recorded" in broken["out"] and broken["out"]["not recorded"].startswith("check: ")


def test_a_grouping_round_reads_what_group_run_gives(monkeypatch):
    """group.run gives (plan, files): the round's trace note read it as a dict and failed every round, skipping the
    look-agains it should have sent (2026-10-08's load test)."""
    from grouper import group, serve
    monkeypatch.setattr(group, "run", lambda bid: ({"bundles": {"SOR1": {}}, "documents": [{"hold": "x"}]}, []))
    sent = []
    monkeypatch.setattr(serve, "dispatch", lambda bid: sent.append(bid) or [3])
    import worker.learn as learn
    monkeypatch.setattr(learn, "after_grouping", lambda bid, show=None: [])
    assert serve.round_(["b1"]) == {"b1": [3]} and sent == ["b1"]      # the look-again was sent


CUT = '''{
  "fields": {"dpp": {"block": "b45", "text": "TOTAL AFTER DISCOUNT : 13.856.360", "value": "13856360.00"},
             "sor": null},
  "lines": [
    {"row": "b31", "description": "ELLIPS HAIR VITAMIN", "qty": "6"},
    {"row": "b32", "description": "ELLIPS HAIR SRM", "qty": "12"},
    {"row": "b33", "description": "ELLIPS HAIR SRM ULTRA TRMN 48ML; BTL",
      "customer_item_code": "4528342",'''


def test_a_mapping_cut_at_its_limit_keeps_the_fields_and_the_whole_rows(monkeypatch):
    """The load test (2026-10-08): 5 mapping answers stopped at exactly 4,096 tokens ("finish_reason": "length") on
    long tables, were thrown away, and every retry was paid and cut the same way."""
    from common.models import openai_vlm as ov
    got = ov.salvage_map(CUT)
    assert got["fields"]["dpp"]["value"] == "13856360.00" and [r["row"] for r in got["lines"]] == ["b31", "b32"]
    assert ov.salvage_map("not json at all") == {}
    monkeypatch.setattr(ov, "_post", lambda spec, content, max_tokens: (CUT, {"tokens_out": max_tokens}))
    raw, meta = ov.map_text("prompt", "dashscope:qwen-plus")
    assert raw["fields"]["dpp"]["value"] == "13856360.00" and meta["salvaged"] == 2 and meta["cut"]
    assert ov.MAP_TOKENS >= 8192


def test_a_stopping_worker_takes_no_new_page():
    from worker import main
    main.STOP.clear()
    main._stop()
    assert main.STOP.is_set()
    main.STOP.clear()


def test_a_tests_correction_never_wakes_the_real_teacher(monkeypatch):
    """2026-10-08: the test suite's corrections woke the running teacher, which paid the text model for them."""
    from common import queue
    sent = []
    monkeypatch.setattr(queue, "connect", lambda: sent.append("connected"))
    queue.wake_teacher("a test's correction")                 # tests/conftest.py mutes this process
    assert sent == []


def test_a_used_up_free_quota_says_what_to_do_not_a_wait():
    """2026-10-08: the page said "lanjut sendiri dalam ±18 menit" while qwen3-vl-plus's free quota was gone for good."""
    from api import stuck
    t = stuck.blocked_text("DailyLimit: dashscope:qwen3-vl-plus's free quota is used up (AllocationQuota.FreeTierOnly):"
                           " change the model on the Teknis screen, or turn billing on for it")
    assert t.startswith("Kuota gratis qwen3-vl-plus di Alibaba sudah habis") and "Ganti model" in t and "menit" not in t
    assert "18 menit" in stuck.blocked_text("DailyLimit: 18 min left of dashscope:x's refusal")


def test_an_old_runs_ticket_is_dropped_before_it_can_be_parked_again(monkeypatch):
    """A stopped batch's tickets came back from the waiting room and were parked again, turning its pages 'queued'."""
    from worker import main, vf
    acked, parked = [], []
    monkeypatch.setattr(main, "current_run", lambda bid: 3)
    monkeypatch.setattr(vf, "blocked", lambda: "DailyLimit: x")
    monkeypatch.setattr(vf, "park", lambda t, why: parked.append(t))
    monkeypatch.setattr(main.settings, "refresh", lambda *a, **k: False)

    class Ch:
        def basic_ack(self, tag):
            acked.append(tag)

    class M:
        delivery_tag, redelivered = 9, False
    main.on_message(Ch(), M(), None, b'{"batch_id": "b", "page_no": 1, "run": 2, "parked": "DailyLimit: x"}')
    assert acked == [9] and parked == []


def test_pages_per_worker_is_a_setting_on_the_screen(monkeypatch):
    """The user (2026-10-08): "make this a setting in the teknis side that we can modify without touching the code";
    4 until one is saved; running workers take it within seconds (worker/main.py pages_at_once)."""
    from common import config, settings
    from worker import main
    assert config.WORKER_CONCURRENCY_DEFAULT == 4
    assert settings.number({}, "WORKER_CONCURRENCY") == 4                      # nothing saved: the default
    assert settings.number({"WORKER_CONCURRENCY": "8"}, "WORKER_CONCURRENCY") == 8
    assert settings.number({"WORKER_CONCURRENCY": "99"}, "WORKER_CONCURRENCY") == 16   # kept within its range
    assert settings.number({"WORKER_CONCURRENCY": "lots"}, "WORKER_CONCURRENCY") == 4

    class C:
        def execute(self, *a):
            self.put = a
    for bad, why in (("0", "1 to 16"), ("2.5", "whole number"), ("17", "1 to 16")):
        with pytest.raises(ValueError, match=why):
            settings.save_number(C(), "WORKER_CONCURRENCY", bad, "tester")
    with pytest.raises(ValueError, match="say who"):
        settings.save_number(C(), "WORKER_CONCURRENCY", "6", "")
    c = C()
    assert settings.save_number(c, "WORKER_CONCURRENCY", " 6 ", "tester") == 6 and c.put[1] == ("WORKER_CONCURRENCY", "6", "tester")

    monkeypatch.setattr(settings, "refresh", lambda *a, **k: False)
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", 6)
    assert main.pages_at_once() == 6
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", 40)
    assert main.pages_at_once() == 16
