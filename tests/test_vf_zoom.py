"""vlm-first: Tesseract's zoomed second look at a value's spot. Pure logic, no images: what counts as backed by print."""
from worker import zoom


def test_page_22_two_readers_sharing_a_mistake_is_not_proof():
    # printed S10232; Gemini read 510232; zoomed in, Tesseract also reads 510232; its whole-page read said $10232
    ok, why = zoom.confirm("510232", ["510232 PT. SARANA ABADI"], ["Kepada", "YTH", ":", "$10232", "PT.", "SARANA"])
    assert not ok and "two ways" in why


def test_a_clean_zoomed_read_with_no_contrary_reading_counts():
    ok, _ = zoom.confirm("1.126.006", ["Total Include Tax : 1.126.006"], ["Total", "Include", "Tax", ":11.126.006"])
    assert ok                           # ':11.126.006' contains the value: a glued colon, not a different number


def test_neighbouring_words_and_cut_off_crops_are_not_disagreements():
    assert zoom.disagreement("58423525", ["PO", "58423525"]) is None
    assert zoom.disagreement("SARANA ABADI MAKMUR BERSAMA PT", "SARANA ABADI MAKMUR BERSAMA".split()) is None
    assert zoom.disagreement("5043773365", ["9043773365"]) == "9043773365"


def test_not_found_zoomed_in_stays_unconfirmed():
    ok, why = zoom.confirm("SOR26110256810", ["Sales Order [SO] # SOR20110258010"], [])
    assert not ok and "not found" in why


def test_a_changed_digit_never_passes():
    texts = ["Total Include Tax : 1.126.006"]
    assert zoom.confirm("1.126.006", texts, [])[0]
    assert not zoom.confirm("2.126.006", texts, [])[0]


def test_ai_box_is_used_first_and_clamped():
    assert zoom.by_ai_box([100, 200, 150, 400], (3000, 2000)) == (400, 300, 800, 450)
    assert zoom.by_ai_box([150, 200, 100, 400], (3000, 2000)) is None       # empty box
    assert zoom.by_ai_box(None, (3000, 2000)) is None


def test_a_number_sharing_only_its_date_digits_is_not_the_value():
    """2026-10-16: a receipt's PO number TA05PO1009260248 (its line unread by Tesseract) was cropped at the GR ID line,
    TA05GR1009260233: 7 shared characters were enough. A line counts only when it holds the value itself."""
    from worker import zoom
    gr = [["GR", 90, 1496, 144, 68, 38], ["ID", 90, 1580, 144, 50, 38], [":", 90, 1640, 144, 10, 38],
          ["TA05GR1009260233", 90, 1700, 144, 468, 38]]
    assert zoom.by_words(gr, "TA05PO1009260248") is None
    misread = [["Po", 90, 1300, 470, 50, 38], ["Number:", 90, 1360, 470, 200, 38], ["TA05P01009260248", 90, 1600, 470, 470, 38]]
    assert zoom.by_words(gr + misread, "TA05PO1009260248")[1] == 470          # one O read as 0: still its line
    assert zoom.by_words([["Rp1.126.011,00", 90, 10, 900, 300, 30]], "1126011")[1] == 900
