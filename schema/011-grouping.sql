-- Phase 6: grouping (pages → documents → SOR), built on branch vlm-first; the shape is the same for v1.
-- Safe to re-run.
--
-- A page links only on RESOLVED keys: backed by print (Tesseract's reading, the QR code, the zoomed spot, a look-again
-- that print backs), matching Satellite's SO record, or confirmed by a person. Page order is used for one thing only:
-- a CONTINUATION page belongs to the document on the page before it.

-- A person's confirmation (or correction) of one value on one page. Kept apart from staging.field_check, which is
-- recomputed on every re-check, so a person's decision survives re-runs.
CREATE TABLE IF NOT EXISTS staging.field_confirmation (
  batch_id     text NOT NULL,
  page_no      integer NOT NULL,
  field        text NOT NULL,                  -- the type's field name, e.g. sor, nomor_cpo, purchase_order_no
  value        text NOT NULL,                  -- what the person says is printed
  confirmed_by text NOT NULL,
  confirmed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, page_no, field),
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no) ON DELETE CASCADE
);

ALTER TABLE staging.document
  ADD COLUMN IF NOT EXISTS keys          jsonb,     -- the resolved keys the link could use: {sor, po_no} with how
  ADD COLUMN IF NOT EXISTS evidence      jsonb,     -- how it was linked (or why not): readable steps
  ADD COLUMN IF NOT EXISTS hold_reason   text,      -- NULL = placed in its SOR's bundle
  ADD COLUMN IF NOT EXISTS suggested_sor text;      -- a hint for the person, never a link

ALTER TABLE staging.bundle
  ADD COLUMN IF NOT EXISTS hold_reason   text,      -- e.g. fp_missing; NULL = FP + documents together
  ADD COLUMN IF NOT EXISTS folder        text;      -- storage folder holding the bundle's pages (one per SOR)
