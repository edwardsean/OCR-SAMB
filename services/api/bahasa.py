"""Bahasa Indonesia for the screens Finance works in (the user, 2026-10-01: "use indonesian language ... clear and
simple for users to use and know what is going on, even when a new user just used the dashboard").

Display only. What forms post and the database stores (accept reasons, "(not printed)", field and check names) stays
as it is, so nothing the pipeline reads changes; wording other modules produce (grouping's hold reasons, the
teacher's status lines) is translated here, at the screen, and falls back to the original when a sentence is new."""
import re
from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
BULAN_PANJANG = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
                 "November", "Desember"]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# ------------------------------------------------------------------------------------------------ documents
DOC = {"FP": "Faktur Penjualan", "TTG": "Tanda Terima", "PO": "Purchase Order (PO)", "SJ": "Surat Jalan",
       "FPJ": "Faktur Pajak", "PEL": "Pelunasan", "CONTINUATION": "Halaman lanjutan", "OTHER": "Dokumen lain",
       "unsure": "Jenis belum pasti"}
DOC_SHORT = {"FP": "Faktur", "TTG": "Tanda Terima", "PO": "PO", "SJ": "Surat Jalan", "FPJ": "Faktur Pajak",
             "PEL": "Pelunasan", "CONTINUATION": "Lanjutan", "OTHER": "Lainnya", "unsure": "Belum pasti"}

# The Label screen's choices: (key, name, what it is). The key is what is stored.
LABEL_TYPES = [("FP", "Faktur Penjualan", "faktur milik SAMB sendiri, mencetak nomor SOR"),
               ("TTG", "Tanda Terima", "bukti terima barang dari pelanggan, apa pun namanya (GRN, Receiving…)"),
               ("PO", "Purchase Order", "pesanan pelanggan ke SAMB, termasuk Surat Pesanan"),
               ("SJ", "Surat Jalan", "surat pengantar pengiriman"),
               ("FPJ", "Faktur Pajak", "faktur pajak"),
               ("PEL", "Pelunasan", "bukti pembayaran / transfer"),
               ("CONTINUATION", "Halaman lanjutan", "halaman berikutnya dari dokumen sebelumnya, tanpa judul sendiri"),
               ("OTHER", "Lainnya", "bukan salah satu di atas, atau tidak terbaca")]

# ------------------------------------------------------------------------------------------------ fields
FIELD = {"sor": "Nomor SOR", "dpp": "DPP", "ppn": "PPN", "total": "Total", "nomor_cpo": "Nomor CPO",
         "customer_name": "Pelanggan", "customer_code": "Kode pelanggan", "posting_date": "Tanggal terima",
         "document_no": "Nomor dokumen", "purchase_order_no": "Nomor PO", "vendor_number": "Nomor vendor SAMB",
         "no_ref": "No Ref (nomor SOR)", "vendor_code": "Kode vendor SAMB", "vendor_name": "Nama vendor",
         "billing_number": "Nomor billing", "kode_seri": "Kode seri", "npwp_pengusaha": "NPWP penjual",
         "nitku_pengusaha": "NITKU penjual", "npwp_pembeli": "NPWP pembeli", "nitku_pembeli": "NITKU pembeli",
         "tanggal_transaksi": "Tanggal transaksi"}
FIELD_BY_TYPE = {("FP", "customer_name"): "Kepada (pelanggan)", ("FP", "total"): "Total faktur",
                 ("PO", "total"): "Total PO", ("TTG", "document_no"): "Nomor tanda terima",
                 ("FP", "dpp"): "DPP (Dasar Pengenaan Pajak)"}
