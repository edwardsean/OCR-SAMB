"""Phase 6 rules, on made-up pages: resolved keys only, the FP as the hub, page order only for continuations, and
Satellite's record as a witness (the cases are the sample's real ones: pages 3, 4, 6, 8, 23)."""
from common import keys as keymod
from common import satellite
from grouper import group

SOS = {satellite.flat(s): {"sor_no": s, "customer_code": code, "customer_name": name, "cpo_no": cpo}
       for s, code, name, cpo in [
           ("SOR26110245292", "1400001602", "HARI HARI BINTARO TANGSEL", "5190721"),
           ("SOR26110255837", "1400000454", "BOOTS HARAPAN INDAH AVENUE", "4505832724"),
           ("SOR26110256585", "1400001602", "HARI HARI BINTARO TANGSEL", "5213310"),
           ("SOR26110256810", "1400001602", "HARI HARI BINTARO TANGSEL", "5213349"),
           ("SOR26110257257", "1400001889", "HERO DC PBF [320] CIBITUNG BEKASI", "58415552"),
           ("SOR26110257258", "1400001889", "HERO DC PBF [320] CIBITUNG BEKASI", "58415556")]}


def page(n, t, status="decided", **keys):
    """keys: name=(value, confirmed_by or None)."""
    return {"page_no": n, "doc_type": t, "type_status": status,
            "keys": {k: {"value": v, "confirmed_by": by} for k, (v, by) in keys.items()}}


def placed(out):
    return {n: s for s, b in out["bundles"].items() for d in b["documents"] for n in d["pages"]}


def held(out):
    return {d["pages"][0]: d["hold"] for d in out["documents"] if d["hold"]}


# ------------------------------------------------------------------------------------------------- grouping

def test_the_fp_is_the_hub_and_documents_join_by_resolved_keys():
    out = group.plan([
        page(1, "FP", sor=("SOR26110245292", "qr"), po_no=("5190721", "ocr_text")),
        page(2, "TTG", sor=("SOR26110245292", "ocr_text"), po_no=("5201510", None)),     # by its No Ref
        page(3, "FP", sor=("SOR26110255837", "qr"), po_no=("4505832724", "satellite")),
        page(4, "PO", po_no=("4505832724", "satellite")),                                 # by PO number = CPO
        page(5, "TTG", po_no=("4505832724", "ocr_text"))], SOS)
    assert placed(out) == {1: "SOR26110245292", 2: "SOR26110245292", 3: "SOR26110255837", 4: "SOR26110255837",
                           5: "SOR26110255837"}
    assert not any(b["hold"] for b in out["bundles"].values())
    ttg = next(d for d in out["documents"] if d["pages"] == [2])
    assert ttg["linked_by"] == "sor" and "prints the SOR" in ttg["evidence"][0]


def test_an_unresolved_key_never_links_and_order_never_does():
    out = group.plan([
        page(10, "FP", sor=("SOR26110245292", "qr")),
        page(11, "TTG", po_no=("5190721", None))], SOS)           # only read: waits, although it would match p10
    assert placed(out) == {10: "SOR26110245292"} and held(out) == {11: "no_resolved_key"}
    assert next(d for d in out["documents"] if d["pages"] == [11])["suggest"] == "SOR26110245292"   # a hint only


def test_a_continuation_belongs_to_the_page_before_it():
    out = group.plan([
        page(13, "FP", sor=("SOR26110245292", "qr"), po_no=("5190721", "ocr_text")),
        page(14, "PO", po_no=("5190721", "ocr_text")),
        page(15, "CONTINUATION", status="labelled"),
        page(17, "CONTINUATION")], SOS)                           # the page before it isn't part of a document
    assert placed(out) == {13: "SOR26110245292", 14: "SOR26110245292", 15: "SOR26110245292"}
    assert held(out) == {17: "continuation_without_start"}


def test_what_is_held_and_why():
    out = group.plan([
        page(1, "FP", sor=("SOR26110245292", "qr")),
        page(2, "FP", sor=("SOR26110245292", "ocr_text")),        # two FPs, one SOR
        page(3, "TTG", sor=("SOR26110255837", "ocr_text"), po_no=("5213310", "ocr_text")),   # keys disagree
        page(4, "PO", po_no=("99999999", "ocr_text")),            # resolved, but no such SO
        page(5, "TTG", status="unsure"),
        page(6, "FPJ"),
        {"page_no": 7, "doc_type": None, "type_status": None, "keys": {}}], SOS)
    assert held(out) == {1: "two_fps_one_sor", 2: "two_fps_one_sor", 3: "keys_disagree", 4: "so_unknown",
                         5: "type_unknown", 6: "needs_sap_billing", 7: "not_read"}


