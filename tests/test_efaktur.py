"""e-Faktur PDFs (common/efaktur.py, the user 2026-10-12): the tax office's file for an order, recognised by its own
text, linked when Satellite holds its SOR with the billing number it prints, and appended to the order's PDF. Never
read by the AI. The real files are customer data (docs/17, git-ignored): these tests make their own."""
import io

from PIL import Image
from pypdf import PdfReader

from common import db, efaktur

SOR, BILL = "SOR99990000001", "7999999901"            # not real: no SO in Satellite has these
LINES = ["Faktur Pajak", "Kode dan Nomor Seri Faktur Pajak : 040.004-26.00000001",
         "Pengusaha Kena Pajak", "Nama : PT SARANA ABADI MAKMUR BERSAMA",
         "Pembeli Barang Kena Pajak / Penerima Jasa Kena Pajak", "Nama : PT CONTOH PEMBELI",
         "Dasar Pengenaan Pajak      1.000.000,00", "Total PPN      120.000,00", f"{BILL}/{SOR}"]


def text_pdf(lines):
    """A one-page PDF carrying these lines as text, the way an e-Faktur does."""
    body = " ".join("(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for l in lines)
    content = f"BT /F1 10 Tf 40 800 Td 14 TL {body} ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> "
            "/Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def image_pdf(pages=1):
    """A scan: pages that are pictures, no text."""
    ims = [Image.new("1", (200, 280), 1) for _ in range(pages)]
    b = io.BytesIO()
    ims[0].save(b, "PDF", save_all=True, append_images=ims[1:], resolution=72)
    return b.getvalue()


def test_an_efakturs_own_text_says_which_order_it_belongs_to():
    got = efaktur.parse(efaktur.text_of(text_pdf(LINES)))
    assert got == {"billing_no": BILL, "sor_no": SOR, "kode_seri": "040.004-26.00000001", "buyer": "PT CONTOH PEMBELI",
                   "dpp": 1_000_000.0, "ppn": 120_000.0}


def test_a_scan_or_another_pdf_is_never_taken_for_one():
    assert not efaktur.text_of(image_pdf()).strip() and efaktur.parse("") is None    # a scan carries no text
    assert efaktur.parse(efaktur.text_of(text_pdf(LINES[:-1]))) is None               # no billing/SOR reference
    assert efaktur.parse(efaktur.text_of(text_pdf(LINES[1:2] + [f"{BILL}/{SOR}"]))) is not None
    two = LINES + ["7999999902/SOR99990000002"]                                      # two orders: never guess
    assert efaktur.parse(efaktur.text_of(text_pdf(two))) is None
    assert efaktur.parse(efaktur.text_of(text_pdf(LINES[2:]))) is None                # no title: not an e-Faktur


def test_two_witnesses_must_agree_and_it_never_links_on_one():
    assert efaktur.decide(BILL, None)[0] == "waiting"                                  # SOR not in Satellite (yet)
    assert efaktur.decide(BILL, {"billing_no": None})[0] == "waiting"                  # not posted in SAP (yet)
    status, why = efaktur.decide(BILL, {"billing_no": "7999999999"})
    assert status == "held" and BILL in why and "7999999999" in why                   # a person looks
    assert efaktur.decide(BILL, {"billing_no": BILL}) == ("linked", None)


def test_its_pages_are_appended_to_the_orders_pdf_as_they_are():
    merged = efaktur.merge(image_pdf(4), [text_pdf(LINES), image_pdf(2)])
    pages = PdfReader(io.BytesIO(merged)).pages
    assert len(pages) == 7 and BILL in pages[4].extract_text()                        # the e-Faktur's text is kept


class Store:
    """Storage, in memory."""
    def __init__(self):
        self.files = {}

    def put_object(self, bucket, key, data, length, content_type=None):
        self.files[key] = data.read()


def test_an_efaktur_waits_for_satellite_then_links_by_itself(monkeypatch):
    store = Store()
    monkeypatch.setattr(efaktur.storage, "ensure_bucket", lambda: store)
    monkeypatch.setattr(efaktur.storage, "bucket", lambda: "test")
    data = text_pdf(LINES)
    try:
        out = efaktur.receive(data, "test-efaktur.pdf")
        e = out["efaktur"]
        assert (e["sor_no"], e["billing_no"], e["status"]) == (SOR, BILL, "waiting") and len(store.files) == 1
        assert efaktur.receive(data, "again.pdf")["duplicate"] is True                # the same file once
        with db.connect() as c:                                    # Satellite gets the SO, posted (no lines yet)
            c.execute("""INSERT INTO satellite.sor (sor_no, customer_code, customer_name, billing_no)
                         VALUES (%s, 'TEST', 'TEST', %s)""", (SOR, BILL))
        assert efaktur.relink([e["id"]]) == {"linked": 1}
        with db.connect() as c:
            assert efaktur.one(c, e["id"])["status"] == "linked"
            assert efaktur.of_orders(c, [SOR])[SOR]["status"] == "linked"             # goes in when the order is sent
            c.execute("UPDATE satellite.sor SET billing_no='7999999999' WHERE sor_no=%s", (SOR,))
        assert efaktur.relink([e["id"]]) == {"held": 1}                               # Satellite says another number
        with db.connect() as c:
            assert efaktur.for_order(c, SOR) == [] and SOR in {r["sor_no"] for r in efaktur.not_linked(c)}   # never in the PDF
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM staging.efaktur WHERE sor_no=%s", (SOR,))
            c.execute("DELETE FROM satellite.sor WHERE sor_no=%s", (SOR,))
