-- GENERATED from services/common/fields.py by `python -m common.fields ddl`. Do not edit by hand.
-- Redefines the Satellite document tables so their columns are exactly the field lists.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM satellite.doc_faktur_penjualan) THEN RAISE EXCEPTION 'satellite.doc_faktur_penjualan has rows; migrate by hand'; END IF;
  IF EXISTS (SELECT 1 FROM satellite.doc_ttg) THEN RAISE EXCEPTION 'satellite.doc_ttg has rows; migrate by hand'; END IF;
  IF EXISTS (SELECT 1 FROM satellite.doc_po) THEN RAISE EXCEPTION 'satellite.doc_po has rows; migrate by hand'; END IF;
  IF EXISTS (SELECT 1 FROM satellite.doc_faktur_pajak) THEN RAISE EXCEPTION 'satellite.doc_faktur_pajak has rows; migrate by hand'; END IF;
END $$;
DROP TABLE IF EXISTS satellite.doc_faktur_penjualan_line;
DROP TABLE IF EXISTS satellite.doc_faktur_penjualan CASCADE;
DROP TABLE IF EXISTS satellite.doc_ttg_line;
DROP TABLE IF EXISTS satellite.doc_ttg CASCADE;
DROP TABLE IF EXISTS satellite.doc_po_line;
DROP TABLE IF EXISTS satellite.doc_po CASCADE;
DROP TABLE IF EXISTS satellite.doc_faktur_pajak CASCADE;

CREATE TABLE satellite.doc_faktur_penjualan (  -- FP: Faktur Penjualan
  id           bigserial PRIMARY KEY,
  sor_no       text NOT NULL UNIQUE REFERENCES satellite.sor (sor_no),
  dpp                  numeric(18,2),
  ppn                  numeric(18,2),
  total                numeric(18,2),
  nomor_cpo            text,
  customer_name        text,
  customer_code        text,
  page_ref     integer[],
  linked_by    link_key,
  confidence   numeric(4,3),
  source_batch text,
  source_pages integer[]
);
COMMENT ON COLUMN satellite.doc_faktur_penjualan.sor_no IS '§6.1: SOR';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.dpp IS '§6.1: Dasar Pengenaan Pajak';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.ppn IS '§6.1: PPN';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.total IS '§6.1: Total';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.nomor_cpo IS 'linking (not in §6.1): Nomor CPO';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.customer_name IS 'linking (not in §6.1): Kepada';
COMMENT ON COLUMN satellite.doc_faktur_penjualan.customer_code IS 'linking (not in §6.1): Customer code';
CREATE TABLE satellite.doc_faktur_penjualan_line (
  doc_id   bigint NOT NULL REFERENCES satellite.doc_faktur_penjualan (id) ON DELETE CASCADE,
  line_no  smallint NOT NULL,
  kode_material        text,
  nama_produk          text,
  kemasan              text,
  qty_crt              numeric(12,3),
  qty_pcs              numeric(12,3),
  PRIMARY KEY (doc_id, line_no)
);
COMMENT ON COLUMN satellite.doc_faktur_penjualan_line.kode_material IS '§6.1: Kode material (field "Kode")';
COMMENT ON COLUMN satellite.doc_faktur_penjualan_line.nama_produk IS '§6.1: Nama produk';
COMMENT ON COLUMN satellite.doc_faktur_penjualan_line.kemasan IS '§6.1: Kemasan';
COMMENT ON COLUMN satellite.doc_faktur_penjualan_line.qty_crt IS '§6.1: Qty (CRT)';
COMMENT ON COLUMN satellite.doc_faktur_penjualan_line.qty_pcs IS '§6.1: Qty (PCS)';

CREATE TABLE satellite.doc_ttg (  -- TTG: Tanda Terima
  id           bigserial PRIMARY KEY,
  sor_no       text NOT NULL REFERENCES satellite.sor (sor_no),
  posting_date         date,
  document_no          text,
  purchase_order_no    text,
  vendor_number        text,
  no_ref               text,
  customer_name        text,
  page_ref     integer[],
  linked_by    link_key,
  confidence   numeric(4,3),
  source_batch text,
  source_pages integer[]
);
COMMENT ON COLUMN satellite.doc_ttg.posting_date IS '§6.1: Posting date';
COMMENT ON COLUMN satellite.doc_ttg.document_no IS '§6.1: Document No';
COMMENT ON COLUMN satellite.doc_ttg.purchase_order_no IS '§6.1: Purchase Order No';
COMMENT ON COLUMN satellite.doc_ttg.vendor_number IS '§6.1: Vendor Number';
COMMENT ON COLUMN satellite.doc_ttg.no_ref IS 'linking (not in §6.1): No Ref';
COMMENT ON COLUMN satellite.doc_ttg.customer_name IS 'linking (not in §6.1): Customer';
CREATE TABLE satellite.doc_ttg_line (
  doc_id   bigint NOT NULL REFERENCES satellite.doc_ttg (id) ON DELETE CASCADE,
  line_no  smallint NOT NULL,
  item_code            text,
  material_description text,
  qty                  numeric(12,3),
  uom                  text,
  PRIMARY KEY (doc_id, line_no)
);
COMMENT ON COLUMN satellite.doc_ttg_line.item_code IS '§6.1: Item code';
COMMENT ON COLUMN satellite.doc_ttg_line.material_description IS '§6.1: Material description';
COMMENT ON COLUMN satellite.doc_ttg_line.qty IS '§6.1: Qty';
COMMENT ON COLUMN satellite.doc_ttg_line.uom IS '§6.1: UOM';

