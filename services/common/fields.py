"""THE field list — one per document type. Everything else is generated from this file:
  - the AI OCR's instructions (common/models/schemas.py)
  - the Satellite tables (schema/008-document-fields.sql, via `python -m common.fields ddl`)
  - the Field lists screen in the UI (/fields)

Source: Problem Statement §6.1 "Field penting per dokumen" (22 Sep 2026), plus a few LINKING fields that §6.1 doesn't
list but grouping needs (decided with the user 2026-09-24). Everything else was dropped on purpose:
  - FP handwritten number: it is the SAP billing number, already in SAP after posting (P7)
  - dates/subtotal/page markers not in §6.1
Surat Jalan: §6.1 says not observed yet -> no field list, pages are not extracted.
Dokumen Pelunasan: a later stage -> no field list yet.

source:  "6.1"      listed in §6.1 (the fields to store)
         "linking"  not in §6.1; read so documents can be tied to their SOR (§6.2) and shown separately
         "check"    not stored in Satellite: read so the bundle's checks can compare it (a receipt's own total)
kind:    id | text | amount | qty | date   (drives normalisation, SQL type and verification)
"""

def F(name, kind, source, label, desc):
    return {"name": name, "kind": kind, "source": source, "label": label, "desc": desc}


SQL = {"id": "text", "text": "text", "amount": "numeric(18,2)", "qty": "numeric(12,3)", "date": "date"}

