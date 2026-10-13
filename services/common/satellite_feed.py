"""Satellite's data, in: the one place that turns Satellite's fields into rows of satellite.sor and satellite.sor_item
(common/tables.py: Sor, SorItem). The CSV export uses it now (scripts/load_satellite.py); a live feed from Satellite
(its API, a read-only database view, or a daily file) uses the same upsert.

  SOR, SOR_ITEM              each column: (where it comes from in Satellite's export, what it means). They are also
                             what docs/satellite-schema/*.schema.json says (python -m common.tablegen jsonschema), the files
                             a person who writes Satellite's data for us works from
  read_export(folder)        the export's three CSV files → ([Sor], [SorItem], counts)
  upsert(conn, sos, lines)   every SO given is inserted or updated, never deleted (documents already sent point at it);
                             the lines of exactly those SOs are replaced. An SO not given keeps its lines (until
                             2026-10-13 a load emptied every line first, which left older SOs with none).

Three files, from the user (2026-09-25; real customer data: kept on this machine, *.csv is git-ignored):
  HEADER-1-bulan-kebelakang.csv          one row per SO: customer, amounts, billing number, CGR, status
  ITEM-1-bulan-kebelakang.csv            one row per SO line (joined to HEADER by order_id)
  Header-1-bulan-po-customer-number.csv  SOR → SG sales-order number → the customer's PO number (the FP's Nomor CPO)
"""
import csv
import json
import os
import sys

from common import tables
from common.tables import Sor, SorItem

csv.field_size_limit(sys.maxsize)

PO_FILE = "Header-1-bulan-po-customer-number.csv"
HEADER_FILE = "HEADER-1-bulan-kebelakang.csv"
ITEM_FILE = "ITEM-1-bulan-kebelakang.csv"
DISCOUNTS = ("reg", "dc", "b2b", "dmg", "new", "go", "special", "lsb", "promo", "additional")

SOR = {
    "sor_no": ("order_no", "the SOR, e.g. SOR26110264726"),
    "customer_code": ("customer_id", "the ship-to store's id"),
    "customer_name": ("customer_name", "the ship-to store's name"),
    "cpo_no": ("po_number, in the PO file",
               "the customer's PO number: the Faktur Penjualan's Nomor CPO; a PO or a receipt finds its order by it"),
    "tgl_so": ("created_at", "the order's date"),
    "total": ("inv_grand_total_amount", "the total as invoiced, with PPN (after any tolakan; 0 before billing)"),
    "so_no": ("sales_order_no, in the PO file", "Satellite's sales-order number, SG…"),
    "dpp": ("inv_subtotal_after_discount_amount", "DPP as invoiced"),
    "ppn": ("inv_vat_amount", "PPN as invoiced"),
    "vat_pct": ("vat_percentage", "the PPN rate, e.g. 11.00"),
    "billing_no": ("sap_bill_doc_no", "SAP's billing number, once the order is posted"),
    "cgr_no": ("cgr_doc_no", "the goods receipt (CGR) number"),
    "cgr_date": ("cgr_doc_date", "the goods receipt's date"),
    "posting_date": ("posting_date", "SAP's posting date"),
    "status": ("document_status", "the order's status: INVOICE_GENERATED, CGR, …"),
    "loaded_at": (None, "when this row was written here (set by us)"),
    "order_dpp": ("subtotal_after_discount_amount", "DPP as ordered: what the Faktur Penjualan prints"),
    "order_ppn": ("vat_amount", "PPN as ordered"),
    "order_total": ("grand_total_amount", "the total as ordered, with PPN: what the Faktur Penjualan prints"),
    "customer_parent": ("ship_to_parent_customer_id", "the parent customer id the store is under"),
}
SOR_ITEM = {
    "sor_no": ("order_id, as its order's order_no", "the order this line belongs to"),
    "line_no": ("line_no", "the line's number in the order"),
    "item_code": ("item_code", "SAMB's material code (the Faktur Penjualan's Kode)"),
    "description": ("item_description", "the product's name"),
    "uom": ("ordered_uom_id", "the unit it was ordered in: CAR, PCS, …"),
    "pcs_per_uom": ("conversion_factor", "pieces in one unit"),
    "qty_uom": ("ordered_qty", "the quantity ordered, in units"),
    "qty_pcs": ("ordered_base_qty", "the quantity ordered, in pieces"),
    "price_uom": ("price_list_amount", "the price of one unit"),
    "price_pcs": ("base_price_list_amount", "the price of one piece (the Faktur Penjualan's Harga)"),
    "discounts": ("disc_<kind>_type, disc_<kind>_value, disc_<kind>_amount",
                  "each discount given: {kind: {type, value, amount}}, kinds " + ", ".join(DISCOUNTS)),
    "discount_total": ("total_discount_amount", "all discounts together"),
    "gross": ("subtotal_amount", "the amount before discounts"),
    "line_amount": ("total_amount", "the net amount (the Faktur Penjualan's Jumlah)"),
    "vat": ("vat_amount", "PPN on the line"),
    "invoice_qty": ("invoice_base_qty", "invoiced, in pieces"),
    "invoice_amount": ("invoice_nett_amount", "the invoiced net amount"),
    "cgr_qty": ("cgr_base_qty", "received, in pieces (the goods receipt)"),
    "rejected_qty": ("cgr_rejected_base_qty", "rejected by the store (tolakan), in pieces"),
    "reject_reason": ("cgr_reject_reason", "why the store rejected it"),
}


def num(s):
    s = (s or "").strip()
    return float(s) if s else None


