-- =============================================================================
-- Rekonsiliasi AR — OCR ingestion, this stage:
--   scan → verified per-SOR bundle → Satellite document tables + one PDF per SOR
--
-- Sources
--   Problem Statement & Process Delivery - Rekonsiliasi AR (22 Sep 2026)
--     §6.1 field per dokumen · §6.2 field penghubung · §6.3 verifikasi · §07 open items
--   Sample scan 7000356304 - 7000356499.pdf (288 pages, 3 customers)
--
-- Two schemas, two databases in reality:
--   staging    lives in the Docker Postgres. It is the pipeline's memory:
--              append-only, replayable, never read by SAP.
--   satellite  is the system of record in front of SAP. `satellite.sor` already
--              exists there; the stub below declares only the columns the pipeline
--              reads. Every other satellite.* table is new.
--
-- Provisional fields are marked:
--   TODO(zulmy)      field list observed on one customer only (§6.1 "jangan dipercaya 100%")
--   unobserved (SJ)  Surat Jalan has no filled example yet (§6.1)
--
-- Load order matters: types → satellite (sor stub first) → staging.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Shared vocabulary
-- ---------------------------------------------------------------------------

-- Canonical document types. Customer naming is mapped to these via
-- satellite.customer_profile.doc_aliases (note 1: Receiving Slip Order,
-- Goods Receive Note and Good Receipt are all TTG).
CREATE TYPE doc_type AS ENUM (
  'FP',            -- Faktur Penjualan (SAMB's own invoice; carries the SOR)
  'TTG',           -- Tanda Terima Gudang: customer's receipt of goods
  'SJ',            -- Surat Jalan
  'PO',            -- customer's Purchase Order / Surat Pesanan
  'FPJ',           -- Faktur Pajak (next stage)
  'PEL',           -- Dokumen Pelunasan (later stage)
  'CONTINUATION',  -- page n>1 of a multi-page document; joins the previous page
  'OTHER'
);

-- How a document was tied to its SOR, strongest first.
CREATE TYPE link_key AS ENUM (
  'sor',         -- the SOR is printed on the document (FP always; some TTG as "No Ref")
  'po_no',       -- customer PO number → satellite.sor.cpo_no → SOR
  'billing_no',  -- Faktur Pajak: Billing No → SAP → SOR (next stage)
  'amount',      -- totals match the FP within tolerance
  'adjacency'    -- nearest preceding FP in the scan; fallback only
);

CREATE TYPE page_status   AS ENUM ('queued', 'read', 'failed', 'dead_letter');
CREATE TYPE bundle_status AS ENUM ('grouping', 'auto_ok', 'needs_review', 'reviewed', 'published');

-- ---------------------------------------------------------------------------
-- satellite — system of record
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS satellite;

-- STUB of the existing Satellite sales-order table. Only what the pipeline reads:
-- the SOR ↔ customer-PO mapping (Admin SO creates the SO from the PO number) and
-- the customer. CGR quantities per line live in Satellite's existing goods-receipt
-- tables and are read there; they are not modelled here.
CREATE TABLE IF NOT EXISTS satellite.sor (
  sor_no        text PRIMARY KEY,              -- SOR26110245292
  customer_code text NOT NULL,                 -- 1400001602
  customer_name text,
  cpo_no        text,                          -- customer PO number the SO was created from
  tgl_so        date,
  total         numeric(18,2)
);
CREATE INDEX IF NOT EXISTS sor_cpo_no ON satellite.sor (cpo_no);
COMMENT ON COLUMN satellite.sor.cpo_no IS
  'The key that lets a TTG or PO find its SOR without the FP: TTG.purchase_order_no = PO.purchase_order_no = sor.cpo_no (§6.2).';

-- Per-customer knowledge the grouping stage depends on.
CREATE TABLE satellite.customer_profile (
  customer_code text PRIMARY KEY,
  customer_name text NOT NULL,
  doc_aliases   jsonb NOT NULL DEFAULT '{}',   -- {"TTG":["Receiving Slip Order","Goods Receive Note","Good Receipt"],"PO":["Purchase Order","Surat Pesanan"]}
  expected_docs doc_type[] NOT NULL DEFAULT '{FP,TTG}',  -- full set or subset per customer (note 2)
  link_key      link_key NOT NULL DEFAULT 'po_no',        -- how this customer's TTG points back
  notes         text
);
COMMENT ON COLUMN satellite.customer_profile.link_key IS
  'Observed: Hari Hari prints the SOR as "No Ref." on its Receiving Slip (sor); Boots and Hero print only the PO number (po_no).';

-- §07 open item: "Apakah sudah ada tabel mapping kode produk customer ke kode material SAMB?"
-- Required for the line-item cross-checks in §6.3. If Satellite already has one, drop this and point at it.
CREATE TABLE satellite.product_code_map (
  customer_code      text NOT NULL REFERENCES satellite.customer_profile (customer_code),
  customer_item_code text NOT NULL,            -- K6N302030561 (Boots), 81244362 (Hari Hari)
  customer_barcode   text,                     -- 8852021647342 when printed
  samb_material_code text NOT NULL,            -- FP "Kode": 1000566
  PRIMARY KEY (customer_code, customer_item_code)
);

-- One PDF per SOR, cut from the scan batch at publish time (note 2).
-- Faktur Pajak is appended in the next stage, bumping version.
CREATE TABLE satellite.sor_document (
  sor_no       text PRIMARY KEY REFERENCES satellite.sor (sor_no),
  pdf_path     text NOT NULL,                  -- documents/SOR26110245292.pdf in Satellite's store
  page_count   integer NOT NULL CHECK (page_count > 0),
  version      integer NOT NULL DEFAULT 1,
  source_batch text,                           -- staging.scan_batch.id the pages were cut from
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Columns every doc_* header table shares:
--   sor_no        FK → satellite.sor (note 2: one foreign key to one SOR)
--   page_ref      pages this document occupies inside the SOR PDF (1-based)
--   linked_by     how it was tied to the SOR
--   confidence    lowest field confidence after verification (0–1)
--   source_batch / source_pages   provenance back to the scan

-- Faktur Penjualan — §6.1 "link ke SOR · cek qty & pajak"
-- NOTE: all satellite.doc_* tables are redefined by 008-document-fields.sql, generated from services/common/fields.py.
CREATE TABLE satellite.doc_faktur_penjualan (
  id              bigserial PRIMARY KEY,
  sor_no          text NOT NULL UNIQUE REFERENCES satellite.sor (sor_no),  -- one FP per SOR
  tgl_so          date,
  tgl_jatuh_tempo date,
  nomor_cpo       text,                        -- customer PO number as printed on the FP
  tgl_cpo         date,
  cpo_expired     date,
  warehouse       text,
  zone            text,
  salesman        text,
  jumlah_item     integer,
  subtotal        numeric(18,2),
  dpp             numeric(18,2),               -- §6.1 Dasar Pengenaan Pajak
  ppn             numeric(18,2),               -- §6.1
  total           numeric(18,2),               -- §6.1
  page_ref        integer[] NOT NULL,
  linked_by       link_key NOT NULL DEFAULT 'sor',
  confidence      numeric(4,3),
  source_batch    text,
  source_pages    integer[]
);
COMMENT ON COLUMN satellite.doc_faktur_penjualan.nomor_cpo IS
  'Should equal satellite.sor.cpo_no. A mismatch is a grouping error, not a data error.';

CREATE TABLE satellite.doc_faktur_penjualan_line (
  doc_id        bigint NOT NULL REFERENCES satellite.doc_faktur_penjualan (id) ON DELETE CASCADE,
  line_no       smallint NOT NULL,
  kode_material text NOT NULL,                 -- §6.1 "Kode material (field Kode)"
  nama_produk   text,                          -- §6.1
  kemasan       text,                          -- §6.1  48X85GR
  qty_crt       numeric(12,3),                 -- FP prints "QTY (CRT / PCS)"
  qty_pcs       numeric(12,3),
  harga         numeric(18,2),
  disc          numeric(8,2)[],                -- Disc 1 … Disc 5 as printed
  jumlah        numeric(18,2),
  PRIMARY KEY (doc_id, line_no)
);

-- Tanda Terima Gudang — §6.1 "link ke PO · cek qty vs CGR"
CREATE TABLE satellite.doc_ttg (
  id                bigserial PRIMARY KEY,
  sor_no            text NOT NULL REFERENCES satellite.sor (sor_no),
  customer_doc_name text NOT NULL,             -- 'Receiving Slip Order' | 'Goods Receive Note' | 'Good Receipt'
  document_no       text,                      -- §6.1 Document No (No Receive / NO GRN)
  purchase_order_no text,                      -- §6.1 — links TTG ↔ PO ↔ SO
  no_ref            text,                      -- some customers print the SOR here (Hari Hari)
  vendor_number     text,                      -- §6.1 SAMB's supplier code at the customer
  vendor_name       text,
  posting_date      date,                      -- §6.1 (Tgl Terima / GR Date / Tgl Konfirmasi GRN)
  total             numeric(18,2),
  page_ref          integer[] NOT NULL,
  linked_by         link_key NOT NULL,
  confidence        numeric(4,3),
  source_batch      text,
  source_pages      integer[]
);
CREATE INDEX doc_ttg_po ON satellite.doc_ttg (purchase_order_no);

CREATE TABLE satellite.doc_ttg_line (
  doc_id               bigint NOT NULL REFERENCES satellite.doc_ttg (id) ON DELETE CASCADE,
  line_no              smallint NOT NULL,
  item_code            text NOT NULL,          -- §6.1 customer's code → product_code_map
  material_description text,                   -- §6.1
  qty                  numeric(12,3),          -- §6.1 compared to FP qty and to CGR
  uom                  text,                   -- §6.1 KTN / PC / EA
  PRIMARY KEY (doc_id, line_no)
);

-- PO Customer — §6.1 "link PO ↔ SO · cek harga & diskon"
CREATE TABLE satellite.doc_po (
  id                bigserial PRIMARY KEY,
  sor_no            text NOT NULL REFERENCES satellite.sor (sor_no),
  purchase_order_no text NOT NULL,             -- §6.1
  vendor_code       text,                      -- §6.1
  vendor_name       text,                      -- §6.1 "nama lengkap SAMB"
  po_date           date,
  expiry_date       date,
  delivery_date     date,
  payment_terms     text,                      -- ZP30
  ppn               numeric(18,2),             -- §6.1
  total             numeric(18,2),             -- §6.1
  page_ref          integer[] NOT NULL,
  linked_by         link_key NOT NULL,
  confidence        numeric(4,3),
  source_batch      text,
  source_pages      integer[]
);
CREATE INDEX doc_po_po ON satellite.doc_po (purchase_order_no);

CREATE TABLE satellite.doc_po_line (
  doc_id       bigint NOT NULL REFERENCES satellite.doc_po (id) ON DELETE CASCADE,
  line_no      smallint NOT NULL,
  product_code text NOT NULL,                  -- §6.1
  description  text,                           -- §6.1
  qty          numeric(12,3),                  -- §6.1
  uom          text,                           -- §6.1
  unit_price   numeric(18,2),                  -- §6.1
  discount     numeric(8,2)[],                 -- §6.1 (Boots prints 1st / 2nd / 3rd %)
  total        numeric(18,2),                  -- §6.1
  PRIMARY KEY (doc_id, line_no)
);

-- Surat Jalan — unobserved (SJ). Shape only; fill in when a customer sends one.
CREATE TABLE satellite.doc_surat_jalan (
  id           bigserial PRIMARY KEY,
  sor_no       text NOT NULL REFERENCES satellite.sor (sor_no),
  no_sj        text,                           -- TODO(zulmy)
  tgl_sj       date,                           -- TODO(zulmy)
  page_ref     integer[] NOT NULL,
  linked_by    link_key NOT NULL,
  confidence   numeric(4,3),
  source_batch text,
  source_pages integer[]
);

-- Faktur Pajak — NEXT STAGE. Row shape only, so Billing No has somewhere to land
-- and sor_document.version has a reason to bump. sor_no stays NULL until
-- Billing No → SAP → SOR resolves (§6.2; replaces the handwritten Billing No, P7).
CREATE TABLE satellite.doc_faktur_pajak (
  id                bigserial PRIMARY KEY,
  sor_no            text REFERENCES satellite.sor (sor_no),
  billing_number    text NOT NULL,             -- §6.2 auto-link key
  kode_seri         text,                      -- §6.1
  nsfp              text,
  npwp_pengusaha    text,                      -- §6.1
  nitku_pengusaha   text,
  npwp_pembeli      text,                      -- §6.1
  nitku_pembeli     text,
  dpp               numeric(18,2),             -- §6.1, cross-checked vs FP
  ppn               numeric(18,2),             -- §6.1
  tanggal_transaksi date,                      -- §6.1
  page_ref          integer[],
  source_path       text                       -- softcopy as received; appended to the SOR PDF on link
);
CREATE INDEX doc_faktur_pajak_billing ON satellite.doc_faktur_pajak (billing_number);

-- Dokumen Pelunasan — LATER STAGE. One row per line of the customer's payment
-- document (§6.1 list). Ref No is resolved to an AR → SO SAP → SOR later.
CREATE TABLE satellite.doc_pelunasan_line (
  id                   bigserial PRIMARY KEY,
  payment_document_no  text,                   -- §6.1
  payment_date         date,                   -- §6.1
  total_payment_amount numeric(18,2),          -- §6.1 document-level total
  customer_name        text,                   -- §6.1
  reference_no         text,                   -- §6.1 GR No | Billing No | PO No, per customer
  reference_kind       text,                   -- resolved: gr_no | billing_no | po_no
  vendor_code          text,                   -- §6.1
  vendor_name          text,
  year_month           text,                   -- §6.1
  invoice_receipt_date date,                   -- §6.1
  store_code           text,                   -- §6.1
  due_date             date,                   -- §6.1
  amount               numeric(18,2),          -- §6.1
  line_text            text,                   -- §6.1 "Text"
  sor_no               text REFERENCES satellite.sor (sor_no),  -- NULL until traced
  ar_document          text
);

-- ---------------------------------------------------------------------------
-- staging — the pipeline's memory (Docker Postgres)
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS staging;

-- One row per PDF that lands in the temp repository. The unit of fan-out / fan-in.
CREATE TABLE staging.scan_batch (
  id          text PRIMARY KEY,                -- b-9f2a
  file_name   text NOT NULL,                   -- 7000356304 - 7000356499.pdf
  file_path   text NOT NULL,                   -- oss://scans/2026-09-21/…
  sha256      char(64) NOT NULL UNIQUE,        -- same file twice = same batch, never re-run
  scanned_day date NOT NULL,                   -- the one-folder-per-day key
  page_total  integer NOT NULL CHECK (page_total > 0),
  page_done   integer NOT NULL DEFAULT 0,
  status      text NOT NULL DEFAULT 'received', -- received | reading | grouping | done
  received_at timestamptz NOT NULL DEFAULT now(),
  CHECK (page_done <= page_total)
);
COMMENT ON COLUMN staging.scan_batch.page_done IS
  'Fan-in counter. Each page worker runs UPDATE … SET page_done = page_done + 1 RETURNING page_done, page_total; the worker that sees them equal publishes {batch_id} to q.group.';

-- One row per page. Written by the page worker; never updated by grouping.
CREATE TABLE staging.page (
  batch_id        text NOT NULL REFERENCES staging.scan_batch (id),
  page_no         integer NOT NULL CHECK (page_no > 0),
  image_path      text NOT NULL,               -- upright image after enhancement
  rotation        smallint NOT NULL DEFAULT 0, -- degrees applied to make it upright
  black_ratio     numeric(4,3),                -- share of black pixels; > 0.40 = feeder artefact
  classical_text  text,                        -- non-AI OCR output: the verifier's ground truth
  doc_type        doc_type,                    -- Jev's answer
  doc_type_conf   numeric(4,3),
  is_continuation boolean NOT NULL DEFAULT false,
  footer          text,                        -- "Hal 1/1", "Page 5 of 35" as printed
  keys            jsonb NOT NULL DEFAULT '{}', -- {"sor":…, "po_no":…, "document_no":…, "billing_no":…} each with confirmed_by
  fields          jsonb NOT NULL DEFAULT '{}', -- VLM output: header + lines, every value with source_text
  status          page_status NOT NULL DEFAULT 'queued',
  model_vlm       text,
  model_ms        integer,
  model_cost_idr  numeric(12,2),
  read_at         timestamptz,
  PRIMARY KEY (batch_id, page_no)
);
CREATE INDEX page_status ON staging.page (status);

-- Page-level verification. One row per extracted field.
CREATE TABLE staging.field_check (
  id                bigserial PRIMARY KEY,
  batch_id          text NOT NULL,
  page_no           integer NOT NULL,
  field_path        text NOT NULL,             -- header.total · lines[2].qty
  vlm_value         text,
  classical_match   boolean NOT NULL,          -- found in classical_text after digit/punctuation normalisation
  adjudicated_value text,                      -- Model B's answer; only when classical_match = false
  adjudicator       text,                      -- model id, different family from the VLM
  status            text NOT NULL,             -- ok | corrected | unresolved
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no)
);

-- Level 1 of grouping: pages → documents. A CONTINUATION page extends page_to.
CREATE TABLE staging.document (
  id            bigserial PRIMARY KEY,
  batch_id      text NOT NULL REFERENCES staging.scan_batch (id),
  doc_type      doc_type NOT NULL,
  page_from     integer NOT NULL,
  page_to       integer NOT NULL,
  customer_code text,
  key_sor       text,                          -- FP: printed SOR · TTG (some customers): No Ref
  key_po_no     text,                          -- TTG / PO: customer PO number
  resolved_sor  text,                          -- after satellite.sor lookup or fallback
  linked_by     link_key,
  confidence    numeric(4,3),
  CHECK (page_to >= page_from)
);
CREATE INDEX document_resolved_sor ON staging.document (resolved_sor);

-- Level 2 of grouping: documents → SOR. One open bundle per SOR at a time;
-- a TTG that arrives in a later batch joins the open bundle for its SOR.
CREATE TABLE staging.bundle (
  id           bigserial PRIMARY KEY,
  sor_no       text NOT NULL,
  status       bundle_status NOT NULL DEFAULT 'grouping',
  json         jsonb NOT NULL DEFAULT '{}',    -- the per-SOR JSON handed to publish
  checks       jsonb NOT NULL DEFAULT '{}',    -- §6.3: qty_fp_vs_ttg · kode_match · dpp_ppn · sor_in_satellite · cgr_qty · vendor_is_samb · sequence_ok
  confidence   numeric(4,3),
  reviewed_by  text,
  reviewed_at  timestamptz,
  published_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX bundle_open_per_sor ON staging.bundle (sor_no) WHERE status <> 'published';

CREATE TABLE staging.bundle_document (
  bundle_id   bigint NOT NULL REFERENCES staging.bundle (id),
  document_id bigint NOT NULL REFERENCES staging.document (id),
  PRIMARY KEY (bundle_id, document_id)
);