def test_a_po_number_that_is_the_cpo_of_two_sos_is_held():
    sos = {**SOS, "SOR26110299999": {"sor_no": "SOR26110299999", "customer_code": "1", "customer_name": "X",
                                     "cpo_no": "5190721"}}
    out = group.plan([page(2, "PO", po_no=("5190721", "ocr_text"))], sos)
    assert held(out) == {2: "po_matches_several_sos"}


def test_a_bundle_without_its_fp_is_held_and_the_fp_gets_a_careful_suggestion():
    """Pages 6-9 today: both FPs' SORs unresolved (no QR code read); their TTGs link by their own keys."""
    out = group.plan([
        page(1, "FP", sor=("SOR26110245292", "qr"), po_no=("5190721", "ocr_text")),
        page(6, "FP", sor=("SOR26110256585", None), po_no=("6213310", None)),
        page(7, "TTG", sor=("SOR26110256585", "satellite"), po_no=("5213310", "ocr_text")),
        page(8, "FP", sor=("SOR26110245292", None), po_no=("R213379", None)),    # misread as page 1's real SOR
        page(9, "TTG", sor=("SOR26110256810", "ocr_text"), po_no=("5213349", "ocr_text"))], SOS)
    assert out["bundles"]["SOR26110256585"]["hold"] == "fp_missing"
    assert out["bundles"]["SOR26110256810"]["hold"] == "fp_missing"
    fp = {d["pages"][0]: d for d in out["documents"] if d["type"] == "FP"}
    assert fp[6]["suggest"] == "SOR26110256585"
    assert fp[8]["suggest"] == "SOR26110256810"           # never page 1's SOR, which already has its FP


def test_an_sor_printed_without_its_letters_is_still_an_sor():
    out = group.plan([page(1, "FP", sor=("SOR26110256585", "qr")),
                      page(2, "TTG", sor=("26110256585", "ocr_text"))], SOS)   # Puri Indah's DO#
    assert placed(out) == {1: "SOR26110256585", 2: "SOR26110256585"}
    k = keymod.derive("TTG", {"no_ref": {"value": "26110256585"}}, "DO# : 26110256585", None)
    assert k["sor"]["value"] == "SOR26110256585" and k["sor"]["confirmed_by"] == "ocr_text"


# ------------------------------------------------------------------------------------------------- satellite

def v(by=None, verdict="ok"):
    return {"verdict": verdict, **({"by": by} if by else {})}


def test_satellite_corrects_an_fp_whose_sor_is_known():
    """Page 3: SOR from the QR code; the AI read Nomor CPO 4505632724 (printed 4505832724)."""
    fields = {"sor": {"value": "SOR26110255837"}, "nomor_cpo": {"value": "4505632724"},
              "customer_code": {"value": "[1400000454]"}}
    f, h = satellite.settle("FP", fields, {"sor": v("qr"), "nomor_cpo": v(verdict="check"),
                                          "customer_code": v(verdict="check")}, SOS)
    assert f["nomor_cpo"] == {"value": "4505832724", "ai_value": "4505632724"} and h["nomor_cpo"]["by"] == "satellite"
    assert h["customer_code"]["by"] == "satellite" and f["customer_code"]["value"] == "[1400000454]"   # same code


def test_an_fp_sor_comes_from_satellite_only_as_a_pair():
    both = {"sor": {"value": "SOR26110256585"}, "nomor_cpo": {"value": "5213310"}}
    _, h = satellite.settle("FP", both, {"sor": v(verdict="check"), "nomor_cpo": v(verdict="check")}, SOS)
    assert h["sor"]["by"] == "satellite" and h["nomor_cpo"]["by"] == "satellite"
    page8 = {"sor": {"value": "SOR26110245292"}, "nomor_cpo": {"value": "R213379"}}   # a real SOR, the wrong one
    f, h = satellite.settle("FP", page8, {"sor": v(verdict="check"), "nomor_cpo": v(verdict="check")}, SOS)
    assert h["sor"]["verdict"] == "check" and f["nomor_cpo"]["value"] == "R213379"   # nothing confirmed, nothing corrected
    page6 = {"sor": {"value": "SOR26110256585"}, "nomor_cpo": {"value": "6213310"}}   # the pen stroke through the 5
    _, h = satellite.settle("FP", page6, {"sor": v(verdict="check"), "nomor_cpo": v(verdict="check")}, SOS)
    assert h["sor"]["verdict"] == "check"                                          # a person confirms page 6's SOR


def test_print_and_satellite_disagreeing_goes_to_a_person():
    fields = {"sor": {"value": "SOR26110255837"}, "nomor_cpo": {"value": "4505832725"}}
    _, h = satellite.settle("FP", fields, {"sor": v("qr"), "nomor_cpo": v("text")}, SOS)
    assert h["nomor_cpo"]["verdict"] == "check" and "Satellite" in h["nomor_cpo"]["why"]


