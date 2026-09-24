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
        ],
        "lines": [
            F("item_code", "id", "6.1", "Item code", "customer item code / SKU / PLU"),
            F("material_description", "text", "6.1", "Material description", "item description"),
            F("qty", "qty", "6.1", "Qty", "quantity received"),
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
            if f["name"] == "sor":
                continue
            cols.append(f"  {column(f):<20} {SQL[f['kind']]}")
        cols += ["  page_ref     integer[]", "  linked_by    link_key", "  confidence   numeric(4,3)",
                 "  source_batch text", "  source_pages integer[]"]
        out.append(f"\nCREATE TABLE satellite.{t['table']} (  -- {code}: {t['name']}\n" + ",\n".join(cols) + "\n);")
        for f in t["header"]:
            out.append(f"COMMENT ON COLUMN satellite.{t['table']}.{column(f)} IS '{'§6.1' if f['source'] == '6.1' else 'linking (not in §6.1)'}: {f['label']}';")
        if t["lines"]:
            lcols = [f"  doc_id   bigint NOT NULL REFERENCES satellite.{t['table']} (id) ON DELETE CASCADE", "  line_no  smallint NOT NULL"]
            lcols += [f"  {f['name']:<20} {SQL[f['kind']]}" for f in t["lines"]]
            lcols.append("  PRIMARY KEY (doc_id, line_no)")
            out.append(f"CREATE TABLE satellite.{t['table']}_line (\n" + ",\n".join(lcols) + "\n);")
            for f in t["lines"]:
                out.append(f"COMMENT ON COLUMN satellite.{t['table']}_line.{f['name']} IS '§6.1: {f['label']}';")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["ddl"]:
        print(ddl(), end="")
