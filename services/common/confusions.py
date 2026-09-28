"""Bounded alternatives for a misread value: what these 1-bit scans and their readers confuse, one change at a time.

Only ever used to look a reading up among Satellite's records, a closed list. An alternative never becomes a value by
itself: a record it points to must be the only one, and another attribute of the page must agree with it
(satellite.resolve_fp). Every substitution is reported with the match.

  characters   5/6/8, 0/6/8, 1/7, 3/8, S/5/$, R/5 (page 8's faint .80 was read .86; page 22's S10232 as 510232)
  digits       one dropped, or one doubled
  amounts      the decimal mark read as the other one (754.022,80 / 754.022.80)
"""
from common import verify

GROUPS = ("568", "068", "17", "38", "S5$", "R5")
SAME = {}
for _g in GROUPS:
    for _a in _g:
        SAME.setdefault(_a, set()).update(c for c in _g if c != _a)


def alternatives(s):
    """{(alternative, what changed)} for s: each character swapped for one it's confused with, each digit dropped,
    each digit doubled. s itself is not included."""
    s, out = str(s or ""), {}
    for i, ch in enumerate(s):
        for alt in SAME.get(ch.upper(), ()):
            out.setdefault(s[:i] + alt + s[i + 1:], f"{ch}→{alt} at {i + 1}")
        if ch.isdigit():
            out.setdefault(s[:i] + s[i + 1:], f"{ch} at {i + 1} dropped")
            out.setdefault(s[:i] + ch + s[i:], f"{ch} at {i + 1} doubled")
    out.pop(s, None)
    return out


def amounts(printed):
    """{amount: what changed} a printed amount could be: as read (''), one character confused, or the decimal mark
    read as the other one. Amounts are rupiah conventions (verify.amount)."""
    printed = str(printed or "").strip()
    out = {}
    a = verify.amount(printed)
    if a is not None:
        out[round(a, 2)] = ""
    last = max(printed.rfind("."), printed.rfind(","))
    variants = dict(alternatives(printed))
    if last >= 0:
        variants.setdefault(printed[:last] + {".": ",", ",": "."}[printed[last]] + printed[last + 1:],
                            "the decimal mark read as the other one")
    for alt, why in variants.items():
        x = verify.amount(alt)
        if x is not None:
            out.setdefault(round(x, 2), why)
    return out
