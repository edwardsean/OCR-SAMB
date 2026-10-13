"""e-Faktur PDFs: a Faktur Pajak attached to its order's PDF, never read by the AI (schema/031-efaktur.sql).

A Faktur Pajak exists only after its order was posted in SAP, so it comes in a later upload than the order's other
papers, and it comes as the tax office's PDF, with its text inside (the user, 2026-10-12: "they are e-faktur PDFs,
actually when faktur pajak is there you dont need to send more data, just attach the faktur pajak in the document").

  parse(text_of(pdf))   an e-Faktur's own text: its title, and exactly one "7000363545/SOR26110267792" (billing number
                        / SOR) on its last line. All 1,257 e-Faktur PDFs of 24-25 Sep (docs/17) have one, and on all
                        1,257 Satellite holds that SOR with that billing number. A scan (no text) is never one.
  receive(...)          stored and recorded, then linked at once
  decide(billing, so)   two witnesses must agree, as for a Faktur Pajak read from a scan (grouper/group.py
                        _link_fpj): the SOR it prints is in Satellite, and Satellite's billing number for it is the one
                        it prints. Satellite doesn't have it yet: it waits, and links by itself once Satellite does
                        (relink, after every Satellite load and every EFAKTUR_EVERY_MINUTES). Another billing number:
                        held for a person. Never attached on one witness (the user's choice, 2026-10-12).
  relink()              the e-Fakturs not attached yet, decided again; one whose order is already sent is appended to
                        that order's PDF now (publisher.publish.attach: a new version). An order sent later takes its
                        e-Faktur in its PDF then (publisher.publish.publish_one).

No AI, no page images: the e-Faktur's own pages are appended to the order's PDF as they are.
"""
import hashlib
import io
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

from pypdf import PdfReader, PdfWriter

from common import config, db, satellite, storage, trace, verify

PREFIX = config.STORAGE_PREFIX
TITLE = "Kode dan Nomor Seri Faktur Pajak"
REF = re.compile(r"(?<![0-9])(\d{10})/(SOR\d{11})(?![0-9])")
WIB = timezone(timedelta(hours=7))
OPEN = ("waiting", "held", "linked")
DUP = "SELECT id, file_name, received_at, sor_no, status FROM staging.efaktur WHERE sha256=%s"   # sor_no: an e-Faktur


