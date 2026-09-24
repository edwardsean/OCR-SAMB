-- Phase 3: page classification. "Don't know" is a first-class answer.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS type_status      text,           -- decided | unsure
  ADD COLUMN IF NOT EXISTS type_guess       doc_type,       -- best guess, also when unsure (shown to a person, never used to link)
  ADD COLUMN IF NOT EXISTS type_votes       jsonb,          -- {keyword, jev:{choice,confidence,probabilities}, layout, qr}
  ADD COLUMN IF NOT EXISTS layout_score     numeric(4,3),   -- similarity to SAMB's Faktur Penjualan print layout
  ADD COLUMN IF NOT EXISTS enhance_version  integer,        -- re-runs skip enhance + OCR when this matches the code
  ADD COLUMN IF NOT EXISTS classify_version integer;
COMMENT ON COLUMN staging.page.doc_type IS 'Decided type; NULL when type_status = unsure. Only a decided type may be used by grouping.';
-- Pages read by the phase 2 code are enhance version 1.
UPDATE staging.page SET enhance_version = 1 WHERE upright_path IS NOT NULL AND enhance_version IS NULL;