DOCS = {
    "FP": {
        "name": "Faktur Penjualan", "about": "SAMB's own sales invoice · link ke SOR · cek qty & pajak",
        "table": "doc_faktur_penjualan", "sor_required": True, "one_per_sor": True,
        "header": [
            F("sor", "id", "6.1", "SOR", "Sales Order [SO] #, starts with SOR"),
            F("dpp", "amount", "6.1", "Dasar Pengenaan Pajak", "Dasar Pengenaan Pajak"),
            F("ppn", "amount", "6.1", "PPN", "PPn"),
            F("total", "amount", "6.1", "Total", "TOTAL of the invoice (not the item count)"),
            F("nomor_cpo", "id", "linking", "Nomor CPO", "Nomor CPO: the customer's PO number (links the customer's PO / TTG to this SOR)"),
            F("customer_name", "text", "linking", "Kepada", "Kepada: customer name"),
            F("customer_code", "id", "linking", "Customer code", "customer code in brackets under Kepada"),
        ],
        "lines": [
            F("kode_material", "id", "6.1", 'Kode material (field "Kode")', "Kode: SAMB material code"),
            F("nama_produk", "text", "6.1", "Nama produk", "Nama Produk"),
            F("kemasan", "text", "6.1", "Kemasan", "Kemasan"),
            F("qty_crt", "qty", "6.1", "Qty (CRT)", "QTY is printed 'CRT / PCS': the number BEFORE the slash (cartons)"),
            F("qty_pcs", "qty", "6.1", "Qty (PCS)", "QTY is printed 'CRT / PCS': the number AFTER the slash (pieces)"),
        ],
    },
    "TTG": {
        "name": "Tanda Terima", "about": "customer's receipt of goods (any name: Receiving Slip, GRN, Good Receipt…) · link ke PO · cek qty vs CGR",
        "table": "doc_ttg", "sor_required": True,
        "header": [
            F("posting_date", "date", "6.1", "Posting date", "receipt / posting / GR date"),
            F("document_no", "id", "6.1", "Document No", "receipt number: No Receive / NO GRN / No. GR / Document No"),
            F("purchase_order_no", "id", "6.1", "Purchase Order No", "No PO / Purchase Order / Ref PO No / No. Reff"),
            F("vendor_number", "id", "6.1", "Vendor Number", "SAMB's supplier / vendor number at this customer"),
            F("no_ref", "id", "linking", "No Ref", "No Ref / No Reference, ONLY if it is an SOR number (some customers print SAMB's SOR here)"),
            F("customer_name", "text", "linking", "Customer", "the customer that issued this receipt"),
        ],       # no amounts (the mentors, 2026-09-28): a receipt is compared with Satellite's goods receipt in quantities
        "lines": [
            F("item_code", "id", "6.1", "Item code", "customer item code / SKU / PLU"),
            F("material_description", "text", "6.1", "Material description", "item description"),
            F("qty", "qty", "6.1", "Qty", "quantity received (not ordered, not the pack size)"),
            F("uom", "text", "6.1", "UOM", "unit"),
        ],
    },
    "PO": {
        "name": "PO Customer", "about": "customer's purchase order / Surat Pesanan to SAMB · link PO ↔ SO · cek harga & diskon",
        "table": "doc_po", "sor_required": True,
        "header": [
            F("purchase_order_no", "id", "6.1", "Purchase Order No", "purchase order number"),
            F("vendor_code", "id", "6.1", "Vendor code", "SAMB's vendor code at this customer"),
            F("vendor_name", "text", "6.1", "Vendor name (nama lengkap SAMB)", "vendor name as printed (SAMB's full name)"),
            F("ppn", "amount", "6.1", "PPN", "PPN / tax"),
            F("total", "amount", "6.1", "Total", "total of the order"),
            F("customer_name", "text", "linking", "Customer", "the customer that issued the order"),
        ],
        "lines": [
            F("product_code", "id", "6.1", "Product code", "article / SKU / PLU"),
            F("product_description", "text", "6.1", "Product description", "item description"),
            F("qty", "qty", "6.1", "Qty", "quantity"),
            F("uom", "text", "6.1", "UOM", "unit"),
            F("unit_price", "amount", "6.1", "Unit price", "unit price"),
            F("discount", "text", "6.1", "Discount", "discount(s) as printed, e.g. '3.00% / 3.50%'"),
        ],
    },
    "FPJ": {
        "name": "Faktur Pajak", "about": "tax invoice · auto-link ke SOR via Billing No",
        "table": "doc_faktur_pajak", "sor_required": False,
        "header": [
            F("sor", "id", "6.1", "SOR", "SOR number if printed"),
            F("billing_number", "id", "6.1", "Billing Number", "billing number / nomor referensi"),
            F("kode_seri", "id", "6.1", "Kode Seri", "kode dan nomor seri faktur pajak"),
            F("npwp_pengusaha", "id", "6.1", "NPWP pengusaha", "NPWP of the seller (Pengusaha Kena Pajak)"),
            F("nitku_pengusaha", "id", "6.1", "NITKU pengusaha", "NITKU of the seller"),
            F("npwp_pembeli", "id", "6.1", "NPWP pembeli", "NPWP of the buyer"),
            F("nitku_pembeli", "id", "6.1", "NITKU pembeli", "NITKU of the buyer"),
            F("dpp", "amount", "6.1", "Dasar Pengenaan Pajak", "Dasar Pengenaan Pajak"),
            F("ppn", "amount", "6.1", "PPN", "PPN"),
            F("tanggal_transaksi", "date", "6.1", "Tanggal transaksi", "transaction date"),
        ],
        "lines": [],
    },
}
NOT_YET = {"SJ": "Surat Jalan: §6.1 'belum diobservasi' — no field list until a filled example arrives",
           "PEL": "Dokumen Pelunasan: later stage"}