def text_of(data):
    """The PDF's own text, laid out as printed; '' for a scan (an image carries no text) or a file it can't read."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            r = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, timeout=30)
        except Exception:
            return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def _amount(m):
    return round(verify.amount(m.group(1)), 2) if m and verify.amount(m.group(1)) is not None else None


def parse(text):
    """An e-Faktur's text → {billing_no, sor_no, kode_seri, buyer, dpp, ppn}, or None when the text isn't one: its
    title line and exactly one billing/SOR reference. Kept for the screens; only the reference links it."""
    if TITLE not in (text or ""):
        return None
    refs = set(REF.findall(text))
    if len(refs) != 1:
        return None
    (billing, sor), = refs
    kode = re.search(TITLE + r"\s*:\s*([0-9][0-9.\-]*)", text)
    buyer = re.search(r"Pembeli Barang Kena Pajak.*?\n\s*Nama\s*:\s*(.+)", text)
    return {"billing_no": billing, "sor_no": sor, "kode_seri": kode.group(1).strip(".-") if kode else None,
            "buyer": buyer.group(1).strip() if buyer else None,
            "dpp": _amount(re.search(r"Dasar Pengenaan Pajak\s+([\d.,]+)", text)),
            "ppn": _amount(re.search(r"Total PPN\s+([\d.,]+)", text))}


def decide(billing_no, so):
    """Pure: (status, why) for an e-Faktur printing this billing number, against its SOR's record (None: not in
    Satellite). `why` is for Finance, in Indonesian (the screens show it as it is)."""
    if not so:
        return "waiting", "SOR ini belum ada di data Satellite."
    if not so.get("billing_no"):
        return "waiting", ("Satellite belum punya nomor billing untuk SOR ini (belum diposting di SAP, atau data "
                           "Satellite belum diperbarui).")
    if satellite.flat(so["billing_no"]) != satellite.flat(billing_no):
        return "held", f"Nomor billing di e-Faktur {billing_no}, di Satellite {so['billing_no']}."
    return "linked", None


def merge(first, more):
    """One PDF: `first`'s pages, then each of `more`'s, as they are."""
    w = PdfWriter()
    for data in [first, *more]:
        for page in PdfReader(io.BytesIO(data)).pages:
            w.add_page(page)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def file_bytes(path):
    r = storage.client().get_object(storage.bucket(), path)
    try:
        return r.read()
    finally:
        r.close()
        r.release_conn()


def receive(data, file_name, upload=None, found=None):
    """An e-Faktur upload (found = parse(text_of(data))): stored under efaktur/<day>/, recorded, linked at once.
    {"efaktur": its row} or {"duplicate": True, "earlier": …} for the same file again."""
    found = found or parse(text_of(data))
    if not found:
        raise ValueError("not an e-Faktur")
    sha = hashlib.sha256(data).hexdigest()
    with db.connect() as c:
        dup = c.execute(DUP, (sha,)).fetchone()
    if dup:
        return {"duplicate": True, "earlier": dict(dup)}
    pages = len(PdfReader(io.BytesIO(data)).pages)
    key = f"{PREFIX}efaktur/{datetime.now(WIB).date().isoformat()}/{sha[:10]}/{file_name}"
    storage.ensure_bucket().put_object(storage.bucket(), key, io.BytesIO(data), len(data),
                                       content_type="application/pdf")
    with db.connect() as c:
        row = c.execute("""INSERT INTO staging.efaktur (sha256, upload_id, file_name, file_path, pages, billing_no,
                                                         sor_no, kode_seri, buyer, dpp, ppn)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (sha256) DO NOTHING
                           RETURNING id""",
                        (sha, (upload or {}).get("id"), file_name, key, pages, found["billing_no"], found["sor_no"],
                         found["kode_seri"], found["buyer"], found["dpp"], found["ppn"])).fetchone()
    if not row:                                           # the same file, uploaded twice at the same moment
        with db.connect() as c:
            dup = c.execute(DUP, (sha,)).fetchone()
        return {"duplicate": True, "earlier": dict(dup)}
    trace.event("efaktur.received", sor=found["sor_no"], file=file_name, pages=pages, upload=(upload or {}).get("id"))
    relink([row["id"]])
    with db.connect() as c:
        return {"efaktur": one(c, row["id"])}


def one(c, efaktur_id):
    r = c.execute(f"SELECT {COLS} FROM staging.efaktur WHERE id=%s", (efaktur_id,)).fetchone()
    return dict(r) if r else None


COLS = ("id, file_name, sor_no, billing_no, kode_seri, buyer, dpp, ppn, pages, status, why, attached_version, "
        "received_at, upload_id")


def of_upload(c, upload_id):
    return [dict(r) for r in c.execute(f"SELECT {COLS} FROM staging.efaktur WHERE upload_id=%s ORDER BY file_name",
                                       (upload_id,))]


def of_orders(c, sors):
    """{SOR: the e-Faktur that counts for it}: an attached one first, then a linked one."""
    rank = {"attached": 0, "linked": 1}
    out = {}
    for r in c.execute(f"SELECT {COLS} FROM staging.efaktur WHERE sor_no = ANY(%s) AND status IN ('attached', 'linked') "
                       "ORDER BY received_at", (list(sors),)):
        if r["sor_no"] not in out or rank[r["status"]] < rank[out[r["sor_no"]]["status"]]:
            out[r["sor_no"]] = dict(r)
    return out


def not_linked(c, limit=200):
    """The e-Fakturs that wait for Satellite or for a person, oldest first."""
    return [dict(r) for r in c.execute(f"SELECT {COLS} FROM staging.efaktur WHERE status IN ('waiting', 'held') "
                                       "ORDER BY received_at, id LIMIT %s", (limit,))]


def for_order(c, sor):
    """The e-Fakturs an order's PDF carries (or will): linked or attached, in the order they came."""
    return [dict(r) for r in c.execute("SELECT id, file_path, pages, status FROM staging.efaktur WHERE sor_no=%s "
                                       "AND status IN ('linked', 'attached') ORDER BY received_at, id", (sor,))]


def relink(ids=None):
    """Decide again every e-Faktur not attached yet (or these): waiting and held ones follow Satellite's data; a
    linked one whose order is already sent goes into that order's PDF now. No AI. {status: count}."""
    with db.connect() as c:
        rows = c.execute("SELECT id, sor_no, billing_no, status FROM staging.efaktur WHERE "
                         + ("id = ANY(%s)" if ids else "status = ANY(%s)"),
                         (list(ids) if ids else list(OPEN),)).fetchall()
        rows = [r for r in rows if r["status"] != "attached"]
        sos = satellite.load(c, sorted({r["sor_no"] for r in rows})) if rows else {}
        counts, linked = {}, set()
        for r in rows:
            status, why = decide(r["billing_no"], sos.get(satellite.flat(r["sor_no"])))
            c.execute("UPDATE staging.efaktur SET status=%s, why=%s, checked_at=now() WHERE id=%s AND status <> 'attached'",
                      (status, why, r["id"]))
            counts[status] = counts.get(status, 0) + 1
            if status != r["status"]:
                trace.event("efaktur." + status, sor=r["sor_no"], billing=r["billing_no"], why=why)
            if status == "linked":
                linked.add(r["sor_no"])
        sent = [r["sor_no"] for r in c.execute("SELECT DISTINCT sor_no FROM staging.bundle WHERE status='published' "
                                                "AND sor_no = ANY(%s)", (sorted(linked),))]
    if sent:
        from publisher import publish
        for sor in sent:
            publish.attach(sor)
    return counts


if __name__ == "__main__":                               # python -m common.efaktur relink   (after a Satellite load)
    import sys
    if sys.argv[1:2] == ["relink"]:
        print(relink())
    else:
        print("usage: python -m common.efaktur relink")
