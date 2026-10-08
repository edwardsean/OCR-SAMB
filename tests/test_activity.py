"""What each page is doing now (api/activity.py) and the developers' view of the trace (api/observe.py): one state
per page for a batch's step 1, its estimate, and the trace summed up per batch and over time."""
from datetime import datetime, timedelta, timezone

from api import activity, observe

T0 = datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc)


def page(**kw):
    return {"status": "read", "outcome": "clear", "was_read": True, "stuck": False, **kw}


def test_one_state_per_page():
    s = activity.state
    assert s(page())["state"] == "done"
    reading = s(page(status="queued", reading_since=T0, stage="page.tesseract"))
    assert reading["state"] == "reading" and reading["text"] == "Mencocokkan dengan teks yang tercetak" and reading["again"]
    queued = s(page(status="queued", was_read=False, queued_at=T0, ahead=2))
    assert queued["state"] == "queued" and queued["text"] == "Antre: 2 halaman di depannya" and not queued["again"]
    assert s(page(status="queued", was_read=False, queued_at=T0, ahead=0))["text"] == "Antre: halaman berikutnya"
    parked = s(page(status="queued", queued_at=T0, parked_at=T0 + timedelta(seconds=5), parked_why="DailyLimit: 45 min left"))
    assert parked["state"] == "waiting" and "45 menit" in parked["text"]
    blocked = s(page(status="queued", queued_at=T0), "NotSet: the vision model not set: set it on …")
    assert blocked["state"] == "waiting" and "Model belum diatur" in blocked["text"]
    assert s(page(outcome="waiting_ai"))["state"] == "waiting_ai"
    failed = s(page(status="read", extract_status="failed", extract_error="ReadTimeout: x", stuck=True))
    assert failed["state"] == "failed" and failed["can_retry"] and failed["cause"] == "connection"
    assert s(page(status="rendered", scan_status="read", was_read=False))["state"] == "idle"     # never sent
    assert s(page(status="rendered", scan_status="split", was_read=False))["state"] == "idle"    # batch 1's clones
    assert s(page(status="rendered", scan_status="splitting", was_read=False))["state"] == "queued"  # being split


def test_the_estimate():
    now = T0 + timedelta(seconds=30)
    pages = [{"state": "reading", "since": T0}, {"state": "queued", "ahead": 0}, {"state": "queued", "ahead": 4},
             {"state": "done"}]
    # 5 pages up to its last one, 3 workers: 2 rounds of 90 s, after the soonest reading page frees a worker (60 s)
    assert activity.eta(pages, 90, 3, now) == 2 * 90 + 60
    assert activity.eta([{"state": "reading", "since": T0}], 90, 3, now) == 60
    assert activity.eta([{"state": "done"}], 90, 3, now) is None
    assert activity.pace([{"ms": 80_000, "service": "w@1", "recent": True}, {"ms": 100_000, "service": "w@2",
                          "recent": True}, {"ms": 90_000, "service": "w@3", "recent": False}]) == (90.0, 2)
    assert activity.pace([]) == (activity.DEFAULT_PAGE_S, activity.DEFAULT_WORKERS)


def test_the_trace_summed_up():
    assert observe.pct([5, 1, 3, 2, 4], 50) == 3 and observe.pct([], 50) is None and observe.pct([7], 90) == 7
    assert observe.dur(850) == "850 ms" and observe.dur(42_000) == "42 dtk" and observe.dur(185_000) == "3 mnt 5 dtk"
    assert observe.cost("dashscope:qwen3-vl-plus", {"tokens_in": 1_000_000, "tokens_out": 0}) == 0.20
    assert observe.cost("someone:unknown-model", {"tokens_in": 10}) is None              # never a guessed price
    assert observe.summary({"outcome": "clear", "skip": None, "pages": [3, 4]}) == "outcome=clear · pages=3, 4"


