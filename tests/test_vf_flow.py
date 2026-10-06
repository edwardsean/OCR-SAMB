"""vlm-first page flow, pure parts: the decision, the second look's rules, and what each model is allowed to see."""
import json

import numpy as np

from common import context, verify
from common.fields import project
from worker import classify, vf


def ctx():
    return context.seed_content()


def test_decide_jev_leads_and_the_image_vetoes_a_wrong_fp():
    assert vf.decide({"choice": "TTG", "confidence": 0.9}, False, 0.1)[:2] == ("decided", "TTG")
    assert vf.decide({"choice": "TTG", "confidence": 0.8}, False, 0.1)[0] == "unsure"          # below 0.85
    assert vf.decide({"choice": "TTG", "confidence": 0.99}, True, 0.1)[0] == "unsure"          # QR says FP
    assert vf.decide({"choice": "FP", "confidence": 0.99}, False, 0.3)[0] == "unsure"          # no QR, no FP layout
    assert vf.decide({"choice": "FP", "confidence": 0.9}, False, 0.8)[:2] == ("decided", "FP")  # FP layout
    assert vf.decide({"error": "HTTP 503"}, True, 0.9)[0] == "unsure"                          # Jev down: a person


def test_jev_sees_only_the_ai_reading():
    fields_all = {"po_number": {"value": "4505832724", "source_text": "4505832724"},
                  "document_title": {"value": "Purchase Order", "source_text": "Purchase Order"},
                  "total": {"value": None, "source_text": None}, "lines": [{"row_text": "00010 K6N302030561 ..."}]}
    s = vf.jev_state(fields_all, ctx())
    assert s["found"] == {"po_number": "4505832724"} and s["document_title"] == "Purchase Order"
    assert "total" in s["not_found"] and s["line_rows"] == 1


def test_settle_second_look():
    first = {"value": "510232", "source_text": "510232"}
    assert vf.settle(first, {"value": None, "unsure": True}, False)[0] is False
    assert vf.settle(first, {"value": "S10232", "source_text": "S10232", "unsure": False}, True)[0] is True
    keep, why = vf.settle(first, {"value": "510232", "source_text": "510232", "unsure": False}, False)
    assert not keep and "same twice" in why
    keep, why = vf.settle(first, {"value": "S10232", "source_text": "S10232", "unsure": False}, False)
    assert not keep and "changed its reading" in why


def test_second_look_is_blind(monkeypatch):
    """The look-again request carries field names, meanings and images only: never Tesseract's reading. (An FP's
    own amounts: the page asks them. A PO's are its bundle's, S1.)"""
    tesseract_text = "Total : 1.126.911,00 SOR26110 255837"
    fields_all = {"sor": {"value": "SOR26110255837", "source_text": "SOR26110255837", "box": [100, 100, 120, 300]},
                  "total": {"value": "1126006.00", "source_text": "1.126.006,00", "box": [800, 600, 820, 900]},
                  "ppn": {"value": None, "source_text": None}}
    rd = {"classical_text": tesseract_text, "ocr_words": []}
    res = verify.run("FP", project(fields_all, "FP"), tesseract_text, None)
    seen = {}

    def fake_ai_call(purpose, bid, n, call, png, asks, crops):
        seen.update(purpose=purpose, asks=asks, crop_fields=[c for c, _ in crops])
        return {"total": {"value": "1126006.00", "source_text": "1.126.006,00", "unsure": False}}, {"model": "fake"}
    monkeypatch.setattr(vf, "ai_call", fake_ai_call)
    up = np.full((1200, 900), 255, np.uint8)
    out, res2, _, sl = vf.look_again("FP", fields_all, res, rd, None, up, ctx(), "b-test", 1)
    sent = json.dumps({k: v for k, v in seen.items()})
    assert seen["purpose"] == "second_look"
    assert "1.126.911" not in sent and "SOR26110 255837" not in sent           # nothing Tesseract read
    assert "1.126.006" not in sent                                             # nor its own first answer
    asked = [a for a, _ in seen["asks"]]
    assert "total" in asked and "ppn" in asked and "sor" not in asked          # ⚠ + empty required; not what's backed
    assert sl["results"]["total"]["kept_second"] is False                      # same twice, still not backed


def test_waiting_for_the_daily_limit_to_refill():
    why = "DailyLimit: groq:qwen/qwen3.8-27b: daily limit reached (limit 200000, used 198464, requested 3949), try again in 17m22.416s."
    assert abs(vf.seconds_until(why) - 1042.416) < 0.01
    assert vf.seconds_until("try again in 1h2m3s") == 3723 and vf.seconds_until("try again in 7.5s") == 7.5
    assert vf.seconds_until("daily limit reached") is None


