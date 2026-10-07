"""read-then-map Stage 2b: which customer a page is from, before grouping (common/customer.py). Shadow only.
"Don't know" is fine; the wrong customer never is: pass B would map the page with another customer's knowledge."""
import os

import pytest

from common import customer

SOS = {s["sor_no"]: s for s in [
    # AEON EASTVARA's POs run in sequence, all one customer's
    {"sor_no": "SOR26110264129", "cpo_no": "10101000125418", "customer_parent": "AEON", "customer_code": "1400000488",
     "customer_name": "AEON EASTVARA TANGERANG"},
    {"sor_no": "SOR26110264130", "cpo_no": "10101000125419", "customer_parent": "AEON", "customer_code": "1400000488",
     "customer_name": "AEON EASTVARA TANGERANG"},
    {"sor_no": "SOR26110264131", "cpo_no": "10101000999999", "customer_parent": "AEON", "customer_code": "1400000400",
     "customer_name": "PT. AEON INDONESIA"},
    # Hari Hari's PO 5190721 has another customer's PO one character away
    {"sor_no": "SOR26110245292", "cpo_no": "5190721", "customer_parent": "HARI", "customer_code": "1400001602",
     "customer_name": "HARI HARI BINTARO TANGSEL"},
    {"sor_no": "SOR26110245293", "cpo_no": "5190722", "customer_parent": "OTHER", "customer_code": "1400001603",
     "customer_name": "TOKO LAIN"},
    # a different company whose name is 0.86 like Hero's printed one
    {"sor_no": "SOR26110245300", "cpo_no": "77000001", "customer_parent": "SURI", "customer_code": "1400002000",
     "customer_name": "PT. SURI RETAIL NUSANTARA"},
]}
HERO = {"HERO": {"names": {"DFIRETAILNUSANTARA": {1, 2}}, "vendor": {"S10232": {1, 2}}}}


def page(**values):
    return {k: {"value": v} for k, v in values.items()}


def test_a_po_number_whose_neighbours_are_all_one_customers_names_it():
    got = customer.identify(page(po_number="10101000125418"), None, SOS, {})
    assert got["chain"] == "AEON" and got["signals"][0][1] == "strong"


def test_a_key_with_another_customers_neighbour_is_weak_and_one_weak_signal_names_nobody():
    got = customer.identify(page(po_number="5190721"), None, SOS, {})
    assert got["chain"] is None and got["signals"] == [("HARI", "weak", got["signals"][0][2])]


def test_two_weak_signals_for_one_customer_name_it():
    known = {"HARI": {"names": {"SINARSAHABATINTIMAKMUR": {7}}, "vendor": {}}}   # printed on one bundle: weak
    got = customer.identify(page(po_number="5190721", customer_name="PT SINARSARAHABAT INTIMAKMUR"), None, SOS, known)
    assert got["chain"] == "HARI"


def test_signals_for_two_customers_give_dont_know():
    got = customer.identify(page(po_number="10101000125418", customer_name="HARI HARI BINTARO TANGSEL"), None, SOS, {})
    assert got["chain"] is None and "different customers" in got["why"]


def test_the_qr_code_is_the_sor_whatever_was_read():
    got = customer.identify(page(sor="SOR26110245292"), "SOR26110264129", SOS, {})     # the SOR read is misread
    assert got["chain"] == "AEON"


def test_satellites_store_name_and_a_learned_name_are_signals():
    assert customer.identify(page(customer_name="PT AEON INDONESIA"), None, SOS, {})["chain"] == "AEON"
    assert customer.identify(page(customer_name="PT DFI RETAIL NUSANTARA, TBK"), None, SOS, HERO)["chain"] == "HERO"


def test_a_name_about_as_close_to_another_customers_is_not_enough():
    # DFI vs SURI RETAIL NUSANTARA: a misread that fits both names nobody
    got = customer.identify(page(customer_name="PT DURI RETAIL NUSANTARA"), None, SOS, HERO)
    assert got["chain"] is None


def test_a_learned_vendor_code_is_weak():
    got = customer.identify(page(vendor_code="S10232"), None, SOS, HERO)
    assert got["chain"] is None and got["signals"][0][:2] == ("HERO", "weak")
    assert customer.identify(page(vendor_code="S10232", customer_name="PT DFI RETAIL NUSANTARA"), None, SOS,
                             HERO)["chain"] == "HERO"


def test_no_one_digit_misread_of_a_key_names_the_wrong_customer():
    for fa0, truth in ((page(po_number="10101000125418", customer_name="AEON EASTVARA TANGERANG"), "AEON"),
                       (page(po_number="5190721", customer_name="HARI HARI BINTARO TANGSEL"), "HARI")):
        for _, fa in customer.bends(fa0):
            assert customer.identify(fa, None, SOS, {})["chain"] in (None, truth), fa


def test_names_compare_without_legal_forms_and_spaces():
    assert customer.norm("PT. DFI RETAIL NUSANTARA, Tbk.") == customer.norm("DFI RETAIL NUSANTARA TBK") \
        == "DFIRETAILNUSANTARA"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
@pytest.mark.parametrize("bid,pages", [("b-4bab9b736d", range(1, 32)), ("b-c80bbbde4d", None)])
def test_no_page_of_the_grouped_batches_gets_the_wrong_customer(bid, pages):
    tally = customer.shadow(bid, pages, show=lambda *a: None)
    if not tally:
        pytest.skip(f"{bid} not in this database")
    assert tally["WRONG"] == 0 and tally["right"] > 0