def test_one_batch_traced():
    """A page queued at T0, read from T0+20 s to T0+80 s (read 50 s of it), a second attempt waiting on a limit;
    an AI call; an order's status after."""
    files = [{"id": "b1", "file_name": "1 FP.pdf", "page_total": 1, "status": "read", "received_at": T0, "error": None}]
    at = lambda s: T0 + timedelta(seconds=s)                          # noqa: E731
    rows = [{"id": 1, "kind": "page.queued", "at": at(0), "status": "info", "batch_id": "b1", "page_no": 1, "ms": None},
            {"id": 2, "kind": "page", "at": at(20), "ended_at": at(80), "ms": 60_000, "status": "ok",
             "batch_id": "b1", "page_no": 1, "detail": {"outcome": "clear"}},
            {"id": 3, "kind": "page.read", "at": at(21), "ended_at": at(71), "ms": 50_000, "status": "ok",
             "batch_id": "b1", "page_no": 1, "parent": 2},
            {"id": 4, "kind": "order.status", "at": at(85), "status": "info", "batch_id": "b1", "page_no": None,
             "sor_no": "SOR1", "ms": None, "detail": {"was": "grouping", "now": "auto_ok"}}]
    ai = [observe._ai_row({"id": 9, "at": at(22), "ms": 40_000, "purpose": "transcribe", "ok": True, "error": None,
                           "provider": "dashscope", "model": "qwen3-vl-plus", "batch_id": "b1", "page_no": 1,
                           "tokens": {"tokens_in": 3000, "tokens_out": 4000}})]
    s = observe.summarise(files, rows, ai)
    p = s["pages"][0]
    assert (p["attempts"], p["queue_ms"], p["proc_ms"], p["stages"], p["ai_n"]) == (1, 20_000, 60_000, {"read": 50_000}, 1)
    assert p["outcome"] == "clear" and round(p["usd"], 5) == round((3000 * 0.20 + 4000 * 1.60) / 1e6, 5)
    assert s["batch"]["wall_ms"] == 80_000 and s["files"][0]["pages_done"] == 1      # sent at T0, read at T0+80 s
    assert observe.cost("qwen3-vl-plus", {"tokens_in": None, "tokens_out": None}) is None   # no count: no price
    assert [e["kind"] for e in s["events"]] == ["order.status"]
    lanes = observe.lanes(s)
    segs = {g["kind"]: g for g in lanes["pages"][0]["segments"]}
    assert segs["queue"]["left"] == 0 and round(segs["read"]["left"], 1) == round(21 / 85 * 100, 1)


def test_the_trace_in_plain_words():
    """The user (2026-10-08): "what is ai.transcribe, etc? make it human readable and simple"."""
    from api import trace_words as tw
    assert tw.title("ai.transcribe") == "AI call: copy the page's text" and tw.step("ai.transcribe") == "read"
    assert tw.title("page.tesseract") == "Tesseract reads the print" and tw.explain("page.tesseract")
    assert tw.title("person.something_new") == "A person: something new"      # a kind not listed yet still reads
    assert [k for k, _, _ in tw.STEPS][:4] == ["upload", "split", "read", "group"]
    ai = {"kind": "ai.map", "detail": {"model": "qwen-plus", "tokens_in": 1656, "tokens_out": 317, "usd": 0.0007}}
    assert tw.describe(ai) == "qwen-plus · 1,656 tokens in, 317 out · $0.0007"
    assert tw.describe({"kind": "page.queued", "detail": {"why": "new"}}) == "Why: new upload"
    assert tw.describe({"kind": "page", "status": "ok", "detail": {"doc_type": "PO", "outcome": "clear"}}) \
        == "type PO; every value backed"
    assert tw.describe({"kind": "file.split", "detail": {"pages": 1}}) == "1 page"
    assert tw.describe({"kind": "group", "detail": {"orders": ["SOR1"], "waiting": 2}}) \
        == "order SOR1; 2 documents still waiting for its order"
    assert tw.describe({"kind": "order.status", "detail": {"was": "grouping", "now": "auto_ok"}}) == "grouping → auto_ok"
    idle = {"kind": "job.notify", "status": "ok", "detail": {"result": {"new": False}}}
    busy = {"kind": "job.sweep", "status": "ok", "detail": {"result": {"sent": {"b": [3, 4]}, "regrouped": []}}}
    assert tw.idle_job(idle) and not tw.idle_job(busy) and tw.describe(busy) == "sent 2 page(s) back to the AI"
    assert not tw.idle_job({**idle, "status": "fail"})                        # a failed job is never hidden


