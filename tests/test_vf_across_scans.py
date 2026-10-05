"""An order whose documents sit in several scans (the user, 2026-10-05: each of a customer's documents is its own file,
and in real life a receipt comes back days after its invoice). Documents link scan by scan; the order is decided over
every scan (grouper/members.py): its FP may be in any scan, and its checks, Review and publishing see all its pages.
A Faktur Pajak links by the SOR it prints when Satellite's billing number for that SO is the one it prints too."""
import os

import pytest

from common import satellite
from grouper import group, members
from publisher import publish

SOS = {"SOR26110200001": {"sor_no": "SOR26110200001", "customer_code": "1", "customer_name": "TOKO CONTOH DC",
                          "cpo_no": "PO00000001", "billing_no": "7000300001"},
       "SOR26110200002": {"sor_no": "SOR26110200002", "customer_code": "2", "customer_name": "TOKO KEDUA",
                          "cpo_no": "5200001", "billing_no": None}}


def page(n, t, **keys):
    return {"page_no": n, "doc_type": t, "type_status": "decided",
            "keys": {k: {"value": v, "confirmed_by": by} for k, (v, by) in keys.items()}}


def held(out):
    return {d["pages"][0]: d["hold"] for d in out["documents"] if d["hold"]}


# ---------------------------------------------------------------------------------------------- the Faktur Pajak

def test_a_faktur_pajak_links_by_the_sor_it_prints_when_satellites_billing_number_agrees():
    out = group.plan([page(1, "FPJ", sor=("SOR26110200001", "ocr_text"),
                           billing_no=("7000300001/SOR26110200001", "ocr_text"))], SOS)
    (d,) = out["documents"]
    assert (d["sor"], d["linked_by"], d["hold"]) == ("SOR26110200001", "billing_no", None)
    assert "7000300001 is Satellite's billing number" in d["evidence"][0]
    # both read into one field, as printed: still both
    out = group.plan([page(1, "FPJ", billing_no=("7000300001/SOR26110200001", "ocr_text"))], SOS)
    assert out["documents"][0]["sor"] == "SOR26110200001"
    assert group._fpj_parts("7000300004/SOF26110000001") == ("SOF26110000001", "7000300004")   # free goods


def test_a_faktur_pajak_is_held_unless_both_agree():
    cases = {
        "billing_disagrees": page(1, "FPJ", billing_no=("7000300002/SOR26110200001", "ocr_text")),   # one digit off
        "fpj_needs_both": page(2, "FPJ", sor=("SOR26110200001", "ocr_text")),                         # no billing
        "needs_sap_billing": page(3, "FPJ", billing_no=("7000300003/SOR26110200002", "ocr_text")),    # not in SAP yet
        "so_unknown": page(4, "FPJ", billing_no=("7000000000/SOR26110299999", "ocr_text")),
        "no_resolved_key": page(5, "FPJ", billing_no=("7000300001/SOR26110200001", None)),            # only read
        "keys_disagree": page(6, "FPJ", sor=("SOR26110200002", "ocr_text"),
                              billing_no=("7000300001/SOR26110200001", "ocr_text")),
    }
    out = group.plan(list(cases.values()), SOS)
    assert held(out) == {p["page_no"]: why for why, p in cases.items()}
    assert not out["bundles"]


def test_a_faktur_pajak_never_counts_as_the_orders_fp():
    out = group.plan([page(1, "FPJ", billing_no=("7000300001/SOR26110200001", "ocr_text"))], SOS)
    assert out["bundles"]["SOR26110200001"]["hold"] == "fp_missing"
    assert members.hold_of(["FPJ", "PO", "TTG"]) == "fp_missing"
    assert members.hold_of(["FP", "FPJ"]) is None and members.hold_of(["FP", "FP"]) == "two_fps_one_sor"


# ---------------------------------------------------------------------------------------------- one order, many scans

def _rows(*docs):
    return [{"batch_id": b, "t": t, "page_from": f, "page_to": to, "file_name": f"{b}.pdf", "received_at": at}
            for b, t, f, to, at in docs]


def test_an_order_in_one_scan_keeps_its_page_numbers():
    to_key, where = members.keys(_rows(("b-1", "FP", 4, 4, 1), ("b-1", "PO", 6, 9, 1), ("b-1", "TTG", 5, 5, 1)))
    assert sorted(to_key.values()) == [4, 5, 6, 7, 8, 9]
    assert where[7] == {"batch": "b-1", "page": 7, "scan": "b-1.pdf"} and members.name(7, where) == "7"


def test_an_order_across_scans_numbers_the_fps_scan_first():
    rows = _rows(("b-po", "PO", 1, 1, "2026-10-05 10:00"), ("b-fp", "FP", 1, 2, "2026-10-05 11:00"),
                 ("b-ttg", "TTG", 1, 1, "2026-10-05 10:30"))
    assert members.order_batches(rows) == ["b-fp", "b-po", "b-ttg"]          # the FP's scan, then by arrival
    to_key, where = members.keys(rows)
    assert to_key == {("b-fp", 1): 1, ("b-fp", 2): 2, ("b-po", 1): 10001, ("b-ttg", 1): 20001}
    assert where[10001]["batch"] == "b-po" and where[10001]["page"] == 1
    assert members.name(10001, where) == "1 (b-po.pdf)"