DESC = {"sor": "nomor Sales Order, diawali SOR",
        "dpp": "Dasar Pengenaan Pajak",
        "ppn": "pajak (PPN)",
        "total": "total uang (bukan jumlah barang)",
        "nomor_cpo": "nomor PO pelanggan; menghubungkan PO dan Tanda Terima pelanggan ke SOR ini",
        "customer_code": "kode pelanggan dalam kurung di bawah Kepada",
        "posting_date": "tanggal terima / posting / GR",
        "document_no": "nomor tanda terima: No Receive / No GRN / No. GR / Document No",
        "purchase_order_no": "No PO / Purchase Order / Ref PO No / No. Reff",
        "vendor_number": "nomor SAMB sebagai pemasok di pelanggan ini",
        "vendor_code": "nomor SAMB sebagai pemasok di pelanggan ini",
        "vendor_name": "nama vendor seperti tercetak (nama lengkap SAMB)",
        "no_ref": "hanya bila isinya nomor SOR (sebagian pelanggan mencetak SOR milik SAMB di sini)"}
DESC_BY_TYPE = {("FP", "customer_name"): "nama pelanggan di bagian Kepada",
                ("TTG", "customer_name"): "pelanggan yang menerbitkan tanda terima ini",
                ("PO", "customer_name"): "pelanggan yang menerbitkan PO ini",
                ("PO", "purchase_order_no"): "nomor purchase order",
                ("PO", "total"): "total pesanan"}
COL = {"kode_material": "Kode material", "nama_produk": "Nama produk", "kemasan": "Kemasan",
       "qty_crt": "Qty (karton)", "qty_pcs": "Qty (pcs)", "item_code": "Kode barang",
       "material_description": "Nama barang", "qty": "Qty", "uom": "Satuan", "product_code": "Kode produk",
       "product_description": "Nama produk", "unit_price": "Harga satuan", "discount": "Diskon"}

# what an unsettled value does (the page viewer's fields; common.fields.DECIDES levels)
ROLE = {"keys": "menghubungkan halaman ke ordernya", "page": "jumlah milik faktur ini",
        "bundle": "dicek terhadap Satellite", "support": "dipastikan oleh Satellite", None: "disimpan seperti terbaca"}
BY = {"text": "tercetak (Tesseract)", "qr": "kode QR", "adds_up": "DPP + PPN = Total", "zoom": "tercetak (diperbesar)",
      "second_look": "dibaca ulang AI, didukung cetakan", "satellite": "data Satellite", "person": "orang",
      "ship_to": "nama toko", "rows": "baris barang", "receipt_no": "nomor tanda terima"}


def field(name, t=None):
    """A field's name for people; a line cell ('lines[<key>].<col>') by its column."""
    m = re.fullmatch(r"lines\[.*\]\.(\w+)", name or "")
    if m:
        return COL.get(m[1], m[1].replace("_", " "))
    return FIELD_BY_TYPE.get((t, name)) or FIELD.get(name) or (name or "").replace("_", " ")


def desc(name, t=None):
    return DESC_BY_TYPE.get((t, name)) or DESC.get(name) or ""


# ------------------------------------------------------------------------------------------------ checks, statuses
CHECK = {"sor_in_satellite": "SO ada di Satellite", "docs_complete": "Dokumen lengkap",
         "vendor_is_samb": "PO ditujukan ke SAMB", "fp_po_total": "Total PO = order SAMB",
         "fp_po_lines": "Baris PO = baris order", "received": "Barang diterima = CGR Satellite",
         "dates": "Urutan tanggal benar", "fpj": "Faktur = Faktur Pajak", "calibration": "Kalibrasi pelanggan",
         "store_named": "Toko di halaman = toko order"}
CHECK_STATUS = {"pass": "cocok", "accepted": "diterima", "fail": "tidak cocok", "unknown": "belum bisa dicek",
                "waiting": "menunggu", "info": "info", "n/a": "tidak berlaku"}
BUNDLE = {"needs_review": "Perlu dicek", "grouping": "Menunggu sistem", "auto_ok": "Siap dikirim",
          "reviewed": "Disetujui", "published": "Terkirim"}
