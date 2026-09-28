-- Phase 7, part 1: Satellite's real sales-order data (the user's one-month export, loaded by
-- scripts/load_satellite.py). Safe to re-run.
--
-- satellite.sor is one row per SO, satellite.sor_item one row per SO line. SAMB prints the Faktur Penjualan FROM these
-- records, so they are the truth for what an FP says (header and lines). They are never the truth for the customer's
-- own documents (PO, TTG): those are compared with them.

ALTER TABLE satellite.sor
  ADD COLUMN IF NOT EXISTS so_no        text,            -- SG26110282977: Satellite's sales-order number
  ADD COLUMN IF NOT EXISTS dpp          numeric(18,2),   -- inv_subtotal_after_discount_amount = the FP's DPP
  ADD COLUMN IF NOT EXISTS ppn          numeric(18,2),   -- inv_vat_amount = the FP's PPN
  ADD COLUMN IF NOT EXISTS vat_pct      numeric(5,2),    -- 11.00
  ADD COLUMN IF NOT EXISTS billing_no   text,            -- sap_bill_doc_no: 7000356304 is "356304" handwritten on the FP
  ADD COLUMN IF NOT EXISTS cgr_no       text,            -- the customer's goods receipt, confirmed in Satellite
  ADD COLUMN IF NOT EXISTS cgr_date     date,
  ADD COLUMN IF NOT EXISTS posting_date date,
  ADD COLUMN IF NOT EXISTS status       text,            -- document_status: INVOICE_GENERATED, CGR, ...
  ADD COLUMN IF NOT EXISTS loaded_at    timestamptz;
COMMENT ON COLUMN satellite.sor.total IS 'inv_grand_total_amount = the FP''s Total';
CREATE INDEX IF NOT EXISTS sor_customer_day ON satellite.sor (customer_code, tgl_so);

-- One row per SO line. item_code is SAMB's material code (the FP's "Kode"); quantities in the ordered unit (a carton)
-- and in pieces; cgr_qty and rejected_qty are what the customer confirmed receiving and rejected ("tolakan").
CREATE TABLE IF NOT EXISTS satellite.sor_item (
  sor_no         text NOT NULL REFERENCES satellite.sor (sor_no) ON DELETE CASCADE,
  line_no        integer NOT NULL,
  item_code      text NOT NULL,
  description    text,
  uom            text,                                   -- CAR
  pcs_per_uom    numeric(12,3),                          -- conversion_factor: pieces per carton
  qty_uom        numeric(14,3),                          -- ordered_qty (cartons)
  qty_pcs        numeric(14,3),                          -- ordered_base_qty (pieces)
  price_uom      numeric(18,2),                          -- price_list_amount (per carton)
  price_pcs      numeric(18,4),                          -- base_price_list_amount (per piece: the FP's "Harga")
  discounts      jsonb NOT NULL DEFAULT '{}',            -- the non-zero ones: {reg: {type, value, amount}, dc: ...}
  discount_total numeric(18,2),
  gross          numeric(18,2),                          -- subtotal_amount: before discount
  line_amount    numeric(18,2),                          -- total_amount: after discount (the FP's "Jumlah")
  vat            numeric(18,2),
  invoice_qty    numeric(14,3),
  invoice_amount numeric(18,2),
  cgr_qty        numeric(14,3),                          -- received, confirmed in Satellite
  rejected_qty   numeric(14,3),                          -- cgr_rejected_base_qty: "tolakan"
  reject_reason  text,
  PRIMARY KEY (sor_no, line_no)
);