def day(s):
    s = (s or "").strip()
    return s[:10] if s else None


def text(s):
    return (s or "").strip() or None


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def order_row(h, po) -> Sor:
    """One SO from the export's HEADER row and its row in the PO file ({} when it has none)."""
    return Sor(sor_no=h["order_no"].strip(), customer_code=h["customer_id"].strip(), customer_name=h["customer_name"].strip(),
               cpo_no=text(po.get("po_number")), tgl_so=day(h["created_at"]), total=num(h["inv_grand_total_amount"]),
               so_no=text(po.get("sales_order_no")), dpp=num(h["inv_subtotal_after_discount_amount"]),
               ppn=num(h["inv_vat_amount"]), vat_pct=num(h["vat_percentage"]), billing_no=text(h["sap_bill_doc_no"]),
               cgr_no=text(h["cgr_doc_no"]), cgr_date=day(h["cgr_doc_date"]), posting_date=day(h["posting_date"]),
               status=text(h["document_status"]),
               # the SO as ordered: what its FP printed (a tolakan later changes only the inv_ amounts)
               order_dpp=num(h["subtotal_after_discount_amount"]), order_ppn=num(h["vat_amount"]),
               order_total=num(h["grand_total_amount"]), customer_parent=text(h["ship_to_parent_customer_id"]))


def line_row(r, sor) -> SorItem:
    """One SO line from the export's ITEM row; sor = its order's SOR (found by order_id)."""
    disc = {k: {"type": r[f"disc_{k}_type"], "value": num(r[f"disc_{k}_value"]), "amount": num(r[f"disc_{k}_amount"])}
            for k in DISCOUNTS if num(r.get(f"disc_{k}_amount")) or num(r.get(f"disc_{k}_value"))}
    return SorItem(sor_no=sor, line_no=int(r["line_no"]), item_code=r["item_code"].strip(),
                   description=r["item_description"].strip(), uom=r["ordered_uom_id"].strip(),
                   pcs_per_uom=num(r["conversion_factor"]), qty_uom=num(r["ordered_qty"]),
                   qty_pcs=num(r["ordered_base_qty"]), price_uom=num(r["price_list_amount"]),
                   price_pcs=num(r["base_price_list_amount"]), discounts=disc,
                   discount_total=num(r["total_discount_amount"]), gross=num(r["subtotal_amount"]),
                   line_amount=num(r["total_amount"]), vat=num(r["vat_amount"]), invoice_qty=num(r["invoice_base_qty"]),
                   invoice_amount=num(r["invoice_nett_amount"]), cgr_qty=num(r["cgr_base_qty"]),
                   rejected_qty=num(r["cgr_rejected_base_qty"]), reject_reason=text(r["cgr_reject_reason"]))


def read_export(folder):
    """([Sor], [SorItem], counts) from the three files. An SO in the PO file but not in HEADER is skipped (no customer);
    a line whose order isn't in HEADER is skipped; a repeated (SO, line) keeps its last row."""
    po = {r["order_no"].strip(): r for r in rows(os.path.join(folder, PO_FILE))}
    orders, sor_of = [], {}
    for h in rows(os.path.join(folder, HEADER_FILE)):
        o = order_row(h, po.get(h["order_no"].strip()) or {})
        sor_of[h["order_id"]] = o["sor_no"]
        orders.append(o)
    lines, skipped = {}, 0
    for r in rows(os.path.join(folder, ITEM_FILE)):
        sor = sor_of.get(r["order_id"])
        if not sor:
            skipped += 1
            continue
        lines[(sor, int(r["line_no"]))] = line_row(r, sor)
    counts = {"orders": len(orders), "with_po_number": sum(1 for o in orders if o["cpo_no"]), "lines": len(lines),
              "lines_without_order": skipped, "only_in_po_file": len(set(po) - {o["sor_no"] for o in orders})}
    return orders, list(lines.values()), counts


def upsert(conn, sos: list[Sor], lines: list[SorItem]):
    """Every SO given inserted or updated (loaded_at = now), and the lines of exactly those SOs replaced by `lines`.
    One transaction: the caller's connection commits it."""
    cols = tuple(c for c in tables.columns(Sor) if c != "loaded_at")
    icols = tables.columns(SorItem)
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE sor_in (LIKE satellite.sor INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy(f"COPY sor_in ({', '.join(cols)}) FROM STDIN") as cp:
            for so in sos:
                cp.write_row([so.get(c) for c in cols])
        cur.execute(f"""INSERT INTO satellite.sor ({', '.join(cols)}, loaded_at)
                        SELECT {', '.join(cols)}, now() FROM sor_in
                        ON CONFLICT (sor_no) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in cols[1:])},
                                                           loaded_at = now()""")
        cur.execute("DELETE FROM satellite.sor_item WHERE sor_no = ANY(%s)",
                    (sorted({so["sor_no"] for so in sos} | {ln["sor_no"] for ln in lines}),))
        with cur.copy(f"COPY satellite.sor_item ({', '.join(icols)}) FROM STDIN") as cp:
            for ln in lines:
                cp.write_row([json.dumps(ln.get(c) or {}) if c == "discounts" else ln.get(c) for c in icols])
    return {"orders": len(sos), "lines": len(lines)}


def load_folder(folder):
    """The CSV export into the database: read, then upsert. Returns the counts."""
    from common import db, satellite
    orders, lines, counts = read_export(folder)
    with db.connect() as c:
        upsert(c, orders, lines)
    satellite.refresh()
    return counts
