"""Stored data a test reads that the repository doesn't carry: the sample scans' pages and orders, read pages, upload
batches. A fresh clone starts with an empty database, so these tests skip there (with the reason) instead of failing."""
import pytest

from common import db


def _one(sql, args=()):
    try:
        with db.connect() as c:
            return c.execute(sql, args).fetchone()
    except Exception:                         # no such table (an empty v1 database), or no database at all
        return None


def scan(bid):
    """Skip unless the scan is stored (with its pages)."""
    if not _one("SELECT 1 FROM staging.page WHERE batch_id=%s LIMIT 1", (bid,)):
        pytest.skip(f"needs the stored scan {bid} (real customer documents, not in the repository)")


def order(sor):
    """Skip unless the order (bundle) is stored."""
    if not _one("SELECT 1 FROM staging.bundle WHERE sor_no=%s", (sor,)):
        pytest.skip(f"needs the stored order {sor} (real customer documents, not in the repository)")


def row(sql, args=(), what="stored data"):
    """The first row of a query, or skip when there is none (an empty database)."""
    r = _one(sql, args)
    if not r:
        pytest.skip(f"needs {what}: none in this database yet")
    return r
