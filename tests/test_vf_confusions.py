"""Bounded alternatives (common/confusions.py): only the confusions these scans make, one change at a time."""
from common import confusions


def test_confused_characters_one_at_a_time():
    alts = confusions.alternatives("S10232")
    assert "510232" in alts and "$10232" in alts            # page 22: S10232 read as 510232
    assert "S70232" in alts and "S10832" not in alts         # 1/7, but never 2/8
    assert all(sum(a != b for a, b in zip(alt, "S10232")) <= 1 for alt in alts if len(alt) == 6)


def test_a_digit_dropped_or_doubled():
    alts = confusions.alternatives("5213349")
    assert "521349" in alts and "52133349" in alts
    assert "5213349" not in alts                             # the value itself is not an alternative


def test_amounts_a_faint_total_could_be():
    got = confusions.amounts("754.022,86")                   # page 8's faint total; Satellite has 754,022.80
    assert got[754022.86] == ""
    assert 754022.80 in got and "6→0" in got[754022.80]
    assert 754022.96 not in got                              # 8/9 is not a confusion these scans make
    assert 754022.30 not in got                              # two changes (8→3 and 6→0) are never asked
    assert confusions.amounts("754.022.86")[754022.86] == "the decimal mark read as the other one"


def test_the_list_stays_small():
    assert len(confusions.amounts("10.304.789,28")) < 60     # looked up among ~38K SO totals: few chances to collide
