-- read-then-map (the mentor, 2026-09-29): the AI OCR copies everything on a page (no field list), then a text model
-- maps the copy onto the field list and collects notes (common/transcript.py). vf database / its worktree copies only.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS transcript jsonb,            -- blocks [{id, kind, text, cells?, box, about?}]: the AI's copy
  ADD COLUMN IF NOT EXISTS transcript_version text,     -- model + prompt + page image it was made from
  ADD COLUMN IF NOT EXISTS transcript_status text,      -- done | failed (the mapping's own status is extract_status)
  ADD COLUMN IF NOT EXISTS notes jsonb,                 -- [{kind, text, blocks, about, box}]: handwriting, stamps, marks
  ADD COLUMN IF NOT EXISTS mapping jsonb;               -- where each value came from: {fields, rows, dropped, chain}

COMMENT ON COLUMN staging.page.transcript IS 'the AI OCR''s copy of the whole page, never a witness (read-then-map)';
COMMENT ON COLUMN staging.page.notes IS 'handwriting, stamps, marks and remarks the text model found: AI claims, shown, never checked';

-- A trial reading beside the page's own, to measure the two-step reading before adopting it (python -m worker.vf
-- trial). Nothing in the pipeline reads this table; adopting copies a trial's transcript to the page.
CREATE TABLE IF NOT EXISTS staging.reading_trial (
  batch_id    text NOT NULL,
  page_no     int  NOT NULL,
  variant     text NOT NULL,                             -- e.g. 'vlm-map' (the AI OCR maps) or 'text-map' (a text model)
  transcript  jsonb,
  fields_all  jsonb,
  notes       jsonb,
  mapping     jsonb,
  versions    jsonb,                                     -- {transcript, map}
  meta        jsonb,                                     -- tokens and ms per call; a second mapping for the flip rate
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, page_no, variant),
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no) ON DELETE CASCADE
);