# Which values decide something (verification redesign, the user 2026-09-26). Every other value is still read and
# stored, "kept as read": it never blocks a page or a bundle and is never asked again (a TTG's number, vendor codes,
# customer names, every row cell of a PO or TTG, the FP's pack size).
#   keys     a page links through one of them (grouping)
#   page     settled on the page: the FP's amounts, from Satellite's SO as ordered (7b)
#   support  settled by Satellite once the key is; blocks only when print contradicts Satellite (a person decides)
#   bundle   judged by the bundle's checks against Satellite: the order side (PO ↔ SO as ordered) and the delivery
#            side (TTG ↔ the CGR, what was received), never the page
# Kept apart from CANON on purpose: a reading's version (context.fields_version) hashes the AI's field list, and a
# change there would re-read every page.
DECIDES = {
    "FP": {"keys": ("sor",), "page": ("dpp", "ppn", "total"), "support": ("nomor_cpo", "customer_code"), "bundle": ()},
    "PO": {"keys": ("purchase_order_no",), "page": (), "support": (), "bundle": ("total", "ppn")},
    "TTG": {"keys": ("no_ref", "purchase_order_no"), "page": (), "support": (), "bundle": ("posting_date",)},
}


def decides(doc_type, *levels):
    """The type's decision values (its names), at the levels asked for (keys, page, support, bundle), else all."""
    d = DECIDES.get(doc_type) or {}
    return {f for level in (levels or ("keys", "page", "support", "bundle")) for f in d.get(level, ())}


def column(f):
    return "sor_no" if f["name"] == "sor" else f["name"]


def ddl():
    """Satellite document tables, generated from DOCS. Tables are dropped and recreated: refuses if any has rows."""
    out = ["-- GENERATED from services/common/fields.py by `python -m common.fields ddl`. Do not edit by hand.",
           "-- Redefines the Satellite document tables so their columns are exactly the field lists.",
           "DO $$ BEGIN"]
    for t in DOCS.values():
        out.append(f"  IF EXISTS (SELECT 1 FROM satellite.{t['table']}) THEN RAISE EXCEPTION 'satellite.{t['table']} has rows; migrate by hand'; END IF;")
    out.append("END $$;")
    for t in DOCS.values():
        if t["lines"]:
            out.append(f"DROP TABLE IF EXISTS satellite.{t['table']}_line;")
        out.append(f"DROP TABLE IF EXISTS satellite.{t['table']} CASCADE;")
    for code, t in DOCS.items():
        cols = ["  id           bigserial PRIMARY KEY",
                f"  sor_no       text {'NOT NULL ' if t['sor_required'] else ''}{'UNIQUE ' if t.get('one_per_sor') else ''}REFERENCES satellite.sor (sor_no)"]
        for f in t["header"]:
            if f["name"] == "sor" or f["source"] == "check":
                continue
            cols.append(f"  {column(f):<20} {SQL[f['kind']]}")
        cols += ["  page_ref     integer[]", "  linked_by    link_key", "  confidence   numeric(4,3)",
                 "  source_batch text", "  source_pages integer[]"]
        out.append(f"\nCREATE TABLE satellite.{t['table']} (  -- {code}: {t['name']}\n" + ",\n".join(cols) + "\n);")
        for f in t["header"]:
            if f["source"] == "check":
                continue
            out.append(f"COMMENT ON COLUMN satellite.{t['table']}.{column(f)} IS '{'§6.1' if f['source'] == '6.1' else 'linking (not in §6.1)'}: {f['label']}';")
        if t["lines"]:
            lcols = [f"  doc_id   bigint NOT NULL REFERENCES satellite.{t['table']} (id) ON DELETE CASCADE", "  line_no  smallint NOT NULL"]
            lcols += [f"  {f['name']:<20} {SQL[f['kind']]}" for f in t["lines"]]
            lcols.append("  PRIMARY KEY (doc_id, line_no)")
            out.append(f"CREATE TABLE satellite.{t['table']}_line (\n" + ",\n".join(lcols) + "\n);")
            for f in t["lines"]:
                out.append(f"COMMENT ON COLUMN satellite.{t['table']}_line.{f['name']} IS '§6.1: {f['label']}';")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------------------------