def test_a_customer_po_number_needs_an_exact_match_and_no_near_neighbour():
    _, h = satellite.settle("PO", {"purchase_order_no": {"value": "4505832724"}},
                            {"purchase_order_no": v(verdict="check")}, SOS)                    # page 4: Boots
    assert h["purchase_order_no"]["by"] == "satellite"
    _, h = satellite.settle("TTG", {"purchase_order_no": {"value": "58415552"}},
                            {"purchase_order_no": v(verdict="check", by=None)}, SOS)           # Hero: 58415556 is 1 away
    assert h["purchase_order_no"]["verdict"] == "check" and "one character away" in h["purchase_order_no"]["why"]


def test_a_person_has_the_last_word_and_satellite_then_settles_the_fp():
    fields = {"sor": {"value": "SOR26110256585"}, "nomor_cpo": {"value": "6213310"},
              "customer_name": {"value": "HARI HARI BINTARO TANOSOL"}}
    confirmed = {"sor": {"value": "SOR26110256585", "confirmed_by": "Edward"}}
    f, h = satellite.settle("FP", fields, {"sor": v(verdict="check"), "nomor_cpo": v(verdict="check"),
                                          "customer_name": v(verdict="check")}, SOS, confirmed)
    assert h["sor"] == {"verdict": "ok", "by": "person", "why": "confirmed by Edward"}
    assert f["nomor_cpo"]["value"] == "5213310" and h["nomor_cpo"]["by"] == "satellite"
    assert h["customer_name"]["verdict"] == "check" and "TANGSEL" in h["customer_name"]["why"]   # a hint, not a change


def test_keys_follow_the_final_verdicts():
    fields = {"sor": {"value": "SOR26110255837"}, "nomor_cpo": {"value": "4505832724", "ai_value": "4505632724"}}
    k = keymod.derive("FP", fields, "", "SOR26110255837", {"sor": v("qr"), "nomor_cpo": v("satellite")})
    assert k["sor"]["confirmed_by"] == "qr" and k["po_no"] == {"value": "4505832724", "confirmed_by": "satellite",
                                                              "problems": []}
    k = keymod.derive("TTG", {"purchase_order_no": {"value": "58423526"}}, "PO 58423526", None,
                      {"purchase_order_no": v(verdict="check")})
    assert k["po_no"]["confirmed_by"] is None       # printed, but its verdict says check: the verdict wins


def test_satellite_fills_an_fp_value_the_ai_did_not_read():
    """qwen3-vl-plus left page 4's customer code unread (None in the projection); filling it from the SO record
    crashed the page (2026-09-28)."""
    fields = {"sor": {"value": "SOR26110255837"}, "nomor_cpo": None, "customer_code": None}
    f, h = satellite.settle("FP", fields, {"sor": v("qr"), "nomor_cpo": v(verdict="empty"),
                                          "customer_code": v(verdict="empty")}, SOS)
    assert f["customer_code"]["value"] == "1400000454" and h["customer_code"]["by"] == "satellite"
    assert f["nomor_cpo"]["value"] == "4505832724" and "not read" in h["nomor_cpo"]["why"]


def test_the_qr_code_is_the_fps_sor():
    """Page 13 of 7000363700-03: the AI read SOR26110254669 twice; its QR code decodes SOR26110264669 (2026-09-28)."""
    fields = {"sor": {"value": "SOR26110245297"}, "nomor_cpo": {"value": "5190721"}}
    f, h = satellite.settle("FP", fields, {"sor": v(verdict="check"), "nomor_cpo": v(verdict="check")}, SOS,
                            qr="SOR26110245292")
    assert f["sor"] == {"value": "SOR26110245292", "ai_value": "SOR26110245297"} and h["sor"]["by"] == "qr"
    assert h["nomor_cpo"]["by"] == "satellite"                       # the SO record then settles the rest
    k = keymod.derive("FP", f, "", "SOR26110245292", h)
    assert k["sor"]["value"] == "SOR26110245292" and k["sor"]["confirmed_by"] == "qr"
    person = {"sor": {"value": "SOR26110256585", "confirmed_by": "Edward"}}
    assert satellite.settle("FP", fields, {"sor": v(verdict="check")}, SOS, person, qr="SOR26110245292")[1]["sor"]["by"] == "person"
    assert satellite.settle("TTG", {"no_ref": {"value": "X"}}, {"no_ref": v(verdict="check")}, SOS,
                            qr="SOR26110245292")[1]["no_ref"]["verdict"] == "check"          # only an FP's QR is its SOR