BATCH = {"received": "Diterima", "splitting": "Sedang dipecah per halaman", "split": "Sudah dipecah",
         "rendered": "Sudah dipecah", "queued": "Antre dibaca", "reading": "Sedang dibaca", "read": "Selesai dibaca",
         "grouping": "Sedang dikelompokkan", "done": "Selesai", "failed": "Gagal"}
OUTCOME = {"clear": "Beres", "waiting_ai": "Menunggu AI", "needs_person": "Perlu dicek", "held_unsure": "Jenis belum pasti"}
FLAG = {"rotated": "diputar", "skewed": "miring", "dark_band": "ada pita hitam", "faint": "cetakan pudar",
        "poor_quality": "kualitas buruk"}
HOLD = {"not_read": "belum dibaca AI",
        "type_unknown": "jenis halamannya belum pasti: menunggu ditentukan di layar Jenis halaman",
        "continuation_without_start": "halaman lanjutan, tetapi halaman sebelumnya bukan bagian dari dokumen",
        "needs_sap_billing": "Satellite belum punya nomor billing untuk SO ini (belum diposting di SAP)",
        "fpj_needs_both": "Faktur Pajak dihubungkan bila nomor SOR dan nomor billing yang tercetak sama-sama pasti",
        "billing_disagrees": "nomor billing yang tercetak bukan nomor billing SO tersebut di Satellite",
        "later_stage": "baris Pelunasan dihubungkan satu per satu (tahap berikutnya)",
        "not_grouped": "jenis ini belum dikelompokkan",
        "fp_sor_unresolved": "nomor SOR-nya belum pasti",
        "two_fps_one_sor": "dua faktur memiliki SOR yang sama",
        "no_resolved_key": "belum ada nomor penghubung (SOR atau PO) yang pasti",
        "so_unknown": "nomornya tidak ada di Satellite maupun di faktur mana pun",
        "keys_disagree": "nomor-nomornya menunjuk ke SO yang berbeda",
        "po_matches_several_sos": "nomor PO-nya cocok dengan beberapa SO",
        "fp_missing": "Faktur Penjualan untuk SO ini belum ada di scan mana pun"}
LINK = {"sor": "lewat nomor SOR", "po_no": "lewat nomor PO", "billing_no": "lewat nomor billing",
        "amount": "lewat jumlahnya", "adjacency": "lanjutan halaman sebelumnya"}
# what backed a published value (app.published_docs): the label beside it on Data terkirim
PUB_STATE = {"print": "✓ sesuai cetakan", "satellite": "✓ dari data Satellite", "person": "✓ dipastikan orang",
             "ai": "cek di kertas", "empty": "kosong"}
REASON = {"rounding": "Pembulatan", "tolakan confirmed": "Tolakan sudah dipastikan",
          "the customer's own price": "Harga khusus pelanggan", "the document comes later": "Dokumen menyusul",
          "other (say in the note)": "Lainnya"}
NONE_REASON = {"not in SAMB's order": "tidak ada di order SAMB", "a free (bonus) item": "barang gratis (bonus)",
               "another product (say in the note)": "produk lain (tulis di catatan)"}


# ------------------------------------------------------------------------------------------------ numbers and dates
def _swap(s):
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def angka(x, dec=2):
    """1234567.8 -> '1.234.567,80' (Indonesian: dot groups thousands, comma marks decimals)."""
    if x is None or x == "":
        return "—"
    try:
        return _swap(f"{float(x):,.{dec}f}")
    except (TypeError, ValueError):
        return str(x)


def rp(x, dec=2):
    return "—" if x is None or x == "" else "Rp " + angka(x, dec)


def qty(x):
    """A quantity without trailing zeros: 2.0 -> '2', 0.5 -> '0,5'."""
    if x is None or x == "":
        return "—"
    try:
        return _swap(f"{float(x):,.3f}".rstrip("0").rstrip("."))
    except (TypeError, ValueError):
        return str(x)


