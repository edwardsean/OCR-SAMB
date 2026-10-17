"""Satellite's orders in memory are a recent window (the user, 2026-10-17: "the server is OOM because of this"). The
server held a year of orders (418K); every page worker and the grouper kept its own copy with its one-character
index, and the kernel killed them. The database keeps every order; load() keeps the last SATELLITE_WINDOW_DAYS days
(counted back from the newest order), every order a bundle names, and orders with no date."""
import os

import pytest

from common import config, db, satellite

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
NEW, OLD, OLD_KNOWN, NO_DATE = "SORWINTEST01", "SORWINTEST02", "SORWINTEST03", "SORWINTEST04"
ALL = [NEW, OLD, OLD_KNOWN, NO_DATE]


@pytest.fixture
def orders(monkeypatch):
    def clean(c):
        c.execute("DELETE FROM staging.bundle WHERE sor_no = ANY(%s)", (ALL,))
        c.execute("DELETE FROM satellite.sor WHERE sor_no = ANY(%s)", (ALL,))
    with db.connect() as c:
        clean(c)
        newest = c.execute("SELECT max(tgl_so) AS d FROM satellite.sor WHERE tgl_so <= current_date").fetchone()["d"]
        assert newest, "the test copy has Satellite's export loaded"
        for sor, back in ((NEW, 0), (OLD, 400), (OLD_KNOWN, 400)):
            c.execute("""INSERT INTO satellite.sor (sor_no, customer_code, cpo_no, tgl_so)
                         VALUES (%s, 'T', %s, %s::date - %s)""", (sor, "CPO" + sor, newest, back))
        c.execute("INSERT INTO satellite.sor (sor_no, customer_code) VALUES (%s, 'T')", (NO_DATE,))
        c.execute("INSERT INTO staging.bundle (sor_no, status) VALUES (%s, 'needs_review')", (OLD_KNOWN,))
    monkeypatch.setattr(config, "SATELLITE_WINDOW_DAYS", 120)
    satellite._cache.update(sos=None)
    yield
    with db.connect() as c:
        clean(c)
    satellite._cache.update(sos=None)
    satellite._index.clear()


def test_memory_keeps_recent_orders_and_the_ones_a_document_names(orders):
    with db.connect() as c:
        sos = satellite.load(c)
    assert NEW in sos and OLD_KNOWN in sos and NO_DATE in sos
    assert OLD not in sos                                      # still in the database, just not in memory


def test_an_old_order_is_still_found_by_its_own_number(orders):
    with db.connect() as c:
        assert OLD in satellite.load(c, sors=[OLD])            # exact lookups read the database


def test_changing_the_window_reloads(orders, monkeypatch):
    with db.connect() as c:
        assert OLD not in satellite.load(c)
        monkeypatch.setattr(config, "SATELLITE_WINDOW_DAYS", 500)
        assert OLD in satellite.load(c)                        # no ten-minute wait for a new window
