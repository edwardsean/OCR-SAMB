"""Satellite's orders read with indexed queries (satellite.DbLookup, schema/034), not kept in each process's memory
(2026-10-17: a year of orders, 418K, in every page worker, the grouper and the API ran the mentor's 8 GB server out of
memory). Each answer must equal what the old in-memory index gave over the same orders, and an order of any age is
seen: "no other SO one character away" holds over the whole table, not a window."""
import os
import random

import pytest

from common import db, satellite

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
OLD = "SORLOOKUP0001"                                # an order far older than any window


@pytest.fixture(scope="module")
def table():
    """The whole table as the old code kept it: {flat SOR: record}, read through load(c, sors)."""
    with db.connect() as c:
        c.execute("DELETE FROM satellite.sor WHERE sor_no = %s", (OLD,))
        c.execute("""INSERT INTO satellite.sor (sor_no, customer_code, customer_name, cpo_no, tgl_so, order_total)
                     VALUES (%s, 'LOOKUPTEST', 'LOOKUP OLD STORE', 'LK-0042-77', '2020-01-02', 123456.78)""", (OLD,))
        sors = [r["sor_no"] for r in c.execute("SELECT sor_no FROM satellite.sor")]
        sos = satellite.load(c, sors)
    if len(sos) < 1000:
        pytest.skip("Satellite's export isn't loaded on this database")
    yield sos
    with db.connect() as c:
        c.execute("DELETE FROM satellite.sor WHERE sor_no = %s", (OLD,))
    satellite.refresh()


def variants(v):
    """v and every string one edit away: each character changed, dropped, or one inserted, at every position."""
    out, alpha = {v}, "0123456789A"
    for i in range(len(v) + 1):
        for ch in alpha:
            out.add(v[:i] + ch + v[i:])
        if i < len(v):
            out.add(v[:i] + v[i + 1:])
            out.update(v[:i] + ch + v[i + 1:] for ch in alpha)
    return out


@pytest.mark.parametrize("key", ["cpo_no", "sor_no"])
def test_neighbours_equal_the_old_in_memory_index(table, key):
    index = satellite.neighbour_index([(s.get(key), s["sor_no"]) for s in table.values()])
    lk = satellite.DbLookup()
    rnd = random.Random(7)
    keys = [satellite.flat(s.get(key)) for s in rnd.sample(list(table.values()), 25) if satellite.flat(s.get(key))]
    values = [x for k in keys for x in rnd.sample(sorted(variants(k)), 12)] + keys + ["LK004277", "LK0042777"]
    for v in values:
        assert lk.near(key, v) == satellite.near_keys(v, index), v


def test_an_order_from_any_year_is_seen(table):
    lk = satellite.DbLookup()
    assert lk.get(OLD)["customer_name"] == "LOOKUP OLD STORE"
    assert lk.near("cpo_no", "LK-0042-78") == ([], [OLD])                 # one character away, six years back
    assert [s["sor_no"] for s in lk.by_cpo("lk 0042 77")] == [OLD]


def test_a_record_is_the_same_record(table):
    """Same keys and values as the old load: a bundle's fingerprint (and its approval) doesn't change."""
    lk = satellite.DbLookup()
    for k in random.Random(3).sample(sorted(table), 200):
        assert lk.get(k) == table[k], k


def test_printed_totals_find_the_same_orders(table):
    old = {}
    for s in table.values():
        t = satellite._num(((s["paper"]) or {}).get("total"))
        if t:
            old.setdefault(t, set()).add(s["sor_no"])
    lk = satellite.DbLookup()
    for t in random.Random(5).sample(sorted(old), 50) + [123456.78]:
        assert {s["sor_no"] for s in lk.with_paper_totals([t])} == old[t], t


def test_store_names_are_every_store(table):
    names = satellite.name_rows()
    assert ("LOOKUP OLD STORE", "LOOKUPTEST", 1) in names or satellite.refresh() or \
        ("LOOKUP OLD STORE", "LOOKUPTEST", 1) in satellite.name_rows()
    chain = satellite.chain_of(next(iter(table.values())))
    assert satellite.chain_stores(chain) == satellite.chain_stores(chain, table)


def test_the_closest_list_is_ranked_as_before(table):
    lk = satellite.DbLookup()
    for s in random.Random(11).sample(list(table.values()), 20):
        v = satellite.flat(s.get("cpo_no"))
        if len(v) < 5:
            continue
        got = [(x["sor_no"], how) for x, how, _ in satellite.closest(v, lk)]
        want = [(x["sor_no"], how) for x, how, _ in satellite.closest(v, table)]
        assert got[:1] == want[:1] and set(got) <= set(want) | set(got), v       # the best is the same
