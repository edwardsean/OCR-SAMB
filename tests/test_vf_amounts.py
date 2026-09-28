"""Amounts: one meaning per printed text (rupiah conventions), and amounts the scan cut off never get ✅.
Found with Qwen on Groq (2026-09-24): it stored 111.586 (one hundred eleven) for a printed 111.586, and on page 3 both
it and Tesseract read the half-cut "1.014.424,3x" as "1.014.424,5"."""
import pytest

from common import context, verify
from worker import classify, vf


@pytest.mark.parametrize("printed,number", [
    ("1.126.011,00", 1126011.0), ("9,410,527.00", 9410527.0), ("111.586", 111586.0), ("475.333,70", 475333.7),
    ("1.078.330,", 1078330.0), ("57,082", 57082.0), ("12,5", 12.5), ("Rp 1.014.424,36", 1014424.36), ("8328", 8328.0)])
def test_one_meaning_per_printed_amount(printed, number):
    assert verify.amount(printed) == number


def test_no_number_is_none():
    assert verify.amount("abc") is None and verify.amount(None) is None


def test_the_value_must_be_the_printed_amount_not_another_reading_of_it():
    assert not verify.agrees("amount", "111.586", "111.586")       # one hundred eleven: wrong
    assert verify.agrees("amount", "111586.00", "111.586")


def test_an_amount_cut_off_at_the_edge_never_gets_a_tick():
    text = "Jumtah 8 hem 10144245\nDasar Pengenaan Pajak 1. M4A24,5"
    f = {"dpp": {"value": "1014424.50", "source_text": "1.014.424,5"}}
    v = verify.header("FP", f, text, None)["dpp"]
    assert v["verdict"] == "check" and "cut off" in v["why"]
    f2 = {"dpp": {"value": "1014424.36", "source_text": "1.014.424,36"}}
    assert verify.header("FP", f2, "DPP 1.014.424,36", None)["dpp"]["verdict"] == "ok"


def test_amount_values_come_from_the_printed_text():
    ctx = context.seed(classify.JEV_QUESTION["doc_type"]["criteria"], classify.KEYWORDS, classify.JEV_TYPES)
    fa = {"total": {"value": "1126.006", "source_text": "1.126.006"}, "po_number": {"value": "45", "source_text": "45"}}
    vf.normalise_amounts(fa, ctx)
    assert fa["total"] == {"value": "1126006.00", "source_text": "1.126.006", "ai_value": "1126.006"}
    assert fa["po_number"] == {"value": "45", "source_text": "45"}             # ids are left alone
    vf.normalise_amounts(fa, ctx)
    assert fa["total"]["ai_value"] == "1126.006"                                  # idempotent