def test_publishing_stores_each_documents_own_scan_and_pages():
    pages = {1: {"doc_type": "FP", "fields": {"sor": {"value": "SOR26110200001"}}, "checks": {}},
             10001: {"doc_type": "PO", "fields": {"purchase_order_no": {"value": "PO00000001"}}, "checks": {}}}
    docs = [{"type": "PO", "pages": [10001], "linked_by": "po_no"}, {"type": "FP", "pages": [1], "linked_by": "sor"}]
    where = {1: {"batch": "b-fp", "page": 1}, 10001: {"batch": "b-po", "page": 1}}
    p = publish.plan("SOR26110200001", docs, pages, where)
    assert p["pdf"] == [1, 10001]                                           # FP first, then the PO
    assert [(d["type"], d["source_batch"], d["source_pages"], d["page_ref"]) for d in p["documents"]] == \
        [("FP", "b-fp", [1], [1]), ("PO", "b-po", [1], [2])]


# ---------------------------------------------------------------------------------------------- on the database

@pytest.fixture
def three_scans(monkeypatch):
    """A real Satellite SO (a PO number no other SO has, a billing number) and three throwaway one-page scans: its
    PO, its FP, its Faktur Pajak. Removed after."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs the database")
    from common import db
    with db.connect() as c:
        so = c.execute("""SELECT s.sor_no, s.cpo_no, s.billing_no FROM satellite.sor s
                           WHERE s.sor_no LIKE 'SOR%%' AND s.cpo_no IS NOT NULL AND s.billing_no IS NOT NULL
                             AND NOT EXISTS (SELECT 1 FROM satellite.sor o WHERE o.cpo_no = s.cpo_no AND o.sor_no <> s.sor_no)
                             AND NOT EXISTS (SELECT 1 FROM staging.bundle b WHERE b.sor_no = s.sor_no)
                           ORDER BY s.sor_no LIMIT 1""").fetchone()
    if not so:
        pytest.skip("no suitable SO in this database")
    woken = []
    monkeypatch.setattr(group, "_wake", lambda batches: woken.extend(sorted(batches)))
    scans = {"PO": "b-test-x-po", "FP": "b-test-x-fp", "FPJ": "b-test-x-fpj"}
    keys = {"PO": {"po_no": {"value": so["cpo_no"], "confirmed_by": "ocr_text"}},
            "FP": {"sor": {"value": so["sor_no"], "confirmed_by": "qr"}},
            "FPJ": {"billing_no": {"value": f"{so['billing_no']}/{so['sor_no']}", "confirmed_by": "ocr_text"}}}

    def add(t, i):
        with db.connect() as c:
            c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total,
                                                          status, run, received_at)
                         VALUES (%s, %s, 't', repeat(%s, 64), current_date, 1, 'read', 1, now() + %s * interval '1 s')""",
                      (scans[t], f"{t}.pdf", "abc"[i], i))
            c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, status, doc_type, type_status, keys,
                                                   fields, fields_all, outcome)
                         VALUES (%s, 1, 'x', 'read', %s, 'decided', %s::jsonb, '{}'::jsonb, '{}'::jsonb, 'clear')""",
                      (scans[t], t, __import__("json").dumps(keys[t])))
    yield so, scans, add, woken
    with db.connect() as c:
        ids = list(scans.values())
        c.execute("""DELETE FROM staging.bundle_document bd USING staging.document d
                     WHERE bd.document_id = d.id AND d.batch_id = ANY(%s)""", (ids,))
        c.execute("DELETE FROM staging.document WHERE batch_id = ANY(%s)", (ids,))
        c.execute("DELETE FROM staging.bundle WHERE sor_no = %s", (so["sor_no"],))
        c.execute("DELETE FROM staging.field_check WHERE batch_id = ANY(%s)", (ids,))
        c.execute("DELETE FROM staging.page WHERE batch_id = ANY(%s)", (ids,))
        c.execute("DELETE FROM staging.scan_batch WHERE id = ANY(%s)", (ids,))


def _bundle(sor):
    from common import db
    with db.connect() as c:
        b = c.execute("SELECT * FROM staging.bundle WHERE sor_no=%s AND status <> 'published'", (sor,)).fetchone()
        docs = c.execute("""SELECT d.batch_id, d.doc_type::text AS t, d.linked_by::text AS linked_by
                              FROM staging.bundle_document bd JOIN staging.document d ON d.id = bd.document_id
                             WHERE bd.bundle_id = %s ORDER BY d.batch_id""", (b["id"],)).fetchall() if b else []
    return b, docs


def test_an_order_comes_together_from_three_scans_in_any_order(three_scans):
    from common import db
    from grouper import crosscheck
    so, scans, add, woken = three_scans
    add("PO", 0)                                          # the PO arrives first: the order waits for its FP
    group.run(scans["PO"], folders=False)
    b, docs = _bundle(so["sor_no"])
    assert b["hold_reason"] == "fp_missing" and [d["t"] for d in docs] == ["PO"]

    add("FP", 1)                                          # its FP in another scan: the order is whole
    group.run(scans["FP"], folders=False)
    b, docs = _bundle(so["sor_no"])
    assert b["hold_reason"] is None and sorted(d["t"] for d in docs) == ["FP", "PO"]
    assert woken == [scans["PO"]]                         # the PO's scan is regrouped (its folder follows)

    group.run(scans["PO"], folders=False)                 # regrouping the PO's scan alone keeps the order whole
    assert _bundle(so["sor_no"])[0]["hold_reason"] is None

    add("FPJ", 2)                                         # its Faktur Pajak, by SOR + billing number
    group.run(scans["FPJ"], folders=False)
    b, docs = _bundle(so["sor_no"])
    assert {(d["t"], d["linked_by"]) for d in docs} == {("FP", "sor"), ("PO", "po_no"), ("FPJ", "billing_no")}

    with db.connect() as c:                               # the checks see the whole order, whichever scan asks
        (x,) = [x for x in crosscheck.inputs(c, scans["PO"]) if x["sor"] == so["sor_no"]]
    assert sorted(x["pages"]) == [1, 10001, 20001]
    assert {k: (w["batch"], w["page"]) for k, w in x["where"].items()} == \
        {1: (scans["FP"], 1), 10001: (scans["PO"], 1), 20001: (scans["FPJ"], 1)}

    with db.connect() as c:                               # and publishing gathers it from every scan
        c.execute("UPDATE staging.bundle SET status='auto_ok' WHERE id=%s", (b["id"],))
        pdocs, ppages, where = publish._inputs(c, scans["PO"], so["sor_no"])
    assert sorted(d["type"] for d in pdocs) == ["FP", "FPJ", "PO"] and sorted(ppages) == [1, 10001, 20001]


def test_berkas_per_sor_and_periksa_order_show_each_order_once_whole(three_scans):
    """The user (2026-10-05): "why in Berkas per SOR do we view it from each document? why not per SOR?" Each order is
    one entry with all its documents, from whichever scan each came in; choosing a scan only narrows."""
    from api import app
    so, scans, add, woken = three_scans
    for i, t in enumerate(("PO", "FP", "FPJ")):
        add(t, i)
        group.run(scans[t], folders=False)
    for chosen in (None, scans["PO"], scans["FPJ"]):
        v = app.bundles_screen(chosen)
        (o,) = [b for b in v["bundles"] if b["sor"] == so["sor_no"]]
        assert sorted((d["type"], d["batch_id"]) for d in o["documents"]) == \
            sorted([("FP", scans["FP"]), ("PO", scans["PO"]), ("FPJ", scans["FPJ"])])
        assert o["many_scans"] and o["batch"] == scans["FP"] and o["hold"] is None   # Review opens from the FP's scan
        (r,) = [x for x in app.review_list(chosen) if x["sor_no"] == so["sor_no"]]
        assert r["scans"] == 3 and r["batch"] == scans["FP"]
    assert so["sor_no"] not in {b["sor"] for b in app.bundles_screen("b-no-such-scan")["bundles"]}


def test_the_screens_narrow_to_one_upload_batch(three_scans):
    """An order whose documents came in two batches shows under either; a batch without it doesn't list it."""
    from api import app
    from common import db, uploads
    so, scans, add, woken = three_scans
    for i, t in enumerate(("PO", "FP")):
        add(t, i)
        group.run(scans[t], folders=False)
    a, b, other = uploads.create("Edward"), uploads.create("Ayu"), uploads.create("Budi")
    try:
        with db.connect() as c:
            c.execute("UPDATE staging.scan_batch SET upload_id=%s WHERE id=%s", (a["id"], scans["FP"]))
            c.execute("UPDATE staging.scan_batch SET upload_id=%s WHERE id=%s", (b["id"], scans["PO"]))
        for u in (a, b):
            (r,) = [x for x in app.review_list(None, u["id"]) if x["sor_no"] == so["sor_no"]]
            assert sorted(x["code"] for x in r["uploads"]) == sorted([a["code"], b["code"]])
            assert so["sor_no"] in {o["sor"] for o in app.bundles_screen(None, u["id"])["bundles"]}
        assert so["sor_no"] not in {x["sor_no"] for x in app.review_list(None, other["id"])}
        assert app._upload_of(scans["PO"])["uploaded_by"] == "Ayu"
    finally:
        with db.connect() as c:
            c.execute("UPDATE staging.scan_batch SET upload_id=NULL WHERE id = ANY(%s)", (list(scans.values()),))
            c.execute("DELETE FROM staging.upload WHERE id = ANY(%s)", ([a["id"], b["id"], other["id"]],))
