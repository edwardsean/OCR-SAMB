-- What happened, when, and how long it took (the user, 2026-10-08: "we need monitoring and observability for
-- developers … trace all of it for a single file or single batch"). Written by common/trace.py; read by the Teknis
-- screens Jejak and Metrik (services/api/observe.py) and by a batch's step 1 (what each page is doing now).
--
-- One row per span (a piece of work with a start and an end: a page, one of its stages, a grouping round, a lesson,
-- a job) or event (a moment: a page sent to the queue, an order's status changing, a person's action). A span is
-- written 'running' when it starts and closed with its status and duration, so what is running now is visible, and
-- one a crash cut off stays 'running' until the next span of the same page closes it.
-- The AI calls stay in staging.model_call (the budget's ledger); the screens show them beside these rows.
CREATE TABLE IF NOT EXISTS staging.trace (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT clock_timestamp(),   -- start (a span) or the moment (an event)
  ended_at  timestamptz,
  ms        integer,
  status    text NOT NULL CHECK (status IN ('running', 'ok', 'fail', 'wait', 'skip', 'info')),
  kind      text NOT NULL,          -- page, page.read, file.split, page.queued, group, bundle.status, person.label, …
  service   text,                   -- the service and container that did it (rtm-worker@3f2a1c)
  batch_id  text,                   -- the scan (a file); its upload batch is scan_batch.upload_id
  page_no   integer,
  sor_no    text,
  parent    bigint,                 -- the span this one is part of
  who       text,                   -- the person, for what a person did
  detail    jsonb,
  error     text
);
CREATE INDEX IF NOT EXISTS trace_page ON staging.trace (batch_id, page_no, at);
CREATE INDEX IF NOT EXISTS trace_at ON staging.trace (at DESC);
CREATE INDEX IF NOT EXISTS trace_kind ON staging.trace (kind, at DESC);
CREATE INDEX IF NOT EXISTS trace_sor ON staging.trace (sor_no, at) WHERE sor_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS trace_running ON staging.trace (batch_id, page_no) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS trace_parent ON staging.trace (parent) WHERE parent IS NOT NULL;

-- the AI calls, read beside the trace by file and page, and by time (Metrik)
CREATE INDEX IF NOT EXISTS model_call_page ON staging.model_call (batch_id, page_no, at);
CREATE INDEX IF NOT EXISTS model_call_at ON staging.model_call (at DESC);

-- Each AI call's exact request and response (the user, 2026-10-08: "the input and output payloads"): the body sent
-- to the model (the prompt, the field list, the model's settings; an image as a placeholder, never its bytes: the page
-- image is in storage) and the raw answer that came back. Kept PAYLOAD_KEEP_DAYS (14): ~100 KB a page.
CREATE TABLE IF NOT EXISTS staging.ai_payload (
  call_id   bigint PRIMARY KEY REFERENCES staging.model_call(id) ON DELETE CASCADE,
  at        timestamptz NOT NULL DEFAULT now(),
  http      integer,                -- the HTTP status of the last attempt
  request   jsonb,
  response  jsonb
);
CREATE INDEX IF NOT EXISTS ai_payload_at ON staging.ai_payload (at);
