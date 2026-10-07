-- read-then-map, Stage 2c (2026-09-30): what people taught, as a wiki (Karpathy's LLM-wiki pattern) kept behind a gate.
-- One page per document type (markdown): "## Any customer", then "## <customer> (chain <id>)" sections; each claim is
-- one line "- <field>: <what to do> [<kind> · <anchor> · pages <batch>/<page>, …]". The text model's second mapping
-- (pass B, after Jev) gets "Any customer" plus the page's own customer's section, never the AI OCR. Versions work like
-- Jev's context (staging.context_version): a proposal is replayed on stored pages (the gate) before it can be active.
-- The pages name real customers: they live here, never in git.
CREATE TABLE IF NOT EXISTS staging.knowledge_page (
  doc_type     text NOT NULL,
  version      int  NOT NULL,          -- per type: TTG #1, #2, …
  parent       int,                    -- the version it was built on (NULL: the first)
  status       text NOT NULL CHECK (status IN ('proposed', 'active', 'rejected', 'retired')),
  markdown     text NOT NULL,
  source       text NOT NULL CHECK (source IN ('marked', 'typed', 'person', 'teacher')),
                                       -- drafted from corrections marked on the paper (active by itself once the gate
                                       -- passes), from typed ones, or written by a person or the teacher (approved)
  gate         jsonb,                  -- the replay: pages, right/wrong before and after, passed
  note         text,                   -- the log line: what changed and why
  created_by   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  approved_by  text,
  approved_at  timestamptz,
  PRIMARY KEY (doc_type, version),
  FOREIGN KEY (doc_type, parent) REFERENCES staging.knowledge_page (doc_type, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_page_one_active ON staging.knowledge_page (doc_type) WHERE status = 'active';

-- Pass B's answers, per page and per knowledge it was given (hints_sha): the gate's replay pays for them once, and the
-- page, run again after the knowledge is approved, reuses them (no second call).
CREATE TABLE IF NOT EXISTS staging.knowledge_map (
  batch_id     text NOT NULL,
  page_no      int  NOT NULL,
  hints_sha    text NOT NULL,
  map_version  text NOT NULL,          -- pass A's mapping version: the same transcript, field list and text model
  raw          jsonb NOT NULL,         -- the text model's answer
  raw2         jsonb,                  -- its second answer (VF_MAP_TWICE), merged like pass A's
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, page_no, hints_sha, map_version),
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no) ON DELETE CASCADE
);

COMMENT ON TABLE staging.knowledge_page IS
  'what people taught per document type and customer (read-then-map Stage 2c): versioned markdown, gated by a replay';
COMMENT ON TABLE staging.knowledge_map IS
  'the text model''s pass-B answers per page and knowledge, kept so the gate and the page never pay twice';