CREATE TABLE satellite.doc_po (  -- PO: PO Customer
  id           bigserial PRIMARY KEY,
  sor_no       text NOT NULL REFERENCES satellite.sor (sor_no),
  purchase_order_no    text,
  vendor_code          text,
  vendor_name          text,
  ppn                  numeric(18,2),
  total                numeric(18,2),
  customer_name        text,
  page_ref     integer[],
  linked_by    link_key,
  confidence   numeric(4,3),
  source_batch text,
  source_pages integer[]
);
COMMENT ON COLUMN satellite.doc_po.purchase_order_no IS '§6.1: Purchase Order No';
COMMENT ON COLUMN satellite.doc_po.vendor_code IS '§6.1: Vendor code';
COMMENT ON COLUMN satellite.doc_po.vendor_name IS '§6.1: Vendor name (nama lengkap SAMB)';
COMMENT ON COLUMN satellite.doc_po.ppn IS '§6.1: PPN';
COMMENT ON COLUMN satellite.doc_po.total IS '§6.1: Total';
COMMENT ON COLUMN satellite.doc_po.customer_name IS 'linking (not in §6.1): Customer';
CREATE TABLE satellite.doc_po_line (
  doc_id   bigint NOT NULL REFERENCES satellite.doc_po (id) ON DELETE CASCADE,
  line_no  smallint NOT NULL,
  product_code         text,
  product_description  text,
  qty                  numeric(12,3),
  uom                  text,
  unit_price           numeric(18,2),
  discount             text,
  PRIMARY KEY (doc_id, line_no)
);
COMMENT ON COLUMN satellite.doc_po_line.product_code IS '§6.1: Product code';
COMMENT ON COLUMN satellite.doc_po_line.product_description IS '§6.1: Product description';
COMMENT ON COLUMN satellite.doc_po_line.qty IS '§6.1: Qty';
COMMENT ON COLUMN satellite.doc_po_line.uom IS '§6.1: UOM';
COMMENT ON COLUMN satellite.doc_po_line.unit_price IS '§6.1: Unit price';
COMMENT ON COLUMN satellite.doc_po_line.discount IS '§6.1: Discount';

CREATE TABLE satellite.doc_faktur_pajak (  -- FPJ: Faktur Pajak
  id           bigserial PRIMARY KEY,
  sor_no       text REFERENCES satellite.sor (sor_no),
  billing_number       text,
  kode_seri            text,
  npwp_pengusaha       text,
  nitku_pengusaha      text,
  npwp_pembeli         text,
  nitku_pembeli        text,
  dpp                  numeric(18,2),
  ppn                  numeric(18,2),
  tanggal_transaksi    date,
  page_ref     integer[],
  linked_by    link_key,
  confidence   numeric(4,3),
  source_batch text,
  source_pages integer[]
);
COMMENT ON COLUMN satellite.doc_faktur_pajak.sor_no IS '§6.1: SOR';
COMMENT ON COLUMN satellite.doc_faktur_pajak.billing_number IS '§6.1: Billing Number';
COMMENT ON COLUMN satellite.doc_faktur_pajak.kode_seri IS '§6.1: Kode Seri';
COMMENT ON COLUMN satellite.doc_faktur_pajak.npwp_pengusaha IS '§6.1: NPWP pengusaha';
COMMENT ON COLUMN satellite.doc_faktur_pajak.nitku_pengusaha IS '§6.1: NITKU pengusaha';
COMMENT ON COLUMN satellite.doc_faktur_pajak.npwp_pembeli IS '§6.1: NPWP pembeli';
COMMENT ON COLUMN satellite.doc_faktur_pajak.nitku_pembeli IS '§6.1: NITKU pembeli';
COMMENT ON COLUMN satellite.doc_faktur_pajak.dpp IS '§6.1: Dasar Pengenaan Pajak';
COMMENT ON COLUMN satellite.doc_faktur_pajak.ppn IS '§6.1: PPN';
COMMENT ON COLUMN satellite.doc_faktur_pajak.tanggal_transaksi IS '§6.1: Tanggal transaksi';
