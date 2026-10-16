-- Stopping a file's AI work, and going on with it later (the user, 2026-10-15: "a stop button to stop a working batch").
-- A stopped file has status 'stopped' (scan_batch.status is plain text); these say who stopped it, when, and the status
-- it had, which "Lanjutkan membaca" goes back to (common/stopping.py). Its unread pages are status 'failed' with an
-- error starting "Stopped on purpose" (api/stuck.py's convention). Safe to run again.
ALTER TABLE staging.scan_batch ADD COLUMN IF NOT EXISTS stopped_at   timestamptz;
ALTER TABLE staging.scan_batch ADD COLUMN IF NOT EXISTS stopped_by   text;
ALTER TABLE staging.scan_batch ADD COLUMN IF NOT EXISTS stopped_from text;
COMMENT ON COLUMN staging.scan_batch.stopped_at IS 'When a person stopped this file''s AI work (status stopped); NULL once it goes on.';
COMMENT ON COLUMN staging.scan_batch.stopped_by IS 'Who stopped it.';
COMMENT ON COLUMN staging.scan_batch.stopped_from IS 'The status it had when stopped (received, splitting, split, queued, reading, read): where going on starts.';
