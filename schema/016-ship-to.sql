-- Verification redesign S4 (vf database only): keys by the store the page is delivered to.
-- A key only the AI OCR read (a PO number, an SOR reference) that matches exactly one SO, with other SOs one character
-- away, links when the store printed on the page names that SO's ship-to and none of the others'. The store is asked
-- only then, in one blind question (the page image; never Satellite's store names), outside the AI's field list: a
-- field there would change every reading's version and re-read every page.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS ship_to jsonb;   -- {value, source_text, unsure, model, at}: the AI's answer; NULL = not asked

COMMENT ON COLUMN staging.page.ship_to IS
  'S4: the store the page says the goods go to, asked blind only when it can decide a key (worker/vf.py store_step)';
