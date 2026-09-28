-- vlm-first experiment only (database ocr_vf; never applied to v1's ocr).
-- AI OCR reads every page against ONE combined field list, Jev classifies from that reading, Tesseract checks
-- afterwards, a teacher model proposes changes to Jev's context from people's labels.

-- Jev's context, versioned. content = {fields: {name: {...}}, types: {FP..OTHER: {..., fields: [{name, how_often, note}]}}}
-- Invariant (common/context.validate): the union of all types' field names = the combined field list.
CREATE TABLE IF NOT EXISTS staging.context_version (
  version      integer PRIMARY KEY,
  parent       integer REFERENCES staging.context_version (version),
  status       text NOT NULL CHECK (status IN ('proposed', 'active', 'rejected', 'retired')),
  content      jsonb NOT NULL,
  gate         jsonb,                      -- replay result: practice labels + anchors, before vs after
  created_by   text NOT NULL,              -- seed | teacher:<model> | a person
  created_at   timestamptz NOT NULL DEFAULT now(),
  approved_by  text,
  approved_at  timestamptz,
  note         text
);
CREATE UNIQUE INDEX IF NOT EXISTS context_version_one_active ON staging.context_version ((status)) WHERE status = 'active';

-- A person's label on a practice-pile page the machine got unsure or wrong: something the teacher can learn from.
CREATE TABLE IF NOT EXISTS staging.lesson (
  id            bigserial PRIMARY KEY,
  batch_id      text NOT NULL,
  page_no       integer NOT NULL,
  label         doc_type NOT NULL,
  status        text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'proposed', 'no_change', 'failed')),
  teacher_model text,
  answer        jsonb,                     -- the teacher's evidence, reason and proposed change
  proposal      integer REFERENCES staging.context_version (version),
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  asked_at      timestamptz,
  UNIQUE (batch_id, page_no),
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no) ON DELETE CASCADE
);

-- Every model call: the free tiers are small, so every call is counted per Google's day (midnight Pacific).
CREATE TABLE IF NOT EXISTS staging.model_call (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  pacific_day date NOT NULL,
  provider    text NOT NULL,               -- gemini | jev | zai
  model       text,
  purpose     text NOT NULL,               -- read_all | second_look | classify | replay | teach
  batch_id    text,
  page_no     integer,
  ok          boolean NOT NULL,
  ms          integer,
  tokens      jsonb,
  error       text
);

ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS fields_all      jsonb,     -- the AI OCR's reading against the combined list: {name: {value, source_text, box}} + lines
  ADD COLUMN IF NOT EXISTS fields_version  text,      -- which combined field list it read against (reused while unchanged)
  ADD COLUMN IF NOT EXISTS context_version integer,   -- which Jev context classified it
  ADD COLUMN IF NOT EXISTS zoom            jsonb,     -- Tesseract re-reading each checked value's spot, zoomed in
  ADD COLUMN IF NOT EXISTS second_look     jsonb,     -- the AI OCR's blind second look at fields Tesseract didn't back
  ADD COLUMN IF NOT EXISTS prep_version    integer,   -- image preparation (upright, straighten, QR) done by vlm-first
  ADD COLUMN IF NOT EXISTS outcome         text;
-- clear: every §6.1 value backed by print · waiting_ai: the AI OCR still has to read the page or look again (skipped,
-- out of budget, or the call failed: `python -m worker.vf again`) · needs_person: still not backed after the look-again
-- · held_unsure: waiting for a label. A value never goes to a person before the AI OCR has looked again.
ALTER TABLE staging.page DROP CONSTRAINT IF EXISTS page_outcome_check;
ALTER TABLE staging.page ADD CONSTRAINT page_outcome_check
  CHECK (outcome IN ('clear', 'waiting_ai', 'needs_person', 'held_unsure'));
COMMENT ON COLUMN staging.page.fields IS 'vlm-first: fields_all projected onto the page''s type (per-type names, as v1), so the phase-5 check and table mapping are unchanged.';
