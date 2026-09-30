-- read-then-map, Stage 2a (2026-09-30): a person's correction as an EXAMPLE for the knowledge to learn from.
-- A correction fixes its page at once (staging.field_confirmation, as before); the example keeps what the knowledge
-- needs: where the person marked the value on the paper, what was printed there, and what is printed beside it (the
-- label to its left, the column header above). Nothing is learned from examples yet (Stage 2c).
CREATE TABLE IF NOT EXISTS staging.extract_example (
  id           bigserial PRIMARY KEY,
  batch_id     text NOT NULL,
  page_no      int  NOT NULL,
  confirmation text NOT NULL,        -- the field_confirmation it came from: its field (lines[<row key>].<col> for a cell)
  doc_type     text,                 -- the page's type when the person corrected it
  chain        text,                 -- the customer (satellite.chain_of) when the page's order is known
  field        text NOT NULL,        -- the type's field name, or lines.<col> for a row cell
  row_key      text,                 -- a row cell's row (satellite.row_keys)
  kind         text NOT NULL CHECK (kind IN ('value', 'not_printed')),
  value        text,                 -- what the person gave
  shown        text,                 -- what the reading had
  region       jsonb,                -- [ymin, xmin, ymax, xmax] on 0-1000 where it is printed (marked, or found)
  tess_words   text,                 -- Tesseract's words inside the region
  blocks       jsonb,                -- the transcript blocks there: [{id, kind, text}] (a snapshot: transcripts are remade)
  anchor       jsonb,                -- what is printed beside it: {left, above, header_cell, under_kind}
  printed      boolean,              -- Tesseract read the value inside the region
  source       text NOT NULL CHECK (source IN ('marked', 'typed')),   -- marked on the paper, or typed on Review
  pile         text NOT NULL CHECK (pile IN ('practice', 'exam')),    -- drawn once: the exam pile scores knowledge
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'contradicted')),
  made_by      text,
  made_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (batch_id, page_no, confirmation) REFERENCES staging.field_confirmation (batch_id, page_no, field)
    ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS extract_example_active ON staging.extract_example (doc_type, field) WHERE status = 'active';

COMMENT ON TABLE staging.extract_example IS
  'a person''s correction kept as an example for learned extraction knowledge (read-then-map Stage 2); undone with its confirmation';
