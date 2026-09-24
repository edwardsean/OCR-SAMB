-- Phase 5: every value the AI OCR read is checked against the page by plain code (common/verify.py),
-- one staging.field_check row per value. No verifier model (decided with the user 2026-09-24): a value is ok only when
-- something printed backs it; otherwise a person checks it (phase 7 Review).
ALTER TABLE staging.page ADD COLUMN IF NOT EXISTS verify_version integer;
ALTER TABLE staging.field_check
  ADD COLUMN IF NOT EXISTS source_text  text,   -- what the AI OCR says is printed (header fields)
  ADD COLUMN IF NOT EXISTS confirmed_by text,   -- status ok: text (in Tesseract's text) | qr | adds_up (FP DPP + PPN = Total)
  ADD COLUMN IF NOT EXISTS reason       text;   -- status check: why a person must look
CREATE UNIQUE INDEX IF NOT EXISTS field_check_page_path ON staging.field_check (batch_id, page_no, field_path);
COMMENT ON COLUMN staging.field_check.status IS 'ok | check | empty (phase 5, code); corrected (a person, phase 7)';
COMMENT ON COLUMN staging.field_check.classical_match IS 'value printed in Tesseract''s text (same line, letters+digits, not inside a longer number)';
COMMENT ON COLUMN staging.field_check.adjudicated_value IS 'phase 7: the value a person corrected it to';
COMMENT ON COLUMN staging.field_check.adjudicator IS 'phase 7: who corrected it (a person; there is no verifier model)';
COMMENT ON COLUMN staging.page.fields IS 'AI OCR output for the decided type: header fields {value, source_text} + line items. Each value''s check is a staging.field_check row.';