# vlm-first: ONE combined field list. Fields that mean the same thing on different document types are merged:
# 29 per-type header fields -> 18 stored fields, plus 2 clue fields that only help Jev classify.
# The AI OCR reads every page against this whole list. A page's per-type fields (the Satellite columns above) are a
# projection of it (TYPE_MAP), so the phase-5 check, the keys and table mapping keep working on per-type names.
# The live copy of this list, with each type's context, is versioned in staging.context_version (common/context.py).
# ------------------------------------------------------------------------------------------------------------------

def CF(name, kind, meaning, printed_as=(), role="store"):
    return {"name": name, "kind": kind, "meaning": meaning, "printed_as": list(printed_as), "role": role}


CANON = {f["name"]: f for f in [
    CF("sor", "id", "SAMB's sales order number: SOR followed by 11 digits (SOR26110245292). Some customers print it "
                    "without the letters SOR (11 digits starting 26…)",
       ["Sales Order [SO] #", "No Ref", "No. Reff", "DO#", "S/Fak"]),
    CF("po_number", "id", "the customer's purchase order number (SAMB's invoice calls it Nomor CPO)",
       ["Nomor CPO", "No PO", "PO No", "Purchase Order No", "Order No", "No. Pesanan", "PO# or OT#", "Receipt No (AEON)"]),
    CF("customer_name", "text", "the customer buying from SAMB: the addressee (Kepada) on SAMB's invoice, or the company "
                                "that issued a receipt or order", ["Kepada", "Customer"]),
    CF("customer_code", "id", "SAMB's code for the customer, in brackets under Kepada on SAMB's invoice (14000…)",
       ["Kepada [kode]"]),
    CF("vendor_code", "id", "SAMB's supplier / vendor number at this customer",
       ["No Supplier", "Vendor", "Kepada YTH", "Supplier Code", "Supplier"]),
    CF("vendor_name", "text", "SAMB's name as the customer printed it (as the supplier)",
       ["Nama Supplier", "Vendor Name", "Supplier Description"]),
    CF("document_no", "id", "the customer's goods-receipt number",
       ["No Receive", "NO GRN", "No. GR", "Document No", "REC NO", "BPB No", "Receiving#"]),
    CF("posting_date", "date", "the date the customer received or posted the goods",
       ["Tgl Terima", "Posting Date", "TGL KONFIRMASI GRN", "Tanggal BPB", "Date Received"]),
    CF("dpp", "amount", "Dasar Pengenaan Pajak: the tax base amount", ["Dasar Pengenaan Pajak", "DPP"]),
    CF("ppn", "amount", "PPN: the VAT amount", ["PPN", "PPn", "VAT", "Tax"]),
    CF("total", "amount", "the document's total amount (never an item count)", ["TOTAL", "Total", "Total Include Tax"]),
    CF("billing_number", "id", "billing / reference number printed on a Faktur Pajak; never the handwritten number "
                               "written on SAMB's invoice", ["Nomor Referensi", "Billing"]),
    CF("kode_seri", "id", "the Faktur Pajak's code and serial number", ["Kode dan Nomor Seri Faktur Pajak"]),
    CF("npwp_pengusaha", "id", "NPWP of the seller (Pengusaha Kena Pajak) on a Faktur Pajak", ["NPWP"]),
    CF("nitku_pengusaha", "id", "NITKU of the seller on a Faktur Pajak", ["NITKU"]),
    CF("npwp_pembeli", "id", "NPWP of the buyer on a Faktur Pajak", ["NPWP"]),
    CF("nitku_pembeli", "id", "NITKU of the buyer on a Faktur Pajak", ["NITKU"]),
    CF("tanggal_transaksi", "date", "the transaction date on a Faktur Pajak", ["Tanggal"]),
    CF("document_title", "text", "the document's printed title, exactly as printed", role="clue"),
    CF("page_marker", "text", "a page marker if printed, e.g. 'Page 12 of 35' or 'Hal : 1 / 1'", ["Page", "Hal"],
       role="clue"),
]}

