-- Every column of Satellite's two tables says what it is and where it comes from in Satellite's export (2026-10-13).
-- The same words as common/satellite_feed.py (SOR, SOR_ITEM), so the database, the table models (common/tables.py)
-- and docs/satellite-schema/*.schema.json agree. Replaces 012's comment on sor.total, which called it the FP's Total: the FP
-- prints the order as ordered (order_total), and total is the invoiced amount. Safe to run again.
COMMENT ON COLUMN satellite.sor.sor_no IS 'The SOR, e.g. SOR26110264726. Satellite''s export: order_no.';
COMMENT ON COLUMN satellite.sor.customer_code IS 'The ship-to store''s id. Satellite''s export: customer_id.';
COMMENT ON COLUMN satellite.sor.customer_name IS 'The ship-to store''s name. Satellite''s export: customer_name.';
COMMENT ON COLUMN satellite.sor.cpo_no IS 'The customer''s PO number: the Faktur Penjualan''s Nomor CPO; a PO or a receipt finds its order by it. Satellite''s export: po_number, in the PO file.';
COMMENT ON COLUMN satellite.sor.tgl_so IS 'The order''s date. Satellite''s export: created_at.';
COMMENT ON COLUMN satellite.sor.total IS 'The total as invoiced, with PPN (after any tolakan; 0 before billing). Satellite''s export: inv_grand_total_amount.';
COMMENT ON COLUMN satellite.sor.so_no IS 'Satellite''s sales-order number, SG…. Satellite''s export: sales_order_no, in the PO file.';
COMMENT ON COLUMN satellite.sor.dpp IS 'DPP as invoiced. Satellite''s export: inv_subtotal_after_discount_amount.';
COMMENT ON COLUMN satellite.sor.ppn IS 'PPN as invoiced. Satellite''s export: inv_vat_amount.';
COMMENT ON COLUMN satellite.sor.vat_pct IS 'The PPN rate, e.g. 11.00. Satellite''s export: vat_percentage.';
COMMENT ON COLUMN satellite.sor.billing_no IS 'SAP''s billing number, once the order is posted. Satellite''s export: sap_bill_doc_no.';
COMMENT ON COLUMN satellite.sor.cgr_no IS 'The goods receipt (CGR) number. Satellite''s export: cgr_doc_no.';
COMMENT ON COLUMN satellite.sor.cgr_date IS 'The goods receipt''s date. Satellite''s export: cgr_doc_date.';
COMMENT ON COLUMN satellite.sor.posting_date IS 'SAP''s posting date. Satellite''s export: posting_date.';
COMMENT ON COLUMN satellite.sor.status IS 'The order''s status: INVOICE_GENERATED, CGR, …. Satellite''s export: document_status.';
COMMENT ON COLUMN satellite.sor.loaded_at IS 'When this row was written here (set by us).';
COMMENT ON COLUMN satellite.sor.order_dpp IS 'DPP as ordered: what the Faktur Penjualan prints. Satellite''s export: subtotal_after_discount_amount.';
COMMENT ON COLUMN satellite.sor.order_ppn IS 'PPN as ordered. Satellite''s export: vat_amount.';
COMMENT ON COLUMN satellite.sor.order_total IS 'The total as ordered, with PPN: what the Faktur Penjualan prints. Satellite''s export: grand_total_amount.';
COMMENT ON COLUMN satellite.sor.customer_parent IS 'The parent customer id the store is under. Satellite''s export: ship_to_parent_customer_id.';
COMMENT ON COLUMN satellite.sor_item.sor_no IS 'The order this line belongs to. Satellite''s export: order_id, as its order''s order_no.';
COMMENT ON COLUMN satellite.sor_item.line_no IS 'The line''s number in the order. Satellite''s export: line_no.';
COMMENT ON COLUMN satellite.sor_item.item_code IS 'SAMB''s material code (the Faktur Penjualan''s Kode). Satellite''s export: item_code.';
COMMENT ON COLUMN satellite.sor_item.description IS 'The product''s name. Satellite''s export: item_description.';
COMMENT ON COLUMN satellite.sor_item.uom IS 'The unit it was ordered in: CAR, PCS, …. Satellite''s export: ordered_uom_id.';
COMMENT ON COLUMN satellite.sor_item.pcs_per_uom IS 'Pieces in one unit. Satellite''s export: conversion_factor.';
COMMENT ON COLUMN satellite.sor_item.qty_uom IS 'The quantity ordered, in units. Satellite''s export: ordered_qty.';
COMMENT ON COLUMN satellite.sor_item.qty_pcs IS 'The quantity ordered, in pieces. Satellite''s export: ordered_base_qty.';
COMMENT ON COLUMN satellite.sor_item.price_uom IS 'The price of one unit. Satellite''s export: price_list_amount.';
COMMENT ON COLUMN satellite.sor_item.price_pcs IS 'The price of one piece (the Faktur Penjualan''s Harga). Satellite''s export: base_price_list_amount.';
COMMENT ON COLUMN satellite.sor_item.discounts IS 'Each discount given: {kind: {type, value, amount}}, kinds reg, dc, b2b, dmg, new, go, special, lsb, promo, additional. Satellite''s export: disc_<kind>_type, disc_<kind>_value, disc_<kind>_amount.';
COMMENT ON COLUMN satellite.sor_item.discount_total IS 'All discounts together. Satellite''s export: total_discount_amount.';
COMMENT ON COLUMN satellite.sor_item.gross IS 'The amount before discounts. Satellite''s export: subtotal_amount.';
COMMENT ON COLUMN satellite.sor_item.line_amount IS 'The net amount (the Faktur Penjualan''s Jumlah). Satellite''s export: total_amount.';
COMMENT ON COLUMN satellite.sor_item.vat IS 'PPN on the line. Satellite''s export: vat_amount.';
COMMENT ON COLUMN satellite.sor_item.invoice_qty IS 'Invoiced, in pieces. Satellite''s export: invoice_base_qty.';
COMMENT ON COLUMN satellite.sor_item.invoice_amount IS 'The invoiced net amount. Satellite''s export: invoice_nett_amount.';
COMMENT ON COLUMN satellite.sor_item.cgr_qty IS 'Received, in pieces (the goods receipt). Satellite''s export: cgr_base_qty.';
COMMENT ON COLUMN satellite.sor_item.rejected_qty IS 'Rejected by the store (tolakan), in pieces. Satellite''s export: cgr_rejected_base_qty.';
COMMENT ON COLUMN satellite.sor_item.reject_reason IS 'Why the store rejected it. Satellite''s export: cgr_reject_reason.';
