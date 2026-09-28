-- Phase 7c: comparing the documents of one bundle (Problem Statement §6.3). Safe to re-run.
--
-- The FP is printed once, with the goods, and never again (the user, 2026-09-25): it shows the SO AS ORDERED. Satellite's
-- header carries those amounts beside the invoice ones (subtotal_after_discount_amount, vat_amount, grand_total_amount;
-- equal to the inv_ amounts on the 31,747 SOs a tolakan didn't change).
ALTER TABLE satellite.sor
  ADD COLUMN IF NOT EXISTS order_dpp       numeric(18,2),   -- subtotal_after_discount_amount: the FP's DPP
  ADD COLUMN IF NOT EXISTS order_ppn       numeric(18,2),   -- vat_amount: the FP's PPN
  ADD COLUMN IF NOT EXISTS order_total     numeric(18,2),   -- grand_total_amount: the FP's Total
  ADD COLUMN IF NOT EXISTS customer_parent text;            -- ship_to_parent_customer_id: the chain every store is under

-- satellite.product_code_map (base schema, §07's open item) holds which of SAMB's items a customer's own code names,
-- per chain: customer_code is the chain (ship_to_parent_customer_id, e.g. 1100002447 for every Hero DC store), with its
-- customer_profile row. It grows one pair at a time: the AI proposes a matching, code checks the numbers, a person
-- confirms the pair once (Review, 7d); from then on it matches with no AI. A printed barcode (customer_barcode) names
-- one product whoever prints it, so it is looked up across chains too.
ALTER TABLE satellite.product_code_map
  ADD COLUMN IF NOT EXISTS description  text,               -- the customer's wording when it was confirmed
  ADD COLUMN IF NOT EXISTS confirmed_by text,
  ADD COLUMN IF NOT EXISTS confirmed_at timestamptz DEFAULT now();
CREATE INDEX IF NOT EXISTS product_code_map_barcode ON satellite.product_code_map (customer_barcode);

-- Each customer line (a PO or TTG row) and the SO line it is: how that is known, and whether a person has said so.
CREATE TABLE IF NOT EXISTS staging.line_match (
  batch_id        text NOT NULL,
  page_no         integer NOT NULL,
  row_index       integer NOT NULL,                         -- lines[i] of the page's reading
  sor_no          text NOT NULL,
  so_line_no      integer,                                  -- NULL: no SO line (a bonus row, or none fits)
  how             text NOT NULL,                            -- map · only_line · numbers · po_row · ai · person
  status          text NOT NULL CHECK (status IN ('matched', 'proposed', 'refused')),
  reason          text,
  customer_code   text,
  ean             text,
  proposed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, page_no, row_index)
);

-- The bundle's checks (§6.3) and status: auto_ok only when every required value is resolved and every applicable check
-- passes; a reviewed bundle whose inputs change (its fingerprint) goes back to needs_review.
ALTER TABLE staging.bundle
  ADD COLUMN IF NOT EXISTS fingerprint text,
  ADD COLUMN IF NOT EXISTS checked_at  timestamptz;
