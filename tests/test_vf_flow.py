"""vlm-first page flow, pure parts: the decision, the second look's rules, and what each model is allowed to see."""
import json

import numpy as np

from common import context, verify
from common.fields import project
from worker import classify, vf


def ctx():
    return context.seed(classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS, classify.JEV_TYPES)


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
    """The look-again request carries field names, meanings and images only: never Tesseract's reading."""
    tesseract_text = "Total Include Tax : 1.126.911,00 PO 4505 832724"
    fields_all = {"po_number": {"value": "4505832724", "source_text": "4505832724", "box": [100, 100, 120, 300]},
                  "total": {"value": "1126006.00", "source_text": "1.126.006", "box": [800, 600, 820, 900]},
                  "ppn": {"value": None, "source_text": None}}
    rd = {"classical_text": tesseract_text, "ocr_words": []}
    res = verify.run("PO", project(fields_all, "PO"), tesseract_text, None)
    seen = {}

    def fake_gemini(purpose, bid, n, call, png, asks, crops):
        seen.update(purpose=purpose, asks=asks, crop_fields=[c for c, _ in crops])
        return {"total": {"value": "1126006.00", "source_text": "1.126.006", "unsure": False}}, {"model": "fake"}
    monkeypatch.setattr(vf, "gemini", fake_gemini)
    up = np.full((1200, 900), 255, np.uint8)
    out, res2, _, sl = vf.look_again("PO", fields_all, res, rd, None, up, ctx(), "b-test", 1)
    sent = json.dumps({k: v for k, v in seen.items()})
    assert seen["purpose"] == "second_look"
    assert "1.126.911" not in sent and "4505 832724" not in sent               # nothing Tesseract read
    assert "1.126.006" not in sent                                             # nor its own first answer
    assert "total" in [a for a, _ in seen["asks"]] and "ppn" in [a for a, _ in seen["asks"]]   # ⚠ + empty required
    assert sl["results"]["total"]["kept_second"] is False                      # same twice, still not backed


def test_outcome():
    assert vf.outcome("unsure", None, None) == "held_unsure"
    assert vf.outcome("decided", "OTHER", None) == "needs_person"
    assert vf.outcome("labelled", "CONTINUATION", None) == "clear"
    ok = {"verdict": "ok", "by": "text"}
    res = {"header": {f: ok for f in vf.REQUIRED["FP"]}}
    assert vf.outcome("decided", "FP", res) == "clear"
    res["header"]["total"] = {"verdict": "check", "why": "not in Tesseract's text"}
    assert vf.outcome("decided", "FP", res) == "needs_person"
