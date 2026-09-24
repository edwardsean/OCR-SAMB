-- Phase 4: AI OCR extraction. A failed extraction is recorded on the page; it does not fail the page.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS extract_status  text,     -- done | failed | skipped (unsure / OTHER / no schema)
  ADD COLUMN IF NOT EXISTS extract_error   text,
  ADD COLUMN IF NOT EXISTS extract_version integer,
  ADD COLUMN IF NOT EXISTS vlm_meta        jsonb,    -- model that answered, fell_back_from, ms, tokens
  ADD COLUMN IF NOT EXISTS vlm_read        jsonb;    -- type-agnostic reading, only for pages classification was unsure about
COMMENT ON COLUMN staging.page.fields IS 'AI OCR output for the decided type: header fields {value, source_text} + line items. UNVERIFIED until phase 5.';
COMMENT ON COLUMN staging.page.keys IS 'Keys derived by code from fields + QR: sor, po_no, … each with how it was confirmed. Only confirmed keys may be used for grouping.';
