"""Fan-in under concurrency: N pages finished at the same moment ring exactly one bell, and a ticket
from an older run never counts. Uses a throwaway batch; touches nothing else."""
import threading

import pytest

from common import db
from worker.main import tick

N = 40
BID = "test-fanin"
SAVE = """UPDATE staging.page SET status='read' WHERE batch_id=%s AND page_no=%s AND status <> 'read'
          AND EXISTS (SELECT 1 FROM staging.scan_batch WHERE id=%s AND run=%s) RETURNING page_no"""


def reset(run=1):
    with db.connect() as c:
        c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
        c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status, run)
                     VALUES (%s,'t','t',repeat('0',64),current_date,%s,'queued',%s)""", (BID, N, run))
        for n in range(1, N + 1):
            c.execute("INSERT INTO staging.page (batch_id, page_no, image_path, status) VALUES (%s,%s,'x','queued')", (BID, n))


def finish(n, run, rings, barrier):
    barrier.wait()                                   # every thread saves at the same instant
    with db.connect() as c:
        saved = c.execute(SAVE, (BID, n, BID, run)).fetchone()
    if saved and tick(BID, run)["ring"]:
        rings.append(n)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with db.connect() as c:
        c.execute("DELETE FROM staging.page WHERE batch_id=%s", (BID,))
        c.execute("DELETE FROM staging.scan_batch WHERE id=%s", (BID,))


@pytest.mark.parametrize("attempt", range(20))
def test_exactly_one_bell(attempt):
    reset()
    rings, barrier = [], threading.Barrier(N)
    ts = [threading.Thread(target=finish, args=(n, 1, rings, barrier)) for n in range(1, N + 1)]
    [t.start() for t in ts]; [t.join() for t in ts]
    with db.connect() as c:
        b = c.execute("SELECT page_done, status FROM staging.scan_batch WHERE id=%s", (BID,)).fetchone()
    assert len(rings) == 1, rings
    assert b["page_done"] == N and b["status"] == "read"


def test_stale_run_does_not_count():
    reset(run=2)
    with db.connect() as c:
        assert c.execute(SAVE, (BID, 1, BID, 1)).fetchone() is None   # run-1 ticket after re-run → discarded
    assert tick(BID, 1)["ring"] is False
