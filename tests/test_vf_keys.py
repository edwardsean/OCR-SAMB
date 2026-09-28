"""Verification redesign S4: a key only the AI OCR read links by the store printed on the page (common/satellite.py
by_store, worker/vf.py store_step). The numbers and stores are Satellite's real ones: Boots ships one order to ten
stores (POs 4505832720 … 29), so a one-digit misread lands on another store's SO; AEON EASTVARA, Duta Buah BSD and
Hero's DC get runs of numbers for one store, where the store can't tell them apart."""
import io
import os

import pytest
from PIL import Image

from common import keys as keymod
from common import satellite
from common.models import openai_vlm, vlm
from grouper import group
from worker import vf

ROWS = [   # (Nomor CPO, SOR, ship-to)
    ("4505832714", "SOR26110255744", "BOOTS ASHTA DISTRICT 8"),
    ("4505832720", "SOR26110255799", "BOOTS AEON TANJUNG BARAT"),
    ("4505832721", "SOR26110255815", "BOOTS FX SUDIRMAN GELORA JAKPUS"),
    ("4505832722", "SOR26110255829", "BOOTS TRANS STUDIO MALL CIBUBUR"),
    ("4505832723", "SOR26110258282", "BOOTS BINTARO XCHANGE 2"),
    ("4505832724", "SOR26110255837", "BOOTS HARAPAN INDAH AVENUE"),
    ("4505832725", "SOR26110255885", "BOOTS GRAND OUTLET KARAWANG"),
    ("4505832726", "SOR26110255900", "BOOTS GRAND INDONESIA MENTENG"),
    ("4505832727", "SOR26110255913", "BOOTS LIVING WORLD KOTA WISATA"),
    ("4505832728", "SOR26110255924", "BOOTS THE PARK PEJATEN"),
    ("4505832729", "SOR26110255932", "BOOTS LIVING WORLD GRAND WISATA"),
    ("4505836724", "SOR26110258656", "FOODHALL LIPPO MALL PURI"),
    ("5213318", "SOR26110256834", "HARI HARI DUTA HARAPAN INDAH"),
    ("3013997610", "SOR26110255836", "FARMERS MARKET SUMMARECON DIGITAL C"),
    ("4505803357", "SOR26110255830", "FOODHALL PONDOK INDAH MALL 2"),
    ("10101000125411", "SOR26110263409", "AEON EASTVARA TANGERANG"),
    ("10101000125418", "SOR26110264129", "AEON EASTVARA TANGERANG"),
    ("10101000125428", "SOR26110263399", "AEON EASTVARA TANGERANG"),
    ("PO20260932027", "SOR26110264167", "DUTA BUAH GREEN GARDEN"),
    ("PO20260932028", "SOR26110264646", "DUTA BUAH BUMI SERPONG DAMAI"),
    ("PO20260932029", "SOR26110264346", "DUTA BUAH BUMI SERPONG DAMAI"),
    ("58415552", "SOR26110257257", "HERO DC PBF [320] CIBITUNG BEKASI"),
    ("58415554", "SOR26110257259", "HERO DC PBF [320] CIBITUNG BEKASI"),
]
SOS = {satellite.flat(sor): {"sor_no": sor, "cpo_no": cpo, "customer_name": name} for cpo, sor, name in ROWS}
BOOTS = {cpo: (sor, name) for cpo, sor, name in ROWS[1:11]}
CHECK = {"verdict": "check", "why": "not in Tesseract's text"}


def store(text, unsure=False):
    return {"value": text, "source_text": text, "unsure": unsure}


def po(number, ship_to=None, verdict=CHECK, confirmed=None, doc_type="PO"):
    return satellite.settle(doc_type, {"purchase_order_no": {"value": number}}, {"purchase_order_no": dict(verdict)},
                            SOS, confirmed, ship_to=ship_to)[1]["purchase_order_no"]


def test_page_4_links_by_the_store_it_prints():
    """Page 4: the AI read 4505832724, Tesseract '20583272'; the page prints BOOTS HARAPAN INDAH BEKASI."""
    v = po("4505832724", store("BOOTS HARAPAN INDAH BEKASI"))
    assert v["verdict"] == "ok" and v["by"] == "ship_to" and "BOOTS HARAPAN INDAH AVENUE" in v["why"]
    k = keymod.derive("PO", {"purchase_order_no": {"value": "4505832724"}}, "20583272", None, {"purchase_order_no": v})
    assert k["po_no"]["confirmed_by"] == "ship_to"
    out = group.plan([{"page_no": 3, "doc_type": "FP", "type_status": "decided",
                       "keys": {"sor": {"value": "SOR26110255837", "confirmed_by": "qr"}}},
                      {"page_no": 4, "doc_type": "PO", "type_status": "decided", "keys": k}], SOS)
    assert {n: s for s, b in out["bundles"].items() for d in b["documents"] for n in d["pages"]} == \
        {3: "SOR26110255837", 4: "SOR26110255837"}