# type -> {combined name: that type's field name (its Satellite column)}
TYPE_MAP = {
    "FP": {"sor": "sor", "dpp": "dpp", "ppn": "ppn", "total": "total", "po_number": "nomor_cpo",
           "customer_name": "customer_name", "customer_code": "customer_code"},
    "TTG": {"posting_date": "posting_date", "document_no": "document_no", "po_number": "purchase_order_no",
            "vendor_code": "vendor_number", "sor": "no_ref", "customer_name": "customer_name"},
    "PO": {"po_number": "purchase_order_no", "vendor_code": "vendor_code", "vendor_name": "vendor_name", "ppn": "ppn",
           "total": "total", "customer_name": "customer_name"},
    "FPJ": {n: n for n in ("sor", "billing_number", "kode_seri", "npwp_pengusaha", "nitku_pengusaha", "npwp_pembeli",
                           "nitku_pembeli", "dpp", "ppn", "tanggal_transaksi")},
}

# Line items: one fixed set of columns (not learned). SAMB's "Kode" and a customer's item code are different codes.
LINE_CANON = {f["name"]: f for f in [
    CF("description", "text", "the item's description as printed"),
    CF("customer_item_code", "id", "the customer's own item code (SKU / PLU / article / product code)"),
    CF("samb_material_code", "id", "SAMB's material code (column 'Kode' on SAMB's invoice)"),
    CF("qty", "qty", "the quantity: on a goods receipt the quantity RECEIVED (Diterima, Qty Received), not the quantity "
                     "ordered and not the pack size (24/CTN); on an order the quantity ordered"),
    CF("uom", "text", "unit of measure"),
    CF("qty_crt", "qty", "on SAMB's invoice QTY is printed 'CRT / PCS': the number BEFORE the slash"),
    CF("qty_pcs", "qty", "on SAMB's invoice QTY is printed 'CRT / PCS': the number AFTER the slash"),
    CF("kemasan", "text", "packaging (Kemasan) on SAMB's invoice"),
    CF("unit_price", "amount", "unit price"),
    CF("discount", "text", "discount(s) as printed, e.g. '3.00% / 3.50%'"),
]}
LINE_MAP = {
    "FP": {"samb_material_code": "kode_material", "description": "nama_produk", "kemasan": "kemasan",
           "qty_crt": "qty_crt", "qty_pcs": "qty_pcs"},
    "TTG": {"customer_item_code": "item_code", "description": "material_description", "qty": "qty", "uom": "uom"},
    "PO": {"customer_item_code": "product_code", "description": "product_description", "qty": "qty", "uom": "uom",
           "unit_price": "unit_price", "discount": "discount"},
}


def project(fields_all, doc_type):
    """The combined reading, named the way the page's type names it (per-type fields + that type's line columns).
    This is also the table mapping: a projected page is what its satellite.doc_* row would hold."""
    fields_all = fields_all or {}
    out = {}
    for canon, name in TYPE_MAP.get(doc_type, {}).items():
        f = fields_all.get(canon)
        out[name] = {"value": f.get("value"), "source_text": f.get("source_text")} if f else None
    if DOCS.get(doc_type, {}).get("lines"):
        cols = LINE_MAP[doc_type]
        out["lines"] = [{**{name: row.get(canon) for canon, name in cols.items()}, "row_text": row.get("row_text")}
                        for row in fields_all.get("lines") or []]
    return out


def lift(fields, doc_type):
    """The inverse of project: a per-type reading, renamed to the combined list."""
    fields = fields or {}
    out = {canon: (dict(fields[name]) if fields[name] else None)
           for canon, name in TYPE_MAP.get(doc_type, {}).items() if name in fields}
    if "lines" in fields:
        cols = LINE_MAP[doc_type]
        out["lines"] = [{**{canon: row.get(name) for canon, name in cols.items()}, "row_text": row.get("row_text")}
                        for row in fields["lines"]]
    return out


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["ddl"]:
        print(ddl(), end="")