def test_a_pages_log_puts_each_ai_call_inside_the_stage_it_ran_in():
    at = lambda s: T0 + timedelta(seconds=s)                                   # noqa: E731
    rows = [{"id": 1, "kind": "page.queued", "at": at(0), "status": "info", "batch_id": "b", "page_no": 1},
            {"id": 2, "kind": "page", "at": at(2), "ended_at": at(60), "ms": 58_000, "status": "ok", "batch_id": "b",
             "page_no": 1},
            {"id": 3, "kind": "page.read", "at": at(3), "ended_at": at(40), "ms": 37_000, "status": "ok", "parent": 2,
             "batch_id": "b", "page_no": 1},
            {"id": 4, "kind": "page.classify", "at": at(40), "ended_at": at(41), "ms": 1000, "status": "ok", "parent": 2,
             "batch_id": "b", "page_no": 1}]
    call = lambda i, s, purpose: {"id": i, "kind": f"ai.{purpose}", "at": at(s), "batch_id": "b", "page_no": 1}  # noqa: E731
    pl = observe.page_log(rows, [call(9, 5, "transcribe"), call(10, 40.5, "classify"), call(11, -3600, "replay")])
    assert [r["kind"] for r in pl["log"]] == ["page.queued", "page"]
    attempt = pl["log"][1]
    assert attempt["waited"] == 2000 and [s["kind"] for s in attempt["stages"]] == ["page.read", "page.classify"]
    assert [c["id"] for c in attempt["stages"][0]["calls"]] == [9] and [c["id"] for c in attempt["stages"][1]["calls"]] == [10]
    assert [c["id"] for c in pl["loose"]] == [11]                 # before any traced read: folded on its own


def test_each_stage_says_what_went_in_and_what_came_out():
    """The user (2026-10-08): "for a step that has a input and output, can you put it there too? like … the text model
    output for mapping is the json fields"."""
    from worker import trace_io as io
    blocks = [{"id": "b1", "text": "PURCHASE ORDER"}, {"id": "b8", "text": "70031000039035"}]
    mapping = {"raw": {"fields": {"po_number": {"value": "70031000039035"}, "total": {"value": "4178018.00"}}},
               "raw2": {"fields": {"po_number": {"value": "70031000039035"}, "total": {"value": "4178018.80"}}}}
    fa = {"po_number": {"value": "70031000039035"}, "total": {"value": None}, "lines": [{}, {}]}
    r = io.read(blocks, mapping, fa, [{"kind": "handwriting", "text": "212", "about": "the title"}], 20)
    assert r["in"]["field list"] == "20 fields" and r["out"]["AI OCR copy (vision model)"].startswith("2 blocks\n[b1]")
    assert r["out"]["mapping onto the field list (text model)"] == {"po_number": "70031000039035"}   # what was kept
    assert r["out"]["left empty: the two mappings disagreed"] == "total" and r["out"]["table rows mapped"] == 2
    long = io.tesseract({"classical_text": "x" * 2000, "ocr_conf": 77.1, "ocr_words": [1, 2], "ocr_variant": "close"})
    assert long["out"]["text"].endswith("(500 more characters)") and long["out"]["words"] == 2
    c = io.check({"header": {"total": {"verdict": "ok", "by": "text"}, "vendor_code": {"verdict": "check", "why": "not in Tesseract's text"}}})
    assert c["out"]["values"] == {"total": "✓ text", "vendor_code": "⚠ not in Tesseract's text"}
    p = io.project("PO", {"purchase_order_no": {"value": "70031000039035"}, "dpp": {"value": None}})
    assert p["out"]["PO's fields"] == {"purchase_order_no": "70031000039035"}
    s = io.save("clear", {"po_no": {"value": "70031000039035", "problems": []}}, {"doc_type": "PO", "type_status": "decided"})
    assert s["out"]["linking numbers"] == {"po_no": "70031000039035"}
    assert io.knowledge(None, {}, {})["out"]["tips"].startswith("none")
    from api import trace_words as tw
    assert tw.describe({"kind": "page.check", "detail": c}) == "1 of 2 values backed"
    assert tw.describe({"kind": "page.read", "detail": r}).startswith("2 blocks; 1 field found; left empty")
