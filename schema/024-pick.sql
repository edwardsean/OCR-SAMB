-- read-then-map, Stage 2a: the page viewer's clickable boxes, made once per page in the worker (worker/boxes.py):
-- {v: "<transcript_version>|b<VERSION>", units: [{id, block, i, s, e, text, tess, match, box}], made: {match: n}}.
-- The viewer uses them while v matches the page's transcript; otherwise it pairs the copy with Tesseract's own
-- reading of the page (common/boxes.match), without the worker's closer reads.
ALTER TABLE staging.page ADD COLUMN IF NOT EXISTS pick jsonb;
