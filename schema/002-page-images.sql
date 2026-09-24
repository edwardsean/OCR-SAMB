-- Phase 1: intake stores two images per page and needs a state between "split" and "queued".
ALTER TYPE page_status ADD VALUE IF NOT EXISTS 'rendered' BEFORE 'queued';

ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS original_path text,   -- 300 dpi render of the page exactly as scanned
  ADD COLUMN IF NOT EXISTS thumb_path    text;   -- small JPEG for the batch grid
COMMENT ON COLUMN staging.page.image_path IS
  'Upright image after enhancement (phase 2). Until then it equals original_path.';

ALTER TABLE staging.scan_batch
  ADD COLUMN IF NOT EXISTS pages_rendered integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS error          text;
COMMENT ON COLUMN staging.scan_batch.status IS
  'splitting → split → queued (phase 1) → reading → grouping → done';
