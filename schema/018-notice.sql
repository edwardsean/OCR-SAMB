-- vlm-first (vf database only): notices of bundles that need a person (2026-09-29, Stage 2).
-- n8n's "vf — needs you" schedule calls vf-ui every 5 minutes (/internal/vf/notify): each bundle that newly needs a
-- person (status needs_review, with a fingerprint no earlier notice had) goes into one notice. The UI shows the ones
-- not seen yet as a count on the Review tab and a "new since you last looked" list on /review; opening /review marks
-- them seen. Only in the UI for now (the user's choice); a channel (Telegram, email) is one more node in n8n.
CREATE TABLE IF NOT EXISTS staging.notice (
  id       bigserial PRIMARY KEY,
  at       timestamptz NOT NULL DEFAULT now(),
  kind     text NOT NULL DEFAULT 'needs_you',
  items    jsonb NOT NULL,       -- [{batch, sor, fingerprint, customer, reasons}] : the bundles this notice is about
  text     text NOT NULL,        -- one line, as a person reads it
  seen_at  timestamptz           -- NULL = not seen yet (opening /review sees it)
);
CREATE INDEX IF NOT EXISTS notice_unseen ON staging.notice (at) WHERE seen_at IS NULL;

COMMENT ON TABLE staging.notice IS
  'bundles that newly need a person, recorded by n8n''s schedule (/internal/vf/notify); shown in the UI until seen';