def _as_dt(v):
    if isinstance(v, str):
        try:
            v = date.fromisoformat(v) if len(v.strip()) == 10 else datetime.fromisoformat(v)
        except ValueError:
            return None
    return v


def tgl(v, jam=True):
    """A date (or timestamp) as people write it: '30 Sep 2026' / '30 Sep 2026, 16.26' (WIB)."""
    v = _as_dt(v)
    if v is None:
        return "—"
    if isinstance(v, datetime):
        if v.tzinfo:
            v = v.astimezone(WIB)
        s = f"{v.day} {BULAN[v.month - 1]} {v.year}"
        return f"{s}, {v.hour:02d}.{v.minute:02d}" if jam else s
    if isinstance(v, date):
        return f"{v.day} {BULAN[v.month - 1]} {v.year}"
    return str(v)


def hari_ini():
    d = datetime.now(WIB)
    return f"{HARI[d.weekday()]}, {d.day} {BULAN_PANJANG[d.month - 1]} {d.year}"


# ------------------------------------------------------------------------------------------------ learning status
LESSON_STEPS = {"Fixed on this page": "Diperbaiki di halaman ini", "Kept as a lesson": "Disimpan sebagai pelajaran",
                "Waiting for the teacher": "Menunggu guru AI", "The teacher writes a tip": "Guru AI menulis kiat",
                "Tested on other pages": "Diuji di halaman lain", "Switched on": "Diaktifkan",
                "Applied to stored pages": "Diterapkan ke halaman tersimpan"}
LESSON_HEAD = [
    (r"Fixed\. Not kept as a lesson.*", "Sudah diperbaiki. Tidak disimpan sebagai pelajaran: nilainya tidak ditemukan "
                                       "di salinan halaman, jadi letaknya tidak bisa dipelajari."),
    (r"Fixed\. Kept in the test pile.*", "Sudah diperbaiki. Disimpan di tumpukan ujian: tidak diajarkan, hanya dipakai "
                                        "untuk mengukur seberapa baik sistem belajar."),
    (r"Waiting: the teacher couldn't reach its AI.*", "Menunggu: guru AI belum bisa menghubungi AI-nya. Dicoba lagi "
                                                     "otomatis setiap 30 menit."),
    (r"Waiting: an earlier tip for this kind of document is still open.*", "Menunggu: kiat sebelumnya untuk jenis "
                                                                          "dokumen ini masih terbuka di layar Pengetahuan "
                                                                          "AI. Satu perubahan sekaligus, jadi pelajaran "
                                                                          "ini menunggu sampai kiat itu dipakai atau "
                                                                          "ditolak."),
    (r"Waiting for the teacher \((\d+) lessons? ahead\)\.", r"Menunggu guru AI (\1 pelajaran di depan)."),
    (r"Waiting for the teacher to start\.", "Menunggu guru AI mulai."),
    (r"The teacher is writing a tip from your fix…", "Guru AI sedang menulis kiat dari perbaikan Anda…"),
    (r"The tip is written, but there's no other stored page.*", "Kiat sudah ditulis, tetapi belum ada halaman lain "
                                                               "sejenis untuk mengujinya. Dicoba lagi otomatis saat "
                                                               "halaman baru masuk."),
    (r"Testing the tip on other pages \((\d+) of (\d+)\)…", r"Menguji kiat di halaman lain (\1 dari \2)…"),
    (r"Testing the tip on other pages…", "Menguji kiat di halaman lain…"),
    (r"The tip passed its test\..*", "Kiat lulus uji. Sedang diaktifkan…"),
    (r"The first tip didn't pass its test\..*", "Kiat pertama tidak lulus uji. Guru AI mencoba lagi…"),
    (r"Learned\. Applying the tip to stored pages \((\d+) of (\d+)\)…",
     r"Sudah dipelajari. Menerapkan kiat ke halaman tersimpan (\1 dari \2)…"),
    (r"Learned\. The tip is in use: (\d+) of (\d+) stored pages? changed.*",
     r"Sudah dipelajari. Kiat dipakai: \1 dari \2 halaman tersimpan berubah, dan halaman baru sejenis ikut memakainya."),
    (r"Learned\. The tip is in use\.", "Sudah dipelajari. Kiat sudah dipakai."),
    (r"Nothing to learn: .*", "Tidak ada yang perlu dipelajari: sistem sudah membaca nilai ini dengan benar."),
    (r"The tip was rejected by (.+)\.", r"Kiat ditolak oleh \1."),
    (r"No tip was kept: (.+)", r"Tidak ada kiat yang disimpan: \1"),
    (r"No tip was kept\.", "Tidak ada kiat yang disimpan."),
    (r"The lesson stopped: (.+)\.", r"Pelajaran berhenti: \1."),
    (r"Saved\. \(The lesson's progress can't be shown: (.+)\.\)", r"Tersimpan. (Kemajuan pelajaran tidak bisa "
                                                                 r"ditampilkan: \1.)"),
]
TEACHER = [
    (r"Teacher: testing a (\w+) tip \((\d+)/(\d+)\)", r"Guru AI: menguji kiat \1 (\2/\3)"),
    (r"Teacher: applying a (\w+) tip \((\d+)/(\d+)\)", r"Guru AI: menerapkan kiat \1 (\2/\3)"),
    (r"Teacher: writing a (\w+) tip", r"Guru AI: menulis kiat \1"),
    (r"(\d+) tips? waiting for your approval", r"\1 kiat menunggu persetujuan Anda"),
    (r"Teacher: (\d+) lessons? waiting", r"Guru AI: \1 pelajaran menunggu"),
]


