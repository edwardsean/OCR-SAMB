"""The orders closest to a number that matched none (satellite.closest; the user, 2026-10-16: "why not just show the
most similar ids, so the user can verify that that nomor CPO in satellite is correct"). Pure: no database."""
from common import satellite


def so(sor, cpo, name="X"):
    return {"sor_no": sor, "cpo_no": cpo, "customer_name": name}


SOS = {s["sor_no"]: s for s in [
    so("SOR26120009546", "012474 2609090400600226", "LOTTEMART BINTARO [04006]"),
    so("SOR26120009544", "012474 2609090400600226", "HARI HARI BINTARO TANGSEL"),
    so("SOR26110253529", "2609010400600256"),
    so("SOR26110000001", "4505875905"),
    so("SOR26110000002", "4505875906"),
    so("SOR26110000003", "9999999999"),
]}


def test_a_number_printed_inside_satellites_cpo_is_found_and_marked():
    got = satellite.closest("260909-04006-00226", SOS)
    assert [s["sor_no"] for s, _, _ in got[:2]] == ["SOR26120009544", "SOR26120009546"]
    s, how, (a, b) = got[0]
    assert how == "part" and s["cpo_no"][a:b] == "2609090400600226"
    assert got[2][1].startswith("edits:")                  # a neighbour further away comes after


def test_same_beats_one_character_and_far_numbers_are_left_out():
    got = satellite.closest("4505875905", SOS)
    assert [(s["sor_no"], how) for s, how, _ in got] == [("SOR26110000001", "same"), ("SOR26110000002", "edits:1")]


def test_short_or_empty_numbers_show_nothing():
    assert satellite.closest("1234", SOS) == [] and satellite.closest(None, SOS) == []


def test_an_sor_read_with_one_digit_wrong():
    sos = {"SOR26120009546": so("SOR26120009546", "")}
    got = satellite.closest("SOR26120009846", sos, key="sor_no")
    assert [(s["sor_no"], how) for s, how, _ in got] == [("SOR26120009546", "edits:1")]