def test_the_page_asks_once_and_waits_until_answered():
    v = po("4505832724")                                         # not asked yet
    assert v["verdict"] == "check" and v["store"] == "SOR26110255837" and "not asked yet" in v["why"]
    assert vf.store_pending("PO", {"header": {"purchase_order_no": v}}, None)
    answered = {"header": {"purchase_order_no": po("4505832724", store(None))}}
    assert not vf.store_pending("PO", answered, store(None))    # asked once: a null answer is an answer
    assert answered["header"]["purchase_order_no"]["verdict"] == "check"      # ... and decides nothing


def test_each_boots_po_links_only_with_its_own_store():
    """Ten SOs, one order, the same total: only the number and the store together tell them apart."""
    for number, (sor, name) in BOOTS.items():
        for _, (_, other) in BOOTS.items():
            v = po(number, store(other))
            assert (v["verdict"] == "ok") == (other == name), (number, other, v["why"])


def test_a_misread_number_is_held_by_its_store():
    v = po("4505832723", store("BOOTS HARAPAN INDAH BEKASI"))   # …24 misread as …23 (BINTARO XCHANGE 2)
    assert v["verdict"] == "check" and "SOR26110255837" in v["why"] and "misread" in v["why"]


def test_a_store_that_fits_another_as_well_decides_nothing():
    assert po("4505832724", store("BOOTS"))["verdict"] == "check"                 # every Boots store
    assert po("4505832724", store("HARAPAN INDAH"))["verdict"] == "check"         # Hari Hari's DUTA HARAPAN INDAH too
    assert po("4505832729", store("BOOTS LIVING WORLD"))["verdict"] == "check"    # KOTA WISATA or GRAND WISATA?
    assert po("4505832729", store("LIVING WORLD GRAND WISATA"))["verdict"] == "ok"
    assert po("4505832724", store("BOOTS HARAPAN INDAH BEKASI", unsure=True))["verdict"] == "check"
    assert po("4505832724", store(None))["verdict"] == "check"


def test_one_stores_run_of_numbers_never_asks():
    """AEON EASTVARA …418 (…411, …428 go there too), Duta Buah BSD …29 (…28 too), Hero's DC: no question to ask."""
    for number, name in (("10101000125418", "AEON EASTVARA TANGERANG"),
                         ("PO.2026.09.32029", "DUTA BUAH BUMI SERPONG DAMAI"),
                         ("58415552", "HERO DC PBF [320] CIBITUNG BEKASI")):
        v = po(number)
        assert v["verdict"] == "check" and "store" not in v, number
        assert not vf.store_pending("PO", {"header": {"purchase_order_no": v}}, None)
        assert po(number, store(name))["verdict"] == "check", number


def test_print_satellite_and_a_person_come_first():
    assert po("4505832724", store("BOOTS BINTARO XCHANGE 2"), verdict={"verdict": "ok", "by": "text"}) == \
        {"verdict": "ok", "by": "text"}                          # print decided: the store isn't consulted
    person = {"purchase_order_no": {"value": "4505832724", "confirmed_by": "Edward"}}
    assert po("4505832724", store("BOOTS BINTARO XCHANGE 2"), confirmed=person)["by"] == "person"
    alone = {satellite.flat("SOR26110255837"): SOS[satellite.flat("SOR26110255837")]}
    _, h = satellite.settle("PO", {"purchase_order_no": {"value": "4505832724"}}, {"purchase_order_no": dict(CHECK)},
                            alone)
    assert h["purchase_order_no"]["by"] == "satellite" and "store" not in h["purchase_order_no"]   # no neighbour


def test_a_receipt_sor_reference_without_its_letters():
    """Puri Indah-style DO#: the SOR printed without 'SOR'. SOR26110255836 and …830 are other stores' SOs."""
    f = {"no_ref": {"value": "26110255837"}}
    _, h = satellite.settle("TTG", f, {"no_ref": dict(CHECK)}, SOS)
    assert h["no_ref"]["store"] == "SOR26110255837"
    _, h = satellite.settle("TTG", f, {"no_ref": dict(CHECK)}, SOS, ship_to=store("BOOTS HARAPAN INDAH BEKASI"))
    assert h["no_ref"]["by"] == "ship_to"


def _png():
    b = io.BytesIO()
    Image.new("L", (20, 20), 255).save(b, "PNG")
    return b.getvalue()


def test_the_store_question_is_blind(monkeypatch):
    """The page and the question only: never Satellite's store names, never the key."""
    sent = {}

    def post(spec, content, max_tokens=4096):
        sent["content"] = content
        return '{"value": "BOOTS HARAPAN INDAH BEKASI", "source_text": "BOOTS HARAPAN INDAH BEKASI", "unsure": false}', {}
    monkeypatch.setattr(openai_vlm, "_post", post)
    a, _ = openai_vlm.store(_png(), "groq:qwen/qwen3.8-27b")
    assert a == store("BOOTS HARAPAN INDAH BEKASI")
    text = " ".join(c.get("text", "") for c in sent["content"]).upper()
    assert [c["type"] for c in sent["content"]] == ["text", "image_url"]
    assert not {w for _, _, name in ROWS for w in satellite.store_words(name) if len(w) > 3} & \
        satellite.store_words(text)
    assert "4505832724" not in text

    parts = {}

    def call(p, schema, **kw):                                  # Gemini's adapter: the same question
        parts["p"] = p
        return {"value": None, "source_text": None, "unsure": True}, {}
    monkeypatch.setattr(vlm, "_call", call)
    vlm.store(_png())
    assert len(parts["p"]) == 2 and "HARAPAN" not in parts["p"][0]["text"].upper()


@pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only: Satellite's real export")
def test_on_satellites_real_export():
    from common import db
    with db.connect() as c:
        sos = satellite.load(c)
    if not sos:
        pytest.skip("Satellite's export isn't loaded")
    index = satellite._cached_index(sos, "cpo_no")
    exact, near = satellite.near_keys("4505832724", index)
    names = {s["customer_name"] for s in sos.values() if s.get("customer_name")}
    assert [n for n in names if satellite.by_store(store(n), exact[0], near, sos)[0]] == ["BOOTS HARAPAN INDAH AVENUE"]
    assert satellite.by_store(store("BOOTS HARAPAN INDAH BEKASI"), exact[0], near, sos)[0]


def test_the_look_again_prompt_shows_valid_json(monkeypatch):
    """qwen3-vl-plus copied the example answer literally; '{"x0", "y0", …}' isn't JSON (2026-09-28)."""
    import json as _json
    sent = {}

    def post(spec, content, max_tokens=4096):
        sent["text"] = content[0]["text"]
        return '{"po_number": {"value": "1", "source_text": "1", "unsure": false}}', {}
    monkeypatch.setattr(openai_vlm, "_post", post)
    openai_vlm.second_look(_png(), [("po_number", "the PO number")], [], "groq:qwen/qwen3.8-27b")
    example = sent["text"].split("mapping each field above to ", 1)[1].split(" (0-1000")[0] + ', "unsure": false}'
    assert _json.loads(example)["box"] == {"x0": 0, "y0": 0, "x1": 0, "y1": 0}       # the example is valid JSON


def test_a_garbled_box_keeps_the_answer():
    """qwen3-vl-plus, page 13's look-again (2026-09-28): the values were right, one box wasn't JSON."""
    raw = ('{"sor": {"value": "SOR26110254669", "source_text": "SOR26110254669",\n'
           '  "box": {"x0": 760,   " "`y0": 83, "x1": 930, "y1": 98}, "unsure": false},\n'
           ' "total": {"value": "2687976.00", "source_text": "2.687.976,00", "box": {"x0": 830, "y0": 773, "x1": 950,'
           ' "y1": 788}, "unsure": false}}')
    out = openai_vlm._json(raw)
    assert out["sor"]["value"] == "SOR26110254669" and out["sor"]["box"] is None
    assert out["total"]["source_text"] == "2.687.976,00"


def test_a_key_settles_by_its_rows_and_a_misread_never_does():
    """A single-store customer's POs run in sequence (Duta Buah BSD PO.2026.09.32028, …29), so neither Satellite nor
    the store can vouch for the AI's reading. The page's rows can: amounts and quantities are the order's own
    (7000363700-03 p6: 6 of 7 rows fit its SO, 2 fit the best neighbour's) (2026-09-28)."""
    from grouper import matching
    lines = {"SO29": [{"line_no": 10, "description": "MENTOS ROLL 37GR FRUIT", "line_amount": 72077.76, "vat": 0,
                       "qty_pcs": 24, "pcs_per_uom": 1, "price_pcs": 3003.24},
                      {"line_no": 20, "description": "MENTOS ROLL 37GR MINT", "line_amount": 144155.52, "vat": 0,
                       "qty_pcs": 48, "pcs_per_uom": 1, "price_pcs": 3003.24}],
             "SO28": [{"line_no": 10, "description": "MENTOS ROLL 37GR FRUIT", "line_amount": 36038.88, "vat": 0,
                       "qty_pcs": 12, "pcs_per_uom": 1, "price_pcs": 3003.24},
                      {"line_no": 20, "description": "KOPIKO CANDY 150GR", "line_amount": 99000.00, "vat": 0,
                       "qty_pcs": 10, "pcs_per_uom": 1, "price_pcs": 9900}]}
    fields = {"lines": [
        {"product_description": "MENTOS ROLL FRUIT 37G", "qty": "24", "uom": "PCS", "unit_price": "3004",
         "row_text": "6 MENTOS ROLL FRUIT 37G 24 PCS 3,004.00 72,086.49"},
        {"product_description": "MENTOS ROLL MINT 37GR", "qty": "48", "uom": "PCS", "unit_price": "3004",
         "row_text": "7 MENTOS ROLL MINT 37GR 48 PCS 3,004.00 144,172.97"}]}
    ok, why = matching.rows_tell("PO", fields, "SO29", ["SO28"], lines.get)
    assert ok and "2 of 2" in why
    assert not matching.rows_tell("PO", fields, "SO28", ["SO29"], lines.get)[0]      # the misread: held
    same = {"A": lines["SO29"], "B": lines["SO29"]}                                    # Boots: one order, ten stores
    assert not matching.rows_tell("PO", fields, "A", ["B"], same.get)[0]
