"""Upload batches (2026-10-05; the user: "for an upload … input the uploader's name, date, and a generated batch number,
so that in view we can see the separated processes per batch"). common/uploads.py, the API, and the screens'
batch filter."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


@pytest.fixture
def made():
    """Upload batches and throwaway scans a test makes, removed after."""
    from common import db
    out = {"uploads": [], "scans": []}
    yield out
    with db.connect() as c:
        c.execute("DELETE FROM staging.scan_batch WHERE id = ANY(%s)", (out["scans"],))
        c.execute("DELETE FROM staging.upload WHERE id = ANY(%s)", (out["uploads"],))


def test_a_batch_gets_the_next_number_of_its_day(made):
    from common import uploads
    a = uploads.create("Edward", None)
    b = uploads.create("  Edward  ", str(date.today() - timedelta(days=2)), "kiriman Senin")
    made["uploads"] += [a["id"], b["id"]]
    prefix = a["code"][:-2]
    assert a["code"].startswith("BATCH-") and len(a["code"]) == len("BATCH-20261005-01")
    assert b["code"] == f"{prefix}{int(a['code'][-2:]) + 1:02d}" and b["uploaded_by"] == "Edward"
    assert b["doc_date"] == date.today() - timedelta(days=2)


def test_a_batch_needs_a_name_and_a_date_not_in_the_future():
    from common import uploads
    with pytest.raises(ValueError):
        uploads.create("   ")
    with pytest.raises(ValueError):
        uploads.create("Edward", str(date.today() + timedelta(days=3)))


def test_the_api_starts_a_batch_and_refuses_a_missing_name(made):
    from fastapi.testclient import TestClient
    from api.app import app
    with TestClient(app) as tc:
        r = tc.post("/api/v1/uploads", json={"by": "", "date": None})
        assert r.status_code == 400 and r.json()["error"] == "Tulis nama Anda dulu."
        r = tc.post("/api/v1/uploads", json={"by": "Edward"})
        assert r.status_code == 201 and r.json()["code"].startswith("BATCH-")
        made["uploads"].append(r.json()["id"])
        got = tc.get(f"/api/v1/uploads/{r.json()['id']}").json()
        assert got["upload"]["uploaded_by"] == "Edward" and got["files"] == []
        assert tc.get("/api/v1/uploads/999999").status_code == 404


def test_scans_from_before_batches_get_one_per_upload_moment(made):
    """Scans that arrived within ten minutes of each other were one upload; the uploader wasn't recorded."""
    from common import db, uploads
    ids = ["b-test-up-1", "b-test-up-2", "b-test-up-3"]
    with db.connect() as c:
        for i, (sid, minutes) in enumerate(zip(ids, (0, 2, 40))):
            c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total,
                                                          status, received_at)
                         VALUES (%s, %s, 't', repeat(%s, 64), current_date, 1, 'read',
                                 now() - interval '2 hours' + %s * interval '1 minute')""",
                      (sid, f"{sid}.pdf", "def"[i], minutes))
    made["scans"] += ids
    got = uploads.backfill()
    with db.connect() as c:
        rows = {r["id"]: r["upload_id"] for r in c.execute(
            "SELECT id, upload_id FROM staging.scan_batch WHERE id = ANY(%s)", (ids,))}
        made["uploads"] += sorted(set(rows.values()))
        who = {r["uploaded_by"] for r in c.execute("SELECT uploaded_by FROM staging.upload WHERE id = ANY(%s)",
                                                    (list(rows.values()),))}
    assert rows["b-test-up-1"] == rows["b-test-up-2"] != rows["b-test-up-3"]
    assert who == {uploads.UNKNOWN} and [n for _, n in got][-2:] == [2, 1]