def _sentence(text, table):
    for pat, out in table:
        m = re.fullmatch(pat, text or "", re.S)
        if m:
            return m.expand(out)
    return text                                    # a sentence added later: shown as written, never an error


def lesson(lp):
    """wiki.lesson_progress's {steps, headline, tip, final}, said in Indonesian."""
    return {**lp, "headline": _sentence(lp.get("headline"), LESSON_HEAD),
            "steps": [(LESSON_STEPS.get(label, label), state) for label, state in lp.get("steps") or []]}


def teacher(line):
    return _sentence(line, TEACHER) if line else line


LEFT = [(r"the bundle hasn't been checked.*", "Order ini belum dicek: masih ditahan (Faktur Penjualan-nya belum ada di "
                                             "scan, atau belum dikelompokkan)."),
        (r"pages \[(.+)\] wait for the AI OCR", r"Halaman \1 menunggu AI."),
        (r"pages \[(.+)\]: their type, key or FP values wait for a person",
         r"Halaman \1 menunggu dipastikan orang (jenis, nomor, atau jumlah faktur).")]


def left(items):
    """crosscheck.can_approve's "what is left", in Indonesian where the sentence is known."""
    return [_sentence(x, LEFT) for x in items or []]


def nilai(v):
    """A value read from a page, for showing: '—' when empty; a person's "(not printed)" said in Indonesian (the
    stored value stays "(not printed)": the pipeline reads it)."""
    if v is None or v == "":
        return "—"
    return "(tidak tercetak)" if v == "(not printed)" else v


FILTERS = {"rp": rp, "angka": angka, "qty": qty, "tgl": tgl, "nilai": nilai}
GLOBALS = {"DOC": DOC, "DOC_SHORT": DOC_SHORT, "CHECK": CHECK, "CHECK_STATUS": CHECK_STATUS, "BUNDLE": BUNDLE,
           "BATCH": BATCH, "OUTCOME": OUTCOME, "FLAG": FLAG, "HOLD": HOLD, "REASON": REASON,
           "NONE_REASON": NONE_REASON, "ROLE": ROLE, "BY": BY, "field_id": field, "desc_id": desc,
           "COL": COL, "PUB_STATE": PUB_STATE, "hari_ini": hari_ini, "left_id": left}
