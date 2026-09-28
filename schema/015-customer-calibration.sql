-- Verification redesign, S3: what a person confirms once per customer (the user, 2026-09-26/27). Safe to re-run.
--
-- A customer is a chain: satellite.sor.customer_parent (ship_to_parent_customer_id), since Satellite's customer group is
-- empty. Until a person has looked at a chain's first bundle, its bundles go to Review; after that only anomalies do.
--
--   rounding_allowance  how far this customer's totals (and, with no usable total, each printed row amount) may be
--                       from Satellite's and still be the same: its rounding. Hero prints carton prices rounded to
--                       whole rupiah (129,578.00 for 5,399.10 × 24), so its POs end 9–11 rupiah from the order.
--                       NULL until confirmed: Rp 5 applies, and the chain's bundles wait for that first look.
--   receipt_shows       what this customer's receipt prints after a rejection: 'received' (the lower total) or
--                       'ordered' (the whole order). Asked on the chain's first bundle whose SO Satellite records a
--                       tolakan on. 'ordered': its receipts can't show a tolakan, so a bundle with one goes to a person.
ALTER TABLE satellite.customer_profile
  ADD COLUMN IF NOT EXISTS rounding_allowance numeric(10,2),
  ADD COLUMN IF NOT EXISTS allowance_by       text,
  ADD COLUMN IF NOT EXISTS allowance_at       timestamptz,
  ADD COLUMN IF NOT EXISTS receipt_shows      text CHECK (receipt_shows IN ('received', 'ordered')),
  ADD COLUMN IF NOT EXISTS receipt_by         text,
  ADD COLUMN IF NOT EXISTS receipt_at         timestamptz;