def test_outcome():
    assert vf.outcome(None, None, None) == "waiting_ai"          # not read yet (limit reached): waits, never "unsure"
    assert vf.outcome("unsure", None, None) == "held_unsure"
    assert vf.outcome("decided", "OTHER", None) == "needs_person"
    assert vf.outcome("labelled", "CONTINUATION", None) == "clear"
    ok = {"verdict": "ok", "by": "text"}
    res = {"header": {f: ok for f in vf.REQUIRED["FP"]}}
    assert vf.outcome("decided", "FP", res) == "clear"
    res["header"]["total"] = {"verdict": "check", "why": "not in Tesseract's text"}
    assert vf.outcome("decided", "FP", res) == "needs_person"
    assert vf.outcome("decided", "FP", res, looked=False) == "waiting_ai"     # the look-again hasn't run: it waits


def test_nothing_goes_to_a_person_before_the_look_again(monkeypatch):
    """Skipped (--no-second-look, dry run) or failed (rate limit, budget), the look-again is owed: the page waits.
    A PO page decides only its key (verification redesign, S1): here its number isn't in Tesseract's text."""
    fields_all = {"po_number": {"value": "4505832724", "source_text": "4505832724", "box": [100, 100, 120, 300]},
                  "total": {"value": "1126006.00", "source_text": "1.126.006", "box": [800, 600, 820, 900]}}
    text = "Total 1.126.006"
    rd = {"classical_text": text, "ocr_words": []}
    up = np.full((1200, 900), 255, np.uint8)
    calls = []

    def no_call(*a):
        calls.append(a)
        raise AssertionError("the AI OCR must not be called")
    monkeypatch.setattr(vf, "ai_call", no_call)
    res = verify.run("PO", project(fields_all, "PO"), text, None)
    out, res2, _, sl, looked = vf.look_again_step("PO", fields_all, res, rd, None, up, ctx(), "b-test", 1,
                                                  skip="skipped in this run to save tokens (--no-second-look)")
    assert not calls and not looked and "--no-second-look" in sl["waiting"] and out is fields_all
    assert vf.outcome("decided", "PO", res2, looked) == "waiting_ai"

    def rate_limited(*a):
        raise RuntimeError("groq:qwen/qwen3.8-27b: unavailable after retries (last HTTP 429)")
    monkeypatch.setattr(vf, "ai_call", rate_limited)
    _, res3, _, sl, looked = vf.look_again_step("PO", fields_all, res, rd, None, up, ctx(), "b-test", 1)
    assert not looked and "HTTP 429" in sl["waiting"] and vf.outcome("decided", "PO", res3, looked) == "waiting_ai"

    _, _, _, sl, looked = vf.look_again_step("PO", None, res, rd, None, up, ctx(), "b-test", 1)
    assert not looked and "hasn't read" in sl["waiting"]                       # no reading: nothing to look again at

    full = {**fields_all, "vendor_code": {"value": "214415", "source_text": "214415"},
            "vendor_name": {"value": "PT SARANA ABADI MAKMUR BERSAMA", "source_text": "PT SARANA ABADI MAKMUR BERSAMA"},
            "ppn": {"value": "111586.00", "source_text": "111.586"}}
    printed = "PO 4505832724\nVendor 214415 PT SARANA ABADI MAKMUR BERSAMA\nPPN 111.586\nTotal 1.126.006"
    backed = verify.run("PO", project(full, "PO"), printed, None)
    monkeypatch.setattr(vf, "ai_call", no_call)
    _, _, _, sl, looked = vf.look_again_step("PO", full, backed, rd, None, up, ctx(), "b-test", 1)
    assert looked and sl is None and not calls                                 # everything backed: nothing to ask
    assert vf.outcome("decided", "PO", backed, looked) == "clear"


def test_the_printed_title_is_an_fps_third_image_witness():
    """Pages 1 and 10 of 7000363700-03: Jev FP 1.0, QR too faint to decode, layout 0.69 / 0.61 (2026-09-28)."""
    jev = {"choice": "FP", "confidence": 1.0}
    assert vf.decide(jev, False, 0.69)[0] == "unsure"
    assert vf.decide(jev, False, 0.69, title=True)[:2] == ("decided", "FP")
    assert "printed title" in vf.decide(jev, False, 0.69, title=True)[3]
    assert vf.decide({"choice": "FP", "confidence": 0.8}, False, 0.1, title=True)[0] == "unsure"   # Jev still decides
    assert vf.needs_title(jev, False, 0.69) and not vf.needs_title(jev, True, 0.69)
    assert not vf.needs_title(jev, False, 0.75) and not vf.needs_title({"choice": "PO", "confidence": 1.0}, False, 0.1)
