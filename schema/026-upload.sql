-- read-then-map (2026-10-05): the upload batch. The user: "for an upload (which can be a lot of documents) we should
-- input the uploader's name, date, and a generated batch number, so that in view we can see the separated processes
-- per batch … I was confused on when and who and what batch is this document".
-- One upload action = one batch, however many files: a generated number (BATCH-YYYYMMDD-NN, by the day it was made,
-- WIB), who uploaded it, and the date they give (the day these papers were scanned; it is every file's scanned_day,
-- which the date checks use). Each file stays a scan (staging.scan_batch), now belonging to its batch.
CREATE TABLE IF NOT EXISTS staging.upload (
  id           serial PRIMARY KEY,
  code         text NOT NULL UNIQUE,         -- BATCH-20261005-01: the day it was made, then a counter for that day
  uploaded_by  text NOT NULL,                -- as the person typed it ('(tidak tercatat)' for scans from before)
  doc_date     date NOT NULL,                -- the date the person gives: the day the papers were scanned
  created_at   timestamptz NOT NULL DEFAULT now(),
  note         text
);
ALTER TABLE staging.scan_batch ADD COLUMN IF NOT EXISTS upload_id int REFERENCES staging.upload (id);
CREATE INDEX IF NOT EXISTS scan_batch_upload ON staging.scan_batch (upload_id);

COMMENT ON TABLE staging.upload IS
  'one upload action (one or many files): its batch number, who uploaded it and the scan date they gave';
