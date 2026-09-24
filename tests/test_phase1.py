"""Phase 1 acceptance: upload → MinIO → n8n → 288 page rows → 288 tickets; same file twice rejected."""
import hashlib
import time

import httpx

UI = "http://ui:8000"
SAMPLE = "/data/sample.pdf"
NAME = "7000356304 - 7000356499.pdf"


def _batch_id():
    return "b-" + hashlib.sha256(open(SAMPLE, "rb").read()).hexdigest()[:10]


def _upload():
    with open(SAMPLE, "rb") as f:
        return httpx.post(f"{UI}/upload", files={"file": (NAME, f, "application/pdf")},
                          follow_redirects=False, timeout=60)


def test_upload_split_and_queue():
    bid = _batch_id()
    if httpx.get(f"{UI}/api/batches/{bid}").status_code == 404:
        r = _upload()
        assert r.status_code == 303, r.text[:300]
        assert r.headers["location"] == f"/batches/{bid}"

    deadline = time.time() + 420
    while time.time() < deadline:
        r = httpx.get(f"{UI}/api/batches/{bid}")
        if r.status_code == 200 and r.json()["batch"]["status"] not in ("splitting", "split"):
            break
        time.sleep(3)
    j = r.json()
    # Later phases move the batch past "queued" (reading → read …); phase 1 only needs it to have got there.
    assert j["batch"]["status"] in ("queued", "reading", "read", "grouping", "done"), j["batch"]
    assert j["batch"]["page_total"] == 288
    assert j["page_rows"] == 288
    assert "rendered" not in j["pages_by_status"], j["pages_by_status"]    # every page was given a ticket


def test_same_file_twice_is_rejected():
    r = _upload()
    assert r.status_code == 409
    assert _batch_id() in r.text
